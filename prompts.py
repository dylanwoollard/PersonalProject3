"""
prompts.py — Prompt templates and context assembly (Level 5).

Level 0 analytical standards are hardcoded into every prompt via the
ANALYTICAL_STANDARDS constant, which is injected at render time.

All templates accept the following format keys:
  {analytical_standards}   — injected Level 0 standards block
  {today}                  — ISO date string (YYYY-MM-DD)
  {raw_intelligence}       — raw email intelligence text
  {historical_context}     — formatted 180-day longitudinal context
  {financial_data}         — formatted live market data (trade prompts only)
  {priority_themes_json}   — JSON of already-generated themes (dependent prompts only)

Extra keys passed to build_prompt() but absent from a template are silently
ignored by str.format_map(), so all prompts can share a single builder.
"""

from datetime import date
from typing import Optional


# ── Level 0: Analytical Standards ────────────────────────────────────────────
# SYSTEM_INSTRUCTION carries the full standards block — passed as Gemini's
# system_instruction parameter so it is enforced at the architectural level
# without consuming prompt tokens in every call.
#
# ANALYTICAL_STANDARDS is the slimmed version injected into prompt bodies.
# It carries only the depth and specificity rules that vary by task context.
# Writing style and banned words live exclusively in SYSTEM_INSTRUCTION.

SYSTEM_INSTRUCTION = """\
You are a senior intelligence analyst producing classified assessments for
senior policymakers and portfolio managers.

## WRITING STYLE — MANDATORY
All qualitative text must be written in flowing, bureaucratic narrative prose.
Never use journalistic, dramatic, or promotional language.

**Banned words and phrases (absolute prohibition):** soaring, plummeting,
skyrocketing, surging, tumbling, existential, explosive, dramatic, stunning,
shocking, unprecedented, game-changing, tectonic, seismic, bombshell, crisis
(unless quoting a named official), collapse (unless describing a documented
event), mounting, escalating (prefer: "increasing" or a specific quantitative
descriptor).

## ANALYTICAL DEPTH — MANDATORY
- Do not summarize.  Perform causal analysis structured as:
  Signal → Mechanism → Market Impact
- Preserve specific figures, percentages, named entities, dates, and
  designations verbatim from the source material.
- Contextualize all developments within their broader geopolitical,
  macroeconomic, or sectoral framework.
- Tickers: Full Company Name (EXCHANGE:TICKER) on first mention in body text;
  company name or ticker alone on subsequent mentions.  Indices, ETFs, futures,
  and non-equity instruments use the standard name alone.
- Inline citations: (Source) parenthetical when drawing on a named publication,
  report, or official statement — e.g., (IMF World Economic Outlook).
- If a specific designation exists (ticker, treaty name, regulatory body,
  named individual), use it.  Never substitute specifics with generic labels.

## SPECIFICITY REQUIREMENTS — MANDATORY
- Instruments: exact tickers (e.g., "XLE", "TLT", "DXY", "UST 10Y").
- Persons and organizations: full official names on first reference.
- Quantities: preserve all numbers, percentages, and metrics from the source.
- Historical context: cite the date of prior observations and the specific
  development recorded when referencing the 180-day longitudinal record.
"""

# Slimmed prompt-level standards — writing style and banned words are omitted
# because they are enforced by SYSTEM_INSTRUCTION at the model level.
ANALYTICAL_STANDARDS = """\
## ANALYTICAL STANDARDS (applied in addition to system-level style rules)

### Analytical Depth
- Do not summarize.  Perform causal analysis structured as:
  **Signal → Mechanism → Market Impact**
- Preserve specific figures, percentages, named entities, dates, and
  designations verbatim from the source material.
- Contextualize all developments within their broader geopolitical,
  macroeconomic, or sectoral framework.  No fact is an isolated data point.
- Tickers: Full Company Name (EXCHANGE:TICKER) on first mention in body text;
  company name or ticker alone on subsequent mentions.
- Inline citations: (Source) parenthetical when drawing on a named publication.

### Specificity Requirements
- Instruments: exact tickers (e.g., "XLE", "TLT", "DXY", "UST 10Y").
- Persons and organizations: full official names on first reference.
- Quantities: preserve all numbers, percentages, and metrics from the source.
- Historical context: cite the date and specific development when referencing
  the 180-day longitudinal record.
"""


# ── Prompt Templates ──────────────────────────────────────────────────────────

UNIFIED_THEMES_PROMPT = """\
{analytical_standards}

---

## TASK: UNIFIED INTELLIGENCE THEMES GENERATION

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

### HISTORICAL CONTEXT (180-Day Longitudinal Record)
{historical_context}

---

## INSTRUCTIONS

Identify and analyze the top 3 priority intelligence themes from today's
material.  These are the themes with the greatest combined geopolitical
significance and financial market impact.  Rank them 1 (highest) to 3 (lowest).

For each theme produce the full analytical picture — signal/mechanism, market
impact, and risk transmission — in a single unified output.

**Core analysis:**
- **narrative:** Flowing bureaucratic prose.  Causal analysis is mandatory.
  Contextualize within the broader geopolitical and macroeconomic framework.
  Reference the historical record where a precedent exists.
- **key_data_points:** Verbatim or near-verbatim data points.  Preserve
  specific figures, names, and designations exactly as they appear.
  Generate exactly 4 key_data_points per theme.
- **signal / mechanism / market_impact:** These three fields must form a
  coherent analytical chain.  Signal: the observable intelligence fact.
  Mechanism: the causal pathway.  Market Impact: expected financial consequence
  with specific instruments and directional views named.
- **affected_instruments:** Specific tickers, currency pairs, commodity
  contracts, or index names only.  Never generic category labels.
- **entities_involved:** Named persons, organizations, and state actors driving
  this theme.  Be comprehensive — include all relevant named parties.
- **geographies:** Named geographic locations material to this theme
  (countries, regions, cities, bodies of water, military districts).

**Risk transmission:**
- **primary_channel:** Name the specific transmission mechanism
  (e.g., "Sovereign Credit Channel", "Commodity Input Cost Channel",
  "Currency Redenomination Risk", "Trade Finance Disruption").
- **affected_sectors:** GICS sectors or industry groups directly impacted.
  Provide at least 3 entries.
- **risk_level:** LOW, MEDIUM, HIGH, or CRITICAL — assessed probability and
  severity of transmission within a 30-day horizon.
- **transmission_narrative:** Flowing bureaucratic prose describing the
  propagation path.  Name specific conduits, counterparties, and market
  structures involved.
- **second_order_effects:** Direct consequences of the primary transmission.
  Name specific sectors, instruments, and geographic markets.
- **third_order_effects:** Subsequent systemic or contagion effects following
  second-order impacts.  Name specific sectors, instruments, and geographies.

Output valid JSON conforming to the PriorityThemes schema (exactly 3 themes).
"""

STRATEGIC_TRADE_PROMPT = """\
{analytical_standards}

---

## TASK: STRATEGIC TRADE GENERATION

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

### HISTORICAL CONTEXT (180-Day Longitudinal Record)
{historical_context}

### LIVE FINANCIAL MARKET DATA
{financial_data}

### PRIORITY THEMES (already generated — do not regenerate)
{priority_themes_json}

---

## INSTRUCTIONS

**Trade constraints (mandatory):**
- Do NOT recommend foreign exchange (currency pairs) or commodity trades.
  Permitted asset classes: EQUITY, FIXED_INCOME, DERIVATIVE only.
- HARD RULE: ETFs and Index Funds are STRICTLY PROHIBITED for the strategic
  trade.  You must select an individual company equity.
- Each instrument must be unique across the entire briefing. Do not repeat any
  instrument that will be used in the strategic, positional, or tactical trades.

Generate exactly ONE strategic trade with a time horizon of 3-12 months.
This trade must be grounded in the dominant geopolitical or macroeconomic
theme from today's intelligence.

**instrument:** Must be a specific, liquid, individual company equity using
EXCHANGE:TICKER format (e.g., "NYSE:KO").  ETFs are PROHIBITED.

**last_price:** Populate from the live financial data provided above.

**target_price:** Calculate and output a specific numeric price target based on
valuation and the expected magnitude of the thesis catalyst.  Format as a
dollar string (e.g., "$187.50").  MANDATORY — do not leave it null.

**macro_thesis:** A long-form narrative (3-5 paragraphs) in flowing
bureaucratic prose covering:
  1. The geopolitical or macroeconomic foundation of the thesis
  2. The expected policy, regulatory, or corporate response
  3. The market transmission mechanism (how the thesis reaches price)
  4. The timing rationale (why now, what catalysts, what the historical
     record suggests)
  5. Key conditions that would invalidate the thesis

**historical_precedents:** 2-3 specific historical analogues with approximate
dates and outcomes that support the structural thesis.

**risk_factors:** Must be specific and named — reference concrete events,
dates, or counterparties that could invalidate the setup.

**conviction_level:** HIGH, MEDIUM, or LOW followed by a brief explanatory
clause (e.g., "HIGH — the policy trajectory is well-established and the
catalyst calendar is dense").

Output valid JSON conforming to the StrategicTrade schema.
"""


STRATEGIC_THESIS_PROMPT = """\
{analytical_standards}

---

## TASK: STRATEGIC TRADE MACRO THESIS

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

### HISTORICAL CONTEXT (180-Day Longitudinal Record)
{historical_context}

### PRIORITY THEMES (already generated — do not regenerate)
{priority_themes_json}

---

## INSTRUCTIONS

Generate the macro thesis for today's dominant strategic trade opportunity.
Do NOT select a specific instrument — focus exclusively on the intellectual
and analytical content: the argument, the precedents, the conviction.

**macro_thesis:** A long-form narrative (3-5 paragraphs) in flowing
bureaucratic prose covering:
  1. The geopolitical or macroeconomic foundation of the thesis
  2. The expected policy, regulatory, or corporate response
  3. The market transmission mechanism (how the thesis reaches price)
  4. The timing rationale (why now, what catalysts, what the historical
     record suggests)
  5. Key conditions that would invalidate the thesis

**historical_precedents:** 2-3 specific historical analogues with approximate
dates and outcomes that support the structural thesis.

**time_horizon_months:** Integer months for the strategic horizon (3-12).

**conviction_level:** HIGH, MEDIUM, or LOW followed by a brief explanatory
clause (e.g., "HIGH — the policy trajectory is well-established and the
catalyst calendar is dense").

Output valid JSON conforming to the StrategicThesis schema.
"""


STRATEGIC_INSTRUMENT_PROMPT = """\
{analytical_standards}

---

## TASK: STRATEGIC TRADE INSTRUMENT SELECTION

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

### LIVE FINANCIAL MARKET DATA
{financial_data}

### PRIORITY THEMES (already generated — do not regenerate)
{priority_themes_json}

---

## INSTRUCTIONS

**Trade constraints (mandatory):**
- Do NOT recommend foreign exchange (currency pairs) or commodity trades.
  Permitted asset classes: EQUITY, FIXED_INCOME, DERIVATIVE only.
- HARD RULE: ETFs and Index Funds are STRICTLY PROHIBITED for the strategic
  trade.  You must select an individual company equity.  Sector ETFs, thematic
  ETFs, and broad-market ETFs are all forbidden regardless of thesis fit.
- Time horizon: 3-12 months (strategic).

Identify the single best individual company equity to express the dominant
geopolitical or macroeconomic theme in today's intelligence.

**instrument:** EXCHANGE:TICKER format (e.g., "NYSE:LMT"). US exchanges only.

**last_price:** Populate from the live financial data provided above.

**target_price:** Calculate and output a specific numeric price target based on
the live financial data, valuation framework, and the expected magnitude of the
thesis catalyst.  Format as a dollar string (e.g., "$187.50").  This field is
MANDATORY — do not leave it null or omit it.

**rationale:** 2-3 sentences of flowing bureaucratic prose: why this specific
instrument is the optimal vehicle for the macro thesis, and why the pricing
and liquidity profile make it actionable at this horizon.

**geopolitical_catalyst:** The specific event or development driving the trade.

**risk_factors:** Named, specific risks that could invalidate this instrument
selection (not generic category risks).

**entry_notes / target_notes / stop_notes:** Specific levels or conditions
where applicable, drawn from the financial data.

Output valid JSON conforming to the StrategicInstrument schema.
"""


SINGLE_POSITIONAL_TRADE_PROMPT = """\
{analytical_standards}

---

## TASK: SINGLE POSITIONAL TRADE GENERATION

**Briefing Date:** {today}
**Trade slot:** Positional trade {trade_number} of 2

### INTELLIGENCE SUMMARY (condensed — priority themes contain full detail)
{raw_intelligence}

### HISTORICAL CONTEXT (180-Day Longitudinal Record)
{historical_context}

### LIVE FINANCIAL MARKET DATA
{financial_data}

### PRIORITY THEMES (already generated — do not regenerate)
{priority_themes_json}

### INSTRUMENTS ALREADY USED IN THIS BRIEFING — DO NOT REPEAT
{excluded_tickers}

---

## INSTRUCTIONS

**Trade constraints (mandatory):**
- Do NOT recommend foreign exchange (currency pairs) or commodity trades.
  Permitted asset classes: EQUITY, FIXED_INCOME, DERIVATIVE only.
- Prefer individual company equities over sector or thematic ETFs.
- HARD RULE: You must not use any instrument listed in the "INSTRUMENTS ALREADY
  USED" section above.
- Time horizon: 2-8 weeks (positional).

Generate exactly ONE positional trade driven by near-to-medium-term
developments identified in today's intelligence.

**instrument:** EXCHANGE:TICKER format. US exchanges only.

**last_price:** Populate from the live financial data provided.

**rationale:** 2-3 sentences of bureaucratic prose: the specific catalyst from
the priority themes, why this instrument captures it, and why the timing is
compelling now. Do not repeat the full macro thesis — be precise and brief.

**risk_factors:** 1-2 specific, named risks that could invalidate this setup
within the 2-8 week horizon. One line each — no extended explanation.

**entry_notes / target_notes / stop_notes:** One sentence each where applicable.

Output valid JSON conforming to the SingleTrade schema.
"""


SINGLE_TACTICAL_TRADE_PROMPT = """\
{analytical_standards}

---

## TASK: SINGLE TACTICAL TRADE GENERATION

**Briefing Date:** {today}
**Trade slot:** Tactical trade {trade_number} of 3

### INTELLIGENCE SUMMARY (condensed — priority themes contain full detail)
{raw_intelligence}

### LIVE FINANCIAL MARKET DATA
{financial_data}

### PRIORITY THEMES (already generated — do not regenerate)
{priority_themes_json}

### INSTRUMENTS ALREADY USED IN THIS BRIEFING — DO NOT REPEAT
{excluded_tickers}

---

## INSTRUCTIONS

**Trade constraints (mandatory):**
- Do NOT recommend foreign exchange (currency pairs) or commodity trades.
  Permitted asset classes: EQUITY, FIXED_INCOME, DERIVATIVE only.
- HARD RULE: You must not use any instrument listed in the "INSTRUMENTS ALREADY
  USED" section above.
- Time horizon: 1-10 trading days (tactical).
- If this is trade slot 3, strongly prefer expressing the trade through options.
  For trades 1-2, options are appropriate for event-driven binary catalysts.

Generate exactly ONE tactical trade exploiting a near-term catalyst or
event-driven opportunity confirmed by today's intelligence.

**instrument:** EXCHANGE:TICKER format for equities; for options trades, include
the full contract in instrument (e.g., "NASDAQ:KTOS $25C 18-Apr-2025").

**last_price:** Populate from the live financial data provided.

**rationale:** 2-3 sentences: the specific catalyst, why this instrument
captures it, and the expected move within the tactical horizon. Be concise —
this is a near-term event-driven setup, not a macro thesis.

**risk_factors:** 1-2 named risks that could invalidate this setup within
the 1-10 trading day horizon. One line each.

**entry_notes / target_notes / stop_notes:** One sentence each where applicable.

Output valid JSON conforming to the SingleTrade schema.
"""


POSITIONAL_TRADES_PROMPT = """\
{analytical_standards}

---

## TASK: POSITIONAL TRADES GENERATION

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

### HISTORICAL CONTEXT (180-Day Longitudinal Record)
{historical_context}

### LIVE FINANCIAL MARKET DATA
{financial_data}

### PRIORITY THEMES (already generated — do not regenerate)
{priority_themes_json}

### INSTRUMENTS ALREADY USED IN THIS BRIEFING — DO NOT REPEAT
{excluded_tickers}

---

## INSTRUCTIONS

**Trade constraints (mandatory):**
- Do NOT recommend foreign exchange (currency pairs) or commodity trades.
  Permitted asset classes: EQUITY, FIXED_INCOME, DERIVATIVE only.
- Prefer individual company equities over sector or thematic ETFs. Use an ETF
  only when no single liquid company equity adequately captures the thesis.
- HARD RULE: You must not use any instrument listed in the "INSTRUMENTS ALREADY
  USED" section above. Each trade in this briefing must use a distinct ticker.

Generate exactly 2 positional trades with time horizons of 2-8 weeks.
These should be driven by near-to-medium-term developments identified in
today's intelligence.  They may be related to the strategic trade or capture
distinct opportunities.

Each trade must include:
- An exact ticker or instrument name
- A directional view (LONG or SHORT)
- A flowing narrative rationale covering the macro/geopolitical context,
  why this specific instrument captures the thesis, why the timing is
  compelling now, and the causal chain from signal to expected market effect.
- Specific risk factors — name the concrete events or conditions that would
  invalidate the setup

**instrument:** Must be a specific, liquid, publicly traded instrument using
EXCHANGE:TICKER format (e.g., "NYSE:KO").

**last_price:** Populate from the live financial data provided above.

Output valid JSON conforming to the PositionalTradeSet schema (exactly 2 trades).
"""

TACTICAL_TRADES_PROMPT = """\
{analytical_standards}

---

## TASK: TACTICAL TRADES GENERATION

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

### HISTORICAL CONTEXT (180-Day Longitudinal Record)
{historical_context}

### LIVE FINANCIAL MARKET DATA
{financial_data}

### PRIORITY THEMES (already generated — do not regenerate)
{priority_themes_json}

### INSTRUMENTS ALREADY USED IN THIS BRIEFING — DO NOT REPEAT
{excluded_tickers}

---

## INSTRUCTIONS

**Trade constraints (mandatory):**
- Do NOT recommend foreign exchange (currency pairs) or commodity trades.
  Permitted asset classes: EQUITY, FIXED_INCOME, DERIVATIVE only.
- Prefer individual company equities over sector or thematic ETFs. Use an ETF
  only when no single liquid company equity adequately captures the thesis.
- HARD RULE: You must not use any instrument listed in the "INSTRUMENTS ALREADY
  USED" section above. Each trade in this briefing must use a distinct ticker.

Generate exactly 3 tactical trades with time horizons of 1-10 trading days.
These should exploit near-term catalysts, event-driven opportunities, or
technically confirmed setups validated by today's intelligence.

Each trade must include:
- An exact ticker or instrument name
- A directional view (LONG or SHORT)
- A concise but precise narrative rationale covering the macro/geopolitical
  context, why this instrument captures the thesis, why the timing is
  compelling, and the causal chain from signal to expected market effect.
- Specific risk factors: name the concrete events or conditions that would
  invalidate the setup within the tactical horizon

**instrument:** Must be a specific, liquid, publicly traded instrument using
EXCHANGE:TICKER format (e.g., "NYSE:KO").

**last_price:** Populate from the live financial data provided above.

At least one of the 3 trades must be expressed through options. For options trades,
specify the full contract in the instrument field (e.g., "NASDAQ:KTOS $25C 18-Apr-2025").

Output valid JSON conforming to the TacticalTradeSet schema (exactly 3 trades).
"""

APPENDIX_DATABASE_PROMPT = """\
{analytical_standards}

---

## TASK: INTELLIGENCE DATABASE RECORD

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

---

## INSTRUCTIONS

Generate the database record for today's intelligence briefing.

**key_entities**
List all named persons, organizations, state actors, international bodies, and
financial institutions that appear in today's intelligence.  Be comprehensive.
These values are used as database index keys for longitudinal retrieval.

**key_situations**
List all distinct geopolitical situations, macroeconomic developments, and
market events discussed today.  Use descriptive but concise labels
(e.g., "US-China Semiconductor Export Controls", "ECB Rate Path Divergence").
These values are used as database index keys.

**intelligence_summary**
A 2-3 paragraph bureaucratic prose summary of the total intelligence picture
for today, written for archival purposes and future longitudinal retrieval.
Must be self-contained — a reader with no access to the source emails should
understand the full picture from this summary alone.

Output valid JSON conforming to the AppendixDatabase schema.
"""

APPENDIX_READING_LIST_PROMPT = """\
{analytical_standards}

---

## TASK: READING LIST AND BOOK RECOMMENDATIONS

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

---

## INSTRUCTIONS

Generate the curated reading list and book recommendations for today's briefing.

**reading_list (4-8 items)**
Identify specific publications, reports, articles, or data releases that an
analyst should review to deepen understanding of today's themes.  For each:
- Specify the source organization and author or publication
- Note the relevance to today's specific intelligence themes by name
- Assign priority: ESSENTIAL (directly bears on today's dominant themes),
  RECOMMENDED (important context), or SUPPLEMENTARY (background reading)
Prioritize primary sources — central bank statements, government releases,
multilateral institution reports, official data releases — over secondary
commentary and news analysis.

**book_recommendations (exactly 5 books)**
Select exactly 5 books for today's briefing:
- 3 must be THEMATIC: books directly relevant to the dominant geopolitical,
  macroeconomic, or sectoral themes in today's intelligence.
- 1 must be DEFENSE: a book on military strategy, security affairs, intelligence
  tradecraft, or defense policy. Choose a highly regarded, substantive work.
- 1 must be PERSONAL_ENRICHMENT: a biography, work of history, philosophy, or
  literature selected for intellectual depth and breadth.
For each book provide:
- title, author (full name), category
- synopsis: exactly 2 sentences — the book's core argument or subject matter,
  and its principal contribution or what distinguishes it from other works.
- briefing_connection: exactly 1 sentence — how this specific book connects
  to today's intelligence themes; reference the theme by name for THEMATIC books.

Output valid JSON conforming to the AppendixReadingList schema.
"""

# Legacy alias — kept so any external code importing APPENDIX_PROMPT still works.
APPENDIX_PROMPT = APPENDIX_DATABASE_PROMPT


# ── Split Opening Prompts ─────────────────────────────────────────────────────
# OpeningSections is split into two concurrent calls to stay within the model's
# output ceiling.  OpeningNarrative (epigraph only) runs on raw intel only.
# OpeningCalendar (calendar + earnings) requires the live data feeds.

OPENING_NARRATIVE_PROMPT = """\
{analytical_standards}

---

## TASK: OPENING NARRATIVE GENERATION

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

### HISTORICAL CONTEXT (180-Day Longitudinal Record)
{historical_context}

---

## INSTRUCTIONS

Generate the narrative opening for today's intelligence briefing.

**Epigraph**
Select a brief, relevant quotation from a statesman, strategist, economist, or
philosopher whose wisdom is directly applicable to the dominant theme in
today's intelligence.  Attribute precisely (full name, role or title, year if
known).  The tone must be measured and analytical — not inspirational or
dramatic.

CRITICAL: Do NOT hallucinate quotes.  You must only use widely verifiable,
famous historical quotes that you are certain of — the exact wording and the
exact attribution.  If you cannot perfectly verify the quote and attribution,
omit the epigraph entirely and output an empty string for both epigraph and
epigraph_attribution.

Output valid JSON conforming to the OpeningNarrative schema.
"""


OPENING_CALENDAR_PROMPT = """\
{analytical_standards}

---

## TASK: CALENDAR AND EARNINGS SECTIONS GENERATION

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

---

## INSTRUCTIONS

Generate the calendar and earnings sections of today's intelligence briefing.

**Calendar Events**
A live economic calendar feed is provided below.  For each scheduled release:
- Copy date_str, time_et, event, previous, and forecast fields verbatim from
  the data — do not alter numbers, units, or spellings.
- Fill in entity (the releasing authority, e.g. "Bureau of Labor Statistics").
- Write market_relevance: which instruments are sensitive to this release,
  what the consensus expects, and how a miss or beat in either direction
  would affect those markets.  Name specific tickers and rate expectations.

{economic_calendar}

**Upcoming Earnings Calendar**
From the upcoming earnings listed below, select the 3 most market-significant
(prioritizing relevance to today's intelligence themes and estimated revenue
scale).  For each entry:
- Copy date_str, time_of_day, ticker, eps_estimate, revenue_estimate verbatim.
- HARD RULE: The ticker field must use EXCHANGE:TICKER format
  (e.g., "NASDAQ:AAPL", "NYSE:JPM").  Never output a bare ticker.
- HARD RULE: If eps_estimate or revenue_estimate is provided in the data, you
  MUST copy it exactly.  Never leave these fields null when data is available.
- Fill in company (full legal name from your knowledge of the ticker).
- Write watch_analysis: the 2-3 metrics the market will focus on, what guidance
  language is expected, and how a beat or miss in either direction affects the
  stock and sector peers.  Name specific instruments.
- Write theme_relevance: why this company was selected and how it connects to
  today's priority intelligence themes.

**Recent Earnings Analysis**
From the recent earnings results below, select the 3 most significant.
For each entry:
- Copy date_str, ticker, eps/revenue actuals, estimates, and surprise strings
  verbatim.
- Set result to 'BEAT', 'MISS', or 'IN-LINE' based on EPS vs. estimate.
- Fill in company and write analysis: what the results mean for the company's
  forward thesis, key guidance language and tone, sector read-through effects,
  and market reaction context (positioning, options activity, peer re-ratings).

{earnings_calendar}

Output valid JSON conforming to the OpeningCalendar schema.
"""


# ── Adversarial Assessment Prompt ─────────────────────────────────────────────

ADVERSARIAL_PROMPT = """\
{analytical_standards}

---

## TASK: ADVERSARIAL SCENARIO ASSESSMENT

**Briefing Date:** {today}

### RAW INTELLIGENCE INPUT
{raw_intelligence}

### PRIORITY THEMES (already generated — challenge these)
{priority_themes_json}

---

## INSTRUCTIONS

You are a senior red-cell analyst tasked with identifying the blind spots and
analytical failures in the priority theme assessments above.

Your mandate is adversarial: assume the consensus themes are partially or
wholly wrong.  Your job is NOT to validate the consensus — it is to surface
what the consensus analysis is missing, underweighting, or incorrectly framing.

**Generate exactly 3 adversarial scenarios — one per priority theme, in rank order (Theme 1 first):**

- **consensus_view:** State the prevailing analytical assumption concisely.

- **blind_spot:** Identify the specific analytical gap, cognitive bias, or
  missing data point that makes the consensus vulnerable.  Be precise —
  name the bias (e.g., "recency bias", "anchoring to the 2022 precedent",
  "availability heuristic from recent media coverage").

- **adversarial_case:** In flowing bureaucratic prose, construct the case for
  how this theme could develop in a way that invalidates the consensus
  assessment.  This is NOT a hedge or an "on the other hand" — it is a direct
  challenge to the analytical foundation.  Be rigorous and specific.

- **trigger_conditions:** 2-3 specific, observable signals in the near-term
  that would confirm the adversarial scenario is developing.

- **market_implications:** How markets would react if the adversarial case
  materializes — name specific instruments, directions, and approximate
  magnitude of repricing.

**meta_risk:** After completing the three scenario assessments, identify the
overarching systemic blind spot: the one scenario that causes all three
consensus themes to simultaneously fail in the same direction.  What is the
tail scenario that would be catastrophically wrong for a portfolio positioned
according to the consensus themes above?

Output valid JSON conforming to the AdversarialAssessment schema.
"""


# ── Trade Logic Review Prompt ─────────────────────────────────────────────────

TRADE_LOGIC_REVIEW_PROMPT = """\
## TASK: STRATEGIC TRADE LOGIC REVIEW

**Briefing Date:** {today}

You are a senior risk officer reviewing a proposed strategic trade for logical
integrity.  Your mandate is to identify fallacies, gaps, and weaknesses — not
to validate the trade.  Apply the analytical standards of a rigorous peer reviewer.

### STRATEGIC TRADE AND QUANTITATIVE ANALYSIS
{trade_and_quant_json}

### CROSS-ASSET PRICE CORRELATIONS (30-Day)
{correlation_matrix}

---

## INSTRUCTIONS

Review the strategic trade and its quantitative analysis for:

1. **fallacies_identified** — Named logical fallacies in the causal chain or
   probability estimates.  Examples of applicable fallacies:
   - "Post Hoc Ergo Propter Hoc — correlation between X and Y treated as causation"
   - "Narrative Fallacy — events fitted retrospectively into a story that overstates predictability"
   - "Anchoring Bias — price target anchored to a recent level rather than a fundamental range"
   - "Availability Heuristic — overweighting the most recent geopolitical event"
   - "Base Rate Neglect — scenario probabilities assigned without reference to historical base rates"
   If no material fallacies are present, return an empty list.

2. **analytical_gaps** — Specific, material pieces of analysis absent from the
   trade that a rigorous assessment would require.  Name the missing analysis
   precisely (e.g., "No FX sensitivity analysis for USD exposure in non-US revenues",
   "Regulatory risk from pending CFIUS review not addressed").

3. **steelman** — The strongest possible case against this trade in 2-3 sentences
   of bureaucratic prose.  This is the best version of the opposing view,
   not a generic risk disclaimer.

4. **verdict** — Overall assessment of the logical integrity in 1-2 sentences.
   Is the signal-to-mechanism causal chain internally consistent?  Are the
   scenario probabilities well-calibrated relative to the stated evidence?

5. **conviction_adjustment** — Based on the review: 'Maintain', 'Downgrade',
   or 'Upgrade' to the stated conviction level, with a brief explanatory clause.

Output valid JSON conforming to the TradeLogicReview schema.
"""


SINGLE_QUANT_ANALYSIS_PROMPT = """\
## TASK: QUANTITATIVE TRADE ANALYSIS

**Briefing Date:** {today}

### TRADE (JSON)
{trade_json}

### LIVE FINANCIAL MARKET DATA
{financial_data}

### HISTORICAL CONTEXT (180-Day Longitudinal Record)
{historical_context}

### CROSS-ASSET PRICE CORRELATIONS (30-Day)
{correlation_matrix}

---

## INSTRUCTIONS

Produce one complete QuantAnalysis for the trade above.

**valuation_summary:** Pull current valuation metrics from the live financial
data.  Compare to sector peers and historical averages.  Name specific comps
and relative premium/discount.

**non_consensus_view:** Articulate precisely what the consensus is missing.
Reference specific positioning data, analyst estimates, or structural dynamics
that the market is mispricing.  This must be differentiated — not a restatement
of the trade thesis.

**scenarios:** Build three explicit scenarios with probability-weighted math.
Probabilities must sum to 100%.  Target prices must be specific dollar figures.
The expected_value field must show the arithmetic explicitly.

**invalidation:** Name a specific price level (e.g., "weekly close below $138")
or event (e.g., "FDA rejection of NDA filing") that definitively invalidates
the thesis.  Not a general risk — a precise trip-wire.

**crowding:** Use institutional ownership data from the financial data provided.
Characterize the current positioning regime and whether the trade is consensus-
crowded or off-the-run.

**short_interest:** Quote the specific short interest percentage, days-to-cover,
and note the trend direction.

**seasonality:** Identify the most relevant seasonal pattern for this instrument
or its sector.  Cite a specific historical win-rate and average return if
the data supports it.

**entry_strategy:** Provide a specific, actionable entry approach with levels.
Address whether to enter all at once or scale in, and why.

**instruments:** List every instrument that could express this trade, from the
primary equity to options structures to ETF overlays.  Include specific
options strikes and expiries where relevant.

**hedge_plan:** Write a 2-4 sentence narrative hedge plan.  Name specific
instruments (tickers, options strikes/expiries), approximate sizing relative to
the core position, the mechanism by which each hedge offsets the primary risk,
and the conditions under which the hedge should be added, adjusted, or removed.

Output valid JSON conforming to the QuantAnalysis schema.
"""


SINGLE_TACTICAL_QUANT_PROMPT = """\
## TASK: TACTICAL TRADE QUANTITATIVE SUMMARY

**Briefing Date:** {today}

### TRADE (JSON)
{trade_json}

### LIVE FINANCIAL MARKET DATA
{financial_data}

---

## INSTRUCTIONS

Produce one TacticalQuant for the trade above.

**is_options_trade:** True if this trade should be expressed through options.
For event-driven catalysts with defined binary outcomes, options are preferred.

**option_details:** If is_options_trade is True, specify the exact contract:
exchange-qualified ticker, strike, expiry, and estimated premium
(e.g., "NASDAQ:KTOS $25C 18-Apr-2025 ~$1.50 debit").

**expected_move:** Quantify the expected move over the tactical horizon
as a percentage range (e.g., "+8–12% over 3–5 trading days").

**risk_reward:** Calculate the risk/reward ratio from entry to target vs. entry
to stop (e.g., "3.2:1").

**catalyst_date:** If the thesis is event-driven, provide the ISO date of the
catalyst.

Output valid JSON conforming to the TacticalQuant schema.
"""


# ── Tiered Summarizer Prompts ─────────────────────────────────────────────────
# Used by the pre-Phase-A map-reduce pipeline to convert raw intelligence
# (up to 150k characters) into a ~12k Master Intelligence Map before any
# downstream analytical calls are made.
#
# CHUNK_SUMMARY_PROMPT (Map phase):   one call per ~40k-char chunk
# MERGE_SUMMARIES_PROMPT (Reduce):   one call to synthesize all chunk summaries

CHUNK_SUMMARY_PROMPT = """\
## TASK: INTELLIGENCE CHUNK SUMMARIZATION

**Chunk {chunk_index} of {total_chunks}**

You are performing the Map phase of a multi-stage intelligence summarization.
Your role is signals extraction, not narrative prose.  Compress this chunk into
a high-density analytical summary while preserving every specific data point.

### RAW INTELLIGENCE CHUNK
{chunk_text}

---

## MANDATORY PRESERVATION RULES

You MUST NOT drop or paraphrase away any of the following:
- All named persons — full name and title/role as they appear in the source
- All named organizations, state actors, government bodies, and institutions
- All ticker symbols, ISINs, CUSIPs, and instrument identifiers
- All numerical figures: prices, percentages, rates, quantities, capacities, dates
- All named events, treaties, legislation, regulatory actions, and policy programs
- All named geographic locations material to any development

## OUTPUT STRUCTURE

**Geopolitical Developments:**
All active geopolitical events with named actors, locations, dates, and outcomes.
Preserve policy language and official statements verbatim where material.

**Macroeconomic and Policy Signals:**
Central bank actions, data releases, fiscal measures, rate decisions.
Preserve all figures, rate paths, and forward guidance language verbatim.

**Corporate and Sector Signals:**
Earnings, guidance, M&A, supply-chain, and regulatory events.
Name all tickers (EXCHANGE:TICKER format), figures, and named executives.

**Named Data Point Index:**
A bullet list of every specific entity, ticker, figure, and date in this chunk —
a lossless reference index.  Format: item — context.
"""


MERGE_SUMMARIES_PROMPT = """\
## TASK: MASTER INTELLIGENCE MAP SYNTHESIS

You have received {n_chunks} chunk summaries from today's raw intelligence
collection.  Synthesize them into a single **Master Intelligence Map** of
approximately {target_chars} characters that will serve as the primary
analytical context for all downstream briefing generation tasks.

### CHUNK SUMMARIES
{chunk_summaries}

---

## ANALYTICAL LOSSLESSNESS — ABSOLUTE REQUIREMENT

The Master Intelligence Map MUST preserve EVERY data point that appeared in
ANY of the input chunk summaries.  You may NOT drop, consolidate away, or
paraphrase to the point of losing:
- Named persons, organizations, and state actors
- Ticker symbols, ISINs, and all instrument identifiers
- All numerical figures, percentages, prices, and quantities
- All dates, timelines, and deadlines
- Named events, legislation, treaties, and regulatory actions
- Named geographic locations

When the same entity or event appears in multiple chunks, merge the data points —
do not drop any detail that appeared in any chunk.

## OUTPUT STRUCTURE

### 1. Priority Geopolitical Developments
Major state-level and multilateral developments with direct market implications.
Name all specific actors, events, dates, and policy language verbatim.

### 2. Macroeconomic and Monetary Policy Signals
Central bank communications, economic data releases, fiscal policy, and
monetary transmission dynamics.  Preserve all figures and forward guidance
language verbatim.

### 3. Corporate and Sector Intelligence
Earnings, guidance, M&A, regulatory actions, supply-chain developments.
Name all tickers (EXCHANGE:TICKER format), figures, and named executives verbatim.

### 4. Market Structure Signals
Options flow, positioning data, technical levels, short interest, and
institutional flow indicators.  Preserve all numerical data verbatim.

### 5. Named Data Point Inventory
A consolidated bullet index of every named entity, ticker, figure, and date
from today's intelligence across all chunks.  Format: item — context.
One bullet per distinct data point.  This is the authoritative reference index.

Analytical losslessness takes precedence over brevity.
If preserving all data points requires exceeding {target_chars} characters,
err on the side of completeness.
"""


# ── Builder ───────────────────────────────────────────────────────────────────

class _SafeFormatMap(dict):
    """Return the literal placeholder for any missing key instead of raising."""
    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def build_prompt(
    template: str,
    raw_intelligence: str,
    historical_context: str = "No historical context available — this appears to be the first run.",
    financial_data: str = "Live financial market data was not retrieved for this prompt.",
    priority_themes_json: str = "{}",
    excluded_tickers: str = "None — this is the first trade section generated.",
    economic_calendar: str = "No economic calendar data available.",
    earnings_calendar: str = "No earnings calendar data available.",
    trade_and_quant_json: str = "{}",
    trade_number: int = 1,
    today: Optional[str] = None,
    correlation_matrix: str = "No correlation data available.",
    trade_json: str = "{}",
) -> str:
    """
    Inject all context variables into a prompt template.

    Extra keys provided but absent from the template are silently ignored.
    Missing keys (e.g., {financial_data} not in opening sections prompt) are
    left as literal text rather than raising KeyError.
    """
    if today is None:
        today = date.today().isoformat()

    values = _SafeFormatMap(
        analytical_standards="",   # now injected as system_instruction, not prompt body
        today=today,
        raw_intelligence=raw_intelligence,
        historical_context=historical_context,
        financial_data=financial_data,
        priority_themes_json=priority_themes_json,
        excluded_tickers=excluded_tickers,
        economic_calendar=economic_calendar,
        earnings_calendar=earnings_calendar,
        trade_and_quant_json=trade_and_quant_json,
        trade_number=str(trade_number),
        correlation_matrix=correlation_matrix,
        trade_json=trade_json,
    )
    return template.format_map(values)
