# Contributing to Horyon

Thanks for taking a look. Horyon is a personal project, but issues and PRs are welcome.

## Getting set up

```bash
git clone https://github.com/<you>/horyon.git
cd horyon
cp .env.example .env          # fill in keys; nothing real is committed
ollama pull nomic-embed-text  # local embeddings
docker compose up -d --build
```

You can run most of the pipeline without Docker for quick iteration:

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python -m app.ingest --dry-run        # fetch + filter, write nothing
python -m app.digest  --no-persist    # build a digest, don't store it
```

## Project shape

- `app/` — the Python bot (ingest, digest, narratives, scoring, research agent, podcasts, weekly, LLM client).
- `web/` — the Next.js 14 app and its API routes.
- `deploy/` — `schema.sql` (the source of truth for the database) and the `Caddyfile`.
- `scripts/` — operational helpers.

[ARCHITECTURE.md](ARCHITECTURE.md) explains how the pieces fit together — read it before a non-trivial change.

## Ground rules

- **Never commit secrets.** Real values live only in `.env` (gitignored). CI runs a secret scan on every PR and will fail if it finds one. If you add a new config value, document it in `.env.example` with a placeholder.
- **All env access goes through `app/config.py`.** Don't read `os.environ` elsewhere.
- **Prompts live in `app/prompts.py`** (and `web/lib/*` for the web mirror). Keep them there.
- **Keep the LLM provider chain plural.** A single model means no fallback; see the note in `app/llm.py`.
- Match the style of the surrounding code. No new dependencies for things the stdlib already does.

## Before you open a PR

```bash
python -m py_compile $(git ls-files '*.py')   # syntax check
bash -n scripts/*.sh                          # shell sanity
docker compose config -q                      # compose still valid
```

Keep PRs focused — one logical change per PR, with a short description of the *why*. If it changes architecture, schema, or a component contract, update the relevant docs in the same PR.

## Reporting bugs / ideas

Use the issue templates. For bugs, include what you expected, what happened, and the relevant log lines (`scripts/logs.sh`). Please redact any keys before pasting logs.
