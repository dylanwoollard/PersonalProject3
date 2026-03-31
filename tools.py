"""
tools.py — Data augmentation and research tooling (Level 4).

Functions:
  verify_financial_data          — Fetch live prices, P/E ratios, earnings dates
                                   via httpx async Yahoo Finance client.
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
import math
import os
import re
import sqlite3
from pydantic import BaseModel

try:
    import fmpsdk as _fmpsdk
except ImportError:
    _fmpsdk = None
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

import httpx

from models import FinancialData

DB_PATH = Path("intelligence_memory.db")


# ── Async Yahoo Finance Session ───────────────────────────────────────────────

class _YFSession:
    """
    Shared httpx.AsyncClient for all Yahoo Finance API calls.

    Handles the crumb/cookie authentication that Yahoo Finance requires since
    late 2023.  Authentication is lazy — the crumb is acquired on the first
    API call and reused for the lifetime of the session.  A 401 response
    automatically triggers a single crumb refresh before retrying.

    The module-level `_yf_session` singleton is initialised on first use via
    `_get_yf_session()` and closed at the end of the pipeline via
    `close_yf_session()`.  Using a single shared client across all concurrent
    financial-data calls maximises connection reuse and keeps the cookie jar
    consistent.
    """

    _BASE1 = "https://query1.finance.yahoo.com"
    _BASE2 = "https://query2.finance.yahoo.com"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._crumb:  str | None = None
        self._lock = asyncio.Lock()

    async def _ensure_auth(self) -> None:
        """Acquire cookies + crumb if not already cached (idempotent, lock-protected)."""
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0 Safari/537.36"
                        ),
                        "Accept": "application/json, text/plain, */*",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    follow_redirects=True,
                    timeout=20.0,
                )
            if self._crumb is None:
                # Step 1: seed the A3 cookie that Yahoo requires
                await self._client.get("https://fc.yahoo.com")
                # Step 2: exchange cookies for a crumb token
                r = await self._client.get(f"{self._BASE1}/v1/test/getcrumb")
                r.raise_for_status()
                self._crumb = r.text.strip()

    async def _refresh_crumb(self) -> None:
        """Force a crumb refresh (called after a 401 response)."""
        async with self._lock:
            self._crumb = None
        await self._ensure_auth()

    async def get_quote_summary(self, ticker: str) -> dict:
        """
        Call Yahoo Finance quoteSummary with modules:
          quoteType, summaryDetail, calendarEvents

        Returns the merged module dict (all module keys at the top level),
        or {} if the ticker is not found or the call fails.
        """
        await self._ensure_auth()
        url = f"{self._BASE2}/v10/finance/quoteSummary/{ticker}"
        params = {
            "modules": "quoteType,summaryDetail,calendarEvents",
            "crumb": self._crumb,
        }
        try:
            r = await self._client.get(url, params=params)
            if r.status_code == 401:
                await self._refresh_crumb()
                params["crumb"] = self._crumb
                r = await self._client.get(url, params=params)
            if r.status_code != 200:
                return {}
            data = r.json()
            result_list = (data.get("quoteSummary") or {}).get("result") or []
            if not result_list:
                return {}
            # Merge all module sub-dicts into a single flat dict
            merged: dict = {}
            for module_dict in result_list:
                for module_data in module_dict.values():
                    if isinstance(module_data, dict):
                        merged.update(module_data)
            return merged
        except Exception:
            return {}

    async def get_chart(self, ticker: str, interval: str = "1d", range_: str = "1d") -> dict:
        """
        Call Yahoo Finance chart API and return the result meta + close prices.

        Returns a dict with keys:
          "price"   — latest close price (float | None)
          "closes"  — list[float] of all valid close prices in the range
        """
        await self._ensure_auth()
        url = f"{self._BASE1}/v8/finance/chart/{ticker}"
        params = {"interval": interval, "range": range_, "crumb": self._crumb}
        try:
            r = await self._client.get(url, params=params)
            if r.status_code == 401:
                await self._refresh_crumb()
                params["crumb"] = self._crumb
                r = await self._client.get(url, params=params)
            if r.status_code != 200:
                return {"price": None, "closes": []}
            data = r.json()
            result = ((data.get("chart") or {}).get("result") or [None])[0]
            if not result:
                return {"price": None, "closes": []}
            # Collect non-null close prices
            raw_closes = (
                (result.get("indicators") or {})
                .get("quote", [{}])[0]
                .get("close", [])
            ) or []
            closes = [float(c) for c in raw_closes if c is not None]
            price = closes[-1] if closes else result.get("meta", {}).get("regularMarketPrice")
            return {"price": float(price) if price is not None else None, "closes": closes}
        except Exception:
            return {"price": None, "closes": []}

    async def aclose(self) -> None:
        """Close the underlying httpx client and reset auth state."""
        async with self._lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None
                self._crumb  = None


# Module-level singleton — shared across all financial data functions.
_yf_session: _YFSession | None = None


async def _get_yf_session() -> _YFSession:
    """Return (and lazily initialise) the module-level _YFSession singleton."""
    global _yf_session
    if _yf_session is None:
        _yf_session = _YFSession()
    return _yf_session


async def close_yf_session() -> None:
    """
    Close and reset the module-level Yahoo Finance session.
    Call once at the end of the pipeline to release the httpx client.
    """
    global _yf_session
    if _yf_session is not None:
        await _yf_session.aclose()
        _yf_session = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _yf_raw(module_dict: dict, *keys):
    """
    Navigate a (possibly nested) Yahoo Finance module dict and extract a raw
    numeric value from a {"raw": ..., "fmt": ...} leaf.

    Example:
        _yf_raw(info, "summaryDetail", "regularMarketPrice")
    If `info` has been flattened (all modules merged at the top level) the first
    key resolves immediately to the {"raw": ...} leaf.
    """
    obj = module_dict
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    if isinstance(obj, dict):
        return obj.get("raw")
    return obj


# ── Market Snapshot ───────────────────────────────────────────────────────────

class MarketTick(BaseModel):
    label: str
    ticker: str
    price: float | None = None
    change_pct: float | None = None


_SNAPSHOT_INSTRUMENTS = [
    ("CL=F",     "WTI CRUDE"),
    ("^TNX",     "10Y YIELD"),
    ("DX-Y.NYB", "DXY INDEX"),
    ("^GSPC",    "S&P 500"),
]


async def _fetch_market_tick_async(ticker: str, label: str, session: _YFSession) -> MarketTick:
    """Fetch latest price and day-over-day % change for a single instrument."""
    chart = await session.get_chart(ticker, interval="1d", range_="5d")
    closes = chart["closes"]
    if len(closes) >= 2:
        prev, curr = closes[-2], closes[-1]
        return MarketTick(label=label, ticker=ticker, price=curr,
                          change_pct=(curr - prev) / prev * 100)
    if len(closes) == 1:
        return MarketTick(label=label, ticker=ticker, price=closes[-1])
    return MarketTick(label=label, ticker=ticker)


async def fetch_market_snapshot() -> list[MarketTick]:
    """
    Fetch a real-time market snapshot: WTI Crude, 10Y Yield, DXY Index, S&P 500.

    All four instruments are fetched concurrently via a single shared
    httpx.AsyncClient — no thread-pool executor required.
    """
    session = await _get_yf_session()
    results = await asyncio.gather(
        *[_fetch_market_tick_async(t, lbl, session) for t, lbl in _SNAPSHOT_INSTRUMENTS],
        return_exceptions=True,
    )
    return [
        result if not isinstance(result, Exception)
        else MarketTick(label=lbl, ticker=t)
        for (t, lbl), result in zip(_SNAPSHOT_INSTRUMENTS, results)
    ]


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


def _classify_instrument(ticker: str, quote_type: str) -> str:
    """
    Classify a ticker into one of four instrument classes used to gate
    which fundamental fields are fetched and returned.

    Classes:
      "equity"     — full fundamentals: P/E, earnings date, market cap,
                     volume, 52-week range.
      "etf"        — liquidity data only: market cap (as AUM), volume,
                     52-week range.  No P/E, no earnings date.
      "index"      — price only.  Indices have no tradeable fundamentals.
      "price_only" — futures (=F), FX pairs (=X), and any unrecognised type.
                     Price only.

    Classification uses the quoteType string from Yahoo Finance quoteSummary
    (authoritative when quoteSummary succeeded), falling back to ticker-pattern
    rules when quoteSummary returned an empty result.
    """
    qt = quote_type.lower()

    if qt in _EQUITY_QUOTE_TYPES:   return "equity"
    if qt in _ETF_QUOTE_TYPES:      return "etf"
    if qt in _INDEX_QUOTE_TYPES:    return "index"
    if qt in _FUTURES_QUOTE_TYPES or qt in _FX_QUOTE_TYPES:
        return "price_only"

    # Pattern fallback when quoteSummary returned no quoteType
    if ticker.startswith("^"):  return "index"
    if ticker.endswith("=F"):   return "price_only"
    if ticker.endswith("=X"):   return "price_only"
    return "equity"


# ── Financial Data Verification ───────────────────────────────────────────────

async def _fetch_single_ticker_async(ticker: str, session: _YFSession) -> FinancialData:
    """
    Async replacement for the former synchronous yfinance _fetch_single_ticker.

    Tier 1 — Yahoo Finance quoteSummary (quoteType + summaryDetail + calendarEvents):
      All tickers are attempted.  For indices, futures, and FX pairs the
      quoteSummary often returns a minimal result or an empty dict; Tier 2
      handles price retrieval for those classes.

    Tier 2 — Yahoo Finance chart API (1-day range):
      Used as a price fallback when Tier 1 returned no close price, and as the
      primary data source for index/price_only instruments.

    The same Polymorphic Data Hydration logic as before:
      equity     — full fundamentals
      etf        — price + liquidity + 52-wk range, no P/E or earnings
      index      — price only
      price_only — price only (futures, FX)

    Never raises; returns FinancialData(ticker=ticker) with all-None fields on
    total failure.
    """
    try:
        # ── Tier 1: quoteSummary ───────────────────────────────────────────────
        info = await session.get_quote_summary(ticker)

        quote_type_str: str = ""
        if isinstance(info.get("quoteType"), str):
            # When the quoteType module key itself is a string (rare)
            quote_type_str = info["quoteType"]
        elif isinstance(info.get("quoteType"), dict):
            quote_type_str = info["quoteType"].get("quoteType", "")
        else:
            # Flattened merge: quoteType is a plain string at top level
            # OR it's nested {"quoteType": "EQUITY"} — both handled above.
            # If absent, try the raw field directly.
            quote_type_str = info.get("quoteType") or ""

        iclass = _classify_instrument(ticker, str(quote_type_str))

        # Extract price from summaryDetail (prefers currentPrice, falls back to
        # regularMarketPrice).  Both may be {"raw": float} dicts.
        close_price: float | None = _yf_raw(info, "currentPrice")
        if close_price is None:
            close_price = _yf_raw(info, "regularMarketPrice")

        # ── Tier 2: chart API price fallback ──────────────────────────────────
        if close_price is None:
            chart = await session.get_chart(ticker, interval="1d", range_="1d")
            close_price = chart["price"]

        # ── Next earnings date (equity only) ──────────────────────────────────
        next_earnings: str | None = None
        if iclass == "equity" and info:
            try:
                cal_events = info.get("calendarEvents") or {}
                if isinstance(cal_events, dict):
                    earnings_dates = (
                        (cal_events.get("earnings") or {}).get("earningsDate") or []
                    )
                    if earnings_dates:
                        first = earnings_dates[0]
                        # Yahoo returns {"raw": timestamp, "fmt": "YYYY-MM-DD"}
                        if isinstance(first, dict):
                            next_earnings = first.get("fmt") or str(first.get("raw", ""))
                        else:
                            next_earnings = str(first)
            except Exception:
                pass

        # ── Assemble FinancialData — gate fields by instrument class ───────────
        is_equity      = iclass == "equity"
        has_liquidity  = iclass in ("equity", "etf")
        has_market_cap = iclass in ("equity", "etf")

        return FinancialData(
            ticker=ticker,
            close_price=close_price,
            pe_ratio=_yf_raw(info, "trailingPE")      if is_equity      else None,
            forward_pe=_yf_raw(info, "forwardPE")     if is_equity      else None,
            next_earnings_date=next_earnings,
            market_cap=_yf_raw(info, "marketCap")     if has_market_cap else None,
            fifty_two_week_high=_yf_raw(info, "fiftyTwoWeekHigh")  if has_liquidity else None,
            fifty_two_week_low=_yf_raw(info, "fiftyTwoWeekLow")    if has_liquidity else None,
            avg_daily_volume=_yf_raw(info, "averageVolume")        if has_liquidity else None,
            avg_daily_volume_10d=(
                _yf_raw(info, "averageVolume10days")
                or _yf_raw(info, "averageDailyVolume10Day")
            ) if has_liquidity else None,
        )

    except Exception:
        return FinancialData(ticker=ticker)


async def verify_financial_data(tickers: List[str]) -> Dict[str, FinancialData]:
    """
    Fetch current financial data for a list of tickers.

    All tickers are dispatched concurrently via a single shared httpx.AsyncClient
    — no thread-pool executor is involved.  Each call is independent and failures
    are caught per-ticker so a single bad symbol never blocks the rest.

    Args:
        tickers: List of ticker symbols (e.g., ["XLE", "TLT", "^GSPC"]).

    Returns:
        Dict mapping each ticker to its FinancialData object.
    """
    if not tickers:
        return {}

    session = await _get_yf_session()
    results = await asyncio.gather(
        *[_fetch_single_ticker_async(t, session) for t in tickers],
        return_exceptions=True,
    )

    return {
        ticker: result if not isinstance(result, Exception) else FinancialData(ticker=ticker)
        for ticker, result in zip(tickers, results)
    }


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


# ── Economic Calendar (FMP) ────────────────────────────────────────────────────

class EconomicEvent(BaseModel):
    date_str: str           # "2026-04-01"
    time_et:  str | None    # "08:30" or None
    event:    str           # "Non Farm Payroll"
    country:  str           # "US"
    previous: str | None    # "-92K" or "-0.2%"
    forecast: str | None    # "+50K"
    impact:   str           # "high" | "medium" | "low"


def _format_eco_value(value: float | None, unit: str | None) -> str | None:
    """Format a numeric reading + unit into a human-readable string."""
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
    """Synchronous FMP economic calendar fetch via fmpsdk (run in a thread executor)."""
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key or _fmpsdk is None:
        return []

    from_date = date.today()
    to_date   = from_date + timedelta(days=days_ahead)

    try:
        raw = _fmpsdk.economic_calendar(
            apikey    = api_key,
            from_date = from_date.isoformat(),
            to_date   = to_date.isoformat(),
        )
        data: list = raw if isinstance(raw, list) else []
    except Exception:
        return []

    impact_rank = {"high": 3, "medium": 2, "low": 1}
    min_rank    = impact_rank.get(min_impact.lower(), 3)

    events: list[EconomicEvent] = []
    for item in data:
        # STRICT FILTER: US events only
        if (item.get("country") or "").upper() != "US":
            continue

        item_impact = (item.get("impact") or "").lower()
        if impact_rank.get(item_impact, 0) < min_rank:
            continue

        # FMP `date` field: "2026-04-01 08:30:00" or bare "2026-04-01"
        raw_date = item.get("date") or ""
        parts    = raw_date.split(" ")
        date_str = parts[0] if parts else ""
        time_et  = parts[1][:5] if len(parts) > 1 else None  # "08:30"

        if not date_str:
            continue

        previous_raw = item.get("previous")
        estimate_raw = item.get("estimate") or item.get("consensus") or item.get("forecast")

        events.append(EconomicEvent(
            date_str = date_str,
            time_et  = time_et,
            event    = item.get("event", ""),
            country  = "US",
            previous = str(previous_raw) if previous_raw is not None else "N/A",
            forecast = str(estimate_raw) if estimate_raw is not None else "N/A",
            impact   = item_impact,
        ))

    events.sort(key=lambda e: (e.date_str, e.time_et or "99:99"))
    return events


async def fetch_economic_calendar(
    days_ahead: int = 7,
    min_impact: str = "high",
) -> list[EconomicEvent]:
    """
    Fetch scheduled economic releases from FMP for the next `days_ahead` days.

    Requires FMP_API_KEY in the environment.  Returns an empty list (silently)
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
            "No economic calendar data available — FMP_API_KEY not set "
            "or no high-impact events found for the coming week."
        )

    lines = [
        "### SCHEDULED ECONOMIC RELEASES — Live Data (Next 7 Days)",
        "The following data is sourced verbatim from the FMP economic calendar.",
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


# ── Earnings Calendar (FMP) ────────────────────────────────────────────────────

class EarningsEntry(BaseModel):
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
    Fetch earnings from FMP for the window [today-days_behind, today+days_ahead].
    Returns (upcoming_top5, recent_top5) sorted by estimated revenue (largest first).
    """
    api_key = os.environ.get("FMP_API_KEY", "")
    if not api_key or _fmpsdk is None:
        return [], []

    from_date = date.today() - timedelta(days=days_behind)
    to_date   = date.today() + timedelta(days=days_ahead)

    try:
        raw = _fmpsdk.earning_calendar(
            apikey    = api_key,
            from_date = from_date.isoformat(),
            to_date   = to_date.isoformat(),
        )
        data: list = raw if isinstance(raw, list) else []
    except Exception:
        return [], []

    upcoming: list[EarningsEntry] = []
    recent:   list[EarningsEntry] = []

    # FMP returns a flat list; no country field on earning_calendar —
    # endpoint is already scoped to US-listed equities.
    for item in data:
        ticker = (item.get("symbol") or "").strip()
        d_str  = (item.get("date") or "").strip()
        if not ticker or not d_str:
            continue

        entry = EarningsEntry(
            date_str         = d_str,
            hour             = item.get("time"),           # FMP uses "time": "bmo"/"amc"
            ticker           = ticker,
            eps_estimate     = item.get("epsEstimated"),   # FMP field name
            eps_actual       = item.get("eps"),            # FMP field name
            revenue_estimate = item.get("revenueEstimated"),
            revenue_actual   = item.get("revenue"),
            quarter          = item.get("quarter"),
            year             = int(d_str[:4]) if d_str else None,
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

    upcoming_top5.sort(key=lambda e: e.date_str)              # chronological
    recent_top5.sort(key=lambda e: e.date_str, reverse=True)  # most recent first

    return upcoming_top5, recent_top5


async def fetch_earnings_calendar(
    days_behind: int = 2,
    days_ahead:  int = 7,
) -> tuple[list[EarningsEntry], list[EarningsEntry]]:
    """
    Async wrapper around the FMP earnings calendar fetch.
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
            "No earnings calendar data available — FMP_API_KEY not set "
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

_CLEAN_EQUITY_RE = re.compile(r"^[A-Z][A-Z0-9]{0,8}$")


async def calculate_price_correlations(
    tickers: list[str],
    financial_data_map: Dict[str, "FinancialData"],
) -> str:
    """
    Compute a 30-day daily-return correlation matrix for the given tickers
    and return it as an HTML <table> string for embedding in the briefing.

    Only clean US equity tickers (uppercase letters/digits, no ^ $ = spaces)
    are included.  Indices, futures, FX pairs, and options contract strings
    are silently dropped before fetching.

    Returns a fallback message string if fewer than 2 valid equity tickers
    can be resolved to 30-day price history.
    """
    # ── Sanitize: keep only clean equity symbols ──────────────────────────────
    def _is_clean_equity(raw: str) -> bool:
        sym = raw.split(":")[-1] if ":" in raw else raw
        return bool(_CLEAN_EQUITY_RE.match(sym))

    equity_tickers = [t for t in tickers if _is_clean_equity(t)]
    if len(equity_tickers) < 2:
        return "Insufficient valid equity tickers to generate correlation matrix."

    session = await _get_yf_session()

    async def _fetch_closes(raw_ticker: str) -> tuple[str, list[float]] | None:
        yf_sym = raw_ticker.split(":")[-1] if ":" in raw_ticker else raw_ticker
        try:
            data = await session.get_chart(yf_sym, interval="1d", range_="1mo")
            closes = [c for c in data.get("closes", []) if c is not None]
            if len(closes) < 5:
                return None
            return raw_ticker, closes
        except Exception:
            return None

    fetch_results = await asyncio.gather(
        *[_fetch_closes(t) for t in equity_tickers],
        return_exceptions=True,
    )

    price_series: dict[str, list[float]] = {}
    valid_tickers: list[str] = []
    for res in fetch_results:
        if isinstance(res, tuple) and res is not None:
            raw_ticker, closes = res
            price_series[raw_ticker] = closes
            valid_tickers.append(raw_ticker)

    if len(valid_tickers) < 2:
        return "Insufficient valid equity tickers to generate correlation matrix."

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
    labels = [t.split(":")[-1][:10] for t in valid_tickers]

    # ── Build HTML table (upper-triangular) ───────────────────────────────────
    def _corr_color(v: float) -> str:
        if v >= 0.7:   return "#9b2335"   # strong positive — high co-movement risk
        if v >= 0.3:   return "#c05621"   # moderate positive
        if v <= -0.3:  return "#276749"   # negative — diversification benefit
        return "#4a5568"                  # near-zero

    th_cells = "".join(f"<th>{lbl}</th>" for lbl in labels[1:])
    header_row = f"<tr><th></th>{th_cells}</tr>"

    body_rows: list[str] = []
    for i in range(n - 1):
        row_cells = [f"<th>{labels[i]}</th>"]
        for _ in range(i):
            row_cells.append("<td></td>")
        for j in range(i + 1, n):
            corr = pearson(returns[valid_tickers[i]], returns[valid_tickers[j]])
            color = _corr_color(corr)
            row_cells.append(
                f"<td style='color:{color};font-weight:600;text-align:center;'>{corr:+.2f}</td>"
            )
        body_rows.append(f"<tr>{''.join(row_cells)}</tr>")

    caption = (
        f"<p style='font-family:var(--font-sans);font-size:11px;color:var(--text-muted);"
        f"margin-bottom:10px;'>{min_len} trading days &mdash; {n} instruments. "
        f"Color: <span style='color:#9b2335;'>&#9632;</span> high co-movement "
        f"&nbsp;|&nbsp; <span style='color:#276749;'>&#9632;</span> diversification benefit "
        f"&nbsp;|&nbsp; <span style='color:#4a5568;'>&#9632;</span> uncorrelated</p>"
    )
    table_html = (
        f"<table class='correlation-matrix-table'>"
        f"<thead>{header_row}</thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        f"</table>"
    )
    return caption + table_html
