# Test-Machine Setup (prod DB, read-only)

Run the web UI + monitor on a separate machine against the **prod database**, with
writes blocked at the Postgres level — not just suppressed by application flags.

---

## Write safety model

Two independent layers prevent prod DB writes from the test machine:

| Layer | Mechanism | What it blocks |
|---|---|---|
| **DB role** | `horyon_readonly` — `SELECT` only, no `INSERT`/`UPDATE`/`DELETE` granted | Every write attempt fails at Postgres with "permission denied for table …" |
| **App flag** | `DISABLE_BOT=true` — bot process sleeps before any Telegram/cron code runs | Automatic ingest, digest, audio, entity crons, Telegram polling |

The DB role is enforced by Postgres regardless of what the application code tries to
do. Even `docker exec horyon-bot python3 -m app.digest` against this role will fail
on every INSERT/UPDATE (it will try, but Postgres will reject it).

---

## What runs on the test machine

| Service | Behaviour |
|---|---|
| `horyon-web` | Reads prod DB via SSH tunnel; all routes work (web is already read-only by design) |
| `horyon-monitor` | Reads prod DB via SSH tunnel |
| `horyon-bot` | Starts idle: `DISABLE_BOT=true` suspends all crons before any DB call |
| `horyon-db` | Starts locally (empty, unused) — satisfies `depends_on` only |
| `horyon-caddy` | Not started (dev overlay excludes it) |

---

## One-time prod setup

The `horyon_readonly` role must exist on the prod DB. It was created when this doc
was written, but re-run this after adding new tables to extend the grants:

```bash
# On the prod machine:
READONLY_PW=$(grep READONLY_DB_PASSWORD .env | cut -d= -f2-)
docker exec -i horyon-db psql -U crypto -d crypto \
  -v "readonly_pw=$READONLY_PW" -f - < deploy/readonly_db_role.sql
```

`READONLY_DB_PASSWORD` is already set in prod `.env`. The SQL is idempotent.

---

## Step-by-step (test machine)

### 1. Open the SSH tunnel (keep this terminal open)

```bash
ssh -L 0.0.0.0:15433:127.0.0.1:5433 user@PROD_IP -N
```

Port **15433** avoids conflict with the local `db` container which binds
`127.0.0.1:5433` on the host. Use `autossh -M 0` to keep it alive across drops.

> **Why `0.0.0.0:` and not the usual loopback bind?** On a **Linux** test host the
> containers reach the tunnel via `host.docker.internal`, which resolves to the Docker
> **bridge-gateway** IP (e.g. `172.x.0.1`) — *not* `127.0.0.1`. A tunnel bound only to
> loopback (`ssh -L 15433:…`) is therefore unreachable from inside the containers and
> every page errors with a connection refused. Binding the local end to `0.0.0.0` lets
> the bridge reach it. (On Docker Desktop for Mac/Windows the loopback bind happens to
> work — but your machines are Linux.)
>
> **Lock the port down** — `0.0.0.0` listens on every interface. The host's
> `ufw default deny incoming` already blocks the public side; add an explicit allow for
> just the Docker bridge subnet (same pattern as the host Ollama rule):
>
> ```bash
> sudo ufw allow from 172.16.0.0/12 to any port 15433 proto tcp
> ```
>
> Do **not** open 15433 to anything else. The credential is read-only, but there's no
> reason to expose even that.

### 2. Prepare the bind-mount file

```bash
touch cookies.txt
```

The bot service requires this file to exist; Docker otherwise creates a directory
there. The bot never reads it (`DISABLE_BOT=true`), but the mount must not fail.

### 3. Create `.env`

Minimum required:

```dotenv
POSTGRES_PASSWORD=anything   # initialises the local (unused) db container
READONLY_DB_PASSWORD=<copy from prod .env>
```

Everything else is either overridden by the overlay or irrelevant (bot is disabled).

### 4. Build images

```bash
docker compose build bot web
```

### 5. Launch

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  -f docker-compose.external-db.yml \
  up -d db bot monitor web
```

- Web → http://localhost:3000 (prod data, read-only at DB level)
- Monitor → http://localhost:8090

### 6. Verify the tunnel and read-only enforcement

```bash
# Tunnel is live:
psql "postgresql://horyon_readonly:YOUR_READONLY_PASSWORD@localhost:15433/crypto" \
  -c "SELECT MAX(date) FROM crypto_digest;"

# Write is blocked (should print "ERROR: permission denied for table …"):
psql "postgresql://horyon_readonly:YOUR_READONLY_PASSWORD@localhost:15433/crypto" \
  -c "INSERT INTO entity_memory(slug,name,type) VALUES('_test_','_test_','other');"
```

---

## Auto-deploy (pull new pushes automatically)

Keep the test box tracking a branch so every `git push` is pulled and redeployed
without a manual rebuild. `scripts/test-autodeploy.sh` does one tick: `git fetch`
the tracked branch, no-op if already current, else `git reset --hard` to the
remote tip and rebuild **only** what changed — `app/`·`requirements.txt`·`Dockerfile`
→ rebuild `bot` (so `bot`+`monitor` pick it up); `web/` → rebuild `web` — then
`docker compose up -d` the overlay stack. An flock guard makes overlapping ticks
no-ops.

Install the cron (every 5 min by default), pinned to the launch branch:

```bash
DEPLOY_BRANCH=public-launch-prep scripts/install-autodeploy-cron.sh
tail -f backups/autodeploy.log        # watch deploys land
scripts/install-autodeploy-cron.sh --remove   # stop auto-deploying
```

Run a tick by hand any time: `scripts/test-autodeploy.sh`.

**Prereqs on the box:**
- The git remote must be **fetchable non-interactively** — cron has no TTY. Use a
  token in the remote URL, a stored credential helper, or a read-only deploy key.
- The cron user must be in the `docker` group (compose `build`/`up` run as it).
- The overlay set defaults to the exact test-machine launch stack
  (`docker-compose.yml` + `dev` + `external-db` + `expose` + `public-monitor`).
  `expose.yml` is test-box-local/untracked (survives `git reset --hard`) but MUST
  stay in the list or a redeploy reverts web to the dev loopback bind;
  `public-monitor.yml` needs `MONITOR_AUTH_USER`/`PASS` in `.env`. Override with
  `COMPOSE_FILES=…` (e.g. in the box's `.env`) or change services with
  `DEPLOY_SERVICES=…` if your box differs.

`git reset --hard` discards **tracked-file** drift on the box (a deploy target
mirrors the remote); gitignored files (`.env`, `cookies.txt`) are left alone.

---

## Env var reference

| Var | Test machine value | Notes |
|---|---|---|
| `DATABASE_URL` | Overlay → `horyon_readonly@host.docker.internal:15433/crypto` | All three services |
| `READONLY_DB_PASSWORD` | Copy from prod `.env` | Used in the DATABASE_URL substitution |
| `POSTGRES_PASSWORD` | Any string | Initialises the local (unused) `db` container |
| `DISABLE_BOT` | Overlay → `true` | Suppresses Telegram + all crons |
| `BOT_USE_POLLING` | Overlay → `true` (stub) | Never reached due to `DISABLE_BOT` |
| `TELEGRAM_*` | Overlay → stubs | Never reached due to `DISABLE_BOT` |
| `OPENROUTER_API_KEY` | Not needed | Bot disabled |
| `NIM_API_KEY` | Not needed | Bot disabled |
| `CMC_API_KEY` | Not needed | Bot disabled |
| `WEB_DB_PASSWORD` | Not needed | Overlay uses `horyon_readonly` for web |
| `OLLAMA_HOST` | Default `host.docker.internal:11434` | Web never calls Ollama; bot disabled |
| `PUBLIC_BASE_URL` | Default `https://app.horyon.xyz` | Links point at prod — correct |
| `WEB_INTERNAL_URL` | Default `http://web:3000` | OG pre-render path; bot disabled, never called |
| `MONITOR_PORT` | Default `8090` | Dev overlay exposes on `127.0.0.1:8090` |
| `BACKUP_DIR` | Dev overlay → `/horyon-backups` | Monitor panel hidden if `~/.horyon-db-backups` is empty |
| `BACKUP_REPO` / `BACKUP_REPO_TOKEN` | Optional | Only for monitor auto-pull data-switch panel |
| `CONTAINER_PREFIX` | Default `horyon` | Monitor filters Docker containers by this prefix ✓ |
| `LOG_LEVEL` | Default `INFO` | Fine |

---

## Conflict checklist

| Risk | Mitigation |
|---|---|
| **Telegram polling deletes prod webhook** | `DISABLE_BOT=true` — process never reaches Telegram code |
| **Ingest cron double-writes to prod** | `DISABLE_BOT=true` — APScheduler never starts |
| **Digest / audio / entity crons race prod** | Same |
| **Any write via application code** | `horyon_readonly` role — Postgres rejects at the permission level |
| **Port 5433 clash (local db vs SSH tunnel)** | Tunnel uses 15433; no conflict |
| **Tunnel unreachable from containers (Linux)** | Bind the tunnel to `0.0.0.0:15433` (see step 1) — loopback bind isn't reachable via `host.docker.internal` |
| **`cookies.txt` bind-mount missing** | `touch cookies.txt` in step 2 |
| **`host.docker.internal` unresolved in monitor** | Overlay adds `extra_hosts` to monitor service |
| **Bot healthcheck probing port 8080** | Overlay overrides healthcheck to `exit 0` |
| **Monitor "Database" (wipe/restore) panel hits prod** | Panel auto-hides + endpoint returns 403 when `DATABASE_URL` is remote (external-db mode); the read-only role also rejects the wipe |

---

## Re-running readonly_db_role.sql after schema changes

Any `CREATE TABLE` on prod is not automatically covered. After adding new tables:

```bash
# On prod:
READONLY_PW=$(grep READONLY_DB_PASSWORD .env | cut -d= -f2-)
docker exec -i horyon-db psql -U crypto -d crypto \
  -v "readonly_pw=$READONLY_PW" -f - < deploy/readonly_db_role.sql
```

The SQL is idempotent — safe to re-run at any time.

---

## Teardown

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  -f docker-compose.external-db.yml \
  down

# Close the tunnel: Ctrl-C in the ssh -N terminal (or pkill autossh)
```

The local `db` volume is empty — safe to prune.

---

## Re-enabling the bot for Telegram testing

If you need to test Telegram handlers on the test machine, you must first stop the
prod bot to avoid conflicts:

```bash
# On prod — stop the bot first:
docker compose stop bot

# On test machine — add to .env:
#   TELEGRAM_BOT_TOKEN=<prod token>
#   OPENROUTER_API_KEY=<key>
# Then start WITHOUT the external-db overlay (use a fresh local DB instead),
# or accept that all bot DB writes will fail on the readonly role.
# BOT_USE_POLLING=true is already enforced by docker-compose.dev.yml.

# When done, restart prod bot:
docker compose start bot   # on prod
```

**Never run the test bot and prod bot on the same Telegram token at the same time.**
Polling calls `deleteWebhook` on startup — it kills the prod webhook immediately.
