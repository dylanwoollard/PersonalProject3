"""
config.py — Central configuration for the intelligence briefing system.

This is the single file to edit for all system behavior.  It is imported by
every module so changes here propagate everywhere automatically.
"""

from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
# LLM MODEL
# ══════════════════════════════════════════════════════════════════════════════

# MODEL = "gemini-3.1-flash-lite-preview"
# MODEL = "gemini-3-flash-preview"
MODEL = "gemini-2.5-pro"

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
FETCH_WINDOW_HOURS = 96               # Rolling lookback window for email fetching (hours)
MAX_EMAILS         = 50               # Hard cap on emails retrieved per run
PURGE_LABELS       = True             # Remove GMAIL_LABEL after a successful run.
                                      # Override with --no-pu24rge for testing.


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
# Requires FMP_API_KEY in .env (financialmodelingprep.com).
# ══════════════════════════════════════════════════════════════════════════════

CALENDAR_DAYS_AHEAD = 7    # How many calendar days ahead to fetch scheduled releases
CALENDAR_IMPACT     = "high"  # Minimum FMP impact level: "high", "medium", or "low"


# ══════════════════════════════════════════════════════════════════════════════
# FINANCIAL DATA
# ══════════════════════════════════════════════════════════════════════════════

MAX_TICKERS = 75  # Maximum number of instruments to fetch live market data for per run


# ══════════════════════════════════════════════════════════════════════════════
# API RETRY POLICY
# Applied to all Gemini calls when the model is busy (429 / ResourceExhausted).
# ══════════════════════════════════════════════════════════════════════════════

RETRY_MAX_ATTEMPTS = 7      # Total attempts (1 original + 6 retries).
                            # Increased from 5: 150k-char summarization bursts raise TPM
                            # consumption significantly; more headroom prevents hard failures
                            # on the pre-Phase-A Map calls.
RETRY_BASE_DELAY   = 5.0    # Initial wait in seconds before the first retry.
                            # Increased from 3.0: the Gemini quota replenishment window
                            # is typically 60 seconds; a longer base delay reduces wasted
                            # retries when the model is genuinely rate-limited.
RETRY_BACKOFF      = 2.0    # Multiplicative factor applied to delay each retry
RETRY_JITTER       = 2.0    # Max seconds of random jitter added per retry (increased from 1.0)
LLM_TIMEOUT_SECONDS = 120.0  # Per-call deadline passed to asyncio.wait_for().
                              # If generate_content stalls without raising, this
                              # fires asyncio.TimeoutError which feeds the retry
                              # loop (retriable) rather than locking the semaphore
                              # permanently.  120 s is comfortably above the p99
                              # latency for large structured JSON responses.

MAX_CONCURRENT_LLM_CALLS = 3  # Maximum number of simultaneous active Gemini API calls.
                               # Prevents 503 ServiceUnavailable under concurrent Phase B/C load.
                               # The pre-Phase-A Map phase issues up to ceil(N/CHUNK_SIZE) concurrent
                               # summarization calls — these queue against the same semaphore so
                               # they do not add a separate burst on top of Phase B concurrency.
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
# TIERED SUMMARIZER
# Pre-Phase-A map-reduce pipeline that converts raw intelligence (up to 150k
# characters) into a ~12k Master Intelligence Map before any downstream LLM
# call is made.  Cuts downstream context costs ~90% while preserving all
# named entities, figures, tickers, and dates through analytical losslessness
# requirements enforced in the prompt.
# ══════════════════════════════════════════════════════════════════════════════

SUMMARIZER_ENABLED      = True     # Set False to disable and pass raw content directly to all calls
SUMMARIZER_THRESHOLD    = 30_000   # Only run if raw content exceeds this character count
SUMMARIZER_CHUNK_SIZE   = 40_000   # Max characters per Map-phase chunk
SUMMARIZER_TARGET_CHARS = 12_000   # Target Master Intelligence Map length for the Reduce phase
TEMP_SUMMARIZER         = 0.2      # Map-phase chunk summarization temperature
TEMP_MERGE              = 0.15     # Reduce-phase synthesis temperature (tighter for losslessness)


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE TOGGLES
# ══════════════════════════════════════════════════════════════════════════════

GENERATE_TRADES = True  # Set False to skip all trade generation (strategic, positional,
                        # tactical) and quantitative analysis.  Useful for testing
                        # the briefing narrative without waiting for trade generation.


# ══════════════════════════════════════════════════════════════════════════════
# LLM TELEMETRY
# Writes one JSON line per LLM call to TELEMETRY_PATH (append-only JSONL).
# Each record contains: timestamp, model, call label, prompt/response character
# counts, token usage (prompt + completion), latency in seconds, and the retry
# attempt number on which the call succeeded.
# Set TELEMETRY_ENABLED = False to silence all telemetry writes.
# ══════════════════════════════════════════════════════════════════════════════

TELEMETRY_ENABLED = True
TELEMETRY_PATH    = Path("telemetry.jsonl")  # Relative to the working directory