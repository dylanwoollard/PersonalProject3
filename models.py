"""
models.py — Pydantic data models for all structured Gemini outputs.
All models are used as response_schema in the Google GenAI SDK.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    FIXED_INCOME = "FIXED_INCOME"
    CURRENCY = "CURRENCY"
    COMMODITY = "COMMODITY"
    DERIVATIVE = "DERIVATIVE"
    ETF = "ETF"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class BookCategory(str, Enum):
    THEMATIC = "THEMATIC"
    DEFENSE = "DEFENSE"
    PERSONAL_ENRICHMENT = "PERSONAL_ENRICHMENT"


class EarningsResult(str, Enum):
    BEAT = "BEAT"
    MISS = "MISS"
    IN_LINE = "IN-LINE"


class ReadingListPriority(str, Enum):
    ESSENTIAL = "ESSENTIAL"
    RECOMMENDED = "RECOMMENDED"
    SUPPLEMENTARY = "SUPPLEMENTARY"


# ── Financial Data (Tool Output) ──────────────────────────────────────────────

class FinancialData(BaseModel):
    ticker: str
    close_price: Optional[float] = None
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    next_earnings_date: Optional[str] = None
    market_cap: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    avg_daily_volume: Optional[int] = None       # 3-month average daily volume (shares)
    avg_daily_volume_10d: Optional[int] = None   # 10-day average daily volume (shares)


# ── Trade Models ──────────────────────────────────────────────────────────────

class Trade(BaseModel):
    instrument: str = Field(
        ...,
        description=(
            "Ticker in EXCHANGE:TICKER format. ONLY use instruments listed on US exchanges: "
            "NYSE, NASDAQ, NYSEARCA, CBOE, or BATS. "
            "Examples: 'NYSE:LMT', 'NASDAQ:NVDA', 'NYSEARCA:TLT', 'CBOE:SPX'. "
            "Never use foreign exchange prefixes (e.g., EPA:, LON:, TYO:)."
        ),
    )
    company_name: str = Field(
        ...,
        description=(
            "Full name of the security as commonly known. Required for all instruments. "
            "For equities: full legal name (e.g., 'Lockheed Martin Corporation'). "
            "For ETFs: full ETF name (e.g., 'iShares 20+ Year Treasury Bond ETF'). "
            "For options/derivatives: underlying company name plus contract description "
            "(e.g., 'Kratos Defense & Security Solutions — Apr $25C')."
        ),
    )
    last_price: Optional[str] = Field(
        None, description="Last known price of the instrument from the live financial data provided (e.g., '$145.32' or '4.312%' for yields)"
    )
    avg_daily_volume: Optional[str] = Field(
        None,
        description=(
            "Average daily trading volume from the live financial data provided. "
            "Format as shares and dollar ADV on one line: e.g., '~4.2M shs · ~$2.0B ADV' for large-caps, "
            "'~840K shs · ~$162M ADV' for mid-caps, '~12M shs · ~$348M ADV' for ETFs. "
            "Compute dollar ADV as close_price × avg_daily_volume (3-month). "
            "If data is unavailable, omit this field."
        ),
    )
    asset_class: AssetClass
    direction: TradeDirection
    time_horizon: str = Field(
        ..., description="Specific time horizon (e.g., '3-5 trading days', '2-4 weeks', '3-6 months')"
    )
    rationale: str = Field(
        ...,
        description=(
            "Flowing bureaucratic narrative prose explaining the broader trade thesis: "
            "the macro or geopolitical context, why this instrument captures the thesis, "
            "and why the timing is compelling. No bullet points. No journalistic flair. "
            "3 sentences maximum."
        ),
    )
    geopolitical_catalyst: str = Field(
        ..., description="The specific geopolitical or macroeconomic event driving this trade"
    )
    risk_factors: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "Specific, named risks that could invalidate this thesis. "
            "Never generic (e.g., 'a surprise Fed pivot ahead of the June 2025 FOMC meeting', "
            "not 'monetary policy risk')."
        ),
    )
    target_price: Optional[str] = Field(
        None,
        description=(
            "Specific numeric price target calculated from live financial data and thesis magnitude. "
            "Format as a dollar string (e.g., '$187.50').  MANDATORY for strategic trades — "
            "do not leave null when generating strategic or positional trades."
        ),
    )
    entry_notes: Optional[str] = Field(None, description="Entry level observations or conditions")
    target_notes: Optional[str] = Field(None, description="Target level observations or conditions")
    stop_notes: Optional[str] = Field(None, description="Stop-loss level observations or conditions")


class StrategicThesis(BaseModel):
    """
    Phase B sub-output (split from StrategicTrade).
    Contains only the long-form macro narrative and meta fields — no instrument data.
    Runs concurrently with StrategicInstrument to halve per-call output tokens.
    """
    macro_thesis: str = Field(
        ...,
        description=(
            "Macro thesis in flowing bureaucratic prose (2-3 concise paragraphs). "
            "Must cover: (1) the geopolitical or macroeconomic foundation, "
            "(2) the market transmission mechanism and expected response, "
            "(3) timing rationale and key conditions that would invalidate the thesis."
        ),
    )
    time_horizon_months: int = Field(..., description="Time horizon in months (typically 3-12)")
    historical_precedents: List[str] = Field(
        ...,
        min_length=2,
        max_length=3,
        description=(
            "2-3 specific historical analogues with approximate dates and outcomes "
            "that support the structural thesis."
        ),
    )
    conviction_level: str = Field(
        ..., description="HIGH, MEDIUM, or LOW with a brief explanatory clause"
    )


class StrategicInstrument(BaseModel):
    """
    Phase B sub-output (split from StrategicTrade).
    Contains only the structured trade data (instrument, levels, rationale).
    Runs concurrently with StrategicThesis to halve per-call output tokens.
    """
    trade: Trade


class SingleTrade(BaseModel):
    """
    Single-trade output for the map-reduce positional/tactical generation pattern.
    Each call generates exactly one Trade, keeping the output schema small.
    """
    trade: Trade


class StrategicTrade(BaseModel):
    trade: Trade
    macro_thesis: str = Field(
        ...,
        description=(
            "Macro thesis in flowing bureaucratic prose (3-4 paragraphs, concise). "
            "Must cover: geopolitical foundation, expected policy/corporate response, "
            "market transmission mechanism, timing rationale, and key risks."
        ),
    )
    time_horizon_months: int = Field(..., description="Time horizon in months (typically 3-12)")
    historical_precedents: List[str] = Field(
        ...,
        min_length=2,
        max_length=3,
        description=(
            "2-3 specific historical analogues with approximate dates and outcomes "
            "that support the structural thesis."
        ),
    )
    conviction_level: str = Field(
        ..., description="HIGH, MEDIUM, or LOW with a brief explanatory clause"
    )


class PositionalTradeSet(BaseModel):
    trades: List[Trade] = Field(
        ...,
        min_length=2,
        max_length=2,
        description="Exactly 2 positional trades with 2-8 week horizons",
    )


class TacticalTradeSet(BaseModel):
    trades: List[Trade] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exactly 3 tactical trades with 1-10 trading day horizons",
    )


# ── Quantitative Trade Analysis ───────────────────────────────────────────────

class PriceScenario(BaseModel):
    target_price: str = Field(..., description="Target price with specific level (e.g., '$185.00')")
    probability: str = Field(
        ...,
        description=(
            "Probability as a whole-number percentage string (e.g., '45%'). "
            "RULE: bull + base + bear MUST sum to exactly 100%. "
            "RULE: probabilities MUST be unequal — never output 33/33/34 or any near-uniform split. "
            "Assign probability based on specific evidential weight for THIS trade: "
            "base case typically 40–55%, the better-supported tail 20–35%, weaker tail 10–25%. "
            "Each scenario's probability must be individually justified by distinct data points."
        ),
    )
    logic: str = Field(..., description="2-3 sentences of analytical logic for this scenario")


class TradeScenarios(BaseModel):
    bull: PriceScenario
    base: PriceScenario
    bear: PriceScenario
    expected_value: str = Field(
        ...,
        description=(
            "Probability-weighted EV using the EXACT target_price and probability values from the "
            "bull, base, and bear fields above — not example numbers. Format: "
            "'(bull_prob × bull_target) + (base_prob × base_target) + (bear_prob × bear_target) = EV vs. entry_price (±X%)'. "
            "Every trade's EV must reflect its own specific numbers."
        ),
    )


class QuantAnalysis(BaseModel):
    valuation_summary: str = Field(
        ...,
        description="Current valuation metrics: P/E, EV/EBITDA, P/B, EV/Sales as applicable, vs. sector and historical averages",
    )
    non_consensus_view: str = Field(
        ...,
        description="1-2 paragraphs: what the consensus is missing, why this thesis is differentiated, and what the market is mispricing",
    )
    scenarios: TradeScenarios
    invalidation: str = Field(
        ..., description="The specific price level or observable event that definitively invalidates this thesis"
    )
    crowding: str = Field(
        ..., description="Institutional ownership %, recent fund flows, and net institutional buying/selling trend over the past two quarters"
    )
    short_interest: str = Field(
        ..., description="Short interest as % of float, days-to-cover, and directional trend over the past 60 days"
    )
    seasonality: str = Field(
        ..., description="Relevant seasonal patterns for this instrument or sector, with specific historical win-rate data where available"
    )
    entry_strategy: str = Field(
        ..., description="Specific entry approach: price levels, timing relative to catalysts, and scaling methodology"
    )
    instruments: List[str] = Field(
        ..., description="All instruments to express this trade (equity, options strikes/expiries, ETF overlays)"
    )
    hedge_plan: str = Field(
        ...,
        description=(
            "A coherent narrative hedge plan in 2-4 sentences: which specific instruments to use "
            "(with tickers/strikes), approximate sizing relative to the core position, the "
            "mechanism by which each hedge offsets the primary risk, and the conditions "
            "under which the hedge should be added, adjusted, or removed."
        ),
    )


class TacticalQuant(BaseModel):
    is_options_trade: bool = Field(..., description="True if this trade is expressed through options")
    option_details: Optional[str] = Field(
        None,
        description="For options trades: strike, expiry, premium estimate (e.g., 'KTOS $25C 18-Apr-2025 ~$1.50 debit')",
    )
    expected_move: str = Field(..., description="Expected percentage move over the tactical horizon (e.g., '+8–12%')")
    risk_reward: str = Field(..., description="Risk/reward ratio (e.g., '3.2:1')")
    catalyst_date: Optional[str] = Field(None, description="ISO date of the specific catalyst event if applicable")



# ── Opening Sections Models ───────────────────────────────────────────────────

class RedCellScenario(BaseModel):
    title: str
    probability_assessment: str = Field(
        ...,
        description="Probability range (e.g., 'Low (5-10%)') — never a single point estimate",
    )
    narrative: str = Field(
        ...,
        description=(
            "Sober analytical narrative of the scenario in bureaucratic prose. "
            "Must be derivable from today's intelligence, not speculative fiction."
        ),
    )
    trigger_conditions: List[str] = Field(
        ..., min_length=2, description="Specific observable conditions that would actualize this scenario"
    )
    market_implications: str = Field(
        ..., description="Expected market consequences with specific instruments named"
    )


class CalendarEvent(BaseModel):
    date_str: str = Field(
        ..., description="ISO date (YYYY-MM-DD) — copy exactly from the economic calendar data provided"
    )
    time_et: Optional[str] = Field(
        None, description="Release time in ET, HH:MM format (e.g. '08:30') — copy exactly from the data; null if not provided"
    )
    event: str = Field(
        ..., description="Report or event name — copy exactly from the economic calendar data provided"
    )
    entity: str = Field(
        ..., description="Releasing authority (e.g. 'Bureau of Labor Statistics', 'Federal Reserve', 'European Central Bank')"
    )
    previous: Optional[str] = Field(
        None, description="Previous reading with unit — copy exactly from the data (e.g. '-92K', '-0.2%'); null if not provided"
    )
    forecast: Optional[str] = Field(
        None, description="Consensus forecast with unit — copy exactly from the data; null if not provided"
    )
    market_relevance: str = Field(
        ...,
        description=(
            "Analytical assessment: which specific instruments are sensitive to this release, "
            "what the consensus expects, and how a surprise in either direction would affect markets"
        ),
    )


class UpcomingEarnings(BaseModel):
    date_str: str = Field(..., description="ISO date — copy exactly from earnings data")
    time_of_day: Optional[str] = Field(
        None,
        description=(
            "'Before Market Open', 'After Market Close', or 'During Market Hours' — "
            "copy from data; null if unspecified"
        ),
    )
    ticker: str = Field(..., description="Ticker symbol — copy exactly from earnings data")
    company: str = Field(..., description="Full company name from your knowledge of the ticker")
    eps_estimate: Optional[str] = Field(
        None, description="Consensus EPS estimate — copy exactly from data (e.g. '$1.48')"
    )
    revenue_estimate: Optional[str] = Field(
        None, description="Consensus revenue estimate — copy exactly from data (e.g. '$95.2B')"
    )
    theme_relevance: str = Field(
        ...,
        description=(
            "Why this company was selected: specifically how it connects to today's "
            "priority intelligence themes, and the market implications of a beat or miss "
            "given the current macro and geopolitical context"
        ),
    )
    watch_analysis: str = Field(
        ...,
        description=(
            "What the market will focus on: the 2-3 most critical metrics, guidance "
            "language, margin or mix dynamics, and how a beat or miss affects the stock "
            "and its sector peers"
        ),
    )


class RecentEarnings(BaseModel):
    date_str: str = Field(..., description="ISO date of the release — copy exactly from data")
    ticker: str = Field(..., description="Ticker symbol — copy exactly from data")
    company: str = Field(..., description="Full company name")
    company_description: str = Field(
        ...,
        description=(
            "1-2 sentences describing what the company does: its primary business, "
            "key end markets, and where it sits in its industry value chain. "
            "Write for a reader who may be unfamiliar with the name."
        ),
    )
    result: EarningsResult = Field(
        ..., description="BEAT, MISS, or IN-LINE — based on EPS vs. estimate"
    )
    eps_actual: Optional[str] = Field(None, description="Reported EPS — copy exactly (e.g. '$5.16')")
    eps_estimate: Optional[str] = Field(None, description="Consensus EPS estimate — copy exactly")
    eps_surprise: Optional[str] = Field(
        None, description="EPS surprise string — copy exactly from data (e.g. '+$0.29 beat (+5.9%)')"
    )
    revenue_actual: Optional[str] = Field(None, description="Reported revenue — copy exactly (e.g. '$44.1B')")
    revenue_estimate: Optional[str] = Field(None, description="Revenue estimate — copy exactly")
    revenue_surprise: Optional[str] = Field(
        None, description="Revenue surprise string — copy exactly from data (e.g. '+$0.8B beat (+1.8%)')"
    )
    analysis: str = Field(
        ...,
        description=(
            "Analytical assessment: what the results mean for the company's forward thesis, "
            "key guidance language, sector read-through effects, and market reaction context"
        ),
    )


class OpeningNarrative(BaseModel):
    """
    Phase B sub-output (split from OpeningSections).
    Contains only the epigraph — red cell scenarios were removed in favour of
    centralising all contrarian analysis in AdversarialAssessment.
    Does not require calendar data — runs on raw intelligence + historical context.
    """
    epigraph: str = Field(
        ...,
        description=(
            "A brief, relevant quotation from a statesman, strategist, economist, or philosopher "
            "whose wisdom is directly applicable to the dominant theme in today's intelligence. "
            "Tone must be measured and analytical — not dramatic."
        ),
    )
    epigraph_attribution: str = Field(
        ..., description="Full name, role/title, and year of the quotation source"
    )


class OpeningCalendar(BaseModel):
    """
    Phase B sub-output (split from OpeningSections).
    Contains the economic and earnings calendar sections.
    Requires live calendar data feeds.
    """
    calendar_events: List[CalendarEvent] = Field(
        ...,
        max_length=6,
        description=(
            "Up to 6 most significant scheduled economic releases from the live calendar data provided. "
            "Copy date_str, time_et, event, previous, and forecast verbatim from that data. "
            "Generate entity and market_relevance fields."
        ),
    )
    upcoming_earnings: List[UpcomingEarnings] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Top 3 most market-significant upcoming earnings from the data provided. "
            "Select by estimated revenue and relevance to today's themes. "
            "Copy factual fields verbatim. Generate company and watch_analysis."
        ),
    )
    recent_earnings: List[RecentEarnings] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Most significant earnings reports from the past 48 hours, from the data provided. "
            "Copy factual fields verbatim. Generate company and analysis. "
            "Return an empty list if no recent earnings data is available."
        ),
    )


class OpeningSections(BaseModel):
    epigraph: str = Field(
        ...,
        description=(
            "A brief, relevant quotation from a statesman, strategist, economist, or philosopher "
            "whose wisdom is directly applicable to the dominant theme in today's intelligence. "
            "Tone must be measured and analytical — not dramatic."
        ),
    )
    epigraph_attribution: str = Field(
        ..., description="Full name, role/title, and year of the quotation source"
    )
    calendar_events: List[CalendarEvent] = Field(
        ...,
        max_length=6,
        description=(
            "Up to 6 most significant scheduled economic releases from the live calendar data provided. "
            "Copy date_str, time_et, event, previous, and forecast verbatim from that data. "
            "Generate entity and market_relevance fields."
        ),
    )
    upcoming_earnings: List[UpcomingEarnings] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Top 3 most market-significant upcoming earnings from the data provided. "
            "Select by estimated revenue and relevance to today's themes. "
            "Copy factual fields verbatim. Generate company and watch_analysis."
        ),
    )
    recent_earnings: List[RecentEarnings] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Most significant earnings reports from the past 48 hours, from the data provided. "
            "Copy factual fields verbatim. Generate company and analysis. "
            "Return an empty list if no recent earnings data is available."
        ),
    )


# ── Priority Themes ───────────────────────────────────────────────────────────

class UnifiedIntelligenceTheme(BaseModel):
    """
    Unified theme model replacing separate PriorityTheme + TransmissionOverlay.
    A single LLM call produces the full analytical picture per theme: signal/mechanism,
    market impact, and risk transmission through second/third-order effects.
    """
    rank: int = Field(..., ge=1, le=3, description="Priority rank: 1 = highest significance")
    title: str
    narrative: str = Field(
        ...,
        description=(
            "Flowing bureaucratic narrative prose with mandatory causal analysis. "
            "Do not summarize — analyze. Contextualize within the broader geopolitical "
            "and macroeconomic framework."
        ),
    )
    key_data_points: List[str] = Field(
        ...,
        min_length=3,
        max_length=4,
        description=(
            "3-4 verbatim or near-verbatim specific data points from the intelligence. "
            "Preserve specific figures, names, dates, and designations exactly. "
            "These will be rendered as bullet points."
        ),
    )
    signal: str = Field(..., description="The observable intelligence signal")
    mechanism: str = Field(..., description="The causal pathway from signal to market effect")
    market_impact: str = Field(
        ...,
        description=(
            "Expected financial consequence with specific instruments, sectors, "
            "and directional views named."
        ),
    )
    affected_instruments: List[str] = Field(
        ...,
        min_length=2,
        description=(
            "Specific tickers, currency pairs, commodity contracts, or index names affected. "
            "Never generic category labels."
        ),
    )
    entities_involved: List[str] = Field(
        ..., description="Named persons, organizations, and state actors driving this theme"
    )
    geographies: List[str] = Field(
        ..., description="Named geographic locations involved in this theme"
    )
    primary_channel: str = Field(
        ...,
        description=(
            "Primary risk transmission channel "
            "(e.g., 'Sovereign Credit Channel', 'Commodity Input Cost Channel', "
            "'Currency Redenomination Risk', 'Supply Chain Disruption')"
        ),
    )
    affected_sectors: List[str] = Field(
        ..., min_length=3, description="Specific sectors by GICS classification or common industry name"
    )
    risk_level: RiskLevel
    transmission_narrative: str = Field(
        ...,
        description=(
            "Flowing bureaucratic narrative describing how the risk propagates through "
            "financial markets. Name specific mechanisms and channels."
        ),
    )
    second_order_effects: List[str] = Field(
        ...,
        min_length=2,
        description=(
            "Direct consequences of the primary transmission. "
            "Name specific sectors, instruments, and geographic markets."
        ),
    )
    third_order_effects: List[str] = Field(
        ...,
        min_length=2,
        description=(
            "Subsequent systemic or contagion effects following second-order impacts. "
            "Name specific sectors, instruments, and geographic markets."
        ),
    )


class PriorityThemes(BaseModel):
    themes: List[UnifiedIntelligenceTheme] = Field(
        ..., min_length=3, max_length=3, description="Exactly 3 unified intelligence themes, ranked 1-3"
    )

    @field_validator("themes")
    @classmethod
    def unique_ranks(cls, v: list) -> list:
        ranks = [t.rank for t in v]
        if sorted(ranks) != [1, 2, 3]:
            raise ValueError(f"themes must have ranks [1, 2, 3], got {ranks}")
        return v


# ── Appendix ──────────────────────────────────────────────────────────────────

class ReadingListItem(BaseModel):
    title: str
    source: str = Field(..., description="Publishing organization (e.g., 'Federal Reserve Board', 'IMF')")
    author_or_publication: str
    relevance: str = Field(
        ...,
        description=(
            "Why this specific publication is relevant to today's specific intelligence themes. "
            "Reference the theme or situation by name."
        ),
    )
    priority: ReadingListPriority = Field(
        ..., description="ESSENTIAL, RECOMMENDED, or SUPPLEMENTARY"
    )
    url: Optional[str] = Field(None, description="Direct URL to the publication, report, or official source when available")


class BookRecommendation(BaseModel):
    title: str
    author: str
    category: BookCategory
    synopsis: str = Field(
        ...,
        description=(
            "Exactly 2 sentences: the book's core argument or subject matter, "
            "and its principal contribution or what distinguishes it."
        ),
    )
    briefing_connection: str = Field(
        ...,
        description=(
            "Exactly 1 sentence on how this specific book connects to today's "
            "intelligence themes or briefing context — reference the theme by name."
        ),
    )


class AppendixDatabase(BaseModel):
    """
    Phase A output: entity/situation index and archival summary.
    Small schema — runs first and drives historical context retrieval.
    """
    key_entities: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "All named persons, organizations, state actors, international bodies, "
            "and financial institutions in today's intelligence."
        ),
    )
    key_situations: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "All distinct geopolitical situations, macroeconomic developments, and market events "
            "discussed today. Used for longitudinal database indexing."
        ),
    )
    intelligence_summary: str = Field(
        ...,
        description=(
            "2-3 paragraph bureaucratic prose summary of the total intelligence picture "
            "for today, suitable for database archiving."
        ),
    )


class AppendixReadingList(BaseModel):
    """
    Phase B output: curated reading list and book recommendations.
    Runs concurrently with trade generation — does not block Phase A.
    """
    reading_list: List[ReadingListItem] = Field(
        ...,
        min_length=4,
        max_length=8,
        description=(
            "4-8 specific publications, reports, or data releases. "
            "Prioritize primary sources (central bank statements, government releases, "
            "multilateral institution reports) over secondary commentary."
        ),
    )
    book_recommendations: List[BookRecommendation] = Field(
        ...,
        min_length=5,
        max_length=5,
        description=(
            "Exactly 5 books: 3 THEMATIC (directly related to today's dominant intelligence "
            "themes), 1 DEFENSE (military strategy, security affairs, intelligence tradecraft, "
            "or defense policy), 1 PERSONAL_ENRICHMENT (biography, history, philosophy, or "
            "literature chosen for intellectual depth)."
        ),
    )

    @field_validator("book_recommendations")
    @classmethod
    def valid_book_distribution(cls, v: list) -> list:
        from collections import Counter
        counts = Counter(b.category for b in v)
        if counts.get(BookCategory.THEMATIC, 0) != 3:
            raise ValueError(f"book_recommendations must contain exactly 3 THEMATIC books, got {counts.get(BookCategory.THEMATIC, 0)}")
        if counts.get(BookCategory.DEFENSE, 0) != 1:
            raise ValueError(f"book_recommendations must contain exactly 1 DEFENSE book, got {counts.get(BookCategory.DEFENSE, 0)}")
        if counts.get(BookCategory.PERSONAL_ENRICHMENT, 0) != 1:
            raise ValueError(f"book_recommendations must contain exactly 1 PERSONAL_ENRICHMENT book, got {counts.get(BookCategory.PERSONAL_ENRICHMENT, 0)}")
        return v


class AppendixOutput(BaseModel):
    """
    Combined appendix — assembled from AppendixDatabase + AppendixReadingList
    after both pipeline phases complete.  Used by compiler and storage.
    """
    reading_list: List[ReadingListItem] = Field(
        ...,
        min_length=4,
        max_length=8,
    )
    book_recommendations: List[BookRecommendation] = Field(
        ...,
        min_length=5,
        max_length=5,
    )
    key_entities: List[str] = Field(..., min_length=1)
    key_situations: List[str] = Field(..., min_length=1)
    intelligence_summary: str


# ── Adversarial Assessment ─────────────────────────────────────────────────────

class AdversarialScenario(BaseModel):
    title: str
    consensus_view: str = Field(
        ..., description="The prevailing consensus or analytical assumption this scenario challenges"
    )
    blind_spot: str = Field(
        ..., description="The specific analytical gap, cognitive bias, or missing data point the consensus is making"
    )
    adversarial_case: str = Field(
        ...,
        description=(
            "The contrarian case in flowing bureaucratic prose: how this theme could develop "
            "in a way that invalidates the consensus, and why the market is underpricing this risk."
        ),
    )
    trigger_conditions: List[str] = Field(
        ..., min_length=2, description="Observable signals in the near-term that would confirm this adversarial scenario"
    )
    market_implications: str = Field(
        ..., description="Expected market consequences if adversarial case materializes — name specific instruments and directions"
    )


class AdversarialAssessment(BaseModel):
    """
    Adversarial review of the priority themes — generated at higher temperature
    to surface analytical blind spots and contrarian risks.
    """
    scenarios: List[AdversarialScenario] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exactly 3 adversarial scenarios — one per priority theme, in rank order",
    )
    meta_risk: str = Field(
        ...,
        description=(
            "The overarching systemic blind spot or correlation risk across all three priority themes: "
            "the scenario that would cause all three themes to simultaneously move against the consensus "
            "in ways that current analysis has not priced."
        ),
    )


# ── Trade Logic Review ────────────────────────────────────────────────────────

class TradeLogicReview(BaseModel):
    """
    Logical fallacy and analytical gap review for the strategic trade.
    Generated post-hoc from the completed trade + quant analysis.
    """
    fallacies_identified: List[str] = Field(
        ...,
        description=(
            "Named logical fallacies present in the trade rationale or quant analysis "
            "(e.g., 'Narrative Fallacy — post-hoc causal story fitted to recent data', "
            "'Anchoring Bias — price target anchored to recent high'). "
            "Empty list if no significant fallacies found."
        ),
    )
    analytical_gaps: List[str] = Field(
        ...,
        description=(
            "Specific pieces of analysis absent from the trade that would be required "
            "for a rigorous assessment. Name the missing analysis precisely."
        ),
    )
    steelman: str = Field(
        ...,
        description=(
            "The strongest possible case against the trade in 2-3 sentences of bureaucratic prose. "
            "Best version of the counter-argument, not a strawman."
        ),
    )
    verdict: str = Field(
        ...,
        description=(
            "Overall verdict on the logical integrity of the trade in 1-2 sentences. "
            "Is the causal chain sound? Are the probabilities well-calibrated?"
        ),
    )
    conviction_adjustment: str = Field(
        ...,
        description=(
            "Recommended adjustment to conviction level: 'Maintain', 'Downgrade', or 'Upgrade', "
            "with a brief explanatory clause."
        ),
    )
