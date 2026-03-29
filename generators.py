"""
generators.py — Core generative functions backed by Gemini (Level 3).

Every function makes one structured Gemini call and returns a validated
Pydantic model.  All calls are async and safe to gather concurrently.

Model:   gemini-3-pro-preview
SDK:     google-genai  (google.genai)
Schema:  Pydantic models passed as response_schema for strict JSON generation.
"""

import asyncio
import logging
import os
import random

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
    TransmissionOverlays,
)
from prompts import (
    ADVERSARIAL_PROMPT,
    APPENDIX_DATABASE_PROMPT,
    APPENDIX_PROMPT,
    APPENDIX_READING_LIST_PROMPT,
    OPENING_CALENDAR_PROMPT,
    OPENING_NARRATIVE_PROMPT,
    POSITIONAL_TRADES_PROMPT,
    PRIORITY_THEMES_PROMPT,
    SINGLE_POSITIONAL_TRADE_PROMPT,
    SINGLE_TACTICAL_TRADE_PROMPT,
    STRATEGIC_INSTRUMENT_PROMPT,
    STRATEGIC_THESIS_PROMPT,
    STRATEGIC_TRADE_PROMPT,
    SYSTEM_INSTRUCTION,
    TACTICAL_TRADES_PROMPT,
    TRADE_LOGIC_REVIEW_PROMPT,
    TRANSMISSION_OVERLAYS_PROMPT,
    build_prompt,
)

MODEL = config.MODEL

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
) -> object:
    """
    Send a prompt to Gemini with structured output enforcement.

    The response_schema Pydantic class is passed directly to the GenAI SDK,
    which converts it to JSON Schema and instructs the model to produce
    conforming output.  The raw JSON text is then validated back through
    Pydantic for a fully typed return value.

    Args:
        client:          Authenticated genai.Client instance.
        prompt:          Fully assembled prompt string.
        response_schema: Pydantic BaseModel subclass.
        temperature:     Sampling temperature (lower = more deterministic).

    Returns:
        A validated instance of response_schema.
    """
    # Stagger concurrent calls to reduce simultaneous quota hits.
    # 2s window distributes 5+ concurrent Phase-B calls across the rate-limit window.
    await asyncio.sleep(random.uniform(0, 2.0))

    delay = config.RETRY_BASE_DELAY
    for attempt in range(config.RETRY_MAX_ATTEMPTS):
        try:
            async with _llm_semaphore:
                response = await client.aio.models.generate_content(
                    model=MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=temperature,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                        system_instruction=SYSTEM_INSTRUCTION,
                        max_output_tokens=config.MAX_OUTPUT_TOKENS,
                    ),
                )

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
                    f"Possible safety block. finish_reason={getattr(getattr(response, 'candidates', [None])[0], 'finish_reason', 'unknown')}"
                )

            return response_schema.model_validate_json(response.text)

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
                # A retry with the same prompt usually succeeds.
                or isinstance(exc, ValidationError)
            )
            last_attempt = attempt == config.RETRY_MAX_ATTEMPTS - 1
            if not is_retriable or last_attempt:
                raise

            jitter = random.uniform(0, config.RETRY_JITTER)
            wait   = delay + jitter
            _logger.warning(
                "[HIGH USAGE] %s rate limit for %s — waiting %.1fs "
                "(attempt %d/%d). %s: %s",
                type(exc).__name__,
                response_schema.__name__,
                wait,
                attempt + 1,
                config.RETRY_MAX_ATTEMPTS,
                type(exc).__name__,
                exc_str[:120],
            )
            await asyncio.sleep(wait)
            _logger.info(
                "[RESUMING] Retrying %s after %.1fs wait (attempt %d/%d)...",
                response_schema.__name__,
                wait,
                attempt + 2,
                config.RETRY_MAX_ATTEMPTS,
            )
            delay *= config.RETRY_BACKOFF


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_excluded(tickers: list[str] | None) -> str:
    """Format an exclusion list for prompt injection."""
    if not tickers:
        return "None — this is the first trade section generated."
    return ", ".join(tickers)


# ── Generative Functions ──────────────────────────────────────────────────────

async def generate_priority_themes(
    client: genai.Client,
    raw_intelligence: str,
    historical_context: str = "",  # no longer used in the prompt; kept for API compatibility
) -> PriorityThemes:
    """
    Identify and analyze the top 3 priority intelligence themes.
    This output drives financial data fetching and all downstream trade prompts.
    Runs concurrently with generate_appendix_and_database in the pipeline.
    """
    prompt = build_prompt(
        PRIORITY_THEMES_PROMPT,
        raw_intelligence=raw_intelligence,
    )
    return await _generate_structured(
        client, prompt, PriorityThemes, temperature=config.TEMP_THEMES
    )


async def generate_transmission_overlays(
    client: genai.Client,
    raw_intelligence: str,
    historical_context: str,
    priority_themes: PriorityThemes,
) -> TransmissionOverlays:
    """
    Map how each priority theme's risk propagates through financial markets,
    including second- and third-order effects.
    """
    prompt = build_prompt(
        TRANSMISSION_OVERLAYS_PROMPT,
        raw_intelligence=raw_intelligence,
        historical_context=historical_context,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
    )
    return await _generate_structured(
        client, prompt, TransmissionOverlays, temperature=config.TEMP_OVERLAYS
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


async def generate_quant_analysis_batch(
    client: genai.Client,
    trade_jsons: list[str],
    financial_data: str,
    historical_context: str,
) -> object:
    """
    Generate QuantAnalysis for 1-3 trades in a single Gemini call.
    Returns a QuantAnalysisBatch whose .analyses list mirrors the input order.
    """
    from models import QuantAnalysisBatch
    from prompts import QUANT_ANALYSIS_BATCH_PROMPT
    from datetime import date

    trades_array = "[" + ",\n".join(trade_jsons) + "]"
    prompt = QUANT_ANALYSIS_BATCH_PROMPT.format(
        today=date.today().isoformat(),
        n_trades=len(trade_jsons),
        trades_json=trades_array,
        financial_data=financial_data,
        historical_context=historical_context,
    )
    return await _generate_structured(
        client, prompt, QuantAnalysisBatch, temperature=config.TEMP_STRATEGIC
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
    )
    return await _generate_structured(
        client, prompt, TradeLogicReview, temperature=config.TEMP_STRATEGIC
    )


async def generate_tactical_quant_batch(
    client: genai.Client,
    trade_jsons: list[str],
    financial_data: str,
) -> object:
    """
    Generate TacticalQuant for 1-3 tactical trades in a single Gemini call.
    Returns a TacticalQuantSet whose .quants list mirrors the input order.
    """
    from models import TacticalQuantSet
    from prompts import TACTICAL_QUANT_BATCH_PROMPT
    from datetime import date

    trades_array = "[" + ",\n".join(trade_jsons) + "]"
    prompt = TACTICAL_QUANT_BATCH_PROMPT.format(
        today=date.today().isoformat(),
        n_trades=len(trade_jsons),
        trades_json=trades_array,
        financial_data=financial_data,
    )
    return await _generate_structured(
        client, prompt, TacticalQuantSet, temperature=config.TEMP_TACTICAL
    )


async def generate_strategic_thesis(
    client: genai.Client,
    raw_intelligence: str,
    historical_context: str,
    priority_themes: PriorityThemes,
) -> StrategicThesis:
    """
    Phase B split-call A: generate the macro thesis, historical precedents,
    conviction level, and time horizon — no instrument data.
    Runs concurrently with generate_strategic_instrument to halve token load.
    """
    prompt = build_prompt(
        STRATEGIC_THESIS_PROMPT,
        raw_intelligence=raw_intelligence,
        historical_context=historical_context,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
    )
    return await _generate_structured(
        client, prompt, StrategicThesis, temperature=config.TEMP_STRATEGIC
    )


async def generate_strategic_instrument(
    client: genai.Client,
    raw_intelligence: str,
    priority_themes: PriorityThemes,
    financial_data: str,
) -> StrategicInstrument:
    """
    Phase B split-call B: select the optimal instrument for the strategic
    trade and populate the structured Trade fields.
    Runs concurrently with generate_strategic_thesis to halve token load.
    """
    prompt = build_prompt(
        STRATEGIC_INSTRUMENT_PROMPT,
        raw_intelligence=raw_intelligence,
        financial_data=financial_data,
        priority_themes_json=priority_themes.model_dump_json(indent=2),
    )
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
