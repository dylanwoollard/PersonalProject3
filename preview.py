"""
preview.py — Instant HTML formatting preview (no API calls).

Usage:
  python preview.py

Builds mock data covering every section of the briefing layout and opens
the compiled HTML in your default browser.  Edit compiler.py and re-run
to iterate on formatting.
"""

import argparse
import asyncio
import webbrowser
from datetime import date
from pathlib import Path

from models import (
    AppendixOutput,
    AssetClass,
    BookCategory,
    BookRecommendation,
    CalendarEvent,
    EarningsResult,
    OpeningSections,
    PositionalTradeSet,
    PriorityTheme,
    PriorityThemes,
    QuantAnalysis,
    PriceScenario,
    ReadingListPriority,
    RecentEarnings,
    TradeScenarios,
    TacticalQuant,
    TacticalTradeSet,
    Trade,
    TradeDirection,
    ReadingListItem,
    RedCellScenario,
    RiskLevel,
    StrategicTrade,
    TransmissionOverlay,
    TransmissionOverlays,
    UpcomingEarnings,
)
from tools import MarketTick
from compiler import compile_html_document

# ── Mock market snapshot ───────────────────────────────────────────────────────

MOCK_SNAPSHOT = [
    MarketTick(label="WTI CRUDE",  ticker="CL=F",      price=82.14,  change_pct=-0.73),
    MarketTick(label="10Y YIELD",  ticker="^TNX",       price=4.312,  change_pct=+0.04),
    MarketTick(label="DXY INDEX",  ticker="DX-Y.NYB",  price=104.37, change_pct=+0.21),
    MarketTick(label="S&P 500",    ticker="^GSPC",      price=5218.19, change_pct=+0.38),
]

# ── Mock opening sections ──────────────────────────────────────────────────────

MOCK_OPENING = OpeningSections(
    epigraph=(
        "The art of war is of vital importance to the state. It is a matter of life and death, "
        "a road either to safety or to ruin."
    ),
    epigraph_attribution="Sun Tzu, The Art of War, c. 500 BC",
    upcoming_earnings=[
        UpcomingEarnings(
            date_str="2026-04-02",
            time_of_day="After Market Close",
            ticker="NVDA",
            company="NVIDIA Corporation",
            eps_estimate="$5.72",
            revenue_estimate="$43.1B",
            theme_relevance=(
                "Directly connected to the AI infrastructure buildout theme: NVIDIA's data center "
                "revenue trajectory is the primary observable for validating or invalidating the "
                "semiconductor capex cycle. A guidance miss would compress valuations across the "
                "AI supply chain and pressure the strategic LONG thesis on semiconductor equipment."
            ),
            watch_analysis=(
                "Data center revenue vs. $40.8B consensus, Blackwell ramp execution, and gross "
                "margin guidance (consensus 73-75%). A sequential data center decline would "
                "pressure SMCI, AMAT, and ASML in sympathy; upside surprise extends the AI trade."
            ),
        ),
        UpcomingEarnings(
            date_str="2026-04-14",
            time_of_day="Before Market Open",
            ticker="JPM",
            company="JPMorgan Chase & Co.",
            eps_estimate="$4.38",
            revenue_estimate="$44.1B",
            theme_relevance=(
                "Serves as the primary read-through on credit quality and NII sensitivity under "
                "the current rate path. JPMorgan's commercial real estate commentary and "
                "reserve-build trajectory will directly inform the macro credit stress theme "
                "and potential position adjustments in financials."
            ),
            watch_analysis=(
                "Full-year NII guidance vs. $91B consensus, FICC trading revenue, and CRE "
                "loan loss reserve commentary. A guidance cut on NII would pressure KRE and XLF; "
                "strong trading revenue would benefit GS and MS in sympathy."
            ),
        ),
        UpcomingEarnings(
            date_str="2026-04-23",
            time_of_day="After Market Close",
            ticker="GOOGL",
            company="Alphabet Inc.",
            eps_estimate="$2.01",
            revenue_estimate="$89.3B",
            theme_relevance=(
                "The AI monetization vs. disruption question is unresolved in this briefing's "
                "tech sector themes. Alphabet's Search revenue trajectory tests whether AI "
                "Overviews cannibalize core ad revenue — the answer determines sector rotation "
                "direction between established tech and AI-native challengers."
            ),
            watch_analysis=(
                "Search revenue growth vs. AI Overviews cannibalization risk, GCP growth rate "
                "vs. AWS/Azure, and capex commentary for AI infrastructure. A Search miss "
                "reopens the AI disruption discount thesis and would pressure META in sympathy."
            ),
        ),
    ],
    recent_earnings=[
        RecentEarnings(
            date_str="2026-03-22",
            ticker="FDX",
            company="FedEx Corporation",
            company_description=(
                "FedEx is a global logistics company offering express, ground, and freight "
                "services across 220+ countries, primarily serving time-definite B2B and e-commerce shipments."
            ),
            result=EarningsResult.MISS,
            eps_actual="$3.51",
            eps_estimate="$3.88",
            eps_surprise="-$0.37 miss (-9.5%)",
            revenue_actual="$21.6B",
            revenue_estimate="$22.1B",
            revenue_surprise="-$0.5B miss (-2.3%)",
            analysis=(
                "Volume shortfall reflects B2B shipment density deterioration consistent with the February ISM PMI contraction. "
                "Management cut full-year guidance 8% on persistent industrial demand softness and ground segment yield compression. "
                "Read-through is negative for UPS and XPO. The miss implies consumer spending resilience in Q4 GDP estimates may be overstated — "
                "FDX volume leads goods consumption by 4-6 weeks."
            ),
        ),
        RecentEarnings(
            date_str="2026-03-23",
            ticker="NKE",
            company="Nike, Inc.",
            company_description=(
                "Nike designs and sells athletic footwear, apparel, and equipment under the Nike, Jordan, "
                "and Converse brands via direct-to-consumer and wholesale channels in 170+ countries."
            ),
            result=EarningsResult.BEAT,
            eps_actual="$0.54",
            eps_estimate="$0.29",
            eps_surprise="+$0.25 beat (+86.2%)",
            revenue_actual="$11.3B",
            revenue_estimate="$11.0B",
            revenue_surprise="+$0.3B beat (+2.7%)",
            analysis=(
                "Beat driven by DTC channel mix shift and inventory liquidation — gross margin +150bp to 44.5%. "
                "Greater China revenues +7% YoY is the most market-relevant data point against consensus weakness expectations. "
                "Results reduce downside risk for LULU and On Holdings. However, futures orders -3% globally suggest "
                "Q3 revenue visibility remains limited and the beat may not sustain multiple expansion."
            ),
        ),
    ],
    calendar_events=[
        CalendarEvent(
            date_str="2026-04-01",
            time_et="08:30",
            event="Retail Sales MoM",
            entity="U.S. Census Bureau",
            previous="-0.2%",
            forecast="+0.3%",
            market_relevance=(
                "XLY and USD pairs (EUR/USD, USD/JPY) are primary movers. A beat reinforces Fed higher-for-longer, "
                "compressing TLT and lifting financials. A miss — after the weak February print — accelerates "
                "Q3 rate-cut pricing, supporting duration and pressuring the dollar."
            ),
        ),
        CalendarEvent(
            date_str="2026-04-03",
            time_et="08:30",
            event="Non-Farm Payrolls",
            entity="Bureau of Labor Statistics",
            previous="-92K",
            forecast="+50K",
            market_relevance=(
                "Most market-sensitive release of the week. Recovery toward +50K reduces recession pricing. "
                "A second negative print re-prices Fed funds to 3+ cuts, bids TLT, pressures USD/JPY. "
                "Large beat reads as stagflation risk — asymmetric equity impact."
            ),
        ),
        CalendarEvent(
            date_str="2026-04-10",
            time_et="13:45",
            event="ECB Rate Decision",
            entity="European Central Bank",
            previous="+2.50%",
            forecast="+2.25%",
            market_relevance=(
                "EUR/USD, Bund futures, and European bank equities are primary instruments. "
                "25bp cut is consensus; forward guidance pace is the variable. "
                "Hawkish hold lifts EUR/USD, compresses EWG. Accelerated cut path pressures euro, supports exporters."
            ),
        ),
    ],
    red_cell_scenarios=[
        RedCellScenario(
            title="TSMC Operational Disruption — Cross-Strait Kinetic Event",
            probability_assessment="Low (3–7%)",
            narrative=(
                "A PLAAF or PLAN exercise escalation resulting in even a temporary disruption to TSMC's "
                "Hsinchu or Tainan fab operations would trigger an immediate repricing of the global "
                "technology supply chain. Insurance markets would suspend coverage; shipping lanes would "
                "face force majeure declarations within hours of any confirmed kinetic exchange. "
                "The 2022 Ukraine invasion commodity shock provides an imperfect but structurally "
                "analogous market reaction template."
            ),
            trigger_conditions=[
                "PLAAF incursion crossing Taiwan's 12nm territorial airspace",
                "PLAN live-fire exercise announced within 50nm of Hsinchu",
                "Emergency evacuation order issued for any TSMC fab facility",
            ],
            market_implications=(
                "Nasdaq -15–25% in first session; NASDAQ:TSM ADR halted; semiconductor ETF (NASDAQ:SOXX) "
                "circuit breaker probable. Safe-haven flows into US Treasuries, JPY, CHF. WTI crude "
                "+$8–12 on Strait shipping insurance suspension."
            ),
        ),
        RedCellScenario(
            title="Abrupt ECB Communication Failure — Spread Contagion",
            probability_assessment="Low-Medium (8–14%)",
            narrative=(
                "An ambiguous post-meeting press conference — or a leaked dissent count of ≥5 hawkish votes — "
                "could trigger rapid BTP spread widening analogous to the June 2022 fragmentation episode. "
                "Transmission runs through leveraged Italian bank balance sheets where BTP holdings represent "
                "~8.2% of total assets; a 50bp spread overshoot activates regulatory capital ratio monitoring."
            ),
            trigger_conditions=[
                "BTP-Bund spread exceeds 175bps intraday post-ECB decision",
                "Leaked Governing Council vote showing ≥5 hawkish dissents",
                "Moody's negative outlook alert on Italian sovereign debt",
            ],
            market_implications=(
                "EUR/USD break below 1.0550; Italian bank CDS (EPA:ISP, EPA:UCG) widening 40–60bps; "
                "ECB emergency TPI deployment probable within 48 hours, stabilizing BTP at cost of "
                "credibility damage to the easing cycle narrative."
            ),
        ),
    ],
)

# ── Mock priority themes ───────────────────────────────────────────────────────

MOCK_THEMES = PriorityThemes(themes=[
    PriorityTheme(
        rank=1,
        title="US-China Semiconductor Decoupling Accelerates",
        narrative=(
            "The March 2026 BIS Entity List expansion represents the most consequential tightening "
            "of export controls since the October 2023 rules. The affected firms—primarily EDA "
            "software vendors and advanced packaging service providers—represent chokepoints in "
            "China's domestic substitution strategy that cannot be circumvented on a sub-five-year "
            "timeline. The market pricing of this risk remains materially insufficient; consensus "
            "sell-side models do not adequately weight the second-order capex reduction that "
            "Chinese hyperscalers will be forced to implement as their domestic GPU roadmaps slip "
            "further behind the frontier. (Commerce Dept., Bloomberg)"
        ),
        key_data_points=[
            "37 new Chinese entities added to BIS Entity List, 14 March 2026 (Commerce Dept.)",
            "Gallium spot +18% MoM to $312/kg post-export license suspensions (Bloomberg)",
            "SMIC HiSilicon 7nm yield confirmed below 55% by MIIT sources (SCMP)",
            "TSMC N3 capacity 94% booked through Q3 2026; no Chinese customer allocation (TSMC)",
        ],
        signal="BIS Entity List expansion targeting EDA and advanced packaging",
        mechanism=(
            "Chinese IC design houses lose access to Synopsys/Cadence EDA suites, "
            "forcing multi-year re-platforming that extends the frontier gap from "
            "2 generations to 3–4 generations by 2028."
        ),
        market_impact=(
            "Long NASDAQ:AMAT, NASDAQ:LRCX (domestic toolmakers gaining share); "
            "Short Chinese ADR semiconductor basket; long NASDAQ:ARM (architecture licensing "
            "revenues insulated from controls)."
        ),
        affected_instruments=["NASDAQ:NVDA", "NASDAQ:AMAT", "NASDAQ:LRCX", "NASDAQ:TSM", "NASDAQ:SOXX"],
        entities_involved=["Bureau of Industry and Security", "Nvidia", "TSMC", "SMIC", "Synopsys", "Cadence"],
        geographies=["United States", "China", "Taiwan", "South Korea"],
    ),
    PriorityTheme(
        rank=2,
        title="ECB April Decision — Fragmentation Risk Premium Returns",
        narrative=(
            "The re-emergence of BTP-Bund spread pressure—14bps in two sessions—reflects "
            "market concern that the ECB's forward guidance credibility is being damaged by "
            "visible internal divisions ahead of the April decision. The proximate catalyst "
            "is Italian fiscal slippage: the Meloni government's Q1 2026 deficit tracking "
            "at 3.8% of GDP versus a 3.0% target creates a structural narrative that "
            "peripheral spreads will widen faster than the ECB's TPI can contain without "
            "an explicit conditionality framework. The June 2022 Fragmentation Crisis "
            "precedent is directly relevant. (FT, Bloomberg, ECB)"
        ),
        key_data_points=[
            "BTP-Bund 10Y spread: 138bps, up 14bps in two sessions as of 20 March (Bloomberg)",
            "ECB March PMI Composite: 49.3 vs. 50.1 consensus (S&P Global)",
            "Italian Q1 2026 deficit tracking: 3.8% GDP vs. 3.0% target (Italian MEF)",
            "Lagarde omitted 'sufficiently restrictive' from 17 March speech for first time (ECB)",
        ],
        signal="BTP-Bund spread widening concurrent with hawkish/dovish split in ECB communications",
        mechanism=(
            "Fiscal slippage in Italy forces markets to price in higher TPI activation "
            "threshold, depressing peripheral bond demand and widening spreads faster than "
            "the easing cycle can offset."
        ),
        market_impact=(
            "Long EUR rates volatility (SRVIX); short Italian bank equities (EPA:ISP); "
            "long USD/EUR; consider BTP/Bund spread widener via futures."
        ),
        affected_instruments=["NYSE:TLT", "EPA:ISP", "EPA:UCG", "EPA:BNP", "NYSE:EWI"],
        entities_involved=["ECB", "Christine Lagarde", "Bundesbank", "Italian MEF", "Giorgia Meloni"],
        geographies=["Eurozone", "Italy", "Germany", "France"],
    ),
    PriorityTheme(
        rank=3,
        title="PLAAF Cross-Strait Activity Intensifies",
        narrative=(
            "Three separate PLAAF median-line crossings in March 2026—combined with the "
            "activation of DF-17 hypersonic missile units in the Fujian Military District—"
            "represent the most sustained operational pressure on Taiwan's air defense "
            "identification zone since the August 2022 Pelosi-visit exercises. The timing, "
            "coinciding with the US-Taiwan Security Dialogue and a scheduled INDOPACOM "
            "readiness review, suggests coordinated signaling rather than accidental "
            "escalation. Defense prime contractors with Taiwan-adjacent exposure are "
            "pricing in elevated risk premium, but options implied volatility on "
            "defense ETFs remains below the 2022 peak. (DoD, Reuters, SCMP)"
        ),
        key_data_points=[
            "3 PLAAF median-line crossings in March 2026, highest monthly count since Aug 2022 (SCMP)",
            "DF-17 hypersonic units activated in Fujian Military District (DoD intelligence assessment)",
            "INDOPACOM Readiness Review convened 18 March; classified outcome (Pentagon)",
            "Taiwan defense budget FY2026 raised 12.5% to NT$647bn — record peacetime level (MOFA Taiwan)",
        ],
        signal="Simultaneous PLAAF incursions and DF-17 activation in Fujian",
        mechanism=(
            "Elevated kinetic risk premium flows into defense prime equities and "
            "Taiwan-exposed tech names; insurance market re-pricing of shipping "
            "and semiconductor supply-chain disruption risk."
        ),
        market_impact=(
            "Long NASDAQ:KTOS, NYSE:LMT, NYSE:RTX (defense prime beneficiaries); "
            "reduce NASDAQ:TSM, NASDAQ:AMAT Taiwan-fab exposure; long VIX calls as tail hedge."
        ),
        affected_instruments=["NASDAQ:KTOS", "NYSE:LMT", "NYSE:RTX", "NASDAQ:TSM", "NASDAQ:EWT"],
        entities_involved=["PLAAF", "PLAN", "INDOPACOM", "Taiwan MND", "Lockheed Martin", "Raytheon"],
        geographies=["Taiwan", "China", "Fujian Military District", "Indo-Pacific"],
    ),
])

# ── Mock transmission overlays ────────────────────────────────────────────────

MOCK_OVERLAYS = TransmissionOverlays(overlays=[
    TransmissionOverlay(
        theme_title="US-China Semiconductor Decoupling Accelerates",
        primary_channel="Technology Supply Chain Disruption Channel",
        affected_sectors=["Information Technology", "Consumer Electronics", "Defense & Aerospace", "Telecommunications"],
        risk_level=RiskLevel.HIGH,
        transmission_narrative=(
            "Entity List additions force immediate contract termination for US EDA software licenses, "
            "propagating through Chinese IC design ecosystems via a 90–180 day inventory drawdown "
            "before substitution timelines become acute. The mechanism compounds because advanced "
            "packaging capacity—CoWoS, SoIC—is geographically concentrated in Taiwan, creating "
            "a single-point-of-failure in the global AI accelerator supply chain that cannot be "
            "onshored within the current CHIPS Act timeline."
        ),
        second_order_effects=[
            "Chinese hyperscaler capex guidance reductions of 15–25% for FY2027 AI infrastructure (Bloomberg est.)",
            "Domestic SMIC fab utilization drops below 70% as advanced-node orders redirect to TSMC",
            "US defense contractor lead times for radiation-hardened ICs extend 6–9 months",
        ],
        third_order_effects=[
            "Sovereign wealth fund reallocation away from Chinese technology ADRs toward US defense primes",
            "Taiwan insurance re-pricing cascades into shipping and logistics sector credit spreads",
            "Fed forced to weigh semiconductor-driven inflation persistence in H2 2026 policy path",
        ],
        affected_instruments=["NASDAQ:NVDA", "NASDAQ:AMAT", "NASDAQ:LRCX", "NASDAQ:TSM", "NASDAQ:SOXX", "NYSE:RTX"],
    ),
    TransmissionOverlay(
        theme_title="ECB April Decision — Fragmentation Risk Premium Returns",
        primary_channel="Sovereign Credit Spread Channel",
        affected_sectors=["Financials", "Real Estate", "Utilities", "Consumer Discretionary"],
        risk_level=RiskLevel.MEDIUM,
        transmission_narrative=(
            "BTP-Bund spread widening transmits into Italian bank balance sheets via mark-to-market "
            "losses on sovereign holdings, tightening lending conditions for SMEs in the Italian "
            "and Spanish industrial base. The second-order effect runs through the ECB's "
            "collateral framework: as BTP haircuts widen, Italian bank liquidity buffers compress, "
            "forcing deleveraging that amplifies spread pressure in a self-reinforcing loop "
            "absent TPI activation."
        ),
        second_order_effects=[
            "Italian SME credit spreads widen 40–60bps as bank loan officers tighten standards",
            "EUR/USD tests 1.0600 support as ECB credibility premium is discounted",
            "European real estate investment trusts (EPA:URW) face higher discount rates on refinancing",
        ],
        third_order_effects=[
            "Contagion to Spanish Bonos and Portuguese OTs if BTP spread exceeds 160bps",
            "ECB balance sheet expansion via TPI purchases reignites German fiscal hawk opposition",
            "EM sovereign spreads widen on risk-off EUR deleveraging flows",
        ],
        affected_instruments=["EPA:ISP", "EPA:UCG", "EPA:BNP", "NYSE:EWI", "NYSE:TLT", "EPA:URW"],
    ),
    TransmissionOverlay(
        theme_title="PLAAF Cross-Strait Activity Intensifies",
        primary_channel="Geopolitical Risk Premium Channel",
        affected_sectors=["Defense & Aerospace", "Semiconductors", "Shipping & Logistics", "Insurance"],
        risk_level=RiskLevel.CRITICAL,
        transmission_narrative=(
            "Elevated PLAAF operational tempo forces actuarial re-pricing of Taiwan Strait "
            "shipping and semiconductor supply-chain disruption risk. The transmission channel "
            "runs from kinetic-risk perception through insurance premium adjustments, into "
            "shipping cost inflation, and then into the input-cost structures of technology "
            "and consumer electronics manufacturers globally. Defense prime equities are the "
            "primary beneficiary of sustained cross-strait tension at sub-kinetic levels."
        ),
        second_order_effects=[
            "Taiwan Strait shipping insurance premiums up 35–50% for vessels transiting within 100nm",
            "NASDAQ:TSM ADR trades at an expanded Taiwan discount versus fundamental value",
            "Global defense budget guidance revisions accelerate across NATO and Indo-Pacific allies",
        ],
        third_order_effects=[
            "Reshoring of critical semiconductor packaging capacity to Arizona and Japan accelerates",
            "VIX structural floor elevated 2–3 points as cross-strait tail risk is permanently repriced",
            "Sovereign credit ratings of Taiwan and Taiwan-exposed supply chain nations face negative outlook reviews",
        ],
        affected_instruments=["NASDAQ:KTOS", "NYSE:LMT", "NYSE:RTX", "NASDAQ:TSM", "NASDAQ:EWT", "NYSE:XAR"],
    ),
])

# ── Shared quant analysis helper ───────────────────────────────────────────────

def _make_quant(instrument: str, entry: str, bull_t: str, base_t: str, bear_t: str) -> QuantAnalysis:
    return QuantAnalysis(
        valuation_summary=(
            f"{instrument} trades at 24.3x NTM P/E vs. sector median of 18.7x and its own "
            "5-year average of 21.2x, reflecting a 16% premium that is partially justified by "
            "above-consensus earnings revision momentum (+8.4% 90-day EPS revision ratio). "
            "EV/EBITDA of 14.1x vs. sector 11.2x; EV/Sales 3.8x vs. 2.9x sector average."
        ),
        non_consensus_view=(
            "Consensus is underpricing the durability of the margin expansion story. "
            "Sell-side models assume a 120bps gross margin compression in FY2027 on "
            "input cost normalization, but fail to account for the structural shift in "
            "product mix toward higher-ASP defense-adjacent variants, which carry "
            "a 600–800bps gross margin premium over commercial equivalents. "
            "The differentiated view: FY2027 gross margin flat-to-up 50bps, "
            "generating a $0.40–0.60 EPS beat that is not in the street model."
        ),
        scenarios=TradeScenarios(
            bull=PriceScenario(
                target_price=bull_t,
                probability="30%",
                logic=(
                    "Defense budget supplemental passes in Q2 2026 at $85bn, "
                    "triggering contract announcements that drive upward EPS revisions "
                    "of 12–15%. Multiple expansion to 27x NTM P/E on earnings visibility."
                ),
            ),
            base=PriceScenario(
                target_price=base_t,
                probability="50%",
                logic=(
                    "Organic defense growth of 8–10% YoY sustains current multiple. "
                    "No supplemental needed; existing backlog provides 18-month "
                    "revenue visibility at current burn rates."
                ),
            ),
            bear=PriceScenario(
                target_price=bear_t,
                probability="20%",
                logic=(
                    "Continuing resolution locks FY2026 defense at FY2025 levels, "
                    "forcing a 6–8% revenue miss. Multiple compresses to 19x on "
                    "reduced earnings visibility; sector rotation into cyclicals."
                ),
            ),
            expected_value=(
                f"0.30 × {bull_t} + 0.50 × {base_t} + 0.20 × {bear_t} = {entry} implied EV "
                "(base case weighted; +11.2% vs. current entry level)"
            ),
        ),
        invalidation=(
            f"Close below the 200-day SMA at {entry} on elevated volume (>1.5× 30-day ADV) "
            "would invalidate the structural thesis. The secondary invalidation trigger is "
            "a CR extension beyond 90 days with no resolution pathway visible."
        ),
        crowding=(
            "Institutional ownership at 74.3% of float, up from 68.1% six months ago. "
            "Net institutional buying of $2.1bn over two quarters — "
            "not yet crowded vs. the 81% Q3 2022 peak, but accumulation rate warrants discipline."
        ),
        short_interest=(
            "Short interest at 3.2% of float, down from 5.8% 60 days ago. "
            "Days-to-cover: 2.4 days. Declining trend; no incremental squeeze potential."
        ),
        seasonality=(
            "Defense primes outperform SPX by 3.8% on average in Q2 on appropriations newsflow. "
            "April–May window positive in 8 of past 10 years during active budget deliberation."
        ),
        entry_strategy=(
            f"Scale 50% at current levels ({entry}); add remaining 50% on a confirmed "
            "52-week high close with volume. Avoid chasing ahead of the April 10 ECB decision."
        ),
        instruments=[
            f"Equity: {instrument} (primary)",
            f"Options: {instrument} $110C 19-Jun-2026 ~$4.20 debit (leveraged upside)",
            "Sector overlay: NYSE:XAR (defense ETF, reduces single-name risk)",
        ],
        hedge_plan=(
            "Allocate 10% notional to NYSE:TLT 6-month puts (200-DMA strike) to offset rate risk if "
            "fiscal impulse delays past Q3. Layer 5% notional VIX calls (30-delta, 3-month) as a "
            "cross-strait tail hedge; trim if VIX spot exceeds 25 — hedge will have paid and "
            "further holding adds carry cost."
        ),
    )

# ── Mock strategic trade ───────────────────────────────────────────────────────

MOCK_STRATEGIC = StrategicTrade(
    trade=Trade(
        instrument="NYSE:LMT",
        company_name="Lockheed Martin Corporation",
        last_price="$478.32",
        avg_daily_volume="~1.1M shs · ~$526M ADV",
        asset_class=AssetClass.EQUITY,
        direction=TradeDirection.LONG,
        time_horizon="6–9 months",
        rationale=(
            "Three concurrent intelligence signals — sustained PLAAF cross-strait operations, the DoD "
            "FY2026 supplemental budget request, and allied burden-sharing pressure — create a structural "
            "demand catalyst for Lockheed's F-35 program and missile defense architecture not fully "
            "discounted in the current multiple. Congressional appropriations urgency accelerates contract "
            "awards, improving revenue visibility beyond the 18-month backlog horizon. The result is a "
            "defense prime re-rating as risk premium rotates from Taiwan-exposed tech into domestically-"
            "anchored aerospace. (DoD, Bloomberg)"
        ),
        geopolitical_catalyst="PLAAF cross-strait operational tempo increase and DoD FY2026 supplemental budget request",
        risk_factors=[
            "Continuing resolution extends FY2026 at FY2025 levels, delaying supplemental until Q3 2026",
            "Cross-strait de-escalation following diplomatic backchannel agreement at April security dialogue",
            "LRHW program cost overruns triggering a Nunn-McCurdy breach and contract restructure",
        ],
        entry_notes="Scale in at current levels; add on any dip toward $455 (200DMA support)",
        target_notes="Primary target $545 (27x NTM P/E on $20.19 EPS consensus); secondary $580 on supplemental scenario",
        stop_notes="Hard stop at $440 (close below 200DMA on volume); soft alert at $455",
    ),
    macro_thesis=(
        "The structural re-armament cycle that began in 2022 has entered a more sustained phase driven "
        "by Indo-Pacific security deterioration. Unlike the European defense acceleration, the Taiwan "
        "Strait risk premium is grounded in observable military changes: DF-17 forward deployment, "
        "increased PLAAF sortie rates, and PLAN amphibious exercise frequency doubling since Q4 2024.\n\n"
        "Lockheed Martin's position is differentiated by the F-35's irreplaceable role in allied air "
        "dominance (10 nations, 3,000+ aircraft backlog), PAC-3 MSE as the primary terminal-phase "
        "defense solution, and the LRHW hypersonic program that directly counters the DF-17 threat.\n\n"
        "The consensus underestimates Congressional response speed. Historical precedent — the 2001 "
        "EP-3 collision and the 2022 Pelosi visit — shows each incident accelerates appropriations "
        "by 4–6 weeks on average. Three median-line crossings in one month is historically anomalous "
        "and likely produces a bipartisan supplemental within 90 days."
    ),
    time_horizon_months=9,
    historical_precedents=[
        "Post-9/11 defense re-rating (Sep 2001): LMT +67% over 12 months as procurement urgency accelerated; "
        "parallels present in Congressional response velocity.",
        "Ukraine invasion defense re-rating (Feb 2022): NATO defense prime basket +38% in 6 months; "
        "European allies accelerated procurement from LMT backlogs within 60 days.",
        "Taiwan Strait Crisis (Aug 2022, Pelosi visit): RTX/LMT +12% in 30 days on cross-strait risk premium; "
        "structural re-rating did not reverse fully even after de-escalation.",
    ],
    conviction_level="HIGH — three concurrent intelligence signals with clear appropriations transmission mechanism",
)

MOCK_STRATEGIC_QUANT = _make_quant("NYSE:LMT", "$478", "$565", "$530", "$410")

# ── Mock positional trades ─────────────────────────────────────────────────────

MOCK_POSITIONAL = PositionalTradeSet(trades=[
    Trade(
        instrument="NASDAQ:AMAT",
        company_name="Applied Materials, Inc.",
        last_price="$192.47",
        avg_daily_volume="~5.8M shs · ~$1.1B ADV",
        asset_class=AssetClass.EQUITY,
        direction=TradeDirection.LONG,
        time_horizon="3–6 weeks",
        rationale=(
            "Applied Materials is the primary beneficiary of the BIS Entity List expansion: Chinese "
            "fab investment freezes accelerate reallocation of advanced deposition and etch tooling "
            "orders toward TSMC Arizona and Samsung Taylor. US fabs are pulling forward tool delivery "
            "timelines to reduce lead-time exposure ahead of further controls. Near-term catalyst is "
            "the Q2 FY2026 earnings call on April 18, where consensus models flat YoY revenue — a "
            "threshold domestic order acceleration is likely to beat. (AMAT IR, Bloomberg)"
        ),
        geopolitical_catalyst="BIS Entity List expansion reducing Chinese semiconductor tooling demand and redirecting to US fab investments",
        risk_factors=[
            "TSMC Arizona Phase 2 construction delays pushing tool delivery timelines to Q4 2026",
            "Samsung Taylor fab ramp slower than expected due to local permitting issues",
            "BIS controls partially reversed in diplomatic negotiation ahead of May WTO hearing",
        ],
        entry_notes="Current level attractive; avoid adding ahead of any BIS policy reversal news",
        target_notes="$215–220 target on earnings beat; $235 on supplemental demand scenario",
        stop_notes="Stop at $178 (10% trailing stop from entry)",
    ),
    Trade(
        instrument="NYSEARCA:EWI",
        company_name="iShares MSCI Italy ETF",
        last_price="$30.12",
        avg_daily_volume="~320K shs · ~$9.6M ADV",
        asset_class=AssetClass.ETF,
        direction=TradeDirection.SHORT,
        time_horizon="4–6 weeks",
        rationale=(
            "Intesa Sanpaolo carries ~€42bn in Italian sovereign debt (8.4% of total assets) — the "
            "highest BTP concentration among G-SIB-equivalent European banks. The 14bp spread widening "
            "already created ~€630m in mark-to-market losses invisible until Q2 results but visible in "
            "CDS within days. The short catalyst is the April 10 ECB decision, where a hawkish dissent "
            "count would force immediate BTP re-pricing and equity de-rating. (Bloomberg, ISP AR)"
        ),
        geopolitical_catalyst="ECB Governing Council split creates fragmentation risk that disproportionately impacts Italian sovereign-exposed bank balance sheets",
        risk_factors=[
            "ECB delivers clean 25bp cut with unanimous vote and strong TPI commitment language",
            "Italian government announces credible fiscal consolidation measure before April 10",
            "EU Commission approves Italian structural reform waiver, removing fiscal pressure",
        ],
        entry_notes="Short at current level; add on any bounce toward €3.00 resistance",
        target_notes="Cover at €2.40–2.50 (BTP spread 175bps scenario); full cover at €2.20 (contagion scenario)",
        stop_notes="Stop at €3.10 (break above 20DMA on volume); ECB TPI announcement is hard stop trigger",
    ),
])

MOCK_POSITIONAL_QUANTS = [
    _make_quant("NASDAQ:AMAT", "$192", "$230", "$215", "$170"),
    _make_quant("EPA:ISP",     "€2.85", "€2.20", "€2.50", "€3.10"),
]

# ── Mock tactical trades ───────────────────────────────────────────────────────

MOCK_TACTICAL = TacticalTradeSet(trades=[
    Trade(
        instrument="NASDAQ:KTOS",
        company_name="Kratos Defense & Security Solutions — Apr $25C",
        last_price="$26.14",
        avg_daily_volume="~2.4M shs · ~$62M ADV",
        asset_class=AssetClass.DERIVATIVE,
        direction=TradeDirection.LONG,
        time_horizon="3–5 trading days",
        rationale=(
            "Kratos Defense is the primary pure-play beneficiary of the DoD's accelerated "
            "unmanned systems procurement push, which the FY2026 supplemental budget is "
            "expected to direct toward the Valkyrie XQ-58A program. The near-term catalyst "
            "is the April 3 INDOPACOM readiness review readout, which is expected to contain "
            "specific language endorsing unmanned systems expansion in the Pacific theater. "
            "Options implied volatility is pricing a 4.2% expected move on the event, "
            "below our estimated 7–10% upside scenario. (DoD, Kratos IR)"
        ),
        geopolitical_catalyst="INDOPACOM readiness review endorsement of unmanned systems in Pacific theater",
        risk_factors=[
            "INDOPACOM readout delayed or classified in its entirety",
            "Congress inserts F-35 additional-buy requirement in supplemental, reducing unmanned systems allocation",
        ],
        entry_notes="Buy $25C 18-Apr-2026 at ~$1.50 debit; enter before close on April 1",
        target_notes="Target $1.50 → $4.50 (3:1); take partial profit at $3.00",
        stop_notes="Stop at $0.60 (60% of premium); full exit if INDOPACOM readout delayed past April 7",
    ),
    Trade(
        instrument="NYSEARCA:EWI",
        company_name="iShares MSCI Italy ETF",
        last_price="$30.12",
        avg_daily_volume="~320K shs · ~$9.6M ADV",
        asset_class=AssetClass.ETF,
        direction=TradeDirection.SHORT,
        time_horizon="5–7 trading days",
        rationale=(
            "The iShares MSCI Italy ETF provides a liquid, single-instrument expression "
            "of the ECB fragmentation risk thesis at lower single-name concentration risk "
            "than the ISP short. The ETF is 27% financials (dominated by ISP and UCG) "
            "with additional exposure to utility and industrial names that face higher "
            "refinancing costs as Italian sovereign spreads widen. The April 10 ECB "
            "decision is a defined catalyst with 5-7 day resolution. (Bloomberg, iShares)"
        ),
        geopolitical_catalyst="ECB April decision hawkish dissent risk and Italian fiscal slippage creating BTP spread widening event",
        risk_factors=[
            "ECB unanimous 25bp cut with explicit TPI commitment compresses BTP spread",
            "Italian government announces fiscal adjustment before April 10",
        ],
        entry_notes="Short at current level or on any bounce to $31.00",
        target_notes="Cover at $28.00–28.50; full cover at $26.50 on contagion scenario",
        stop_notes="Stop at $31.75 (break above pre-widening levels)",
    ),
    Trade(
        instrument="NASDAQ:TLT",
        company_name="iShares 20+ Year Treasury Bond ETF",
        last_price="$88.47",
        avg_daily_volume="~36M shs · ~$3.2B ADV",
        asset_class=AssetClass.ETF,
        direction=TradeDirection.LONG,
        time_horizon="3–5 trading days",
        rationale=(
            "A risk-off rotation trigger — either from ECB fragmentation noise or from "
            "cross-strait headline escalation in the April 1–7 window — would generate "
            "safe-haven flows into US Treasuries that are not fully priced in current "
            "options implied volatility. TLT provides a liquid, low-cost instrument to "
            "express this tactical safe-haven thesis ahead of a defined catalyst window. "
            "The 10Y yield at 4.312% is at the upper end of the 4.1–4.4% range that has "
            "contained it since January 2026, creating a technically attractive entry. (Bloomberg)"
        ),
        geopolitical_catalyst="Concurrent ECB fragmentation risk and cross-strait escalation risk creating dual safe-haven demand catalysts",
        risk_factors=[
            "March BLS CPI print (April 14) materially above consensus re-anchors rate fears",
            "Both catalysts defused — ECB clean cut and Taiwan de-escalation in same week",
        ],
        entry_notes="Buy at market; TLT $88C 11-Apr-2026 at ~$1.20 debit provides leveraged upside",
        target_notes="Target $91.50–93.00; take profit on any 10Y yield print below 4.15%",
        stop_notes="Stop at $86.00; exit options at 50% premium loss",
    ),
])

MOCK_TACTICAL_QUANTS = [
    TacticalQuant(
        is_options_trade=True,
        option_details="NASDAQ:KTOS $25C 18-Apr-2026 ~$1.50 debit",
        expected_move="+7–12%",
        risk_reward="3.2:1",
        catalyst_date="2026-04-03",
    ),
    TacticalQuant(
        is_options_trade=False,
        option_details=None,
        expected_move="-5–8%",
        risk_reward="2.8:1",
        catalyst_date="2026-04-10",
    ),
    TacticalQuant(
        is_options_trade=True,
        option_details="NYSE:TLT $88C 11-Apr-2026 ~$1.20 debit",
        expected_move="+2.5–4.0%",
        risk_reward="2.5:1",
        catalyst_date=None,
    ),
]

# ── Mock appendix ──────────────────────────────────────────────────────────────

MOCK_APPENDIX = AppendixOutput(
    reading_list=[
        ReadingListItem(
            title="Department of Commerce Bureau of Industry and Security — Entity List Update (March 2026)",
            source="Bureau of Industry and Security",
            author_or_publication="US Department of Commerce",
            relevance="Primary source for the 37 new Chinese designations driving Theme 1 semiconductor decoupling analysis.",
            priority=ReadingListPriority.ESSENTIAL,
            url="https://www.bis.doc.gov/index.php/policy-guidance/lists-of-parties-of-concern/entity-list",
        ),
        ReadingListItem(
            title="ECB Economic Bulletin — March 2026",
            source="European Central Bank",
            author_or_publication="ECB Research Directorate",
            relevance="Contains the wage tracker model data and Governing Council communication that underpins Theme 2 fragmentation analysis.",
            priority=ReadingListPriority.ESSENTIAL,
            url="https://www.ecb.europa.eu/pub/economic-bulletin/html/index.en.html",
        ),
        ReadingListItem(
            title="Indo-Pacific Strategy Report — Annual Update",
            source="US Department of Defense",
            author_or_publication="Office of the Secretary of Defense",
            relevance="Provides the strategic framework for INDOPACOM readiness posture relevant to Theme 3 cross-strait analysis.",
            priority=ReadingListPriority.RECOMMENDED,
            url=None,
        ),
        ReadingListItem(
            title="Global Semiconductor Supply Chain Risk Assessment",
            source="Semiconductor Industry Association",
            author_or_publication="SIA Research",
            relevance="Quantifies the specific chokepoints in the global semiconductor supply chain relevant to the TSMC/AMAT thesis.",
            priority=ReadingListPriority.RECOMMENDED,
            url="https://www.semiconductors.org/",
        ),
        ReadingListItem(
            title="Italian Fiscal Situation — Spring 2026 Assessment",
            source="European Commission",
            author_or_publication="DG ECFIN",
            relevance="Official tracking of Italian deficit against the 3.0% target commitment relevant to the ECB fragmentation risk analysis.",
            priority=ReadingListPriority.SUPPLEMENTARY,
            url="https://ec.europa.eu/info/business-economy-euro/economic-and-fiscal-policy-coordination",
        ),
    ],
    book_recommendations=[
        BookRecommendation(
            title="Chip War: The Fight for the World's Most Critical Technology",
            author="Chris Miller",
            category=BookCategory.THEMATIC,
            synopsis=(
                "Miller traces how semiconductor manufacturing control became the defining axis of "
                "21st century economic and military power, from Texas Instruments to TSMC. "
                "Its central contribution: chip supply chains are structurally irreplaceable "
                "on any sub-decade timeline — a constraint shaping all export control logic."
            ),
            briefing_connection=(
                "Today's BIS Entity List expansion targeting EDA vendors is precisely the "
                "qualitative escalation Miller's framework identifies as unresolvable "
                "sub-decade, directly underpinning Theme 1."
            ),
        ),
        BookRecommendation(
            title="The Price of Peace: Money, Democracy, and the Life of John Maynard Keynes",
            author="Zachary D. Carter",
            category=BookCategory.THEMATIC,
            synopsis=(
                "Carter's intellectual biography of Keynes reconstructs modern macroeconomic thought "
                "through 20th century crises, from WWI reparations to Bretton Woods. "
                "Its central contribution is tracing how sovereign fiscal credibility, once lost, "
                "is recovered only through institutional constraint rather than market reassurance."
            ),
            briefing_connection=(
                "Italy's deficit overshoot and the TPI's legal limits in Theme 2 mirror the "
                "interwar gold standard constraints Carter identifies as the structural mechanism "
                "of fiscal credibility collapse."
            ),
        ),
        BookRecommendation(
            title="The Avoidable War",
            author="Kevin Rudd",
            category=BookCategory.THEMATIC,
            synopsis=(
                "Rudd structures US-China relations around ten conflict pathways, assessing each for "
                "probability and escalation dynamics from his experience as Australian PM and China scholar. "
                "Its distinguishing feature is systematic treatment of how miscalculation — not deliberate "
                "choice — produces conflict."
            ),
            briefing_connection="Today's PLAAF crossings and DF-17 deployment exemplify Rudd's third conflict pathway — coercive signaling risking automatic defensive responses — the analytical lens for Theme 3.",
        ),
        BookRecommendation(
            title="The Kill Chain: Defending America in the Future of High-Tech Warfare",
            author="Christian Brose",
            category=BookCategory.DEFENSE,
            synopsis=(
                "Brose, former Senate Armed Services Committee staff director, argues the Pentagon's "
                "acquisition bureaucracy produces the wrong weapons for likely future wars. "
                "The book makes the case for distributed, networked unmanned systems as the "
                "structural solution to the pacing threat."
            ),
            briefing_connection=(
                "The DoD FY2026 supplemental and the Kratos trade thesis both derive from "
                "the networked unmanned systems shift Brose identifies as the Pacific "
                "theater operational imperative in Theme 3."
            ),
        ),
        BookRecommendation(
            title="Thinking, Fast and Slow",
            author="Daniel Kahneman",
            category=BookCategory.PERSONAL_ENRICHMENT,
            synopsis=(
                "Kahneman synthesizes decades of behavioral economics into a dual-process model "
                "of human judgment, distinguishing fast intuitive from slow deliberate reasoning. "
                "Its lasting contribution is a rigorous taxonomy of cognitive biases — anchoring, "
                "availability, overconfidence — that distort decisions under uncertainty."
            ),
            briefing_connection=(
                "Processing high-frequency cross-strait and semiconductor signals under time pressure "
                "creates classic availability and anchoring bias conditions that Kahneman's framework "
                "directly corrects."
            ),
        ),
    ],
    key_entities=[
        "Bureau of Industry and Security", "TSMC", "Samsung", "SMIC", "Nvidia", "Applied Materials",
        "European Central Bank", "Christine Lagarde", "Bundesbank", "Intesa Sanpaolo",
        "PLAAF", "PLAN", "INDOPACOM", "Lockheed Martin", "Raytheon Technologies", "Kratos Defense",
        "Italian Ministry of Economy and Finance", "Giorgia Meloni",
    ],
    key_situations=[
        "US-China Semiconductor Export Control Escalation",
        "ECB April 2026 Rate Decision — Governing Council Split",
        "PLAAF Cross-Strait Operational Tempo Increase",
        "BTP-Bund Spread Widening Episode",
        "DoD FY2026 Supplemental Budget Request",
        "Taiwan Strait ADIZ Incursions March 2026",
    ],
    intelligence_summary=(
        "March 25, 2026 intelligence presents three converging risk vectors. US-China tech decoupling "
        "accelerated with the March 14 BIS Entity List expansion targeting EDA vendors — chokepoints "
        "unresolvable sub-five-years. The ECB faces its most challenging environment since June 2022 "
        "with Governing Council divisions and Italian fiscal slippage compounding spread-widening risk.\n\n"
        "Primary tail risk: three PLAAF median-line crossings and DF-17 deployment in Fujian constitute "
        "a historically anomalous tempo increase. Rotate from Taiwan-exposed tech into defense primes "
        "and semiconductor equipment manufacturers with US fab concentration."
    ),
)

# ── Compile and open ──────────────────────────────────────────────────────────

def _build_html() -> tuple[Path, str]:
    output_path = Path(__file__).parent / "preview_output.html"
    html = compile_html_document(
        opening=MOCK_OPENING,
        themes=MOCK_THEMES,
        overlays=MOCK_OVERLAYS,
        strategic=MOCK_STRATEGIC,
        positional=MOCK_POSITIONAL,
        tactical=MOCK_TACTICAL,
        appendix=MOCK_APPENDIX,
        briefing_date=date(2026, 3, 23),
        market_snapshot=MOCK_SNAPSHOT,
        strategic_quant=MOCK_STRATEGIC_QUANT,
        positional_quants=MOCK_POSITIONAL_QUANTS,
        tactical_quants=MOCK_TACTICAL_QUANTS,
    )
    output_path.write_text(html, encoding="utf-8")
    print(f"[PREVIEW] Written → {output_path}")
    return output_path, html


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preview and optionally email the mock briefing.")
    parser.add_argument("--send", action="store_true", help="Email the HTML to BRIEFING_EMAIL after generating.")
    args = parser.parse_args()

    output_path, _ = _build_html()

    if args.send:
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).parent / ".env")
        except ImportError:
            pass
        from delivery import deliver_briefing
        pdf_path = asyncio.run(deliver_briefing(html_path=output_path, briefing_date=date(2026, 3, 23)))
        print(f"[PREVIEW] PDF → {pdf_path}")
    else:
        webbrowser.open(output_path.resolve().as_uri())
        print("[PREVIEW] Opened in browser.")
