"""
generators.py — Core generative functions backed by Gemini (Level 3).

Every function makes one structured Gemini call and returns a validated
Pydantic model.  All calls are async and safe to gather concurrently.

Model:   gemini-3-pro-preview
SDK:     google-genai  (google.genai)
Schema:  Pydantic models passed as response_schema for strict JSON generation.
"""

import asyncio
import json
import logging
import os
import random
import time

from google import genai
from google.genai import types
from pydantic import ValidationError

import config

_logger = logging.getLogger("briefing")

from models import (
    AdversarialAssessment,
    AppendixDatabase,
    AppendixOutput,
    AppendixReadingList,
    OpeningCalendar,
    OpeningNarrative,
    OpeningSections,
    PositionalTradeSet,
    PriorityThemes,
    SingleTrade,
    StrategicInstrument,
    StrategicThesis,
    StrategicTrade,
    TacticalTradeSet,
    TradeLogicReview,
)
from prompts import (
    ADVERSARIAL_PROMPT,
    APPENDIX_DATABASE_PROMPT,
    APPENDIX_PROMPT,
    APPENDIX_READING_LIST_PROMPT,
    CHUNK_SUMMARY_PROMPT,
    MERGE_SUMMARIES_PROMPT,
    OPENING_CALENDAR_PROMPT,
    OPENING_NARRATIVE_PROMPT,
    POSITIONAL_TRADES_PROMPT,
    SINGLE_POSITIONAL_TRADE_PROMPT,
    SINGLE_QUANT_ANALYSIS_PROMPT,
    SINGLE_TACTICAL_QUANT_PROMPT,
    SINGLE_TACTICAL_TRADE_PROMPT,
    STRATEGIC_INSTRUMENT_PROMPT,
    STRATEGIC_THESIS_PROMPT,
    STRATEGIC_TRADE_PROMPT,
    SYSTEM_INSTRUCTION,
    TACTICAL_TRADES_PROMPT,
    TRADE_LOGIC_REVIEW_PROMPT,
    UNIFIED_THEMES_PROMPT,
    build_prompt,
)

# ── Model chain ───────────────────────────────────────────────────────────────
# Resolved once at import time from config.  The list is ordered from most
# preferred (primary) to least preferred (last-resort fallback).
_MODEL_CHAIN: list[str] = [config.MODEL] + list(config.FALLBACK_MODELS)


def _model_for_attempt(attempt: int) -> str:
    """
    Return the model name to use for a given attempt index (0-based).

    Attempts 0..FALLBACK_THRESHOLD-1   → _MODEL_CHAIN[0]  (primary)
    Attempts FALLBACK_THRESHOLD..2*T-1 → _MODEL_CHAIN[1]  (first fallback)
    …and so on, clamped to the last entry.
    """
    idx = min(attempt // config.FALLBACK_THRESHOLD, len(_MODEL_CHAIN) - 1)
    return _MODEL_CHAIN[idx]


# ── Telemetry ─────────────────────────────────────────────────────────────────

def _emit_telemetry(
    call_label: str,
    model: str,
    prompt_chars: int,
    response_chars: int,
    latency_s: float,
    attempt: int,
    temperature: float,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    """
    Append one JSON line to TELEMETRY_PATH.

    Each record captures: ISO timestamp, model name (the model actually used for
    this call — may differ from the primary if a fallback was active), call label
    (schema class name or free-text label), prompt/response character counts,
    token usage from the API response (None when not reported by the SDK),
    wall-clock latency in seconds, retry attempt number, and temperature.

    Writes are synchronous and intentionally fast (< 1 ms) — the JSONL file is
    opened in append mode so no read-modify-write is needed.  A failure to write
    telemetry is silently suppressed so it never interrupts the pipeline.
    """
    if not config.TELEMETRY_ENABLED:
        return
    record = {
        "ts":                 __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "model":              model,
        "call":               call_label,
        "prompt_chars":       prompt_chars,
        "response_chars":     response_chars,
        "prompt_tokens":      prompt_tokens,
        "completion_tokens":  completion_tokens,
        "latency_s":          round(latency_s, 3),
        "attempt":            attempt,
        "temperature":        temperature,
    }
    try:
        with open(config.TELEMETRY_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # telemetry failure must never interrupt the pipeline


# ── Semaphore ─────────────────────────────────────────────────────────────────
# Semaphore caps the number of simultaneous active Gemini HTTP calls.
# Held only during the actual API call — released during retry sleeps so other
# coroutines can acquire it immediately.
_llm_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_LLM_CALLS)


# ── Client Factory ────────────────────────────────────────────────────────────

def get_client() -> genai.Client:
    """
    Construct and return an authenticated Gemini client.
    Reads GEMINI_API_KEY from the environment.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set.  "
            "Export it before running the briefing system."
        )
    return genai.Client(api_key=api_key)


# ── Core Async Wrapper ────────────────────────────────────────────────────────

async def _generate_structured(
    client: genai.Client,
    prompt: str,
    response_schema: type,
    temperature: float = 0.3,
    _label: str | None = None,
) -> object:
    """
    Send a prompt to Gemini with structured output enforcement.

    The response_schema Pydantic class is passed directly to the GenAI SDK,
    which converts it to JSON Schema and instructs the model to produce
    conforming output.  The raw JSON text is then validated back through
    Pydantic for a fully typed return value.

    Model fallback: if the primary model returns repeated 503/overloaded
    errors, the retry loop automatically steps to the next model in
    _MODEL_CHAIN every FALLBACK_THRESHOLD attempts.

    Args:
        client:          Authenticated genai.Client instance.
        prompt:          Fully assembled prompt string.
        response_schema: Pydantic BaseModel subclass.
        temperature:     Sampling temperature (lower = more deterministic).

    Returns:
        A validated instance of response_schema.
    """
    call_label = _label or response_schema.__name__

    # Stagger concurrent calls to reduce simultaneous quota hits.
    await asyncio.sleep(random.uniform(0, 2.0))

    delay = config.RETRY_BASE_DELAY
    _last_model: str | None = None  # track model changes for fallback logging

    for attempt in range(config.RETRY_MAX_ATTEMPTS):
        current_model = _model_for_attempt(attempt)

        # Log whenever we step to a new (fallback) model.
        if current_model != _last_model:
            if _last_model is not None:
                _logger.warning(
                    "[FALLBACK] Primary model exhausted after %d attempt(s). "
                    "Falling back to %s for %s (attempt %d/%d).",
                    attempt,
                    current_model,
                    call_label,
                    attempt + 1,
                    config.RETRY_MAX_ATTEMPTS,
                )
            _last_model = current_model

        t0 = time.perf_counter()
        try:
            async with _llm_semaphore:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=current_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=temperature,
                            response_mime_type="application/json",
                            response_schema=response_schema,
                            system_instruction=SYSTEM_INSTRUCTION,
                            max_output_tokens=config.MAX_OUTPUT_TOKENS,
                        ),
                    ),
                    timeout=config.LLM_TIMEOUT_SECONDS,
                )
            latency = time.perf_counter() - t0

            # Detect truncation before attempting JSON parse.
            try:
                candidate = response.candidates[0]
                finish_reason = str(candidate.finish_reason)
                if "MAX_TOKENS" in finish_reason:
                    raise RuntimeError(
                        f"[TRUNCATED] Model hit MAX_TOKENS limit "
                        f"(max_output_tokens={config.MAX_OUTPUT_TOKENS}) for "
                        f"{response_schema.__name__}. Increase MAX_OUTPUT_TOKENS in config.py."
                    )
            except (IndexError, AttributeError):
                pass  # response.candidates not available — proceed normally

            if not response.text:
                raise RuntimeError(
                    f"[EMPTY] Model returned empty text for {response_schema.__name__}. "
                    f"Possible safety block. finish_reason="
                    f"{getattr(getattr(response, 'candidates', [None])[0], 'finish_reason', 'unknown')}"
                )

            result = response_schema.model_validate_json(response.text)

            # ── Telemetry record (records the model that actually succeeded) ──
            usage = getattr(response, "usage_metadata", None)
            _emit_telemetry(
                call_label=call_label,
                model=current_model,
                prompt_chars=len(prompt),
                response_chars=len(response.text),
                latency_s=latency,
                attempt=attempt + 1,
                temperature=temperature,
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                completion_tokens=getattr(usage, "candidates_token_count", None),
            )
            return result

        except Exception as exc:
            exc_str = str(exc)
            is_retriable = (
                "429" in exc_str
                or "ResourceExhausted" in type(exc).__name__
                or "quota" in exc_str.lower()
                or "overloaded" in exc_str.lower()
                or "503" in exc_str
                or "ServiceUnavailable" in type(exc).__name__
                # Retry on schema validation failures — the model occasionally
                # produces structurally valid JSON that fails Pydantic constraints.
                or isinstance(exc, ValidationError)
                # Retry on stall: asyncio.wait_for fires TimeoutError when the
                # model holds the connection open without completing.
                or isinstance(exc, asyncio.TimeoutError)
            )
            last_attempt = attempt == config.RETRY_MAX_ATTEMPTS - 1
            if not is_retriable or last_attempt:
                raise

            jitter = random.uniform(0, config.RETRY_JITTER)
            wait   = delay + jitter
            if isinstance(exc, asyncio.TimeoutError):
                _logger.warning(
                    "[TIMEOUT] %s stalled on %s within %.0fs — retrying "
                    "(attempt %d/%d), backing off %.1fs.",
                    call_label,
                    current_model,
                    config.LLM_TIMEOUT_SECONDS,
                    attempt + 1,
                    config.RETRY_MAX_ATTEMPTS,
                    wait,
                )
            else:
                _logger.warning(
                    "[HIGH USAGE] %s on %s for %s — waiting %.1fs "
                    "(attempt %d/%d). %s: %s",
                    type(exc).__name__,
                    current_model,
                    call_label,
                    wait,
                    attempt + 1,
                    config.RETRY_MAX_ATTEMPTS,
                    type(exc).__name__,
                    exc_str[:120],
                )
            await asyncio.sleep(wait)
            _logger.info(
                "[RESUMING] Retrying %s after %.1fs wait (attempt %d/%d, model=%s)...",
                call_label,
                wait,
                attempt + 2,
                config.RETRY_MAX_ATTEMPTS,
                _model_for_attempt(attempt + 1),
            )
            delay *= config.RETRY_BACKOFF


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_excluded(tickers: list[str] | None) -> str:
    """Format an exclusion list for prompt injection."""
    if not tickers:
        return "None — this is the first trade section generated."
    return ", ".join(tickers)


async def _generate_text(
    client: genai.Client,
    prompt: str,
    temperature: float = 0.2,
    label: str = "text generation",
) -> str:
    """
    Send a prompt to Gemini and return the raw text response.

    Used for free-form generation tasks (summarization) where response_schema
    and JSON enforcement are not needed.  Applies the same semaphore, retry,
    fallback chain, and stagger logic as _generate_structured.
    """
    await asyncio.sleep(random.uniform(0, 2.0))

    delay = config.RETRY_BASE_DELAY
    _last_model: str | None = None

    for attempt in range(config.RETRY_MAX_ATTEMPTS):
        current_model = _model_for_attempt(attempt)

        if current_model != _last_model:
            if _last_model is not None:
                _logger.warning(
                    "[FALLBACK] Primary model exhausted after %d attempt(s). "
                    "Falling back to %s for %s (attempt %d/%d).",
                    attempt,
                    current_model,
                    label,
                    attempt + 1,
                    config.RETRY_MAX_ATTEMPTS,
                )
            _last_model = current_model

        t0 = time.perf_counter()
        try:
            async with _llm_semaphore:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=current_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=temperature,
                            system_instruction=SYSTEM_INSTRUCTION,
                            max_output_tokens=config.MAX_OUTPUT_TOKENS,
                        ),
                    ),
                    timeout=config.LLM_TIMEOUT_SECONDS,
                )
            latency = time.perf_counter() - t0

            if not response.text:
                raise RuntimeError(
                    f"[EMPTY] Model returned empty text for {label}. "
                    f"finish_reason="
                    f"{getattr(getattr(response, 'candidates', [None])[0], 'finish_reason', 'unknown')}"
                )

            text = response.text.strip()

            # ── Telemetry record (records the model that actually succeeded) ──
            usage = getattr(response, "usage_metadata", None)
            _emit_telemetry(
                call_label=label,
                model=current_model,
                prompt_chars=len(prompt),
                response_chars=len(text),
                latency_s=latency,
                attempt=attempt + 1,
                temperature=temperature,
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                completion_tokens=getattr(usage, "candidates_token_count", None),
            )
            return text

        except Exception as exc:
            exc_str = str(exc)
            is_retriable = (
                "429" in exc_str
                or "ResourceExhausted" in type(exc).__name__
                or "quota" in exc_str.lower()
                or "overloaded" in exc_str.lower()
                or "503" in exc_str
                or "ServiceUnavailable" in type(exc).__name__
                or isinstance(exc, asyncio.TimeoutError)
            )
            last_attempt = attempt == config.RETRY_MAX_ATTEMPTS - 1
            if not is_retriable or last_attempt:
                raise

            jitter = random.uniform(0, config.RETRY_JITTER)
            wait   = delay + jitter
            if isinstance(exc, asyncio.TimeoutError):
                _logger.warning(
                    "[TIMEOUT] %s stalled on %s within %.0fs — retrying "
                    "(attempt %d/%d), backing off %.1fs.",
                    label,
                    current_model,
                    config.LLM_TIMEOUT_SECONDS,
                    attempt + 1,
                    config.RETRY_MAX_ATTEMPTS,
                    wait,
                )
            else:
                _logger.warning(
                    "[HIGH USAGE] %s on %s for %s — waiting %.1fs "
                    "(attempt %d/%d). %s: %s",
                    type(exc).__name__, current_model, label, wait,
                    attempt + 1, config.RETRY_MAX_ATTEMPTS,
                    type(exc).__name__, exc_str[:120],
                )
            await asyncio.sleep(wait)
            _logger.info(
                "[RESUMING] Retrying %s after %.1fs wait (attempt %d/%d, model=%s)...",
                label, wait, attempt + 2, config.RETRY_MAX_ATTEMPTS,
                _model_for_attempt(attempt + 1),
            )
            delay *= config.RETRY_BACKOFF

    # Should be unreachable — last_attempt raises above
    raise RuntimeError(f"[RETRY EXHAUSTED] {label}")


def _chunk_content(content: str, chunk_size: int) -> list[str]:
    """
    Split raw intelligence content into chunks of at most chunk_size characters.

    Splits are made at the nearest newline boundary before the size limit to
    avoid cutting a sentence mid-stream.  If no newline is found within the
    window, the hard limit is used.
    """
    if len(content) <= chunk_size:
        return [content]

    chunks: list[str] = []
    start = 0
    while start < len(content):
        end = min(start + chunk_size, len(content))
        if end < len(content):
            newline = content.rfind("\n", start, end)
            if newline > start:
                end = newline + 1  # include the newline in the preceding chunk
        chunks.append(content[start:end])
        start = end
    return chunks


# ── Tiered Summarizer ─────────────────────────────────────────────────────────

async def _generate_chunk_summary(
    client: genai.Client,
    chunk: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    """
    Map phase: summarize one ~40k-char chunk of raw intelligence into a
    high-density, analytically lossless summary.

    Each call is independent and all chunks are gathered concurrently in
    generate_master_intelligence_map, governed by _llm_semaphore.
    """
    prompt = CHUNK_SUMMARY_PROMPT.format(
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        chunk_text=chunk,
    )
    return await _generate_text(
        client, prompt,
        temperature=config.TEMP_SUMMARIZER,
        label=f"ChunkSummary[{chunk_index}/{total_chunks}]",
    )


async def generate_master_intelligence_map(
    client: genai.Client,
    raw_content: str,
) -> str:
    """
    Multi-stage map-reduce summarizer.

    For inputs below SUMMARIZER_THRESHOLD or when SUMMARIZER_ENABLED is False,
    raw_content is returned unchanged so the rest of the pipeline is unaffected.

    Map phase:   Split raw_content into ~SUMMARIZER_CHUNK_SIZE chunks and
                 summarize each concurrently via _generate_chunk_summary.
                 All calls are governed by the module-level _llm_semaphore.

    Reduce phase: Merge all chunk summaries into a single Master Intelligence
                  Map (~SUMMARIZER_TARGET_CHARS characters) via MERGE_SUMMARIES_PROMPT.
                  The merge prompt enforces analytical losslessness — no named
                  entity, ticker, figure, or date may be dropped.

    Returns the Master Intelligence Map as a plain string, ready to substitute
    for payload.content in all Phase A and Phase B generator calls.
    """
    if not config.SUMMARIZER_ENABLED or len(raw_content) < config.SUMMARIZER_THRESHOLD:
        _logger.info(
            "Summarizer skipped — %d chars (threshold: %d).",
            len(raw_content), config.SUMMARIZER_THRESHOLD,
        )
        return raw_content

    chunks = _chunk_content(raw_content, config.SUMMARIZER_CHUNK_SIZE)
    _logger.info(
        "Summarizer: %d chunk(s) from %d chars — Map phase starting.",
        len(chunks), len(raw_content),
    )

    # Map phase: all chunks run concurrently (semaphore limits simultaneous calls)
    chunk_summaries: list[str] = list(await asyncio.gather(
        *[
            _generate_chunk_summary(client, chunk, i + 1, len(chunks))
            for i, chunk in enumerate(chunks)
        ]
    ))

    if len(chunk_summaries) == 1:
        # Single chunk — no merge needed; return the chunk summary directly
        _logger.info(
            "Summarizer: single-chunk run — Reduce phase skipped.  "
            "Master map: %d chars.", len(chunk_summaries[0]),
        )
        return chunk_summaries[0]

    # Reduce phase: merge all chunk summaries into the Master Intelligence Map
    _logger.info("Summarizer: Reduce phase — merging %d chunk summaries.", len(chunk_summaries))
    numbered = "\n\n".join(
        f"--- CHUNK SUMMARY {i + 1} OF {len(chunk_summaries)} ---\n{s}"
        for i, s in enumerate(chunk_summaries)
    )
    merge_prompt = MERGE_SUMMARIES_PROMPT.format(
        n_chunks=len(chunk_summaries),
        target_chars=config.SUMMARIZER_TARGET_CHARS,
        chunk_summaries=numbered,
    )
    master = await _generate_text(
        client, merge_prompt,
        temperature=config.TEMP_MERGE,
        label="MasterIntelligenceMap",
    )

    _logger.info(
        "Summarizer complete: %d raw chars → %d master map chars (%.1f%% reduction).",
        len(raw_content), len(master),
        (1 - len(master) / len(raw_content)) * 100,
    )
    return master


# ── Generative Functions ──────────────────────────────────────────────────────

async def generate_unified_themes(
    client: genai.Client,
    raw_intelligence: str,
    historical_context: str = "",
) -> PriorityThemes:
    """
    Identify and analyze the top 3 priority intelligence themes, including
    full risk transmission overlays (primary_channel, transmission_narrative,
    second/third-order effects, risk_level) in a single LLM call.

    Replaces the separate generate_priority_themes + generate_transmission_overlays
    calls — halves the number of Phase A LLM calls and eliminates the need to
    inject already-generated themes JSON into a second prompt.

    This output drives financial data fetching and all downstream trade prompts.
    Runs concurrently with generate_appendix_database in the pipeline.
    """
    prompt = build_prompt(
        UNIFIED_THEMES_PROMPT,
        raw_intelligence=raw_intelligence,
        historical_context=historical_context,
    )
    return await _generate_structured(
        client, prompt, PriorityThemes, temperature=config.TEMP_THEMES
    )


async def generate_strategic_trade(
    client: genai.Client,
    raw_intelligence: str,
    historical_context: str,
    priority_themes: PriorityThemes,
    financial_data: str,
) -> StrategicTrade:
    """
    Generate one strategic trade (3-12 month horizon) grounded in the dominant
    geopolitical or macroeconomic theme.
    """
    prompt = build_prompt(
        STRATEGIC_TRADE_PROMPT,
        raw_intelligence=raw_intelligence,
        historical_context=historical_context,
        financial_data=financial_data,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
    )
    return await _generate_structured(
        client, prompt, StrategicTrade, temperature=config.TEMP_STRATEGIC
    )


async def generate_positional_trades(
    client: genai.Client,
    raw_intelligence: str,
    historical_context: str,
    priority_themes: PriorityThemes,
    financial_data: str,
    excluded_tickers: list[str] | None = None,
) -> PositionalTradeSet:
    """
    Generate exactly 2 positional trades (2-8 week horizons) driven by
    near-to-medium-term developments in today's intelligence.
    """
    excluded_str = _format_excluded(excluded_tickers)
    prompt = build_prompt(
        POSITIONAL_TRADES_PROMPT,
        raw_intelligence=raw_intelligence,
        historical_context=historical_context,
        financial_data=financial_data,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
        excluded_tickers=excluded_str,
    )
    return await _generate_structured(
        client, prompt, PositionalTradeSet, temperature=config.TEMP_POSITIONAL
    )


async def generate_tactical_trades(
    client: genai.Client,
    raw_intelligence: str,
    historical_context: str,
    priority_themes: PriorityThemes,
    financial_data: str,
    excluded_tickers: list[str] | None = None,
) -> TacticalTradeSet:
    """
    Generate exactly 3 tactical trades (1-10 trading day horizons) exploiting
    near-term catalysts confirmed by today's intelligence.
    """
    excluded_str = _format_excluded(excluded_tickers)
    prompt = build_prompt(
        TACTICAL_TRADES_PROMPT,
        raw_intelligence=raw_intelligence,
        historical_context=historical_context,
        financial_data=financial_data,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
        excluded_tickers=excluded_str,
    )
    return await _generate_structured(
        client, prompt, TacticalTradeSet, temperature=config.TEMP_TACTICAL
    )


async def generate_appendix_database(
    client: genai.Client,
    raw_intelligence: str,
) -> AppendixDatabase:
    """
    Phase A: Generate the database record — entity index, situation index,
    and archival intelligence summary.

    Runs first in the pipeline; its outputs drive the historical context
    retrieval step.  Small schema, fast response.
    """
    prompt = build_prompt(
        APPENDIX_DATABASE_PROMPT,
        raw_intelligence=raw_intelligence,
    )
    return await _generate_structured(
        client, prompt, AppendixDatabase, temperature=config.TEMP_APPENDIX
    )


async def generate_appendix_reading_list(
    client: genai.Client,
    raw_intelligence: str,
) -> AppendixReadingList:
    """
    Phase B: Generate the curated reading list and book recommendations.

    Runs concurrently with trade generation — does not block Phase A or
    historical context retrieval.
    """
    prompt = build_prompt(
        APPENDIX_READING_LIST_PROMPT,
        raw_intelligence=raw_intelligence,
    )
    return await _generate_structured(
        client, prompt, AppendixReadingList, temperature=config.TEMP_APPENDIX
    )


async def generate_appendix_and_database(
    client: genai.Client,
    raw_intelligence: str,
) -> AppendixOutput:
    """
    Legacy single-call appendix generation.  Prefer the split
    generate_appendix_database / generate_appendix_reading_list pair
    for production use — this combined call risks MAX_TOKENS truncation.
    """
    db, rl = await asyncio.gather(
        generate_appendix_database(client, raw_intelligence),
        generate_appendix_reading_list(client, raw_intelligence),
    )
    return AppendixOutput(
        reading_list=rl.reading_list,
        book_recommendations=rl.book_recommendations,
        key_entities=db.key_entities,
        key_situations=db.key_situations,
        intelligence_summary=db.intelligence_summary,
    )


async def generate_single_quant_analysis(
    client: genai.Client,
    trade_json: str,
    financial_data: str,
    historical_context: str,
    correlation_matrix: str = "No correlation data available.",
) -> object:
    """
    Generate a QuantAnalysis for a single trade.

    Called concurrently once per trade in Phase C (map-reduce fan-out).
    Returns a single QuantAnalysis instance — never a batch wrapper —
    so each call stays well within output token limits.
    """
    from models import QuantAnalysis

    prompt = build_prompt(
        SINGLE_QUANT_ANALYSIS_PROMPT,
        raw_intelligence="",
        trade_json=trade_json,
        financial_data=financial_data,
        historical_context=historical_context,
        correlation_matrix=correlation_matrix,
    )
    return await _generate_structured(
        client, prompt, QuantAnalysis, temperature=config.TEMP_STRATEGIC
    )


async def generate_opening_narrative(
    client: genai.Client,
    raw_intelligence: str,
    historical_context: str,
) -> OpeningNarrative:
    """
    Generate the narrative opening: epigraph + red cell scenarios.
    Split from OpeningSections to reduce schema size and avoid MAX_TOKENS truncation.
    Runs concurrently with generate_opening_calendar in Phase B.
    """
    prompt = build_prompt(
        OPENING_NARRATIVE_PROMPT,
        raw_intelligence=raw_intelligence,
        historical_context=historical_context,
    )
    return await _generate_structured(
        client, prompt, OpeningNarrative, temperature=config.TEMP_NARRATIVE
    )


async def generate_opening_calendar(
    client: genai.Client,
    raw_intelligence: str,
    economic_calendar: str = "No economic calendar data available.",
    earnings_calendar: str = "No earnings calendar data available.",
) -> OpeningCalendar:
    """
    Generate the calendar and earnings sections.
    Split from OpeningSections to reduce schema size and avoid MAX_TOKENS truncation.
    Runs concurrently with generate_opening_narrative in Phase B.
    """
    prompt = build_prompt(
        OPENING_CALENDAR_PROMPT,
        raw_intelligence=raw_intelligence,
        economic_calendar=economic_calendar,
        earnings_calendar=earnings_calendar,
    )
    return await _generate_structured(
        client, prompt, OpeningCalendar, temperature=config.TEMP_NARRATIVE
    )


async def generate_adversarial_assessment(
    client: genai.Client,
    raw_intelligence: str,
    priority_themes: PriorityThemes,
) -> AdversarialAssessment:
    """
    Generate an adversarial red-cell assessment of the priority themes.
    Runs at higher temperature (TEMP_ADVERSARIAL) to surface contrarian views.
    Concurrently with other Phase B calls.
    """
    prompt = build_prompt(
        ADVERSARIAL_PROMPT,
        raw_intelligence=raw_intelligence,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
    )
    return await _generate_structured(
        client, prompt, AdversarialAssessment, temperature=config.TEMP_ADVERSARIAL
    )


async def generate_trade_logic_review(
    client: genai.Client,
    strategic: StrategicTrade,
    strategic_quant,
    correlation_matrix: str = "No correlation data available.",
) -> TradeLogicReview:
    """
    Review the strategic trade and its quant analysis for logical fallacies
    and analytical gaps.  Runs in Phase C alongside the quant batch.
    """
    import json
    from datetime import date

    combined = {
        "strategic_trade": strategic.model_dump(),
        "quant_analysis": strategic_quant.model_dump() if strategic_quant is not None else None,
    }
    prompt = build_prompt(
        TRADE_LOGIC_REVIEW_PROMPT,
        raw_intelligence="",  # not needed — trade + quant are self-contained
        trade_and_quant_json=json.dumps(combined, indent=2),
        today=date.today().isoformat(),
        correlation_matrix=correlation_matrix,
    )
    return await _generate_structured(
        client, prompt, TradeLogicReview, temperature=config.TEMP_STRATEGIC
    )


async def generate_single_tactical_quant(
    client: genai.Client,
    trade_json: str,
    financial_data: str,
) -> object:
    """
    Generate a TacticalQuant for a single tactical trade.

    Called concurrently once per trade in Phase C (map-reduce fan-out).
    Returns a single TacticalQuant instance.
    """
    from models import TacticalQuant

    prompt = build_prompt(
        SINGLE_TACTICAL_QUANT_PROMPT,
        raw_intelligence="",
        trade_json=trade_json,
        financial_data=financial_data,
    )
    return await _generate_structured(
        client, prompt, TacticalQuant, temperature=config.TEMP_TACTICAL
    )


async def generate_strategic_thesis(
    client: genai.Client,
    raw_intelligence: str,
    historical_context: str,
    priority_themes: PriorityThemes,
    previous_critique: str | None = None,
) -> StrategicThesis:
    """
    Phase B split-call A: generate the macro thesis, historical precedents,
    conviction level, and time horizon — no instrument data.
    Runs concurrently with generate_strategic_instrument to halve token load.

    If previous_critique is provided (self-correction loop), a preamble is
    prepended to the prompt instructing the model to address the identified
    logical failures before regenerating.
    """
    base_prompt = build_prompt(
        STRATEGIC_THESIS_PROMPT,
        raw_intelligence=raw_intelligence,
        historical_context=historical_context,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
    )
    if previous_critique:
        critique_preamble = (
            "SELF-CORRECTION REQUIRED\n\n"
            "Your previous strategic trade was reviewed by the risk officer and rejected "
            "for the following reasons:\n\n"
            f"{previous_critique}\n\n"
            "You must correct these logical fallacies and analytical gaps before proposing "
            "a new trade.  Do NOT repeat the same instrument, thesis structure, or causal "
            "chain that was rejected.  Propose a stronger, highly-defensible setup that "
            "addresses each identified weakness point-by-point.\n\n"
            "---\n\n"
        )
        prompt = critique_preamble + base_prompt
    else:
        prompt = base_prompt
    return await _generate_structured(
        client, prompt, StrategicThesis, temperature=config.TEMP_STRATEGIC
    )


async def generate_strategic_instrument(
    client: genai.Client,
    raw_intelligence: str,
    priority_themes: PriorityThemes,
    financial_data: str,
    previous_critique: str | None = None,
) -> StrategicInstrument:
    """
    Phase B split-call B: select the optimal instrument for the strategic
    trade and populate the structured Trade fields.
    Runs concurrently with generate_strategic_thesis to halve token load.

    If previous_critique is provided (self-correction loop), a preamble is
    prepended instructing the model to avoid the rejected instrument and
    address the identified weaknesses.
    """
    base_prompt = build_prompt(
        STRATEGIC_INSTRUMENT_PROMPT,
        raw_intelligence=raw_intelligence,
        financial_data=financial_data,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
    )
    if previous_critique:
        critique_preamble = (
            "SELF-CORRECTION REQUIRED\n\n"
            "Your previous strategic instrument selection was reviewed by the risk officer "
            "and rejected for the following reasons:\n\n"
            f"{previous_critique}\n\n"
            "You must select a different instrument and correct these analytical gaps.  "
            "Do NOT select the same ticker that was rejected.  Choose an instrument that "
            "addresses each weakness point-by-point and produces a more defensible setup.\n\n"
            "---\n\n"
        )
        prompt = critique_preamble + base_prompt
    else:
        prompt = base_prompt
    return await _generate_structured(
        client, prompt, StrategicInstrument, temperature=config.TEMP_STRATEGIC
    )


async def generate_single_positional_trade(
    client: genai.Client,
    intelligence_summary: str,
    historical_context: str,
    priority_themes: PriorityThemes,
    financial_data: str,
    excluded_tickers: list[str] | None = None,
    trade_number: int = 1,
) -> SingleTrade:
    """
    Map-reduce positional trade generation: one Trade per call.

    Receives intelligence_summary (the AppendixDatabase 2-3 paragraph summary)
    rather than the full raw email dump — reduces input tokens by ~70-80%
    while preserving all analytically relevant facts needed for trade selection.

    excluded_tickers must include all instruments already allocated in this
    briefing so the model cannot duplicate a prior selection.
    """
    excluded_str = _format_excluded(excluded_tickers)
    prompt = build_prompt(
        SINGLE_POSITIONAL_TRADE_PROMPT,
        raw_intelligence=intelligence_summary,   # summary substituted for full dump
        historical_context=historical_context,
        financial_data=financial_data,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
        excluded_tickers=excluded_str,
        trade_number=trade_number,
    )
    return await _generate_structured(
        client, prompt, SingleTrade, temperature=config.TEMP_POSITIONAL
    )


async def generate_single_tactical_trade(
    client: genai.Client,
    intelligence_summary: str,
    priority_themes: PriorityThemes,
    financial_data: str,
    excluded_tickers: list[str] | None = None,
    trade_number: int = 1,
) -> SingleTrade:
    """
    Map-reduce tactical trade generation: one Trade per call.

    Receives intelligence_summary (the AppendixDatabase 2-3 paragraph summary)
    rather than the full raw email dump — reduces input tokens by ~70-80%
    while preserving all analytically relevant facts needed for trade selection.

    Historical context is intentionally omitted — tactical trades are
    catalyst-driven and do not require the 180-day longitudinal record.

    excluded_tickers must include all instruments already allocated in this
    briefing so the model cannot duplicate a prior selection.
    """
    excluded_str = _format_excluded(excluded_tickers)
    prompt = build_prompt(
        SINGLE_TACTICAL_TRADE_PROMPT,
        raw_intelligence=intelligence_summary,   # summary substituted for full dump
        financial_data=financial_data,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
        excluded_tickers=excluded_str,
        trade_number=trade_number,
    )
    return await _generate_structured(
        client, prompt, SingleTrade, temperature=config.TEMP_TACTICAL
    )
