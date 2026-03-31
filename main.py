"""
main.py — Orchestration entry point (asyncio pipeline).

  ► To adjust any system setting, edit config.py — not this file.

Usage:
  python main.py                          # fetch DailyBriefing emails, purge labels after
  python main.py --no-purge               # fetch emails but leave labels intact (testing)
  python main.py --since-hours 48         # extend the fetch window for this run only
  python main.py path/to/intel.txt        # use a local file instead of Gmail
  python main.py --date 2025-06-15        # back-date the briefing record
  python main.py --resume                 # resume from pipeline_checkpoint.json after a crash

Pipeline execution order:
  Pre-A    [sequential]: Multi-stage summarization — Map (N concurrent chunk summaries) → Reduce (1 merge)
                         Skipped when raw intelligence is below SUMMARIZER_THRESHOLD (~30k chars)
  Phase A  [concurrent]: AppendixDatabase ‖ Themes ‖ Economic calendar ‖ Earnings calendar ‖ Market snapshot
  Between:               Historical context + Financial data [concurrent]
  Phase B  [concurrent]: OpeningNarrative ‖ OpeningCalendar ‖ Adversarial ‖ Overlays ‖ Reading list
                         + StrategicThesis ‖ StrategicInstrument (if GENERATE_TRADES)
  After B  [bounded]:    Assemble StrategicTrade; positional map-reduce (2 concurrent, Sem=2); tactical map-reduce (3 concurrent, Sem=2)
                         + Supplemental hydration: fetch live data for any trade instruments not in Phase A themes list
  Phase C  [concurrent]: Strategic+Positional quants (batch) ‖ Tactical quants (batch) [if GENERATE_TRADES]
                         + Trade logic review (sequential, after quants)
  Level 2:               Compile HTML + aggregate JSON
  Level 1:               Save files + commit to SQLite + purge expired
  Delivery:              Render PDF + email
  Level 6:               Unlabel ingested emails (only on success, only if purge enabled)
"""

import argparse
import asyncio
import logging
import sys
import time
import webbrowser
from datetime import date
from pathlib import Path

# Load .env before anything else so GEMINI_API_KEY is available to generators.py
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv not installed; rely on shell environment

import config
from compiler import aggregate_json_output, compile_html_document
from delivery import deliver_briefing, upload_html_to_s3
from generators import (
    generate_adversarial_assessment,
    generate_appendix_database,
    generate_appendix_reading_list,
    generate_master_intelligence_map,
    generate_opening_calendar,
    generate_opening_narrative,
    generate_single_positional_trade,
    generate_single_quant_analysis,
    generate_single_tactical_quant,
    generate_single_tactical_trade,
    generate_strategic_instrument,
    generate_strategic_thesis,
    generate_trade_logic_review,
    generate_unified_themes,
    get_client,
)
from models import (
    AppendixOutput,
    OpeningSections,
    PositionalTradeSet,
    StrategicTrade,
    TacticalTradeSet,
)
from ingestion import load_email_intelligence, unlabel_emails
from storage import (
    commit_to_longitudinal_memory,
    delete_checkpoint,
    init_database,
    load_checkpoint,
    purge_expired_memory,
    save_checkpoint,
    save_html_briefing,
    save_json_data,
)
from tools import (
    calculate_price_correlations,
    close_yf_session,
    fetch_earnings_calendar,
    fetch_economic_calendar,
    fetch_market_snapshot,
    format_earnings_for_prompt,
    format_economic_calendar_for_prompt,
    format_financial_data_for_prompt,
    normalize_ticker,
    retrieve_historical_context,
    verify_financial_data,
)
from progress import BriefingProgress, build_pipeline_steps, setup_rich_logging


# ── Logging setup ─────────────────────────────────────────────────────────────

_logger = logging.getLogger("briefing")


def _setup_logging(briefing_date: date) -> None:
    """
    Configure the module logger: RichHandler for the console (colours,
    levels) and a plain FileHandler for the dated log file.
    Delegates to setup_rich_logging from progress.py.
    """
    log_dir = config.OUTPUT_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{briefing_date.isoformat()}.log"
    setup_rich_logging(_logger, log_path)


def _log(level: str, msg: str) -> None:
    _logger.info("[%s] %s", level, msg)


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_briefing(
    intelligence_filepath: str | None = None,
    target_date: date | None = None,
    since_hours: int = config.FETCH_WINDOW_HOURS,
    purge_labels: bool = config.PURGE_LABELS,
    preview: bool = False,
    resume: bool = False,
) -> None:
    """
    Full briefing generation pipeline.

    Args:
        intelligence_filepath: Path to a local intelligence file.  If None,
                               emails are fetched from Gmail automatically.
        target_date:           Briefing date.  Defaults to today.
        since_hours:           Gmail fetch window in hours (default 24).
        purge_labels:          If True (default), remove the DailyBriefing label
                               from ingested emails after successful delivery.
                               Set False during testing to preserve emails.
        resume:                If True, load pipeline_checkpoint.json and skip
                               any phases already recorded as completed.
    """
    if target_date is None:
        target_date = date.today()

    _setup_logging(target_date)
    pipeline_start = time.perf_counter()

    _log("SYSTEM", f"Intelligence Briefing — {target_date.isoformat()}")
    if not purge_labels:
        _log("SYSTEM", "Label purge DISABLED — ingested emails will not be unlabeled.")

    checkpoint: dict = load_checkpoint() if resume else {}
    completed_phases: set[str] = set(checkpoint.get("completed_phases", []))
    if resume and completed_phases:
        _log("RESUME", f"Resuming from checkpoint — skipping: {', '.join(sorted(completed_phases))}")

    # Build the step list before the pipeline starts so the progress bar's
    # M/N count is accurate from the first step.
    _steps = build_pipeline_steps(
        generate_trades=config.GENERATE_TRADES,
        summarizer_enabled=config.SUMMARIZER_ENABLED,
        will_unlabel=(purge_labels and intelligence_filepath is None),
    )

    async with BriefingProgress(_steps) as bp:

        # ── Level 1: Initialize database ──────────────────────────────────────
        async with bp.step("init_db"):
            init_database()

        # ── Level 6: Ingest raw intelligence ──────────────────────────────────
        async with bp.step("ingest"):
            payload = await load_email_intelligence(intelligence_filepath, since_hours=since_hours)
        _log("LEVEL 6", f"Loaded {len(payload.content):,} characters of intelligence.")

        client = get_client()

        # ── Pre-A: Multi-stage summarization (map-reduce) ─────────────────────
        # Converts raw intelligence (up to 150k chars) into a Master Intelligence
        # Map (~12k chars) before any analytical LLM call is made.  All downstream
        # Phase A and Phase B functions receive master_intel instead of the raw
        # email dump, cutting per-call input tokens ~90%.  Skipped automatically
        # when content is below SUMMARIZER_THRESHOLD or SUMMARIZER_ENABLED=False.
        if resume and "summarize" in completed_phases:
            await bp.resume_step("summarize")
            master_intel = checkpoint["master_intel"]
        else:
            async with bp.step("summarize"):
                master_intel = await generate_master_intelligence_map(client, payload.content)
            save_checkpoint({"master_intel": master_intel, "completed_phases": list(completed_phases | {"summarize"})})
            completed_phases.add("summarize")
        _log("SUMMARIZER", f"{len(payload.content):,} raw chars → {len(master_intel):,} master map chars")

        # ── PHASE A: AppendixDB ‖ Themes ‖ Market data [all concurrent] ────────
        if resume and "phase_a" in completed_phases:
            await bp.resume_step("phase_a")
            from models import AppendixDatabase, PriorityThemes
            from tools import EarningsEntry, EconomicEvent, MarketTick
            appendix_db    = AppendixDatabase.model_validate(checkpoint["appendix_db"])
            themes         = PriorityThemes.model_validate(checkpoint["themes"])
            eco_events     = [EconomicEvent.model_validate(e) for e in checkpoint["eco_events"]]
            earnings_result = (
                [EarningsEntry.model_validate(e) for e in checkpoint["upcoming_earnings"]],
                [EarningsEntry.model_validate(e) for e in checkpoint["recent_earnings"]],
            )
            market_snapshot = [MarketTick.model_validate(t) for t in checkpoint["market_snapshot"]]
        else:
            async with bp.step("phase_a"):
                (
                    appendix_db,
                    themes,
                    eco_events,
                    earnings_result,
                    market_snapshot,
                ) = await asyncio.gather(
                    generate_appendix_database(client, master_intel),
                    generate_unified_themes(client, master_intel),
                    fetch_economic_calendar(config.CALENDAR_DAYS_AHEAD, config.CALENDAR_IMPACT),
                    fetch_earnings_calendar(days_behind=2, days_ahead=config.CALENDAR_DAYS_AHEAD),
                    fetch_market_snapshot(),
                )
            save_checkpoint({
                "appendix_db":       appendix_db.model_dump(),
                "themes":            themes.model_dump(),
                "eco_events":        [e.model_dump() for e in eco_events],
                "upcoming_earnings": [e.model_dump() for e in earnings_result[0]],
                "recent_earnings":   [e.model_dump() for e in earnings_result[1]],
                "market_snapshot":   [t.model_dump() for t in market_snapshot],
                "completed_phases":  list(completed_phases | {"phase_a"}),
            })
            completed_phases.add("phase_a")

        upcoming_earnings, recent_earnings = earnings_result
        economic_calendar_str = format_economic_calendar_for_prompt(eco_events)
        earnings_calendar_str = format_earnings_for_prompt(upcoming_earnings, recent_earnings)
        _log("PHASE A", f"Appendix DB: {len(appendix_db.key_entities)} entities, {len(appendix_db.key_situations)} situations.")
        _log("PHASE A", f"Economic calendar: {len(eco_events)} high-impact event(s).")
        _log("PHASE A", f"Earnings calendar: {len(upcoming_earnings)} upcoming, {len(recent_earnings)} recent.")

        # ── Between phases: historical context + financial data [concurrent] ────
        all_tickers = list(
            {
                normalized
                for theme in themes.themes
                for instrument in theme.affected_instruments
                if (normalized := normalize_ticker(instrument)) is not None
            }
        )[:config.MAX_TICKERS]

        async with bp.step("between"):
            historical_context, financial_data_map = await asyncio.gather(
                asyncio.to_thread(
                    retrieve_historical_context,
                    entities=appendix_db.key_entities,
                    situations=appendix_db.key_situations,
                ),
                verify_financial_data(all_tickers),
            )
        financial_data_str = format_financial_data_for_prompt(financial_data_map)

        # ── PHASE B: Opening ‖ Adversarial ‖ Strategic ‖ Reading List ────────────
        # Build the task list; keep a parallel key list for named unpacking.
        # Transmission overlay data is now embedded in each UnifiedIntelligenceTheme
        # from Phase A — no separate overlays call is needed.
        _b_keys: list[str] = [
            "opening_narrative",
            "opening_calendar",
            "adversarial",
            "appendix_rl",
        ]
        _b_tasks: list = [
            generate_opening_narrative(client, master_intel, historical_context),
            generate_opening_calendar(
                client, master_intel,
                economic_calendar=economic_calendar_str,
                earnings_calendar=earnings_calendar_str,
            ),
            generate_adversarial_assessment(client, master_intel, themes),
            generate_appendix_reading_list(client, master_intel),
        ]

        if config.GENERATE_TRADES:
            _b_keys += ["strategic_thesis", "strategic_instrument"]
            _b_tasks += [
                generate_strategic_thesis(client, master_intel, historical_context, themes),
                generate_strategic_instrument(client, master_intel, themes, financial_data_str),
            ]

        async with bp.step("phase_b"):
            _b_results = dict(zip(_b_keys, await asyncio.gather(*_b_tasks)))
        save_checkpoint({"completed_phases": list(completed_phases | {"phase_b"})})
        completed_phases.add("phase_b")

        opening_narrative = _b_results["opening_narrative"]
        opening_calendar  = _b_results["opening_calendar"]
        adversarial       = _b_results["adversarial"]
        appendix_rl       = _b_results["appendix_rl"]

        opening = OpeningSections(
            epigraph=opening_narrative.epigraph,
            epigraph_attribution=opening_narrative.epigraph_attribution,
            calendar_events=opening_calendar.calendar_events,
            upcoming_earnings=opening_calendar.upcoming_earnings,
            recent_earnings=opening_calendar.recent_earnings,
        )
        appendix = AppendixOutput(
            reading_list=appendix_rl.reading_list,
            book_recommendations=appendix_rl.book_recommendations,
            key_entities=appendix_db.key_entities,
            key_situations=appendix_db.key_situations,
            intelligence_summary=appendix_db.intelligence_summary,
        )
        _log("PHASE B", f"Reading list: {len(appendix.reading_list)} items, {len(appendix.book_recommendations)} books.")
        _log("PHASE B", f"Adversarial assessment: {len(adversarial.scenarios)} scenario(s).")

        strategic = positional = tactical = None
        strategic_quant = positional_quants = tactical_quants = None
        logic_review = None
        correlation_matrix_str = "No correlation data available."

        if config.GENERATE_TRADES:
            # ── Agentic self-correction loop for strategic trade (max 3 attempts) ──
            # Flow: generate thesis + instrument → quant → logic_review.
            # If logic_review.conviction_adjustment == "Downgrade", regenerate
            # thesis and instrument with the critique injected as a preamble.
            _MAX_STRATEGIC_ATTEMPTS = 3
            _previous_critique: str | None = None
            strategic = None
            strategic_quant = None
            logic_review = None

            for _attempt in range(_MAX_STRATEGIC_ATTEMPTS):
                _log("STRATEGIC", f"Strategic trade generation — attempt {_attempt + 1}/{_MAX_STRATEGIC_ATTEMPTS}")
                strategic_thesis, strategic_instrument = await asyncio.gather(
                    generate_strategic_thesis(
                        client, master_intel, historical_context, themes,
                        previous_critique=_previous_critique,
                    ),
                    generate_strategic_instrument(
                        client, master_intel, themes, financial_data_str,
                        previous_critique=_previous_critique,
                    ),
                )
                strategic = StrategicTrade(
                    trade=strategic_instrument.trade,
                    macro_thesis=strategic_thesis.macro_thesis,
                    time_horizon_months=strategic_thesis.time_horizon_months,
                    historical_precedents=strategic_thesis.historical_precedents,
                    conviction_level=strategic_thesis.conviction_level,
                )
                _log("PHASE B", f"Strategic trade: {strategic.trade.instrument} ({strategic.trade.direction.value})")

                # Quick quant + logic review to evaluate quality
                _strat_json = strategic.trade.model_dump_json()
                strategic_quant = await generate_single_quant_analysis(
                    client, _strat_json, financial_data_str, historical_context,
                    correlation_matrix=correlation_matrix_str,
                )
                logic_review = await generate_trade_logic_review(
                    client, strategic, strategic_quant,
                    correlation_matrix=correlation_matrix_str,
                )

                _adj = logic_review.conviction_adjustment.strip().lower()
                if not _adj.startswith("downgrade") or _attempt == _MAX_STRATEGIC_ATTEMPTS - 1:
                    # Accept: conviction maintained/upgraded, or attempts exhausted
                    if _adj.startswith("downgrade") and _attempt == _MAX_STRATEGIC_ATTEMPTS - 1:
                        _log("WARN", "Max self-correction attempts reached — accepting downgraded strategic trade.")
                    else:
                        _log("STRATEGIC", f"Strategic trade accepted (conviction: {logic_review.conviction_adjustment}).")
                    break

                # Build critique string for next iteration
                fallacy_lines = "\n".join(f"  • {f}" for f in logic_review.fallacies_identified) or "  (none identified)"
                gap_lines     = "\n".join(f"  • {g}" for g in logic_review.analytical_gaps) or "  (none identified)"
                _previous_critique = (
                    f"Verdict: {logic_review.verdict}\n\n"
                    f"Logical Fallacies:\n{fallacy_lines}\n\n"
                    f"Analytical Gaps:\n{gap_lines}\n\n"
                    f"Steelman Counter-Argument: {logic_review.steelman}"
                )
                _log("STRATEGIC", f"Downgrade verdict — regenerating (attempt {_attempt + 2}).")

            strategic_inst = strategic.trade.instrument
            intel_summary  = appendix_db.intelligence_summary
            _trade_sem     = asyncio.Semaphore(3)

            async def _pos(trade_number: int, excluded: list[str]):
                async with _trade_sem:
                    return await generate_single_positional_trade(
                        client, intel_summary, historical_context, themes, financial_data_str,
                        excluded_tickers=excluded,
                        trade_number=trade_number,
                    )

            async def _tac(trade_number: int, excluded: list[str]):
                async with _trade_sem:
                    return await generate_single_tactical_trade(
                        client, intel_summary, themes, financial_data_str,
                        excluded_tickers=excluded,
                        trade_number=trade_number,
                    )

            # ── Positional map-reduce ──────────────────────────────────────────
            async with bp.step("positional"):
                pos_excl = [strategic_inst]
                p1, p2 = await asyncio.gather(
                    _pos(1, pos_excl),
                    _pos(2, pos_excl),
                )
                if p1.trade.instrument == p2.trade.instrument:
                    _log("WARN", f"Positional duplicate ({p1.trade.instrument}) — regenerating slot 2...")
                    p2 = await _pos(2, pos_excl + [p1.trade.instrument])
                _log("PHASE B", f"Positional 1: {p1.trade.instrument} ({p1.trade.direction.value})")
                _log("PHASE B", f"Positional 2: {p2.trade.instrument} ({p2.trade.direction.value})")
            positional = PositionalTradeSet(trades=[p1.trade, p2.trade])
            save_checkpoint({
                "strategic":        strategic.model_dump(),
                "positional":       positional.model_dump(),
                "completed_phases": list(completed_phases | {"positional"}),
            })
            completed_phases.add("positional")

            # ── Tactical map-reduce ────────────────────────────────────────────
            positional_insts = [p1.trade.instrument, p2.trade.instrument]
            tac_excl = [strategic_inst] + positional_insts

            async with bp.step("tactical"):
                t1, t2, t3 = await asyncio.gather(
                    _tac(1, tac_excl),
                    _tac(2, tac_excl),
                    _tac(3, tac_excl),
                )
                seen: set[str] = set()
                deduped: list = []
                for idx, t in enumerate([t1, t2, t3], start=1):
                    if t.trade.instrument in seen:
                        _log("WARN", f"Tactical duplicate ({t.trade.instrument}) — regenerating slot {idx}...")
                        t = await _tac(idx, tac_excl + list(seen))
                    seen.add(t.trade.instrument)
                    deduped.append(t)
                    _log("PHASE B", f"Tactical {idx}: {t.trade.instrument} ({t.trade.direction.value})")
            tactical = TacticalTradeSet(trades=[t.trade for t in deduped])
            save_checkpoint({
                "tactical":         tactical.model_dump(),
                "completed_phases": list(completed_phases | {"tactical"}),
            })
            completed_phases.add("tactical")

            # ── Supplemental hydration ─────────────────────────────────────────
            _all_trade_raws = [
                strategic.trade.instrument,
                *[tr.instrument for tr in positional.trades],
                *[tr.instrument for tr in tactical.trades],
            ]
            _new_tickers = {
                normalized
                for raw in _all_trade_raws
                if (normalized := normalize_ticker(raw)) is not None
                and normalized not in financial_data_map
            }
            async with bp.step("hydration"):
                if _new_tickers:
                    _new_data = await verify_financial_data(list(_new_tickers))
                    financial_data_map.update(_new_data)
                    financial_data_str = format_financial_data_for_prompt(financial_data_map)
                    _log("HYDRATION", f"Hydrated: {', '.join(sorted(_new_tickers))}")
                else:
                    _log("HYDRATION", "All trade instruments already hydrated from Phase A themes.")

            # ── Correlation matrix: 30-day price correlations across all trade instruments
            _corr_tickers = list({
                normalized
                for raw in _all_trade_raws
                if (normalized := normalize_ticker(raw)) is not None
            })
            correlation_matrix_str = await calculate_price_correlations(
                _corr_tickers, financial_data_map
            )
            _log("CORRELATION", f"Correlation matrix computed for {len(_corr_tickers)} instrument(s).")

            # ── Phase C: Quant analysis (map-reduce fan-out) ──────────────────
            # Strategic quant + logic_review were already produced by the self-
            # correction loop above.  Here we fan out positional and tactical quants.
            async with bp.step("phase_c"):
                _quant_sem = asyncio.Semaphore(3)

                async def _pos_quant(trade_json_str: str) -> object:
                    async with _quant_sem:
                        return await generate_single_quant_analysis(
                            client, trade_json_str, financial_data_str,
                            historical_context,
                            correlation_matrix=correlation_matrix_str,
                        )

                async def _tac_quant(trade_json_str: str) -> object:
                    async with _quant_sem:
                        return await generate_single_tactical_quant(
                            client, trade_json_str, financial_data_str,
                        )

                pos_jsons = [t.model_dump_json() for t in positional.trades]
                tac_jsons = [t.model_dump_json() for t in tactical.trades]

                pos_quant_results, tac_quant_results = await asyncio.gather(
                    asyncio.gather(*[_pos_quant(j) for j in pos_jsons]),
                    asyncio.gather(*[_tac_quant(j) for j in tac_jsons]),
                )

            positional_quants = list(pos_quant_results)
            tactical_quants   = list(tac_quant_results)
            save_checkpoint({
                "strategic_quant":   strategic_quant.model_dump(),
                "positional_quants": [q.model_dump() for q in positional_quants],
                "tactical_quants":   [q.model_dump() for q in tactical_quants],
                "completed_phases":  list(completed_phases | {"phase_c"}),
            })
            completed_phases.add("phase_c")
            save_checkpoint({
                "logic_review":     logic_review.model_dump(),
                "completed_phases": list(completed_phases | {"logic_review"}),
            })
            completed_phases.add("logic_review")

        # ── Level 2: Compile and aggregate ────────────────────────────────────
        async with bp.step("compile"):
            html_document = compile_html_document(
                opening=opening,
                themes=themes,
                appendix=appendix,
                strategic=strategic,
                positional=positional,
                tactical=tactical,
                briefing_date=target_date,
                market_snapshot=market_snapshot,
                strategic_quant=strategic_quant,
                positional_quants=positional_quants,
                tactical_quants=tactical_quants,
                adversarial=adversarial,
                logic_review=logic_review,
                correlation_matrix=correlation_matrix_str,
            )
            json_output = aggregate_json_output(
                opening=opening,
                themes=themes,
                appendix=appendix,
                strategic=strategic,
                positional=positional,
                tactical=tactical,
                briefing_date=target_date,
                correlation_matrix=correlation_matrix_str,
            )

        # ── Level 1: Save to disk ──────────────────────────────────────────────
        async with bp.step("save"):
            html_path, json_path = await asyncio.gather(
                save_html_briefing(html_document, target_date),
                save_json_data(json_output, target_date),
            )

        if preview:
            webbrowser.open(html_path.resolve().as_uri())
            _log("PREVIEW", f"Opened in browser → {html_path}")

        # ── Level 1: Commit to longitudinal memory ─────────────────────────────
        async with bp.step("memory"):
            commit_to_longitudinal_memory(target_date.isoformat(), json_output)
            purged = purge_expired_memory(days=config.MEMORY_DAYS)
        if purged:
            _log("LEVEL 1", f"Purged {purged} expired record(s) from longitudinal memory.")

        # ── Delivery: S3 upload + email link ──────────────────────────────────
        async with bp.step("deliver"):
            hosted_url = await upload_html_to_s3(html_path, target_date)
            _log("DELIVERY", f"Briefing hosted → {hosted_url}")
            await deliver_briefing(
                hosted_url=hosted_url,
                briefing_date=target_date,
            )

        # ── Level 6: Unlabel ingested emails ───────────────────────────────────
        if purge_labels and payload.from_gmail:
            async with bp.step("unlabel"):
                await unlabel_emails(payload.message_ids)
        elif not purge_labels and payload.from_gmail:
            _log("LEVEL 6", f"Skipping label removal ({len(payload.message_ids)} email(s) left labeled).")

        await close_yf_session()
        delete_checkpoint()

        total = time.perf_counter() - pipeline_start
        _logger.info("")
        _log("COMPLETE", f"Pipeline finished in {total:.1f}s")
        _log("COMPLETE", f"  HTML  → {html_path}")
        _log("COMPLETE", f"  S3    → {hosted_url}")
        _log("COMPLETE", f"  JSON  → {json_path}")
        _log("COMPLETE", f"  LOG   → {config.OUTPUT_DIR / f'{target_date.isoformat()}.log'}")
        _log("COMPLETE", f"  DB    → intelligence_memory.db")


# ── CLI Entry Point ───────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Geopolitical & Financial Intelligence Briefing Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py\n"
            "  python main.py --no-purge\n"
            "  python main.py --since-hours 48\n"
            "  python main.py path/to/intel.txt\n"
            "  python main.py --date 2025-06-15\n"
        ),
    )
    parser.add_argument(
        "intelligence_file",
        nargs="?",
        default=None,
        help=(
            "Optional path to a local intelligence text file.  "
            "When omitted, emails are fetched from Gmail (DailyBriefing label)."
        ),
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Override the briefing date (defaults to today).",
    )
    parser.add_argument(
        "--since-hours",
        metavar="N",
        type=int,
        default=config.FETCH_WINDOW_HOURS,
        help=f"Fetch Gmail emails from the last N hours (default: {config.FETCH_WINDOW_HOURS}).",
    )
    parser.add_argument(
        "--no-purge",
        action="store_true",
        default=False,
        help=(
            "Do not remove the DailyBriefing label from ingested emails.  "
            "Use this during testing so emails remain available for re-runs."
        ),
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        default=False,
        help="Open the generated HTML briefing in the default browser after generation.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help=(
            "Resume a previously interrupted run from the last checkpoint.  "
            "Skips phases already recorded in pipeline_checkpoint.json."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    briefing_date: date | None = None
    if args.date:
        try:
            briefing_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"[ERROR] Invalid date format '{args.date}'.  Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)

    asyncio.run(
        run_briefing(
            intelligence_filepath=args.intelligence_file,
            target_date=briefing_date,
            since_hours=args.since_hours,
            purge_labels=False if args.no_purge else config.PURGE_LABELS,
            preview=args.preview,
            resume=args.resume,
        )
    )
