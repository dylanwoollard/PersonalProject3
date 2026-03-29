"""
main.py — Orchestration entry point (asyncio pipeline).

  ► To adjust any system setting, edit config.py — not this file.

Usage:
  python main.py                          # fetch DailyBriefing emails, purge labels after
  python main.py --no-purge               # fetch emails but leave labels intact (testing)
  python main.py --since-hours 48         # extend the fetch window for this run only
  python main.py path/to/intel.txt        # use a local file instead of Gmail
  python main.py --date 2025-06-15        # back-date the briefing record

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
from delivery import deliver_briefing
from generators import (
    generate_adversarial_assessment,
    generate_appendix_database,
    generate_appendix_reading_list,
    generate_master_intelligence_map,
    generate_opening_calendar,
    generate_opening_narrative,
    generate_quant_analysis_batch,
    generate_single_positional_trade,
    generate_single_tactical_trade,
    generate_strategic_instrument,
    generate_strategic_thesis,
    generate_tactical_quant_batch,
    generate_trade_logic_review,
    generate_unified_themes,
    get_client,
)
from models import (
    AppendixOutput,
    ExecutiveDashboard,
    OpeningSections,
    PositionalTradeSet,
    StrategicTrade,
    TacticalTradeSet,
)
from ingestion import load_email_intelligence, unlabel_emails
from storage import (
    commit_to_longitudinal_memory,
    init_database,
    purge_expired_memory,
    save_html_briefing,
    save_json_data,
)
from tools import (
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
    """
    if target_date is None:
        target_date = date.today()

    _setup_logging(target_date)
    pipeline_start = time.perf_counter()

    _log("SYSTEM", f"Intelligence Briefing — {target_date.isoformat()}")
    if not purge_labels:
        _log("SYSTEM", "Label purge DISABLED — ingested emails will not be unlabeled.")

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
        async with bp.step("summarize"):
            master_intel = await generate_master_intelligence_map(client, payload.content)
        _log("SUMMARIZER", f"{len(payload.content):,} raw chars → {len(master_intel):,} master map chars")

        # ── PHASE A: AppendixDB ‖ Themes ‖ Market data [all concurrent] ────────
        async with bp.step("phase_a"):
            loop = asyncio.get_running_loop()
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
                loop.run_in_executor(
                    None,
                    lambda: retrieve_historical_context(
                        entities=appendix_db.key_entities,
                        situations=appendix_db.key_situations,
                    ),
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
        dashboard = None
        correlation_matrix_str = "No correlation data available."

        if config.GENERATE_TRADES:
            # Assemble StrategicTrade from the two split Phase B results
            strategic_thesis     = _b_results["strategic_thesis"]
            strategic_instrument = _b_results["strategic_instrument"]
            strategic = StrategicTrade(
                trade=strategic_instrument.trade,
                macro_thesis=strategic_thesis.macro_thesis,
                time_horizon_months=strategic_thesis.time_horizon_months,
                historical_precedents=strategic_thesis.historical_precedents,
                conviction_level=strategic_thesis.conviction_level,
            )
            _log("PHASE B", f"Strategic trade: {strategic.trade.instrument} ({strategic.trade.direction.value})")

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
            correlation_matrix_str = await loop.run_in_executor(
                None,
                lambda: __import__("tools").calculate_price_correlations(
                    _corr_tickers, financial_data_map
                ),
            )
            _log("CORRELATION", f"Correlation matrix computed for {len(_corr_tickers)} instrument(s).")

            # ── Executive Dashboard ────────────────────────────────────────────
            # Build from already-generated data: top theme → top risk,
            # strategic trade → top opportunity, first calendar event → deadline.
            _top_theme = themes.themes[0]
            _first_eco = eco_events[0] if eco_events else None
            dashboard = ExecutiveDashboard(
                top_risk=(
                    f"{_top_theme.title}: {_top_theme.market_impact} "
                    f"[Risk: {_top_theme.risk_level.value}]"
                ),
                top_opportunity=(
                    f"{strategic.trade.instrument} ({strategic.trade.direction.value}) — "
                    f"{strategic.trade.geopolitical_catalyst}"
                ),
                critical_deadline=(
                    f"{_first_eco.date_str} {_first_eco.event}: {_first_eco.market_relevance}"
                    if _first_eco else "No high-impact scheduled releases in the coming week."
                ),
            )
            _log("DASHBOARD", "Executive Dashboard assembled.")

            # ── Phase C: Quant analysis ────────────────────────────────────────
            async with bp.step("phase_c"):
                strat_pos_jsons = [
                    strategic.trade.model_dump_json(),
                    *[t.model_dump_json() for t in positional.trades],
                ]
                tac_jsons = [t.model_dump_json() for t in tactical.trades]
                quant_batch, tac_quant_set = await asyncio.gather(
                    generate_quant_analysis_batch(
                        client, strat_pos_jsons, financial_data_str, historical_context,
                        correlation_matrix=correlation_matrix_str,
                    ),
                    generate_tactical_quant_batch(client, tac_jsons, financial_data_str),
                )

            strategic_quant   = quant_batch.analyses[0]
            positional_quants = quant_batch.analyses[1:]
            tactical_quants   = tac_quant_set.quants

            async with bp.step("logic_review"):
                logic_review = await generate_trade_logic_review(
                    client, strategic, strategic_quant,
                    correlation_matrix=correlation_matrix_str,
                )

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
                dashboard=dashboard,
            )
            json_output = aggregate_json_output(
                opening=opening,
                themes=themes,
                appendix=appendix,
                strategic=strategic,
                positional=positional,
                tactical=tactical,
                briefing_date=target_date,
                dashboard=dashboard,
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

        # ── Delivery: PDF + email ──────────────────────────────────────────────
        async with bp.step("deliver"):
            pdf_path = await deliver_briefing(
                html_path=html_path,
                briefing_date=target_date,
            )

        # ── Level 6: Unlabel ingested emails ───────────────────────────────────
        if purge_labels and payload.from_gmail:
            async with bp.step("unlabel"):
                await unlabel_emails(payload.message_ids)
        elif not purge_labels and payload.from_gmail:
            _log("LEVEL 6", f"Skipping label removal ({len(payload.message_ids)} email(s) left labeled).")

        total = time.perf_counter() - pipeline_start
        _logger.info("")
        _log("COMPLETE", f"Pipeline finished in {total:.1f}s")
        _log("COMPLETE", f"  HTML  → {html_path}")
        _log("COMPLETE", f"  PDF   → {pdf_path}")
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
        )
    )
