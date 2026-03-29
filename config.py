"""
config.py — Central configuration for the intelligence briefing system.

This is the single file to edit for all system behavior.  It is imported by
every module so changes here propagate everywhere automatically.
"""

from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
# LLM MODEL
# ══════════════════════════════════════════════════════════════════════════════

MODEL = "gemini-3-flash-preview"


# ══════════════════════════════════════════════════════════════════════════════
# GENERATION TEMPERATURES
# Lower = more deterministic.  Higher = more varied phrasing.
# ══════════════════════════════════════════════════════════════════════════════

TEMP_NARRATIVE    = 0.4   # Opening sections (epigraph, red cell)
TEMP_THEMES       = 0.3   # Priority themes
TEMP_OVERLAYS     = 0.2   # Transmission overlays (risk matrices)
TEMP_STRATEGIC    = 0.25  # Strategic trade (tightest — most analytically grounded)
TEMP_POSITIONAL   = 0.3   # Positional trades
TEMP_TACTICAL     = 0.3   # Tactical trades
TEMP_APPENDIX     = 0.2   # Appendix and entity extraction
TEMP_ADVERSARIAL  = 0.7   # Adversarial scenario assessment (higher — contrarian mandate)


# ══════════════════════════════════════════════════════════════════════════════
# GMAIL INGESTION
# ══════════════════════════════════════════════════════════════════════════════

GMAIL_LABEL        = "DailyBriefing"  # Gmail label to query for intelligence emails
FETCH_WINDOW_HOURS = 24               # Rolling lookback window for email fetching (hours)
MAX_EMAILS         = 50               # Hard cap on emails retrieved per run
PURGE_LABELS       = True             # Remove GMAIL_LABEL after a successful run.
                                      # Override with --no-purge for testing.


# ══════════════════════════════════════════════════════════════════════════════
# DELIVERY
# ══════════════════════════════════════════════════════════════════════════════

BRIEFING_EMAIL = "dylanwoollardbiz@gmail.com"       # Recipient address for the emailed PDF.
                          # Leave blank to fall back to the BRIEFING_EMAIL env var in .env.
DAILY_RUN_TIME = "04:30"  # Default time used by schedule_task.py (24-hour HH:MM).


# ══════════════════════════════════════════════════════════════════════════════
# STORAGE
# ══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR  = Path("briefings")              # Directory for HTML, PDF, and JSON outputs
DB_PATH     = Path("intelligence_memory.db") # SQLite longitudinal memory database
MEMORY_DAYS = 180                            # Records older than this are purged automatically


# ══════════════════════════════════════════════════════════════════════════════
# ECONOMIC CALENDAR
# Requires FINNHUB_API_KEY in .env (free tier at finnhub.io — 60 calls/min).
# ══════════════════════════════════════════════════════════════════════════════

CALENDAR_DAYS_AHEAD = 7    # How many calendar days ahead to fetch scheduled releases
CALENDAR_IMPACT     = "high"  # Minimum Finnhub impact level: "high", "medium", or "low"


# ══════════════════════════════════════════════════════════════════════════════
# FINANCIAL DATA
# ══════════════════════════════════════════════════════════════════════════════

MAX_TICKERS = 75  # Maximum number of instruments to fetch live market data for per run


# ══════════════════════════════════════════════════════════════════════════════
# API RETRY POLICY
# Applied to all Gemini calls when the model is busy (429 / ResourceExhausted).
# ══════════════════════════════════════════════════════════════════════════════

RETRY_MAX_ATTEMPTS = 5      # Total attempts (1 original + 4 retries)
RETRY_BASE_DELAY   = 3.0    # Initial wait in seconds before the first retry
RETRY_BACKOFF      = 2.0    # Multiplicative factor applied to delay each retry
RETRY_JITTER       = 1.0    # Max seconds of random jitter added per retry

MAX_CONCURRENT_LLM_CALLS = 3  # Maximum number of simultaneous active Gemini API calls.
                               # Prevents 503 ServiceUnavailable under concurrent Phase B/C load.
                               # Semaphore is held only during the active HTTP call, not during
                               # retry sleeps, so queued coroutines can fill freed slots immediately.


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT TOKEN LIMIT
# OpeningSections is the largest structured response (~3-5k tokens).
# Set this to the model's maximum to prevent JSON truncation.
# Gemini 2.5 Flash/Pro support up to 65536.  Gemini 3 Flash/Pro: check model card.
# Setting higher than the model's actual max is safe — it will be silently capped.
# ══════════════════════════════════════════════════════════════════════════════

MAX_OUTPUT_TOKENS = 120000


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE TOGGLES
# ══════════════════════════════════════════════════════════════════════════════

GENERATE_TRADES = False  # Set False to skip all trade generation (strategic, positional,
                        # tactical) and quantitative analysis.  Useful for testing
                        # the briefing narrative without waiting for trade generation.