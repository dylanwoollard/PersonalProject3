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
from contextlib import asynccontextmanager
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
    generate_opening_calendar,
    generate_opening_narrative,
    generate_priority_themes,
    generate_quant_analysis_batch,
    generate_single_positional_trade,
    generate_single_tactical_trade,
    generate_strategic_instrument,
    generate_strategic_thesis,
    generate_tactical_quant_batch,
    generate_trade_logic_review,
    generate_transmission_overlays,
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


# ── Logging setup ─────────────────────────────────────────────────────────────

_logger = logging.getLogger("briefing")


def _setup_logging(briefing_date: date) -> None:
    """
    Configure the module logger to write to both stdout and a dated log file
    in the briefings directory.  Called once at the start of run_briefing().
    """
    _logger.setLevel(logging.DEBUG)
    if _logger.handlers:          # avoid duplicate handlers on re-entry
        return

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    _logger.addHandler(ch)

    # File — briefings/YYYY-MM-DD.log
    log_dir = config.OUTPUT_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{briefing_date.isoformat()}.log"
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    _logger.addHandler(fh)

    _logger.info(f"Log file: {log_path.resolve()}")


def _log(level: str, msg: str) -> None:
    _logger.info("[%s] %s", level, msg)


@asynccontextmanager
async def _timed(label: str):
    """Async context manager that logs a step's elapsed wall-clock time."""
    _logger.info("  ▶  %s", label)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - t0
        _logger.info("  ✓  %s — %.1fs", label, elapsed)


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

    # ── Level 1: Initialize database ──────────────────────────────────────────
    async with _timed("Init database"):
        init_database()

    # ── Level 6: Ingest raw intelligence ──────────────────────────────────────
    async with _timed("Ingest intelligence"):
        payload = await load_email_intelligence(intelligence_filepath, since_hours=since_hours)
    _log("LEVEL 6", f"Loaded {len(payload.content):,} characters of intelligence.")

    client = get_client()

    # ── PHASE A: AppendixDB ‖ Themes ‖ Market data [all concurrent] ──────────
    # AppendixDatabase (entities/situations/summary) runs here — its output
    # drives historical context retrieval.  The reading list / books are
    # deferred to Phase B where they run alongside the trades.
    async with _timed("Phase A — appendix DB ‖ themes ‖ market data [concurrent]"):
        loop = asyncio.get_running_loop()
        (
            appendix_db,
            themes,
            eco_events,
            earnings_result,
            market_snapshot,
        ) = await asyncio.gather(
            generate_appendix_database(client, payload.content),
            generate_priority_themes(client, payload.content),
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

    # ── Between phases: historical context + financial data [concurrent] ──────
    all_tickers = list(
        {
            normalized
            for theme in themes.themes
            for instrument in theme.affected_instruments
            if (normalized := normalize_ticker(instrument)) is not None
        }
    )[:config.MAX_TICKERS]

    async with _timed(f"Historical context + financial data ({len(all_tickers)} instruments) [concurrent]"):
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

    # ── PHASE B: Opening (split) ‖ Adversarial ‖ Overlays ‖ Trades ‖ Reading List [concurrent] ──
    # Opening is split into two smaller concurrent calls (narrative + calendar)
    # to stay within the model's output ceiling.
    # Trades only run when GENERATE_TRADES is enabled.
    # Build the task list and keep a parallel key list so results can be unpacked
    # by name rather than by fragile positional index.
    _b_keys: list[str] = [
        "opening_narrative",
        "opening_calendar",
        "adversarial",
        "overlays",
        "appendix_rl",
    ]
    _b_tasks: list = [
        generate_opening_narrative(client, payload.content, historical_context),
        generate_opening_calendar(
            client, payload.content,
            economic_calendar=economic_calendar_str,
            earnings_calendar=earnings_calendar_str,
        ),
        generate_adversarial_assessment(client, payload.content, themes),
        generate_transmission_overlays(client, payload.content, historical_context, themes),
        generate_appendix_reading_list(client, payload.content),
    ]

    if config.GENERATE_TRADES:
        # Strategic split: thesis (narrative) and instrument (ticker/levels) run concurrently.
        # Neither call carries the full StrategicTrade schema, halving per-call output tokens.
        _b_keys += ["strategic_thesis", "strategic_instrument"]
        _b_tasks += [
            generate_strategic_thesis(client, payload.content, historical_context, themes),
            generate_strategic_instrument(client, payload.content, themes, financial_data_str),
        ]

    _phase_b_label = (
        "Phase B — opening ‖ adversarial ‖ overlays ‖ strategic (split) ‖ reading list [concurrent]"
        if config.GENERATE_TRADES else
        "Phase B — opening ‖ adversarial ‖ overlays ‖ reading list [concurrent] (trades OFF)"
    )
    async with _timed(_phase_b_label):
        _b_results = dict(zip(_b_keys, await asyncio.gather(*_b_tasks)))

    opening_narrative = _b_results["opening_narrative"]
    opening_calendar  = _b_results["opening_calendar"]
    adversarial       = _b_results["adversarial"]
    overlays          = _b_results["overlays"]
    appendix_rl       = _b_results["appendix_rl"]

    # Merge the split opening parts into OpeningSections for the compiler
    opening = OpeningSections(
        epigraph=opening_narrative.epigraph,
        epigraph_attribution=opening_narrative.epigraph_attribution,
        red_cell_scenarios=opening_narrative.red_cell_scenarios,
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

        # ── Positional map-reduce: 2 concurrent calls [Semaphore(2)] ────────────
        # Both calls share the same initial exclusion list (strategic instrument).
        # Intra-batch duplicates are resolved by a post-gather dedup pass.
        # intelligence_summary substitutes the full email dump to cut input tokens.
        strategic_inst    = strategic.trade.instrument
        intel_summary     = appendix_db.intelligence_summary
        _trade_sem        = asyncio.Semaphore(3)

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

        async with _timed("Positional trades — map-reduce (2 concurrent calls)"):
            pos_excl = [strategic_inst]
            p1, p2 = await asyncio.gather(
                _pos(1, pos_excl),
                _pos(2, pos_excl),
            )
            # Dedup: if both calls landed on the same instrument, regenerate slot 2
            if p1.trade.instrument == p2.trade.instrument:
                _log("WARN", f"Positional duplicate ({p1.trade.instrument}) — regenerating slot 2...")
                p2 = await _pos(2, pos_excl + [p1.trade.instrument])
            _log("PHASE B", f"Positional 1: {p1.trade.instrument} ({p1.trade.direction.value})")
            _log("PHASE B", f"Positional 2: {p2.trade.instrument} ({p2.trade.direction.value})")
        positional = PositionalTradeSet(trades=[p1.trade, p2.trade])

        # ── Tactical map-reduce: 3 concurrent calls [Semaphore(2)] ────────────
        # All 3 calls share the same exclusion list (strategic + positional).
        # Intra-batch duplicates resolved sequentially after the gather.
        positional_insts = [p1.trade.instrument, p2.trade.instrument]
        tac_excl = [strategic_inst] + positional_insts

        async with _timed("Tactical trades — map-reduce (3 concurrent calls, cap=2)"):
            t1, t2, t3 = await asyncio.gather(
                _tac(1, tac_excl),
                _tac(2, tac_excl),
                _tac(3, tac_excl),
            )
            # Dedup within the tactical batch
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

        # ── Supplemental hydration: fetch data for LLM-selected instruments ───
        # Phase B may pick instruments that were not in the pre-Phase-B themes
        # ticker list (e.g., a sector ETF or individual equity the model chose
        # autonomously).  Fetch any such instruments now so Phase C quant
        # analysis has complete, live market data for every trade in the deck.
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
        if _new_tickers:
            async with _timed(f"Supplemental hydration — {len(_new_tickers)} new instrument(s)"):
                _new_data = await verify_financial_data(list(_new_tickers))
                financial_data_map.update(_new_data)
                financial_data_str = format_financial_data_for_prompt(financial_data_map)
            _log("HYDRATION", f"Hydrated: {', '.join(sorted(_new_tickers))}")
        else:
            _log("HYDRATION", "All trade instruments already hydrated from Phase A themes.")

        # ── PHASE C: Quant analysis + logic review [concurrent] ───────────────
        # Strategic+positional quants in one batch; tactical quants in another;
        # logic review runs after quants are available (sequential within Phase C).
        async with _timed("Phase C — strategic+positional quants ‖ tactical quants [concurrent]"):
            strat_pos_jsons = [
                strategic.trade.model_dump_json(),
                *[t.model_dump_json() for t in positional.trades],
            ]
            tac_jsons = [t.model_dump_json() for t in tactical.trades]

            quant_batch, tac_quant_set = await asyncio.gather(
                generate_quant_analysis_batch(client, strat_pos_jsons, financial_data_str, historical_context),
                generate_tactical_quant_batch(client, tac_jsons, financial_data_str),
            )

        strategic_quant   = quant_batch.analyses[0]
        positional_quants = quant_batch.analyses[1:]
        tactical_quants   = tac_quant_set.quants

        async with _timed("Phase C — trade logic review"):
            logic_review = await generate_trade_logic_review(client, strategic, strategic_quant)

    # ── Level 2: Compile and aggregate ────────────────────────────────────────
    async with _timed("Compile HTML + aggregate JSON"):
        html_document = compile_html_document(
            opening=opening,
            themes=themes,
            overlays=overlays,
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
        )
        json_output = aggregate_json_output(
            opening=opening,
            themes=themes,
            overlays=overlays,
            appendix=appendix,
            strategic=strategic,
            positional=positional,
            tactical=tactical,
            briefing_date=target_date,
        )

    # ── Level 1: Save to disk ─────────────────────────────────────────────────
    async with _timed("Save HTML + JSON"):
        html_path, json_path = await asyncio.gather(
            save_html_briefing(html_document, target_date),
            save_json_data(json_output, target_date),
        )

    if preview:
        webbrowser.open(html_path.resolve().as_uri())
        _log("PREVIEW", f"Opened in browser → {html_path}")

    # ── Level 1: Commit to longitudinal memory ────────────────────────────────
    async with _timed("Commit to longitudinal memory"):
        commit_to_longitudinal_memory(target_date.isoformat(), json_output)
        purged = purge_expired_memory(days=config.MEMORY_DAYS)
    if purged:
        _log("LEVEL 1", f"Purged {purged} expired record(s) from longitudinal memory.")

    # ── Delivery: PDF + email ─────────────────────────────────────────────────
    async with _timed("Render PDF + deliver email"):
        pdf_path = await deliver_briefing(
            html_path=html_path,
            briefing_date=target_date,
        )

    # ── Level 6: Unlabel ingested emails ──────────────────────────────────────
    if purge_labels and payload.from_gmail:
        async with _timed(f"Unlabel {len(payload.message_ids)} email(s)"):
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
