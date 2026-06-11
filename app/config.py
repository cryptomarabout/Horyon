"""Environment-backed configuration. Loaded once at import time."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


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

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --- Monitoring dashboard ---
MONITOR_PORT = _int("MONITOR_PORT", 8090)
CONTAINER_PREFIX = os.getenv("CONTAINER_PREFIX", "horyon")

TELEGRAM_WEBHOOK_URL = f"{TELEGRAM_WEBHOOK_BASE}{TELEGRAM_WEBHOOK_PATH}"
