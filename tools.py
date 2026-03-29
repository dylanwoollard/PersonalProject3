"""
tools.py — Data augmentation and research tooling (Level 4).

Functions:
  verify_financial_data          — Fetch live prices, P/E ratios, earnings dates
                                   via yfinance (concurrent, thread-pool).
  format_financial_data_for_prompt — Render FinancialData dict as prompt string.
  retrieve_historical_context    — Query 180-day SQLite memory for overlapping
                                   entities and situations.

Polymorphic Data Hydration:
  _classify_instrument classifies each ticker into one of four instrument classes
  (equity, etf, index, price_only) before fetching, allowing _fetch_single_ticker
  to apply class-appropriate field extraction.  Indices, futures, and FX pairs
  receive a price-only fetch via t.history; ETFs receive price + liquidity data
  but never P/E or earnings; equities receive the full fundamental suite.
  This prevents HTTP 404 errors and avoids injecting meaningless None fields into
  the prompt for instruments where those fields are structurally undefined.
"""

import asyncio
import json
import os
import re
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

from models import FinancialData

DB_PATH = Path("intelligence_memory.db")


# ── Market Snapshot ───────────────────────────────────────────────────────────

@dataclass
class MarketTick:
    label: str
    ticker: str
    price: float | None = None
    change_pct: float | None = None


def _fetch_market_tick_sync(ticker: str, label: str) -> MarketTick:
    """Fetch latest price and day-over-day change for a single instrument."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2d")
        if len(hist) >= 2:
            prev = float(hist["Close"].iloc[-2])
            curr = float(hist["Close"].iloc[-1])
            change_pct = (curr - prev) / prev * 100
        elif len(hist) == 1:
            curr = float(hist["Close"].iloc[-1])
            change_pct = None
        else:
            return MarketTick(label=label, ticker=ticker)
        return MarketTick(label=label, ticker=ticker, price=curr, change_pct=change_pct)
    except Exception:
        return MarketTick(label=label, ticker=ticker)


_SNAPSHOT_INSTRUMENTS = [
    ("CL=F",     "WTI CRUDE"),
    ("^TNX",     "10Y YIELD"),
    ("DX-Y.NYB", "DXY INDEX"),
    ("^GSPC",    "S&P 500"),
]


async def fetch_market_snapshot() -> list[MarketTick]:
    """
    Fetch a real-time market snapshot: WTI Crude, 10Y Yield, DXY Index, S&P 500.
    Each fetch runs in a thread-pool executor. Failures return a tick with None price.
    """
    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, _fetch_market_tick_sync, ticker, label)
        for ticker, label in _SNAPSHOT_INSTRUMENTS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ticks = []
    for (ticker, label), result in zip(_SNAPSHOT_INSTRUMENTS, results):
        if isinstance(result, Exception):
            ticks.append(MarketTick(label=label, ticker=ticker))
        else:
            ticks.append(result)
    return ticks


# ── Ticker Normalization ──────────────────────────────────────────────────────

# Maps common commodity/index names and exchange-prefixed formats to yfinance symbols.
_COMMODITY_MAP: dict[str, str] = {
    "BRENT CRUDE":   "BZ=F",
    "BRENT":         "BZ=F",
    "WTI CRUDE":     "CL=F",
    "WTI":           "CL=F",
    "CRUDE OIL":     "CL=F",
    "NATURAL GAS":   "NG=F",
    "GOLD":          "GC=F",
    "SILVER":        "SI=F",
    "COPPER":        "HG=F",
    "WHEAT":         "ZW=F",
    "CORN":          "ZC=F",
    "S&P 500":       "^GSPC",
    "SPX":           "^GSPC",
    "VIX":           "^VIX",
    "10Y YIELD":     "^TNX",
    "US10Y":         "^TNX",
    "DXY":           "DX-Y.NYB",
    "DXY INDEX":     "DX-Y.NYB",
    "US DOLLAR":     "DX-Y.NYB",
    "EUR/USD":       "EURUSD=X",
    "EURUSD":        "EURUSD=X",
    "USD/JPY":       "USDJPY=X",
    "USDJPY":        "USDJPY=X",
    "GBP/USD":       "GBPUSD=X",
    "USD/CNY":       "USDCNY=X",
}

# Exchange prefixes to strip — CBOE index tickers need a ^ prefix.
_CBOE_INDEX_TICKERS = {"SPX", "VIX", "NDX", "RUT"}


def normalize_ticker(raw: str) -> str | None:
    """
    Convert an LLM-generated instrument string into a yfinance-compatible symbol.

    Handles:
      - "NYSE:DAL | Delta Air Lines"  →  "DAL"
      - "NYSEARCA:TLT"                →  "TLT"
      - "CBOE:SPX"                    →  "^GSPC"
      - "BRENT CRUDE"                 →  "BZ=F"
      - Already-clean tickers         →  unchanged

    Returns None if the string cannot be resolved to a plausible ticker.
    """
    # Strip any " | long name" suffix the LLM may have appended.
    symbol = raw.split("|")[0].strip()

    # Check commodity/index name map first (before stripping colons).
    upper = symbol.upper()
    if upper in _COMMODITY_MAP:
        return _COMMODITY_MAP[upper]

    # Strip exchange prefix (e.g., "NYSE:DAL" → "DAL", "CBOE:SPX" → "SPX").
    if ":" in symbol:
        exchange, ticker = symbol.split(":", 1)
        exchange = exchange.upper().strip()
        ticker   = ticker.strip()
        if exchange == "CBOE" and ticker.upper() in _CBOE_INDEX_TICKERS:
            return f"^{ticker.upper()}"
        symbol = ticker

    # Strip stray punctuation the LLM may have appended (e.g., "DAL)", "NVDA.").
    symbol = re.sub(r"[^A-Za-z0-9\^\=\.\-]", "", symbol)

    # If still contains a space it's a descriptive phrase — drop it.
    if " " in symbol:
        return None

    # Drop anything implausibly long for a ticker symbol.
    if len(symbol) > 10:
        return None

    return symbol if symbol else None


# ── Instrument Classification ─────────────────────────────────────────────────

# yfinance quoteType values for each class
_EQUITY_QUOTE_TYPES   = {"equity"}
_ETF_QUOTE_TYPES      = {"etf", "mutualfund"}
_INDEX_QUOTE_TYPES    = {"index"}
_FUTURES_QUOTE_TYPES  = {"future", "futures"}
_FX_QUOTE_TYPES       = {"currency", "forex"}


def _classify_instrument(ticker: str, info: dict) -> str:
    """
    Classify a ticker into one of four instrument classes used to gate
    which fundamental fields are fetched and returned.

    Classes:
      "equity"     — full fundamentals: P/E, earnings date, market cap,
                     volume, 52-week range.
      "etf"        — liquidity data only: market cap (as AUM), volume,
                     52-week range.  No P/E, no earnings date.
      "index"      — price only (close_price from t.history).
                     Indices have no tradeable fundamentals.
      "price_only" — futures (=F), FX pairs (=X), and any instrument
                     whose quoteType is not recognised.  Price only.

    Classification order:
      1. yfinance quoteType field (authoritative when info is populated).
      2. Ticker pattern fallback (for instruments where t.info returned
         a stub with < 5 keys and info is therefore {}).
    """
    qt = (info.get("quoteType") or "").lower()

    if qt in _EQUITY_QUOTE_TYPES:
        return "equity"
    if qt in _ETF_QUOTE_TYPES:
        return "etf"
    if qt in _INDEX_QUOTE_TYPES:
        return "index"
    if qt in _FUTURES_QUOTE_TYPES or qt in _FX_QUOTE_TYPES:
        return "price_only"

    # Pattern fallback — info was empty/stub
    if ticker.startswith("^"):
        return "index"
    if ticker.endswith("=F"):
        return "price_only"  # futures
    if ticker.endswith("=X"):
        return "price_only"  # FX pair

    # Default: treat as equity — will fail gracefully if fundamentals are absent
    return "equity"


# ── Financial Data Verification ───────────────────────────────────────────────

def _fetch_single_ticker(ticker: str) -> FinancialData:
    """
    Synchronous yfinance call for one ticker — Polymorphic Data Hydration.

    The fetch strategy depends on instrument class (see _classify_instrument):

      equity     — Tier 1 (t.info) for full fundamentals; Tier 2 (t.history)
                   price fallback; calendar API for next earnings date.
      etf        — Tier 1 for price + liquidity + 52-week range; Tier 2 price
                   fallback.  pe_ratio and next_earnings_date are always None.
      index      — Tier 2 only (t.history).  Indices return HTTP 404 or empty
                   stubs from quoteSummary; all fundamentals are None.
      price_only — Tier 2 only (t.history).  Covers futures (=F) and FX (=X).
                   All fundamentals are None by definition.

    Classification is performed after Tier 1 using the quoteType field in the
    info dict, with a ticker-pattern fallback for stub responses.  This means
    equities and ETFs still attempt Tier 1 first to read the quoteType; the
    branch then gates which fields are extracted from the already-fetched dict.

    Returns a FinancialData with all-None fields on total failure; never raises.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)

        # ── Tier 1: t.info ─────────────────────────────────────────────────────
        # Attempted for all tickers so quoteType is available for classification.
        # For indices/futures/FX this will 404 or return a stub — that's expected
        # and the except silently falls through to Tier 2.
        info: dict = {}
        try:
            fetched = t.info
            # yfinance returns single-key stubs ({"trailingPegRatio": None}) for
            # instruments that don't support quoteSummary.  Treat < 5 keys as empty.
            if isinstance(fetched, dict) and len(fetched) >= 5:
                info = fetched
        except Exception:
            pass  # HTTP 404, quoteSummary error, network timeout → Tier 2

        # Classify instrument using quoteType (authoritative) or ticker pattern
        iclass = _classify_instrument(ticker, info)

        # ── Close price ────────────────────────────────────────────────────────
        close_price: float | None = (
            info.get("currentPrice") or info.get("regularMarketPrice")
        )

        # ── Tier 2: t.history price fallback ──────────────────────────────────
        # Used when Tier 1 returned no price (index, futures, FX, or equity stub).
        if close_price is None:
            try:
                hist = t.history(period="1d")
                if not hist.empty:
                    close_price = float(hist["Close"].iloc[-1])
            except Exception:
                pass

        # ── Next earnings date (equity only) ──────────────────────────────────
        # The calendar API is meaningless and will error for ETFs, indices, and
        # futures — skip it entirely for non-equity instrument classes.
        next_earnings: str | None = None
        if iclass == "equity" and info:
            try:
                cal = t.calendar
                if cal is not None and not cal.empty and "Earnings Date" in cal.index:
                    raw   = cal.loc["Earnings Date"]
                    dates = list(raw) if hasattr(raw, "__iter__") else [raw]
                    if dates:
                        next_earnings = str(dates[0])
            except Exception:
                pass

        # ── Assemble FinancialData — gate fields by instrument class ───────────
        #
        #  equity:     all fields populated where available
        #  etf:        market_cap (AUM), volume, 52-week range; no P/E, no earnings
        #  index:      price only
        #  price_only: price only (futures, FX)
        #
        is_equity = iclass == "equity"
        has_liquidity = iclass in ("equity", "etf")   # volume + 52wk range
        has_market_cap = iclass in ("equity", "etf")  # marketCap / AUM

        return FinancialData(
            ticker=ticker,
            close_price=close_price,
            pe_ratio=info.get("trailingPE")  if is_equity      else None,
            forward_pe=info.get("forwardPE") if is_equity      else None,
            next_earnings_date=next_earnings,                           # None unless equity
            market_cap=info.get("marketCap") if has_market_cap else None,
            fifty_two_week_high=info.get("fiftyTwoWeekHigh")   if has_liquidity else None,
            fifty_two_week_low=info.get("fiftyTwoWeekLow")     if has_liquidity else None,
            avg_daily_volume=(
                info.get("averageVolume")
            ) if has_liquidity else None,
            avg_daily_volume_10d=(
                info.get("averageVolume10days") or info.get("averageDailyVolume10Day")
            ) if has_liquidity else None,
        )

    except Exception:
        return FinancialData(ticker=ticker)


async def verify_financial_data(tickers: List[str]) -> Dict[str, FinancialData]:
    """
    Fetch current financial data for a list of tickers.

    Each yfinance call runs in a thread-pool executor so blocking I/O does not
    stall the event loop.  All tickers are dispatched concurrently.

    Args:
        tickers: List of ticker symbols (e.g., ["XLE", "TLT", "DXY"]).

    Returns:
        Dict mapping each ticker to its FinancialData object.
    """
    if not tickers:
        return {}

    loop  = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, _fetch_single_ticker, t) for t in tickers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: Dict[str, FinancialData] = {}
    for ticker, result in zip(tickers, results):
        if isinstance(result, Exception):
            output[ticker] = FinancialData(ticker=ticker)
        else:
            output[ticker] = result  # type: ignore[assignment]

    return output


def format_financial_data_for_prompt(data: Dict[str, FinancialData]) -> str:
    """Render a FinancialData dict as a human-readable block for prompt injection.

    Tickers with no close_price are silently filtered out — they carry no
    useful signal for the model and waste prompt tokens.
    """
    if not data:
        return "No financial market data was retrieved."

    # Filter tickers where price lookup failed (all numeric fields None)
    data = {k: v for k, v in data.items() if v.close_price is not None}

    if not data:
        return "No financial market data was retrieved (all tickers returned empty data)."

    lines = ["### Verified Financial Data (Live)\n"]
    for ticker, fd in data.items():
        lines.append(f"**{ticker}**")
        if fd.close_price is not None:
            lines.append(f"  Current Price:    ${fd.close_price:,.2f}")
        if fd.pe_ratio is not None:
            lines.append(f"  Trailing P/E:     {fd.pe_ratio:.2f}x")
        if fd.forward_pe is not None:
            lines.append(f"  Forward P/E:      {fd.forward_pe:.2f}x")
        if fd.next_earnings_date:
            lines.append(f"  Next Earnings:    {fd.next_earnings_date}")
        if fd.market_cap is not None:
            lines.append(f"  Market Cap:       ${fd.market_cap:,.0f}")
        if fd.fifty_two_week_high is not None and fd.fifty_two_week_low is not None:
            lines.append(
                f"  52-Week Range:    ${fd.fifty_two_week_low:,.2f} – ${fd.fifty_two_week_high:,.2f}"
            )
        if fd.avg_daily_volume is not None:
            vol = fd.avg_daily_volume
            vol_str = f"{vol/1e6:.2f}M" if vol >= 1_000_000 else f"{vol/1e3:.0f}K"
            if fd.close_price:
                adv = fd.close_price * vol
                adv_str = f"~${adv/1e9:.1f}B" if adv >= 1e9 else f"~${adv/1e6:.0f}M"
                lines.append(f"  Avg Daily Vol:    ~{vol_str} shs / {adv_str} ADV (3-month)")
            else:
                lines.append(f"  Avg Daily Vol:    ~{vol_str} shs (3-month)")
        if fd.avg_daily_volume_10d is not None:
            vol10 = fd.avg_daily_volume_10d
            vol10_str = f"{vol10/1e6:.2f}M" if vol10 >= 1_000_000 else f"{vol10/1e3:.0f}K"
            lines.append(f"  Avg Daily Vol 10d:~{vol10_str} shs (10-day, recent liquidity)")
        lines.append("")

    return "\n".join(lines)


# ── Historical Context Retrieval ──────────────────────────────────────────────

def retrieve_historical_context(
    entities: List[str],
    situations: List[str],
    lookback_days: int = 180,
) -> str:
    """
    Query the 180-day SQLite longitudinal memory for prior briefings that share
    entities or situations with today's intelligence.

    Matches are case-insensitive substring matches against stored entity and
    situation lists.  Up to 10 matching records are returned, most recent first.

    Args:
        entities:      Named entities from today's appendix.
        situations:    Situation labels from today's appendix.
        lookback_days: How far back to search (default: 180 days).

    Returns:
        A formatted string ready for injection into prompt templates.
    """
    if not DB_PATH.exists():
        return (
            "No longitudinal memory database found.  "
            "This appears to be the first run of the system."
        )

    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT date, raw_json, entities, situations, themes
            FROM   briefings
            WHERE  date >= ?
            ORDER  BY date DESC
            LIMIT  60
            """,
            (cutoff,),
        ).fetchall()

    if not rows:
        return (
            "No records found within the 180-day lookback window.  "
            "The memory database exists but contains no entries in this period."
        )

    entity_set    = {e.lower() for e in entities}
    situation_set = {s.lower() for s in situations}

    relevant: list[dict] = []
    for row in rows:
        try:
            stored_entities    = {e.lower() for e in json.loads(row["entities"])}
            stored_situations  = {s.lower() for s in json.loads(row["situations"])}
            stored_themes      = json.loads(row["themes"])
            briefing           = json.loads(row["raw_json"])

            entity_overlap    = entity_set    & stored_entities
            situation_overlap = situation_set & stored_situations

            if entity_overlap or situation_overlap:
                relevant.append(
                    {
                        "date":              row["date"],
                        "entity_overlap":    sorted(entity_overlap),
                        "situation_overlap": sorted(situation_overlap),
                        "themes":            stored_themes,
                        "summary": (
                            briefing.get("appendix", {})
                                    .get("intelligence_summary", "No summary on record.")
                        ),
                    }
                )
        except (json.JSONDecodeError, KeyError):
            continue

    if not relevant:
        return (
            f"The database contains {len(rows)} records within the lookback window, "
            "but none share entities or situations with today's intelligence.  "
            "This may indicate a novel or untracked situation."
        )

    # Cap at 5 records to keep prompt size manageable (key for latency)
    relevant = relevant[:5]

    lines = [
        f"### Longitudinal Context — {len(relevant)} Relevant Prior Record(s)\n",
        "The following prior briefings overlap with today's entities or situations:\n",
    ]

    for rec in relevant:
        lines.append(f"#### {rec['date']}")
        if rec["entity_overlap"]:
            lines.append(f"Overlapping Entities:    {', '.join(rec['entity_overlap'])}")
        if rec["situation_overlap"]:
            lines.append(f"Overlapping Situations:  {', '.join(rec['situation_overlap'])}")
        if rec["themes"]:
            lines.append(f"Themes That Day:         {', '.join(rec['themes'])}")
        lines.append(f"\nSummary:\n{rec['summary']}\n")
        lines.append("---")

    return "\n".join(lines)


# ── Economic Calendar (Finnhub) ────────────────────────────────────────────────

@dataclass
class EconomicEvent:
    date_str: str           # "2026-04-01"
    time_et:  str | None    # "08:30" or None
    event:    str           # "Non Farm Payroll"
    country:  str           # "US"
    previous: str | None    # "-92K" or "-0.2%"
    forecast: str | None    # "+50K"
    impact:   str           # "high" | "medium" | "low"


def _format_eco_value(value: float | None, unit: str | None) -> str | None:
    """Format a Finnhub numeric reading + unit into a human-readable string."""
    if value is None:
        return None
    unit = unit or ""
    sign = "+" if value > 0 else ""
    if unit == "%":
        return f"{sign}{value:.1f}%"
    elif unit in ("K", "M", "B"):
        return f"{sign}{value:,.0f}{unit}"
    else:
        # Dimensionless or unknown unit
        formatted = f"{sign}{value:,.2f}"
        return f"{formatted}{unit}" if unit else formatted


def _fetch_economic_calendar_sync(days_ahead: int, min_impact: str) -> list[EconomicEvent]:
    """Synchronous Finnhub economic calendar fetch (run in a thread executor)."""
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        return []

    from_date = date.today()
    to_date   = from_date + timedelta(days=days_ahead)
    url = (
        f"https://finnhub.io/api/v1/calendar/economic"
        f"?from={from_date.isoformat()}&to={to_date.isoformat()}&token={api_key}"
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
    except Exception:
        return []

    impact_rank = {"high": 3, "medium": 2, "low": 1}
    min_rank    = impact_rank.get(min_impact.lower(), 3)

    events: list[EconomicEvent] = []
    for item in data.get("economicCalendar", []):
        item_impact = (item.get("impact") or "").lower()
        if impact_rank.get(item_impact, 0) < min_rank:
            continue

        # Parse date and time from "YYYY-MM-DD HH:MM:SS" or bare "YYYY-MM-DD"
        raw_time = item.get("time") or item.get("date") or ""
        parts    = raw_time.split(" ")
        date_str = parts[0] if parts else ""
        time_et  = parts[1][:5] if len(parts) > 1 else None  # "08:30"

        if not date_str:
            continue

        events.append(EconomicEvent(
            date_str = date_str,
            time_et  = time_et,
            event    = item.get("event", ""),
            country  = (item.get("country") or "US").upper(),
            previous = _format_eco_value(item.get("prev"),     item.get("unit")),
            forecast = _format_eco_value(item.get("estimate"), item.get("unit")),
            impact   = item_impact,
        ))

    events.sort(key=lambda e: (e.date_str, e.time_et or "99:99"))
    return events


async def fetch_economic_calendar(
    days_ahead: int = 7,
    min_impact: str = "high",
) -> list[EconomicEvent]:
    """
    Fetch scheduled economic releases from Finnhub for the next `days_ahead` days.

    Requires FINNHUB_API_KEY in the environment.  Returns an empty list (silently)
    when the key is absent or the API call fails.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _fetch_economic_calendar_sync, days_ahead, min_impact
    )


_DOW        = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_HOUR_LABELS = {
    "bmo": "Before Market Open",
    "amc": "After Market Close",
    "dmh": "During Market Hours",
}


def format_economic_calendar_for_prompt(events: list[EconomicEvent]) -> str:
    """
    Render a list of EconomicEvent objects into a structured text block for
    injection into the opening-sections prompt.
    """
    if not events:
        return (
            "No economic calendar data available — FINNHUB_API_KEY not set "
            "or no high-impact events found for the coming week."
        )

    lines = [
        "### SCHEDULED ECONOMIC RELEASES — Live Data (Next 7 Days)",
        "The following data is sourced verbatim from the Finnhub economic calendar.",
        "When generating calendar_events, copy date_str, time_et, event, previous,",
        "and forecast exactly as shown below.  Generate entity and market_relevance.\n",
    ]

    current_date = ""
    for ev in events:
        if ev.date_str != current_date:
            current_date = ev.date_str
            try:
                d   = date.fromisoformat(current_date)
                dow = _DOW[d.weekday()]
                lines.append(f"**{dow}, {current_date}**")
            except ValueError:
                lines.append(f"**{current_date}**")

        time_part = f"  {ev.time_et} ET" if ev.time_et else ""
        lines.append(f"  • {ev.event}{time_part} [{ev.country}]")
        prev_str = f"Previous: {ev.previous}" if ev.previous is not None else "Previous: —"
        fcst_str = f"Forecast: {ev.forecast}" if ev.forecast is not None else "Forecast: —"
        lines.append(f"    {prev_str}  |  {fcst_str}")

    return "\n".join(lines)


# ── Earnings Calendar (Finnhub) ────────────────────────────────────────────────

@dataclass
class EarningsEntry:
    date_str:          str
    hour:              str | None    # "bmo", "amc", "dmh"
    ticker:            str
    eps_estimate:      float | None
    eps_actual:        float | None
    revenue_estimate:  float | None
    revenue_actual:    float | None
    quarter:           int | None
    year:              int | None


def _fmt_eps(value: float | None) -> str | None:
    if value is None:
        return None
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):.2f}"


def _fmt_revenue(value: float | None) -> str | None:
    if value is None:
        return None
    v = abs(value)
    if v >= 1e12:
        return f"${value / 1e12:.2f}T"
    if v >= 1e9:
        return f"${value / 1e9:.1f}B"
    if v >= 1e6:
        return f"${value / 1e6:.0f}M"
    return f"${value:,.0f}"


def _fmt_eps_surprise(actual: float | None, estimate: float | None) -> str | None:
    if actual is None or estimate is None:
        return None
    diff = actual - estimate
    direction = "beat" if diff > 0.005 else ("miss" if diff < -0.005 else "in-line")
    pct = (diff / abs(estimate) * 100) if estimate != 0 else 0.0
    diff_str = f"+${abs(diff):.2f}" if diff >= 0 else f"-${abs(diff):.2f}"
    return f"{diff_str} {direction} ({pct:+.1f}%)"


def _fmt_rev_surprise(actual: float | None, estimate: float | None) -> str | None:
    if actual is None or estimate is None:
        return None
    diff = actual - estimate
    direction = "beat" if diff > 0 else ("miss" if diff < 0 else "in-line")
    pct = (diff / abs(estimate) * 100) if estimate != 0 else 0.0
    abs_diff = abs(diff)
    if abs_diff >= 1e9:
        diff_str = f"+${abs_diff / 1e9:.1f}B" if diff >= 0 else f"-${abs_diff / 1e9:.1f}B"
    elif abs_diff >= 1e6:
        diff_str = f"+${abs_diff / 1e6:.0f}M" if diff >= 0 else f"-${abs_diff / 1e6:.0f}M"
    else:
        diff_str = f"+${abs_diff:,.0f}" if diff >= 0 else f"-${abs_diff:,.0f}"
    return f"{diff_str} {direction} ({pct:+.1f}%)"


def _fetch_earnings_calendar_sync(
    days_behind: int,
    days_ahead: int,
) -> tuple[list[EarningsEntry], list[EarningsEntry]]:
    """
    Fetch earnings from Finnhub for the window [today-days_behind, today+days_ahead].
    Returns (upcoming_top5, recent_top5) sorted by estimated revenue (largest first).
    """
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        return [], []

    from_date = date.today() - timedelta(days=days_behind)
    to_date   = date.today() + timedelta(days=days_ahead)
    url = (
        f"https://finnhub.io/api/v1/calendar/earnings"
        f"?from={from_date.isoformat()}&to={to_date.isoformat()}&token={api_key}"
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
    except Exception:
        return [], []

    upcoming: list[EarningsEntry] = []
    recent:   list[EarningsEntry] = []

    for item in data.get("earningsCalendar", []):
        ticker = (item.get("symbol") or "").strip()
        d_str  = (item.get("date") or "").strip()
        if not ticker or not d_str:
            continue

        entry = EarningsEntry(
            date_str         = d_str,
            hour             = item.get("hour"),
            ticker           = ticker,
            eps_estimate     = item.get("epsEstimate"),
            eps_actual       = item.get("epsActual"),
            revenue_estimate = item.get("revenueEstimate"),
            revenue_actual   = item.get("revenueActual"),
            quarter          = item.get("quarter"),
            year             = item.get("year"),
        )

        # Classify by whether actuals are present (already reported)
        if entry.eps_actual is not None or entry.revenue_actual is not None:
            recent.append(entry)
        elif d_str >= date.today().isoformat():
            upcoming.append(entry)

    def _rev_key(e: EarningsEntry) -> float:
        return abs(e.revenue_estimate or e.revenue_actual or 0)

    # Take top 5 by revenue, then re-sort for readable prompt display
    upcoming_top5 = sorted(upcoming, key=_rev_key, reverse=True)[:5]
    recent_top5   = sorted(recent,   key=_rev_key, reverse=True)[:5]

    upcoming_top5.sort(key=lambda e: e.date_str)           # chronological
    recent_top5.sort(key=lambda e: e.date_str, reverse=True)  # most recent first

    return upcoming_top5, recent_top5


async def fetch_earnings_calendar(
    days_behind: int = 2,
    days_ahead:  int = 7,
) -> tuple[list[EarningsEntry], list[EarningsEntry]]:
    """
    Async wrapper around the Finnhub earnings calendar fetch.
    Returns (upcoming_top5, recent_top5).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _fetch_earnings_calendar_sync, days_behind, days_ahead
    )


def format_earnings_for_prompt(
    upcoming: list[EarningsEntry],
    recent:   list[EarningsEntry],
) -> str:
    """Render upcoming and recent earnings as a structured text block for prompt injection."""
    if not upcoming and not recent:
        return (
            "No earnings calendar data available — FINNHUB_API_KEY not set "
            "or no earnings found in the fetch window."
        )

    lines: list[str] = []

    if upcoming:
        lines += [
            "### UPCOMING EARNINGS (Next 7 Days) — Live Data",
            "Select the 3 most relevant to today's themes. Copy factual fields verbatim.",
            "",
        ]
        for ev in upcoming:
            try:
                dow = _DOW[date.fromisoformat(ev.date_str).weekday()]
                date_label = f"{dow}, {ev.date_str}"
            except ValueError:
                date_label = ev.date_str

            hour_label = _HOUR_LABELS.get(ev.hour or "", "")
            hour_part  = f" [{hour_label}]" if hour_label else ""
            lines.append(f"**{ev.ticker}** — {date_label}{hour_part}")

            parts = []
            if ev.eps_estimate is not None:
                parts.append(f"EPS Est: {_fmt_eps(ev.eps_estimate)}")
            if ev.revenue_estimate is not None:
                parts.append(f"Rev Est: {_fmt_revenue(ev.revenue_estimate)}")
            if parts:
                lines.append(f"  {' | '.join(parts)}")

    if recent:
        if lines:
            lines.append("")
        lines += [
            "### RECENT EARNINGS (Past 48 Hours) — Actual Results",
            "Select the 3 most significant. Copy factual fields verbatim.",
            "",
        ]
        for ev in recent:
            try:
                dow = _DOW[date.fromisoformat(ev.date_str).weekday()]
                date_label = f"{dow}, {ev.date_str}"
            except ValueError:
                date_label = ev.date_str

            hour_label = _HOUR_LABELS.get(ev.hour or "", "")
            hour_part  = f" [{hour_label}]" if hour_label else ""
            lines.append(f"**{ev.ticker}** — {date_label}{hour_part}")

            eps_act = _fmt_eps(ev.eps_actual)
            eps_est = _fmt_eps(ev.eps_estimate)
            eps_sur = _fmt_eps_surprise(ev.eps_actual, ev.eps_estimate)
            rev_act = _fmt_revenue(ev.revenue_actual)
            rev_est = _fmt_revenue(ev.revenue_estimate)
            rev_sur = _fmt_rev_surprise(ev.revenue_actual, ev.revenue_estimate)

            eps_parts = [x for x in [
                f"Act: {eps_act}" if eps_act else None,
                f"Est: {eps_est}" if eps_est else None,
                f"Surprise: {eps_sur}" if eps_sur else None,
            ] if x]
            rev_parts = [x for x in [
                f"Act: {rev_act}" if rev_act else None,
                f"Est: {rev_est}" if rev_est else None,
                f"Surprise: {rev_sur}" if rev_sur else None,
            ] if x]

            if eps_parts:
                lines.append(f"  EPS — {' | '.join(eps_parts)}")
            if rev_parts:
                lines.append(f"  Revenue — {' | '.join(rev_parts)}")

    return "\n".join(lines)


# ── Cross-Asset Correlation Matrix ────────────────────────────────────────────

def calculate_price_correlations(
    tickers: list[str],
    financial_data_map: Dict[str, "FinancialData"],
) -> str:
    """
    Compute a 30-day daily-return correlation matrix for the given tickers
    and return it as a formatted string for prompt injection.

    Uses yfinance t.history(period="1mo") for each ticker.  Tickers that fail
    to download (e.g. invalid symbols) are silently skipped.  If fewer than
    two tickers yield usable data the function returns a fallback message.

    The matrix is formatted as a compact upper-triangular text table so the
    LLM can reference pairwise correlations without needing to parse JSON.
    """
    import math
    import yfinance as yf

    price_series: dict[str, list[float]] = {}
    valid_tickers: list[str] = []

    for raw_ticker in tickers:
        # Strip EXCHANGE: prefix for yfinance
        yfticker = raw_ticker.split(":")[-1] if ":" in raw_ticker else raw_ticker
        try:
            hist = yf.Ticker(yfticker).history(period="1mo")
            if hist.empty or "Close" not in hist.columns or len(hist) < 5:
                continue
            closes = hist["Close"].dropna().tolist()
            if len(closes) < 5:
                continue
            price_series[raw_ticker] = closes
            valid_tickers.append(raw_ticker)
        except Exception:
            continue

    if len(valid_tickers) < 2:
        return "Insufficient data for correlation matrix (fewer than 2 tickers with 30-day history)."

    def log_returns(prices: list[float]) -> list[float]:
        return [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]

    returns: dict[str, list[float]] = {t: log_returns(price_series[t]) for t in valid_tickers}
    min_len = min(len(r) for r in returns.values())
    returns = {t: r[-min_len:] for t, r in returns.items()}

    def pearson(a: list[float], b: list[float]) -> float:
        n = len(a)
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        num = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
        den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
        den_b = math.sqrt(sum((x - mean_b) ** 2 for x in b))
        if den_a == 0 or den_b == 0:
            return 0.0
        return num / (den_a * den_b)

    n = len(valid_tickers)
    labels = [t.split(":")[-1][:8] for t in valid_tickers]
    output_lines: list[str] = [
        f"30-day daily-return correlations ({min_len} trading days, {n} instruments):",
        "",
        "         " + "  ".join(f"{lbl:>8}" for lbl in labels[1:]),
    ]

    for i in range(n - 1):
        row_label = f"{labels[i]:>8} "
        cells: list[str] = []
        for j in range(i + 1, n):
            corr = pearson(returns[valid_tickers[i]], returns[valid_tickers[j]])
            cells.append(f"{corr:+.2f}    ")
        output_lines.append(row_label + "".join(cells))

    output_lines += [
        "",
        "Interpretation: +1.0 = perfect co-movement, -1.0 = perfect inverse, "
        "0.0 = no linear relationship.",
    ]
    return "\n".join(output_lines)
