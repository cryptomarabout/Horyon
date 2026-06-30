"""Environment-backed configuration. Loaded once at import time."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _csv_ints(name: str) -> set[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    return {int(x) for x in raw.split(",") if x.strip()}


# --- CoinMarketCap (optional — used for weekly digest; falls back to CoinGecko if absent) ---
CMC_API_KEY = os.getenv("CMC_API_KEY", "")

# --- OpenRouter (OpenAI-compatible chat completions) ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
_DEFAULT_MODELS = ",".join([
    "deepseek/deepseek-v4-flash:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-120b:free",
])
OPENROUTER_MODELS: list[str] = [
    m.strip() for m in os.getenv("OPENROUTER_MODELS", _DEFAULT_MODELS).split(",") if m.strip()
]

# --- NVIDIA NIM (build.nvidia.com) — OpenAI-compatible; tried BEFORE OpenRouter ---
# Models are attempted in order, then the OpenRouter chain. Empty NIM_API_KEY = skip NIM.
NIM_API_KEY = os.getenv("NIM_API_KEY", "")
NIM_BASE_URL = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
_DEFAULT_NIM_MODELS = ",".join([
    "mistralai/mistral-medium-3.5-128b",   # primary — fast, reliable
    "deepseek-ai/deepseek-v4-flash",       # fallback — heavy reasoning model, slow
])
NIM_MODELS: list[str] = [
    m.strip() for m in os.getenv("NIM_MODELS", _DEFAULT_NIM_MODELS).split(",") if m.strip()
]
# Hard per-request ceiling (seconds) so a slow/hung model falls through instead of blocking.
# NIM free tier can cold-start ~30–40s; 60s lets mistral complete yet fails over before stalling.
LLM_TIMEOUT_SEC = float(os.getenv("LLM_TIMEOUT_SEC", "60"))
# Global cap on provider calls per rolling minute (0 = unlimited). Set to e.g. 20 for bulk
# backfills to stay under free-tier rate limits; leave 0 for normal cron operation.
LLM_MAX_CALLS_PER_MIN = int(os.getenv("LLM_MAX_CALLS_PER_MIN", "0"))

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_BASE = os.getenv("TELEGRAM_WEBHOOK_BASE", "").rstrip("/")
TELEGRAM_WEBHOOK_PATH = os.getenv("TELEGRAM_WEBHOOK_PATH", "/tg/hook")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
WEBHOOK_LISTEN_HOST = os.getenv("WEBHOOK_LISTEN_HOST", "0.0.0.0")
WEBHOOK_PORT = _int("WEBHOOK_PORT", 8080)
# Set BOT_USE_POLLING=true for local dev/test — no public HTTPS URL required.
# Never use polling in production (misses updates during downtime, no secret validation).
BOT_USE_POLLING: bool = os.getenv("BOT_USE_POLLING", "").lower() in ("1", "true", "yes")
# Set DISABLE_BOT=true on test machines running against the prod DB.
# All Telegram activity (polling, webhook, crons, ingest) is suppressed — the container
# starts but stays idle so the scheduler never double-writes to prod. Also prevents the
# polling handshake from deleting the prod webhook.
BOT_DISABLED: bool = os.getenv("DISABLE_BOT", "").lower() in ("1", "true", "yes")
ALLOWED_CHAT_IDS = _csv_ints("TELEGRAM_ALLOWED_CHAT_IDS")

# --- Postgres ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://crypto:crypto@localhost:5433/crypto")

# --- Ollama ---
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
EMBED_DIMS = 768  # nomic-embed-text

# --- Tunables ---
INGEST_INTERVAL_MIN = _int("INGEST_INTERVAL_MIN", 20)
SEARCH_TOPK = _int("SEARCH_TOPK", 40)
SEARCH_WINDOW_DAYS = _int("SEARCH_WINDOW_DAYS", 30)
# ivfflat recall knob. lists=100 on the index → ~sqrt(100)=10 is a sane floor;
# below this the 30-day filter starves the result set.
IVFFLAT_PROBES = _int("IVFFLAT_PROBES", 10)
DIGEST_WINDOW_HOURS = _int("DIGEST_WINDOW_HOURS", 24)
DIGEST_LIMIT = _int("DIGEST_LIMIT", 200)
CACHE_TTL_HOURS = _int("CACHE_TTL_HOURS", 24)
MEMORY_WINDOW = _int("MEMORY_WINDOW", 20)
AGENT_MAX_STEPS = _int("AGENT_MAX_STEPS", 8)

# --- Intelligence layer ---
# Days of past digests to inject as chain context into each new digest
DIGEST_CHAIN_DAYS = _int("DIGEST_CHAIN_DAYS", 3)
# Days of analyst notes to inject into digest/agent prompts
ANALYST_NOTES_DAYS = _int("ANALYST_NOTES_DAYS", 7)
# Max entities to surface in the pre-digest entity context block
ENTITY_CONTEXT_LIMIT = _int("ENTITY_CONTEXT_LIMIT", 10)

# --- YouTube crypto-podcast transcripts ---
# Curated channel handles (no paid API). New-episode detection via the free
# per-channel RSS feed; transcripts via youtube-transcript-api → yt-dlp fallback.
_DEFAULT_PODCAST_CHANNELS = ",".join([
    "@UnchainedCrypto",
    "@Bankless",
    "@TheRollupCo",
    "@empirepod",
])
PODCAST_CHANNELS: list[str] = [
    h.strip() for h in os.getenv("PODCAST_CHANNELS", _DEFAULT_PODCAST_CHANNELS).split(",") if h.strip()
]
# How often the podcast ingest cron runs (minutes). Default every 6 h.
PODCAST_INTERVAL_MIN = _int("PODCAST_INTERVAL_MIN", 360)
# Map-reduce chunk size in characters (~4 chars/token → ~3k tokens).
PODCAST_CHUNK_CHARS = _int("PODCAST_CHUNK_CHARS", 12000)
# Safety cap on map calls per episode (guards a runaway 4 h transcript on the free tier).
PODCAST_MAX_MAP_CALLS = _int("PODCAST_MAX_MAP_CALLS", 12)
# Pause between sequential map calls (seconds). Higher = gentler on free-tier rate
# limits at the cost of slower ingest. Ingest is a background cron, so latency is fine.
PODCAST_MAP_DELAY_SEC = float(os.getenv("PODCAST_MAP_DELAY_SEC", "4"))
# Episodes shorter than this many transcript chars are skipped as shorts/clips.
PODCAST_MIN_TRANSCRIPT_CHARS = _int("PODCAST_MIN_TRANSCRIPT_CHARS", 2000)
# Max episodes to process per cron run (keeps free-tier usage bounded).
PODCAST_MAX_EPISODES_PER_RUN = _int("PODCAST_MAX_EPISODES_PER_RUN", 8)
# Hours of recent podcast summaries to feed into the daily digest.
PODCAST_DIGEST_WINDOW_HOURS = _int("PODCAST_DIGEST_WINDOW_HOURS", 48)
# Skip episodes published before this date (YYYY-MM-DD) — aligns podcast ingestion
# with the rest of the intelligence window. Empty string = no cutoff.
PODCAST_MIN_DATE = os.getenv("PODCAST_MIN_DATE", "2026-04-11").strip()
# YouTube blocks unauthenticated transcript scraping from datacenter IPs (this host).
# Provide ONE of these to unblock (no paid YouTube API needed):
#   PODCAST_PROXY         — http(s) proxy URL routed through a non-datacenter IP
#   PODCAST_YTDLP_COOKIES — path (inside the container) to a Netscape cookies.txt
#                           exported from a logged-in YouTube session; passed to yt-dlp
PODCAST_PROXY = os.getenv("PODCAST_PROXY", "").strip()
PODCAST_YTDLP_COOKIES = os.getenv("PODCAST_YTDLP_COOKIES", "").strip()

# --- Kaiko Research (kaiko.com) — sitemap-driven editorial scraper ---
# Kaiko exposes NO RSS feed or public REST API (headless WordPress behind a Nuxt
# frontend), so app/kaiko.py discovers article URLs from the Yoast XML sitemaps and
# pulls title/summary/date/category from each page's JSON-LD + OpenGraph meta. Items
# land in feed_items as source_type='news' (creator='Kaiko') exactly like RSS news.
KAIKO_ENABLED = os.getenv("KAIKO_ENABLED", "1").strip().lower() not in ("0", "false", "no", "")
# How often the Kaiko ingest cron runs (minutes). Resources update ~weekly; default 6 h.
KAIKO_INTERVAL_MIN = _int("KAIKO_INTERVAL_MIN", 360)
# Max NEW article pages fetched per run — bounds the one-time first-run backfill and the
# steady-state cost (already-ingested URLs are skipped, so steady state fetches ~0).
KAIKO_MAX_FETCH_PER_RUN = _int("KAIKO_MAX_FETCH_PER_RUN", 40)
# Pause between article fetches (seconds) — be a polite crawler. Ingest is a background
# cron, so latency is fine.
KAIKO_FETCH_DELAY_SEC = _float("KAIKO_FETCH_DELAY_SEC", 1.0)

# Public web base URL — used to build shareable links (e.g. the /api/og card attached to
# the Twitter thread + the "full feed" CTA). This is the APP, not the marketing landing:
# the apex (horyon.xyz) serves the landing and 301s /d/* and /api/* to app.horyon.xyz, so
# pointing here directly avoids a redirect hop when Twitter scrapes the OG card. Override
# per-deploy in .env.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://app.horyon.xyz").rstrip("/")

# Internal web base URL — the bot reaches the Next.js container directly over the docker
# network (no TLS/Caddy/auth hop) to pre-render the /api/og card post-digest (app/og_cache.py).
WEB_INTERNAL_URL = os.getenv("WEB_INTERNAL_URL", "http://web:3000").rstrip("/")

# --- Daily audio briefing (see app/briefing.py + app/tts.py) ---
# A ~5-min spoken digest rendered post-digest into digest_audio, delivered on the web player
# + Telegram. Best-effort — never blocks the digest. Free TTS only.
AUDIO_BRIEFING_ENABLED = os.getenv("AUDIO_BRIEFING_ENABLED", "1").strip().lower() not in ("0", "false", "no", "")
# TTS engine behind the swappable tts.synthesize() interface:
#   'edge'  — Microsoft neural voices via the free `edge-tts` package (default). Zero local
#             compute (a cloud call — matters on this 2-core host), native MP3, good quality.
#             Rides an UNOFFICIAL Microsoft endpoint, so set a fallback if it proves flaky.
#   'piper' — self-hosted ONNX (install the binary + a .onnx voice; see PIPER_* below).
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge").strip().lower()
# Optional fallback tried when the primary engine fails (empty = none). Set 'piper' once Piper
# is installed to neutralize the risk of Edge's unofficial endpoint going down.
TTS_FALLBACK_ENGINE = os.getenv("TTS_FALLBACK_ENGINE", "").strip().lower()
# Edge voice + delivery rate. Default is one of Microsoft's *multilingual conversational* voices
# (Andrew/Ava/Emma/Brian) — markedly warmer and less robotic than the older single-locale neural
# voices (Aria/Jenny). Andrew = calm, confident, podcast-host cadence; swap TTS_VOICE to
# en-US-AvaMultilingualNeural for the female equivalent. A slightly slower rate reads dense
# numbers clearer aloud (Unchained-intro pace).
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-AndrewMultilingualNeural").strip()
# Delivery pace. The script now rounds numbers (no long decimals) so a brisker read stays clear;
# +8% gives the snappier, podcast-host energy the old -3% lacked. Listeners can still slow it with
# the on-player rate control. Edge accepts a signed percentage string.
TTS_RATE = os.getenv("TTS_RATE", "+8%").strip()
# Two-voice "podcast" briefing (default): a female HOST drives the conversation and a male EXPERT
# delivers the analysis (see app/briefing.py + prompts.build_briefing_system). The script is a labeled dialogue;
# each turn is synthesized with its speaker's voice and the MP3 streams are concatenated. Set
# BRIEFING_DIALOGUE=0 for the old single-voice monologue read by TTS_VOICE. Dialogue is edge-only
# (piper is single-voice) — it auto-falls back to a monologue when TTS_ENGINE != 'edge'. Both
# defaults are Microsoft *multilingual conversational* voices (warm, natural turn-taking).
BRIEFING_DIALOGUE = os.getenv("BRIEFING_DIALOGUE", "1").strip().lower() not in ("0", "false", "no", "")
TTS_HOST_VOICE = os.getenv("TTS_HOST_VOICE", "en-US-AvaMultilingualNeural").strip()       # woman, host
TTS_EXPERT_VOICE = os.getenv("TTS_EXPERT_VOICE", "en-US-AndrewMultilingualNeural").strip()  # man, analyst
# Conversational PAUSES inserted into the stitched two-voice dialogue (app/tts.py) so it breathes
# like a real podcast instead of two voices running together. Two lengths: a short beat between
# back-to-back speaker turns, and a longer breath when a NEW story starts (a chapter change), which
# is the gap that was missing when Maya jumped straight from one topic to an unrelated one. Kept
# deliberately SHORT so it feels human, not sluggish/generated. 0 disables. Tunable via env.
TTS_TURN_GAP_MS = _int("TTS_TURN_GAP_MS", 170)
TTS_STORY_GAP_MS = _int("TTS_STORY_GAP_MS", 480)
# First-name personas the script uses on air (makes the hand-offs sound human, not labeled).
BRIEFING_HOST_NAME = os.getenv("BRIEFING_HOST_NAME", "Maya").strip()
BRIEFING_EXPERT_NAME = os.getenv("BRIEFING_EXPERT_NAME", "Daniel").strip()
# Hard ceiling (seconds) on one synth call so a hung endpoint can't stall orchestration.
TTS_TIMEOUT_SEC = float(os.getenv("TTS_TIMEOUT_SEC", "120"))
# Piper fallback (only used when an engine == 'piper'): binary on PATH + a .onnx voice model.
PIPER_BINARY = os.getenv("PIPER_BINARY", "piper").strip()
PIPER_VOICE = os.getenv("PIPER_VOICE", "").strip()
# Target spoken length. ~160 wpm (at the +8% rate) → ~1000 words ≈ 6 min, in the 5–7 min band the
# prompt asks for. Terse models under-deliver, so the prompt also forbids stopping early.
BRIEFING_TARGET_WORDS = _int("BRIEFING_TARGET_WORDS", 1150)
# How many top-ranked bullets the briefing covers. STORY COUNT is the real lever on duration (the
# word floor barely moves a terse model), so ~8 stories centers the show in the 5–7 min band; LLM
# temperature gives day-to-day spread inside it. Fewer are used on thin days (only ranked bullets
# that exist).
BRIEFING_MAX_BULLETS = _int("BRIEFING_MAX_BULLETS", 8)
# Prior-coverage enrichment: for each story, pull up to N previously-published bullets for the same
# entities from the last BRIEFING_PRIOR_DAYS, so the expert can give continuity ("this builds on…").
# These are already-vetted digest bullets → grounded. 0 disables.
BRIEFING_PRIOR_DAYS = _int("BRIEFING_PRIOR_DAYS", 7)
BRIEFING_PRIOR_MAX = _int("BRIEFING_PRIOR_MAX", 3)

# --- Audio briefing VARIANTS (three lengths, ONE set of grounded signals) ---
# The same post-digest pipeline renders three durations from the SAME grounded, importance-ranked
# signals — so there is NO extra hallucination surface: identical known-facts + temporal-modality
# rails, identical pre-synth modality gate; only verbosity/structure differ. Stored in digest_audio
# keyed (digest_date, variant). 'standard' is the original two-voice show and the default everywhere a
# single briefing is implied (the Telegram send + any legacy caller).
#   short     — ~90-second single-host "flash": the top few headlines, no deep explanation.
#   standard  — ~6-min two-voice podcast (host + expert) with chapters. The original.
#   explainer — ~12-min two-voice "deep dive": every story explained with mechanism + context.
BRIEFING_DEFAULT_VARIANT = os.getenv("BRIEFING_DEFAULT_VARIANT", "standard").strip()
BRIEFING_SHORT_TARGET_WORDS = _int("BRIEFING_SHORT_TARGET_WORDS", 230)
BRIEFING_SHORT_MAX_BULLETS = _int("BRIEFING_SHORT_MAX_BULLETS", 4)
BRIEFING_EXPLAINER_TARGET_WORDS = _int("BRIEFING_EXPLAINER_TARGET_WORDS", 2000)
# The deep dive must carry MORE raw material than the standard show (8), or it has nothing extra to
# go deep on and converges to the same length on a quiet day. STORY COUNT is the real duration lever.
BRIEFING_EXPLAINER_MAX_BULLETS = _int("BRIEFING_EXPLAINER_MAX_BULLETS", 14)
# A single LLM call reliably UNDERSHOOTS a large word target (a terse model tops out ~1100–1300 words
# regardless of an "at least 2000" ask), so the deep dive can land shorter than the standard show.
# Two rails fix this in app/briefing.py: (1) per-variant word FLOOR — if a draft lands below
# `target_words * BRIEFING_MIN_WORD_RATIO`, re-prompt to EXPAND it (no new facts) up to
# BRIEFING_EXPAND_ROUNDS times, keeping the longest valid draft; (2) the explainer floor is also
# raised to at least `standard_words * BRIEFING_EXPLAINER_OVER_STANDARD` so the deep dive can never
# come out at or below the briefing it is supposed to deepen.
BRIEFING_MIN_WORD_RATIO = _float("BRIEFING_MIN_WORD_RATIO", 0.9)
BRIEFING_EXPLAINER_OVER_STANDARD = _float("BRIEFING_EXPLAINER_OVER_STANDARD", 1.35)
BRIEFING_EXPAND_ROUNDS = _int("BRIEFING_EXPAND_ROUNDS", 2)
# Hard UPPER bound on the standard "Daily Briefing" so it can never run long. The floor/expand rails
# above only ever push a show LONGER; this is the matching ceiling, enforced deterministically in
# app/briefing.py (`_enforce_ceiling`): if a rendered script exceeds it, whole trailing story
# chapters are dropped (never mid-exchange) and a clean canonical sign-off is re-attached, so the
# show always closes cleanly under ~9 minutes. At ~150 wpm spoken (the conservative estimate; the
# real +8% edge rate is faster), 1400 words ≈ 9 min worst case — we target ~1150 (~7 min), so a trim
# only fires on an unusually verbose draft. 0 disables. The flash (a tight ceiling already) and the
# explainer (the deliberately long deep dive) set no word ceiling here.
BRIEFING_STANDARD_MAX_WORDS = _int("BRIEFING_STANDARD_MAX_WORDS", 1400)
# variant -> render spec. `dialogue=False` forces a single-voice read (the flash); `True` attempts
# the two-voice podcast (auto-falls back to mono when edge is unavailable / the script has no expert
# turn). `max_tokens` bounds the script LLM output (≈1.8 tokens/word + headroom). `floor=True` marks
# the variants that enforce the word floor above (the flash is a tight ceiling, never a floor).
# `label` is the spoken/UI name (the web switcher has its own copy of these names).
BRIEFING_VARIANT_SPECS = {
    "short":     {"target_words": BRIEFING_SHORT_TARGET_WORDS,     "max_bullets": BRIEFING_SHORT_MAX_BULLETS,     "dialogue": False, "max_tokens": 900,  "floor": False, "ceiling": 0,                          "label": "Flash"},
    "standard":  {"target_words": BRIEFING_TARGET_WORDS,           "max_bullets": BRIEFING_MAX_BULLETS,            "dialogue": True,  "max_tokens": 2600, "floor": True,  "ceiling": BRIEFING_STANDARD_MAX_WORDS, "label": "Briefing"},
    "explainer": {"target_words": BRIEFING_EXPLAINER_TARGET_WORDS, "max_bullets": BRIEFING_EXPLAINER_MAX_BULLETS,  "dialogue": True,  "max_tokens": 5200, "floor": True,  "ceiling": 0,                          "label": "Deep Dive"},
}
# Render order (also the order the web switcher shows them in): shortest → longest.
BRIEFING_VARIANTS = ("short", "standard", "explainer")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --- Monitoring dashboard ---
MONITOR_PORT = _int("MONITOR_PORT", 8090)
CONTAINER_PREFIX = os.getenv("CONTAINER_PREFIX", "horyon")
# Optional HTTP Basic Auth for the monitor app itself. In prod the monitor sits
# behind Caddy basic-auth, so these are left UNSET and the app-level gate is a
# no-op. On a publicly-exposed dev/test box (no Caddy) set BOTH to require login
# before the dashboard — and its DB restore/wipe panel — is reachable.
MONITOR_AUTH_USER = os.getenv("MONITOR_AUTH_USER", "")
MONITOR_AUTH_PASS = os.getenv("MONITOR_AUTH_PASS", "")

# --- Dev/test data management ---
# Local path (inside the monitor container) where the offsite backup clone is mounted.
# docker-compose.dev.yml bind-mounts ~/.horyon-db-backups here. When the path does not
# exist (prod) the data-switch panel is simply absent from the monitor UI.
BACKUP_DIR = os.getenv("BACKUP_DIR", "/horyon-backups")
# Extracted from DATABASE_URL so the restore path can set PGPASSWORD without a separate var.
_db_password_parsed = os.getenv("DATABASE_URL", "postgresql://crypto:@localhost/crypto")
DB_PASSWORD: str = ""
try:
    import urllib.parse as _urlparse
    DB_PASSWORD = _urlparse.urlparse(_db_password_parsed).password or ""
except Exception:
    pass

TELEGRAM_WEBHOOK_URL = f"{TELEGRAM_WEBHOOK_BASE}{TELEGRAM_WEBHOOK_PATH}"
