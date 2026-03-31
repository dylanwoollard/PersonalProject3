"""
compiler.py — HTML assembly and JSON aggregation (Level 2).

Public API:
  compile_html_document  — Render all Pydantic objects into one HTML page.
  aggregate_json_output  — Merge all Pydantic objects into a master dict.

All HTML is generated in Python from structured data — Gemini never writes
raw HTML, ensuring deterministic, well-formed output.
"""

from __future__ import annotations

import html as _html
from datetime import date
from typing import Optional

from models import (
    AdversarialAssessment,
    AppendixOutput,
    OpeningSections,
    PositionalTradeSet,
    PriorityThemes,
    RiskLevel,
    StrategicTrade,
    TacticalTradeSet,
    Trade,
    TradeLogicReview,
)


# ── Stylesheet ────────────────────────────────────────────────────────────────
# Written as a plain string (not an f-string) so CSS curly braces are literal.

_STYLES = """
<style>
  :root {
    --bg-primary:    #ffffff;
    --bg-secondary:  #f3f4f5;
    --bg-card:       #ffffff;
    --bg-accent:     #f3f4f5;
    --border:        #cccccc;
    --border-accent: #051c2c;
    --text-primary:  #111111;
    --text-secondary:#444444;
    --text-muted:    #757575;
    --mck-navy:      #051c2c;
    --mck-blue:      #00609c;
    --red:           #b22a2a;
    --green:         #2a7a40;
    --font-serif:    Georgia, 'Times New Roman', serif;
    --font-mono:     'Courier New', Courier, monospace;
    --font-sans:     Arial, 'Helvetica Neue', sans-serif;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg-primary);
    color: var(--text-primary);
    font-family: var(--font-serif);
    font-size: 13px;
    line-height: 1.7;
    max-width: 1080px;
    margin: 0 auto;
    padding: 48px 24px 80px;
  }

  /* ── Typography ── */
  p { margin-bottom: 10px; }
  p:last-child { margin-bottom: 0; }

  h2 {
    font-family: var(--font-sans);
    font-size: 17px;
    font-weight: 700;
    color: var(--mck-navy);
    border-left: none;
    padding-left: 0;
    margin-bottom: 16px;
    text-transform: none;
    break-after: avoid;
    page-break-after: avoid;
  }

  h3 {
    font-family: var(--font-sans);
    font-size: 16px;
    font-weight: 700;
    color: var(--text-primary);
    border-bottom: 1px solid var(--border);
    padding-bottom: 4px;
    margin-bottom: 12px;
  }

  /* ── Utility classes ── */
  .label {
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: var(--mck-navy);
    text-transform: uppercase;
    display: block;
    margin-bottom: 4px;
  }

  .section-wrapper { margin-bottom: 56px; }

  hr.divider {
    border: none;
    border-top: 1px solid var(--border);
    margin: 56px 0;
  }

  /* ── Header ── */
  .briefing-header {
    text-align: center;
    border-bottom: 2px solid var(--mck-blue);
    padding-bottom: 28px;
    margin-bottom: 48px;
  }
  .classification-banner {
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 4px;
    color: var(--mck-blue);
    text-transform: uppercase;
    margin-bottom: 18px;
  }
  .briefing-title {
    font-size: 26px;
    font-weight: 700;
    letter-spacing: 1px;
    margin-bottom: 8px;
  }
  .briefing-date {
    font-family: var(--font-sans);
    font-size: 12px;
    letter-spacing: 3px;
    color: var(--text-secondary);
    text-transform: uppercase;
  }

  /* ── Epigraph ── */
  .epigraph {
    border-left: 3px solid var(--mck-blue);
    padding: 16px 24px;
    background: var(--bg-secondary);
    margin: 36px 0;
    font-style: italic;
    font-size: 17px;
    color: var(--text-secondary);
  }
  .epigraph-attribution {
    font-style: normal;
    font-family: var(--font-sans);
    font-size: 11px;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-top: 10px;
    display: block;
  }

  /* ── Cards ── */
  .card {
    background: transparent;
    border: none;
    border-top: 2px solid var(--mck-navy);
    border-radius: 0;
    padding: 24px 0;
    margin-bottom: 32px;
  }
  .card-gold { border-top-color: var(--mck-blue); border-left: none; }
  .card-red  { border-top-color: var(--red); break-inside: avoid; page-break-inside: avoid; }

  /* ── Adversarial Assessment & Logic Review blocks ── */
  .adversarial-card {
    border-top: 2px solid #e2e8f0;
    border-radius: 8px;
    padding: 1.5rem 2rem;
    margin-bottom: 28px;
    background: transparent;
    break-inside: avoid;
    page-break-inside: avoid;
  }
  .logic-review {
    border-top: 2px solid #e2e8f0;
    border-radius: 8px;
    padding: 1.5rem 2rem;
    margin-top: 8px;
    background: transparent;
    break-inside: avoid;
    page-break-inside: avoid;
  }
  .adversarial-card ul,
  .adversarial-card ol,
  .logic-review ul,
  .logic-review ol {
    margin-left: 1.5rem;
  }
  .adversarial-card li,
  .logic-review li {
    margin-left: 1.5rem;
  }

  /* ── Data-point list ── */
  .dp-list {
    list-style: none;
    padding: 0;
    margin: 12px 0;
  }
  .dp-list li {
    padding: 5px 0;
    display: flex;
    align-items: baseline;
    gap: 9px;
    color: var(--text-secondary);
    font-size: 13px;
    font-family: var(--font-serif);
    border-bottom: 1px solid var(--border);
  }
  .dp-list li:last-child { border-bottom: none; }
  .dp-list li::before {
    content: "■";
    color: var(--mck-navy);
    font-size: 7px;
    flex-shrink: 0;
    position: relative;
    top: -1px;
  }

  /* ── Causal flow (Signal → Mechanism → Impact) — table for PDF reliability ── */
  .causal-table {
    width: 100%;
    border-collapse: collapse;
    margin: 20px 0;
    border: 1px solid var(--border);
    break-inside: avoid;
    page-break-inside: avoid;
  }
  .causal-cell {
    border: 1px solid var(--border);
    vertical-align: top;
    width: 31%;
  }
  .causal-cell-label {
    display: block;
    font-family: var(--font-sans);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1.2px;
    color: var(--mck-navy);
    text-transform: uppercase;
    background: var(--bg-secondary);
    padding: 5px 12px;
    border-bottom: 1px solid var(--border);
  }
  .causal-cell-body {
    display: block;
    font-family: var(--font-serif);
    font-size: 12px;
    color: var(--text-primary);
    padding: 11px 13px;
    line-height: 1.5;
  }
  .causal-arrow-cell {
    text-align: center;
    vertical-align: middle;
    width: 3.5%;
    color: var(--mck-navy);
    font-size: 14px;
    font-weight: 700;
    padding: 0 2px;
    border: none;
    background: var(--bg-secondary);
  }

  /* ── Scenario analysis ── */
  .scenario-table {
    width: 100%;
    border-collapse: collapse;
    margin: 14px 0 0;
    border: 1px solid var(--border);
    break-inside: avoid;
    page-break-inside: avoid;
  }
  .scenario-table thead th {
    font-family: var(--font-sans);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--mck-navy);
    background: var(--bg-secondary);
    padding: 6px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    border-right: 1px solid var(--border);
  }
  .scenario-table thead th:last-child { border-right: none; }
  .scenario-table tbody tr { border-bottom: 1px solid var(--border); }
  .scenario-table tbody tr:last-child { border-bottom: none; }
  .scenario-table td {
    padding: 10px 12px;
    vertical-align: middle;
    border-right: 1px solid var(--border);
  }
  .scenario-table td:last-child { border-right: none; }
  .scenario-bull td:first-child { border-left: 3px solid #2a7a40; }
  .scenario-base td:first-child { border-left: 3px solid #666666; }
  .scenario-bear td:first-child { border-left: 3px solid #b22a2a; }
  .scenario-case-bull { font-family: var(--font-mono); font-size: 10px; font-weight: 700; letter-spacing: 1.5px; color: #2a7a40; }
  .scenario-case-base { font-family: var(--font-mono); font-size: 10px; font-weight: 700; letter-spacing: 1.5px; color: #666666; }
  .scenario-case-bear { font-family: var(--font-mono); font-size: 10px; font-weight: 700; letter-spacing: 1.5px; color: #b22a2a; }
  .scenario-table td:nth-child(2),
  .scenario-table td:nth-child(3) { text-align: center; }
  .scenario-price { font-family: var(--font-mono); font-size: 12px; font-weight: 600; color: var(--text-primary); white-space: nowrap; }
  .scenario-prob  { font-family: var(--font-mono); font-size: 12px; color: var(--text-secondary); white-space: nowrap; }
  .scenario-logic { font-family: var(--font-serif); font-size: 12px; color: var(--text-secondary); line-height: 1.5; }
  .scenario-ev {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    color: var(--text-primary);
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-top: none;
    padding: 7px 12px;
    margin-bottom: 14px;
  }

  /* ── Tags ── */
  .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .tag {
    font-family: var(--font-sans);
    font-size: 11px;
    font-weight: 600;
    padding: 4px 8px;
    background: #f8f9fa;
    border: 1px solid var(--border);
    color: var(--mck-blue);
    border-radius: 0;
  }
  .tag::after{
    content: "";
  }
  .tag:last-child::after { content: ""; }

  /* ── Risk badge ── */
  .badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 2px;
    font-size: 9px;
    font-family: var(--font-mono);
    letter-spacing: 2px;
    font-weight: 700;
    text-transform: uppercase;
    text-align: center;
  }
  .badge-LOW      { color: #4a8c5c; border: 1px solid #4a8c5c; background: rgba(74,140,92,.15);  }
  .badge-MEDIUM   { color: #b5a44a; border: 1px solid #b5a44a; background: rgba(181,164,74,.15); }
  .badge-HIGH     { color: #b57a4a; border: 1px solid #b57a4a; background: rgba(181,122,74,.15); }
  .badge-CRITICAL { color: #b54a4a; border: 1px solid #b54a4a; background: rgba(181,74,74,.15);  }

  /* ── Calendar ── */
  .cal-table { width: 100%; border-collapse: collapse; font-size: 13px; font-family: var(--font-serif); }
  .cal-table th {
    text-align: left; padding: 8px 12px;
    font-family: var(--font-mono); font-size: 9px; letter-spacing: 2px;
    text-transform: uppercase; color: var(--mck-blue);
    border-bottom: 1px solid var(--border);
  }
  .cal-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; color: var(--text-primary); }
  .cal-table tr { break-inside: avoid; page-break-inside: avoid; }
  .cal-table tr:last-child td { border-bottom: none; }
  .cal-date { font-family: var(--font-mono); color: var(--text-primary); white-space: nowrap; }

  /* ── Red cell ── */
  .redcell-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
  .redcell-prob-badge {
    display: inline-block;
    padding: 3px 10px;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--red);
    border: 1px solid var(--red);
    background: rgba(178,42,42,0.07);
    white-space: nowrap;
    flex-shrink: 0;
  }

  /* ── Theme rank ── */
  .theme-rank { font-family: var(--font-mono); font-size: 42px; color: var(--border-accent); line-height: 1; }

  /* ── Trade page (each trade starts on a new page) ── */
  .trade-page { break-before: page; page-break-before: always; margin-top: 0; }

  /* ── Trade card ── */
  .trade-card { background: none; border: none; border-radius: 0; overflow: visible; margin-bottom: 36px; break-inside: avoid; page-break-inside: avoid; }
  .trade-header {
    padding: 18px 0; background: none;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
  }
  .trade-last-price {
    font-family: var(--font-mono);
    font-size: 15px;
    font-weight: 700;
    color: var(--text-primary);
  }
  .trade-ticker  { font-family: var(--font-sans); font-size: 20px; font-weight: 700; letter-spacing: 0.3px; color: var(--mck-navy); }
  .trade-ticker-exchange { font-weight: 700; color: var(--mck-navy); }
  .trade-ticker-sep { margin: 0 10px; color: var(--border); font-weight: 300; }
  .trade-ticker-name { font-weight: 400; font-size: 15px; color: var(--text-secondary); }
  .trade-badges  { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .dir-LONG  { padding: 3px 10px; background: rgba(42,107,64,.12);  color: #1e5c30; border: 1px solid #1e5c30; border-radius: 2px; font-family: var(--font-mono); font-size: 10px; letter-spacing: 2px; }
  .dir-SHORT { padding: 3px 10px; background: rgba(139,42,42,.12);  color: #7a1f1f; border: 1px solid #7a1f1f; border-radius: 2px; font-family: var(--font-mono); font-size: 10px; letter-spacing: 2px; }
  .trade-type-badge { padding: 3px 8px; background: rgba(5,28,44,.06); color: var(--mck-navy); border: 1px solid var(--mck-navy); border-radius: 2px; font-family: var(--font-mono); font-size: 9px; letter-spacing: 2px; }
  .trade-body  { padding: 22px 0; }
  .trade-meta  { display: flex; gap: 36px; flex-wrap: wrap; margin-bottom: 22px; }
  .meta-item   { font-size: 13px; font-family: var(--font-serif); }
  .meta-label  { font-family: var(--font-sans); font-size: 10px; font-weight: 700; letter-spacing: 0.5px; color: var(--mck-navy); text-transform: uppercase; display: block; margin-bottom: 4px; }

  /* ── Market Snapshot ── */
  .market-snapshot {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: hidden;
    background: var(--bg-card);
    margin-bottom: 32px;
    flex-wrap: wrap;
  }
  .snapshot-tick {
    flex: 1;
    min-width: 130px;
    padding: 16px 20px;
    border-right: 1px solid var(--border);
  }
  .snapshot-tick:last-child { border-right: none; }
  .snapshot-label {
    font-family: var(--font-mono);
    font-size: 9px;
    letter-spacing: 1.5px;
    color: var(--mck-blue);
    text-transform: uppercase;
    display: block;
    margin-bottom: 6px;
  }
  .snapshot-price {
    font-family: var(--font-mono);
    font-size: 20px;
    font-weight: 700;
    color: var(--text-primary);
    display: block;
    margin-bottom: 4px;
  }
  .snapshot-change-pos  { color: var(--green); font-family: var(--font-sans); font-size: 12px; }
  .snapshot-change-neg  { color: var(--red);   font-family: var(--font-sans); font-size: 12px; }
  .snapshot-change-flat { color: var(--text-muted); font-family: var(--font-sans); font-size: 12px; }

  /* ── Overlay ── */
  .overlay-grid { display: grid; grid-template-columns: 1fr; gap: 12px; margin-top: 16px; break-inside: avoid; page-break-inside: avoid; }
  .overlay-box  { background: var(--bg-accent); border: 1px solid var(--border); border-radius: 4px; padding: 14px; break-inside: avoid; page-break-inside: avoid; }
  .overlay-box-label { font-family: var(--font-mono); font-size: 9px; letter-spacing: 2px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 8px; display: block; }

  /* ── Reading list ── */
  .reading-item { padding: 16px; border-bottom: 1px solid var(--border); display: flex; gap: 16px; align-items: flex-start; break-inside: avoid; page-break-inside: avoid; }
  .reading-item:last-child { border-bottom: none; }
  .reading-priority { font-family: var(--font-mono); font-size: 9px; letter-spacing: 1px; text-transform: uppercase; white-space: nowrap; margin-top: 2px; }
  .pri-ESSENTIAL    { color: var(--mck-blue);         }
  .pri-RECOMMENDED  { color: var(--mck-blue);         }
  .pri-SUPPLEMENTARY{ color: var(--text-muted);   }
  .reading-content  { flex: 1; }
  .reading-title    { font-weight: 600; color: var(--text-primary); margin-bottom: 3px; font-size: 14px; }
  .reading-source   { font-size: 11px; color: var(--text-muted); font-family: var(--font-sans); margin-bottom: 6px; }
  .reading-rel      { font-size: 13px; color: var(--text-secondary); font-family: var(--font-sans); }

  /* ── Book list ── */
  .book-item { padding: 14px 22px; border-bottom: 1px solid var(--border); break-inside: avoid; page-break-inside: avoid; }
  .book-item:last-child { border-bottom: none; }
  .book-category { font-family: var(--font-mono); font-size: 9px; letter-spacing: 1.5px; text-transform: uppercase; color: var(--mck-blue); margin-bottom: 4px; }
  .book-title  { font-weight: 600; color: var(--text-primary); font-size: 14px; margin-bottom: 2px; }
  .book-author { font-size: 12px; color: var(--text-muted); font-family: var(--font-sans); margin-bottom: 5px; }

  /* ── Upcoming earnings cards ── */
  .earnings-card-header {
    display: flex; justify-content: space-between; align-items: baseline;
    flex-wrap: wrap; gap: 6px;
    border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-bottom: 12px;
  }
  .earnings-company      { font-weight: 600; font-size: 1.05em; color: var(--text-primary); }
  .earnings-ticker-plain { font-size: 0.88em; font-weight: 600; color: var(--mck-blue); margin-left: 8px; }
  .earnings-date-plain   { font-size: 0.85em; color: var(--text-muted); }
  .earnings-tod {
    display: inline-block; padding: 1px 6px; font-size: 0.78em;
    font-family: var(--font-mono); letter-spacing: 0.5px;
    color: var(--mck-navy); border: 1px solid var(--border); margin-left: 6px;
  }
  .earnings-est-row  { display: flex; gap: 28px; flex-wrap: wrap; margin-bottom: 10px; font-size: 0.9em; }
  .earnings-est-item { display: flex; flex-direction: column; gap: 2px; }
  .earnings-est-label { font-family: var(--font-sans); font-size: 9px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.4px; color: var(--text-muted); }
  .earnings-est-value { font-weight: 600; font-size: 1em; color: var(--text-primary); }
  .earnings-section-label {
    font-family: var(--font-sans); font-size: 9px; font-weight: 700;
    letter-spacing: 0.5px; color: var(--mck-navy); text-transform: uppercase;
    display: block; margin: 10px 0 3px;
  }

  /* ── Recent earnings cards ── */
  .recent-card-beat { border-left: 3px solid var(--green) !important; padding-left: 16px; }
  .recent-card-miss { border-left: 3px solid var(--red)   !important; padding-left: 16px; }
  .earnings-badge-beat   { background: rgba(42,122,64,.10); color: var(--green); border: 1px solid var(--green); }
  .earnings-badge-miss   { background: rgba(178,42,42,.10); color: var(--red);   border: 1px solid var(--red); }
  .earnings-badge-inline { background: var(--bg-secondary); color: var(--text-secondary); border: 1px solid var(--border); }
  .earnings-badge {
    display: inline-block; padding: 2px 9px; font-size: 0.72em; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px; vertical-align: middle;
    margin-left: 8px; border-radius: 2px;
  }
  .earnings-beat  { color: var(--green); font-weight: 600; }
  .earnings-miss  { color: var(--red);   font-weight: 600; }
  .earnings-metrics-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 0;
    margin: 10px 0 12px; border: 1px solid var(--border);
  }
  .earnings-metric-cell {
    padding: 8px 12px; border-right: 1px solid var(--border);
  }
  .earnings-metric-cell:last-child { border-right: none; }
  .earnings-metric-label { font-family: var(--font-sans); font-size: 9px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.4px; color: var(--text-muted);
    display: block; margin-bottom: 4px; }
  .earnings-metric-actual { font-size: 1.05em; font-weight: 600; display: block; margin-bottom: 2px; }
  .earnings-metric-vs     { font-size: 0.82em; color: var(--text-secondary); display: block; margin-bottom: 2px; }
  .earnings-metric-surp   { font-size: 0.82em; font-weight: 600; display: block; }
  .earnings-analysis      { font-size: 0.92em; margin: 0; }
  .earnings-link          { font-size: 11px; color: var(--mck-blue); margin-top: 8px; display: inline-block; }

  /* ── Quant analysis side-by-side grid ── */
  .quant-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0;
    margin-top: 16px;
    border-top: 1px solid var(--border);
  }
  .quant-cell {
    padding: 14px 20px 14px 0;
    border-bottom: 1px solid var(--border);
  }
  .quant-cell:nth-child(even) {
    padding-left: 20px;
    padding-right: 0;
    border-left: 1px solid var(--border);
  }

  /* ── Trade meta inline strip ── */
  .trade-meta-strip  { display: flex; flex-wrap: wrap; align-items: center;
    font-size: 13px; gap: 0; margin-bottom: 8px; }
  .meta-strip-item   { white-space: nowrap; }
  .meta-sep          { color: var(--border); margin: 0 10px; font-weight: 300; font-size: 14px; }
  .trade-catalyst    { font-size: 13px; color: var(--text-secondary); margin-bottom: 18px;
    font-family: var(--font-serif); }

  /* ── Footer ── */
  .briefing-footer {
    text-align: center; padding: 40px 0 12px;
    color: var(--text-muted); font-family: var(--font-mono); font-size: 9px;
    letter-spacing: 2px; border-top: 1px solid var(--border); margin-top: 56px;
  }

  /* ── Responsive ── */
  @media (max-width: 720px) {
    .causal-flow  { flex-direction: column; }
    .causal-connector { transform: rotate(90deg); padding: 6px 0; }
    .scenario-grid { flex-direction: column; }
    .overlay-grid { grid-template-columns: 1fr; }
    .trade-meta   { flex-direction: column; gap: 10px; }
  }

  /* ── Print / PDF (McKinsey style) ── */
  @media print {
    @page {
      margin: 20mm 22mm 22mm 22mm;
      size: A4;
    }

    /* Base reset */
    *, *::before, *::after {
      color: #000000 !important;
      background: none !important;
      box-shadow: none !important;
      border-radius: 0 !important;
      text-shadow: none !important;
    }

    body {
      font-family: Georgia, 'Times New Roman', serif !important;
      background: #ffffff;
      font-size: 10pt;
      line-height: 1.35;
      padding: 0;
      max-width: none;
      color: #000;
    }

    /* Headers use Arial for visual hierarchy */
    h2, h3, .label, .snapshot-label, .meta-label, .overlay-box-label,
    .trade-ticker, .cal-table th, .reading-priority, .book-category,
    .causal-node-label, .section-label, .classification-banner,
    .briefing-date, .epigraph-attribution {
      font-family: Arial, Helvetica, sans-serif !important;
    }

    /* ── Hidden in PDF ── */
    .print-hide,
    .theme-rank,
    .trade-type-badge,
    .badge,
    .classification-banner,
    .briefing-footer,
    hr.divider { display: none !important; }

    /* ── Tags (entities / geographies / instruments) ── */
    .tags {
      display: flex !important;
      flex-wrap: wrap !important;
      gap: 4pt !important;
      margin-top: 4pt !important;
      margin-bottom: 4pt !important;
    }
    .tag {
      font-family: Arial, Helvetica, sans-serif !important;
      font-size: 8pt !important;
      font-weight: 400 !important;
      padding: 2pt 6pt !important;
      background: none !important;
      border: 1pt solid #aaaaaa !important;
      color: #000000 !important;
    }

    /* ── Document title block ── */
    .briefing-header {
      text-align: left;
      border-bottom: 4px solid var(--mck-navy);
      padding-bottom: 16px;
      margin-bottom: 48px;
    }
    .briefing-title {
    font-family: var(--font-sans);
    font-size: 32px;
    font-weight: 700;
    color: var(--mck-navy);
    }
    .briefing-date {
      font-size: 9pt;
      font-weight: normal;
      color: #555 !important;
      margin-top: 3pt;
      text-transform: uppercase;
      letter-spacing: 0.5pt;
    }

    /* ── Epigraph ── */
    .epigraph {
      border: none !important;
      border-left: 3pt solid #003366 !important;
      padding: 3pt 0 3pt 12pt !important;
      margin: 12pt 0 12pt 0 !important;
      font-style: italic;
      font-size: 9.5pt;
      color: #222 !important;
    }
    .epigraph-attribution {
      font-style: normal;
      font-size: 8.5pt;
      color: #555 !important;
      margin-top: 3pt;
    }

    /* ── Section labels (McKinsey small-cap rule) ── */
    .section-label {
      display: block !important;
      font-size: 7pt !important;
      font-weight: bold !important;
      color: #003366 !important;
      text-transform: uppercase !important;
      letter-spacing: 1.2pt !important;
      margin-bottom: 2pt !important;
    }

    /* ── Section headers ── */
    h2 {
      font-size: 13pt;
      font-weight: bold;
      color: #003366 !important;
      border: none !important;
      border-top: 2pt solid #003366 !important;
      padding: 5pt 0 2pt 0 !important;
      margin: 18pt 0 6pt 0 !important;
      text-transform: uppercase;
      letter-spacing: 0.4pt;
      break-after: avoid;
    }

    h3 {
      font-family: Arial, Helvetica, sans-serif !important;
      font-size: 12pt;
      font-weight: 700 !important;
      color: #000 !important;
      margin: 9pt 0 3pt 0;
      border-bottom: 0.75pt solid #ccc !important;
      padding-bottom: 2pt !important;
      break-after: avoid;
    }

    /* ── Strip card chrome — flat document layout ── */
    .card, .card-gold, .card-red,
    .red-cell-card, .overlay-box,
    .trade-card, .trade-header, .trade-body {
      border: none !important;
      padding: 0 !important;
      margin: 0 !important;
      background: none !important;
    }

    /* Thin ruled separator between entries */
    .card, .card-gold {
      border-top: 0.5pt solid #ccc !important;
      padding-top: 7pt !important;
      margin-bottom: 7pt !important;
    }
    .card-red, .red-cell-card {
      border-top: 2pt solid #aa0000 !important;
      padding-top: 7pt !important;
      margin-bottom: 7pt !important;
    }

    /* ── Trade cards ── */
    .trade-card {
      border-top: 1.5pt solid #003366 !important;
      padding-top: 7pt !important;
      margin-bottom: 14pt !important;
    }
    .trade-header {
      border-bottom: 0.5pt solid #ccc !important;
      padding-bottom: 4pt !important;
      margin-bottom: 5pt !important;
      display: flex !important;
      align-items: baseline !important;
      flex-wrap: wrap !important;
      gap: 5pt !important;
    }
    .trade-badges {
      display: flex !important;
      align-items: baseline !important;
      flex-wrap: wrap !important;
      gap: 5pt !important;
    }
    .trade-last-price {
      font-family: 'Courier New', Courier, monospace !important;
      font-size: 10pt !important;
      font-weight: bold !important;
      color: #000 !important;
    }
    .trade-ticker {
      font-family: Arial, Helvetica, sans-serif !important;
      font-size: 11pt;
      font-weight: bold;
      color: #003366 !important;
    }
    .trade-ticker-name {
      font-family: Arial, Helvetica, sans-serif !important;
      font-size: 10pt;
      font-weight: 400;
      color: #444 !important;
    }
    .trade-ticker-sep { color: #aaa !important; }
    .dir-LONG, .dir-SHORT {
      font-size: 8.5pt;
      font-weight: bold;
      border: none !important;
      padding: 0 !important;
      text-transform: uppercase;
      letter-spacing: 0.5pt;
    }
    .trade-meta {
      margin-bottom: 6pt !important;
    }
    .meta-label {
      font-weight: bold;
      font-size: 7.5pt;
      text-transform: uppercase;
      letter-spacing: 0.3pt;
      color: #555 !important;
      display: block;
    }
    .meta-item {
      font-size: 9pt;
      display: block;
      margin-bottom: 2pt !important;
    }
    p {
      margin-bottom: 5pt !important;
      margin-top: 0 !important;
    }

    /* ── Causal table ── */
    .causal-table {
      width: 100% !important;
      border-collapse: collapse !important;
      margin: 6pt 0 !important;
      border: 0.5pt solid #aaaaaa !important;
      page-break-inside: avoid !important;
      break-inside: avoid !important;
    }
    .causal-cell { border: 0.5pt solid #aaaaaa !important; vertical-align: top !important; width: 31% !important; }
    .causal-cell-label {
      font-family: Arial, sans-serif !important;
      font-size: 7pt !important;
      font-weight: bold !important;
      color: #003366 !important;
      background: #f0f0f0 !important;
      padding: 3pt 7pt !important;
      border-bottom: 0.5pt solid #aaaaaa !important;
      display: block !important;
    }
    .causal-cell-body {
      font-family: Georgia, serif !important;
      font-size: 8.5pt !important;
      padding: 6pt 8pt !important;
      color: #000 !important;
      display: block !important;
      line-height: 1.4 !important;
    }
    .causal-arrow-cell {
      text-align: center !important;
      vertical-align: middle !important;
      font-size: 10pt !important;
      color: #003366 !important;
      background: #f0f0f0 !important;
      padding: 0 2pt !important;
      border: none !important;
    }
    /* ── Scenario table ── */
    .scenario-table {
      width: 100% !important;
      border-collapse: collapse !important;
      margin: 4pt 0 0 !important;
      border: 0.5pt solid #aaaaaa !important;
      page-break-inside: avoid !important;
      break-inside: avoid !important;
    }
    .scenario-table thead th {
      font-family: Arial, sans-serif !important;
      font-size: 7pt !important;
      background: #f0f0f0 !important;
      padding: 3pt 6pt !important;
      border-bottom: 0.5pt solid #aaaaaa !important;
      border-right: 0.5pt solid #aaaaaa !important;
    }
    .scenario-table td {
      font-size: 8.5pt !important;
      padding: 5pt 7pt !important;
      border-right: 0.5pt solid #aaaaaa !important;
      vertical-align: middle !important;
    }
    .scenario-table tbody tr { border-bottom: 0.5pt solid #dddddd !important; }
    .scenario-price { font-size: 9pt !important; font-weight: 600 !important; text-align: center !important; }
    .scenario-prob  { font-size: 9pt !important; color: #444 !important; text-align: center !important; }
    .scenario-logic { font-family: Georgia, serif !important; font-size: 8.5pt !important; line-height: 1.35 !important; }
    .scenario-ev { font-size: 8pt !important; font-weight: bold !important; padding: 3pt 7pt !important; border-top: 0.5pt solid #aaa !important; margin-bottom: 5pt !important; }

    /* ── Data-point lists ── */
    .dp-list li {
      font-size: 10pt;
      font-family: Georgia, 'Times New Roman', serif !important;
      border: none !important;
      padding: 1.5pt 0 !important;
      color: #000 !important;
      display: flex !important;
      align-items: baseline !important;
      gap: 6pt !important;
    }
    .dp-list li::before { color: #003366 !important; font-size: 6pt !important; flex-shrink: 0 !important; position: static !important; }

    /* ── Transmission overlays: single column ── */
    .overlay-grid {
      display: block !important;
    }
    .overlay-box {
      border-top: 0.5pt solid #ccc !important;
      padding-top: 5pt !important;
      margin-bottom: 6pt;
    }
    .overlay-box-label {
      font-weight: bold;
      font-size: 7.5pt;
      text-transform: uppercase;
      letter-spacing: 0.8pt;
      color: #003366 !important;
      display: block;
      margin-bottom: 2pt;
    }

    /* ── Red cell ── */
    .redcell-header { display: flex; flex-wrap: wrap; gap: 6pt; align-items: flex-start; }
    .redcell-prob-badge {
      font-size: 8pt !important;
      font-weight: bold !important;
      color: #aa0000 !important;
      border: 1pt solid #aa0000 !important;
      background: none !important;
      padding: 2pt 6pt !important;
    }

    /* ── Calendar table (McKinsey ruled table) ── */
    .cal-table {
      border-collapse: collapse;
      width: 100%;
      font-size: 9pt;
    }
    .cal-table th {
    background: var(--mck-navy);
    color: #ffffff;
    border: none;
    }
    .cal-table td {
      border: none !important;
      border-bottom: 0.5pt solid #e0e0e0 !important;
      padding: 3pt 6pt !important;
      color: #000 !important;
    }
    .cal-date { font-weight: bold; color: #003366 !important; white-space: nowrap; }
    .cal-time { font-size: 8pt; color: #555 !important; display: block; margin-top: 1pt; }
    .cal-prev, .cal-fcst { white-space: nowrap; font-variant-numeric: tabular-nums; }
    .cal-fcst { font-weight: 600; }
    .cal-miss  { color: #c0392b !important; }
    .cal-beat  { color: #1a7a3e !important; }

    /* ── Earnings cards (print) ── */
    .earnings-card-header {
      display: flex !important; justify-content: space-between !important;
      border-bottom: 0.5pt solid #ccc !important;
      padding-bottom: 5pt !important; margin-bottom: 6pt !important;
    }
    .earnings-company     { font-weight: bold !important; font-size: 10pt !important; }
    .earnings-ticker-plain { font-size: 8.5pt !important; color: #003366 !important; margin-left: 6pt !important; }
    .earnings-date-plain  { font-size: 8pt !important; color: #555 !important; }
    .earnings-tod         { font-size: 7pt !important; border: 0.5pt solid #999 !important; padding: 0 3pt !important; margin-left: 4pt !important; }
    .earnings-est-row     { display: flex !important; gap: 18pt !important; margin-bottom: 6pt !important; }
    .earnings-est-label   { font-size: 7pt !important; font-weight: bold !important; color: #555 !important; display: block !important; }
    .earnings-est-value   { font-size: 9.5pt !important; font-weight: bold !important; }
    .earnings-section-label { font-size: 7.5pt !important; font-weight: bold !important; color: #003366 !important; display: block !important; margin: 5pt 0 2pt !important; }
    .earnings-badge {
      display: inline-block !important; padding: 1pt 5pt !important;
      font-size: 7pt !important; font-weight: bold !important;
      text-transform: uppercase !important; letter-spacing: 0.5pt !important;
      border: 0.5pt solid !important; margin-left: 5pt !important;
    }
    .earnings-badge-beat   { color: #1a7a3e !important; border-color: #1a7a3e !important; }
    .earnings-badge-miss   { color: #c0392b !important; border-color: #c0392b !important; }
    .earnings-badge-inline { color: #666 !important; border-color: #999 !important; }
    .earnings-beat  { color: #1a7a3e !important; font-weight: bold !important; }
    .earnings-miss  { color: #c0392b !important; font-weight: bold !important; }
    .recent-card-beat { border-left: 2pt solid #1a7a3e !important; padding-left: 8pt !important; }
    .recent-card-miss { border-left: 2pt solid #c0392b !important; padding-left: 8pt !important; }
    .earnings-metrics-grid {
      display: grid !important; grid-template-columns: 1fr 1fr !important;
      border: 0.5pt solid #aaa !important; margin: 5pt 0 8pt !important;
    }
    .earnings-metric-cell { padding: 5pt 8pt !important; border-right: 0.5pt solid #aaa !important; }
    .earnings-metric-cell:last-child { border-right: none !important; }
    .earnings-metric-label  { font-size: 7pt !important; font-weight: bold !important; color: #555 !important; display: block !important; margin-bottom: 2pt !important; }
    .earnings-metric-actual { font-size: 9pt !important; font-weight: bold !important; display: block !important; }
    .earnings-metric-vs     { font-size: 8pt !important; color: #555 !important; display: block !important; }
    .earnings-metric-surp   { font-size: 8pt !important; font-weight: bold !important; display: block !important; }
    .earnings-analysis      { font-size: 8.5pt !important; margin: 0 !important; line-height: 1.35 !important; }
    .earnings-link          { display: none !important; }

    /* ── Trade meta inline (print) ── */
    .trade-meta-strip { display: flex !important; flex-wrap: wrap !important; gap: 0 !important; font-size: 8.5pt !important; margin-bottom: 5pt !important; border-bottom: 0.5pt solid #ccc !important; padding-bottom: 4pt !important; }
    .meta-strip-item  { white-space: nowrap !important; }
    .meta-sep         { color: #aaa !important; margin: 0 6pt !important; }
    .trade-catalyst   { font-size: 8.5pt !important; margin-bottom: 8pt !important; }

    /* ── Quant grid (print) ── */
    .quant-grid {
      display: grid !important; grid-template-columns: 1fr 1fr !important;
      border-top: 0.5pt solid #aaa !important; margin-top: 8pt !important;
    }
    .quant-cell {
      padding: 7pt 12pt 7pt 0 !important;
      border-bottom: 0.5pt solid #ddd !important;
      font-size: 8.5pt !important;
    }
    .quant-cell:nth-child(even) {
      padding-left: 12pt !important; padding-right: 0 !important;
      border-left: 0.5pt solid #aaa !important;
    }

    /* ── Reading list ── */
    .reading-item {
      border: none !important;
      border-top: 0.5pt solid #ccc !important;
      padding: 5pt 0 !important;
      display: block !important;
    }
    .reading-priority {
      font-weight: bold;
      font-size: 7.5pt;
      text-transform: uppercase;
      letter-spacing: 0.5pt;
      color: #003366 !important;
      margin-bottom: 2pt;
    }
    .reading-title  { font-weight: bold; font-size: 9.5pt; }
    .reading-source { font-style: italic; font-size: 8.5pt; color: #555 !important; }
    .reading-rel    { font-size: 9pt; }

    /* ── Section spacing ── */
    .section-wrapper { margin-bottom: 14pt; }

    /* ── Page-break control ── */
    h2 { break-before: auto; break-after: avoid; }
    h3  { break-after: avoid; }
    .trade-card, .red-cell-card, .reading-item { break-inside: avoid !important; page-break-inside: avoid !important; }
    .card { break-inside: auto; }
    p { orphans: 3; widows: 3; }

    /* ── Market snapshot ── */
    .market-snapshot {
      display: flex !important;
      flex-wrap: nowrap !important;
      border: none !important;
      border-bottom: 1.5pt solid #003366 !important;
      padding-bottom: 6pt !important;
      margin-bottom: 12pt !important;
      background: none !important;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
    .snapshot-tick {
      flex: 1;
      border: none !important;
      padding: 0 10pt 0 0 !important;
      background: none !important;
    }
    .snapshot-label {
      font-size: 7pt !important;
      font-weight: bold !important;
      color: #003366 !important;
      letter-spacing: 0.4pt !important;
      display: block !important;
      margin-bottom: 2pt !important;
    }
    .snapshot-price {
      font-size: 11pt !important;
      font-weight: bold !important;
      color: #000 !important;
      display: block !important;
      margin-bottom: 1pt !important;
    }
    .snapshot-change-pos {
      font-size: 8pt !important;
      color: #2d7a2d !important;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
    .snapshot-change-neg {
      font-size: 8pt !important;
      color: #cc0000 !important;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
    .snapshot-change-flat { font-size: 8pt !important; color: #555 !important; }

    /* ── Book list ── */
    .book-item {
      border: none !important;
      border-top: 0.5pt solid #ccc !important;
      padding: 4pt 0 !important;
      display: block !important;
    }
    .book-category {
      font-size: 7pt !important;
      font-weight: bold !important;
      color: #003366 !important;
      text-transform: uppercase !important;
      letter-spacing: 0.4pt !important;
    }
    .book-title  { font-weight: bold; font-size: 9.5pt; }
    .book-author { font-style: italic; font-size: 8.5pt; color: #555 !important; }
    .book-rel    { font-size: 9pt; }
  }

  /* ── Correlation matrix table ── */
  .correlation-matrix-table {
    width: 100%;
    border-collapse: collapse;
    font-family: var(--font-sans);
    font-size: 0.85rem;
    margin-top: 12px;
  }
  .correlation-matrix-table th,
  .correlation-matrix-table td {
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
    text-align: left;
  }
  .correlation-matrix-table thead th {
    font-weight: 700;
    color: var(--mck-navy);
    border-bottom: 2px solid var(--mck-navy);
    text-align: center;
  }
  .correlation-matrix-table thead th:first-child { text-align: left; }
  .correlation-matrix-table tbody th {
    font-weight: 700;
    color: var(--text-secondary);
    text-align: left;
  }
  .correlation-matrix-table tbody tr:last-child td,
  .correlation-matrix-table tbody tr:last-child th { border-bottom: none; }
</style>
"""


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _e(text: str) -> str:
    """HTML-escape user-supplied text."""
    return _html.escape(str(text))


def _dp_list(items: list[str]) -> str:
    return "<ul class='dp-list'>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>"


def _tags(items: list[str]) -> str:
    return "<div class='tags'>" + "".join(f"<span class='tag'>{_e(i)}</span>" for i in items) + "</div>"


def _causal(signal: str, mechanism: str, impact: str) -> str:
    def _cell(label: str, text: str) -> str:
        return (
            "<td class='causal-cell'>"
            f"<span class='causal-cell-label'>{label}</span>"
            f"<span class='causal-cell-body'>{_e(text)}</span>"
            "</td>"
        )
    return (
        "<table class='causal-table'><tr>"
        + _cell("Signal", signal)
        + "<td class='causal-arrow-cell'>&#8594;</td>"
        + _cell("Mechanism", mechanism)
        + "<td class='causal-arrow-cell'>&#8594;</td>"
        + _cell("Market Impact", impact)
        + "</tr></table>"
    )

def _render_market_snapshot(ticks: list) -> str:
    """Render a 4-tile market snapshot bar at the top of the briefing."""
    if not ticks:
        return ""
    items = []
    for tick in ticks:
        if tick.price is None:
            continue
        if tick.label == "10Y YIELD":
            price_str = f"{tick.price:.3f}%"
        elif tick.label in ("DXY INDEX",):
            price_str = f"{tick.price:.2f}"
        else:
            price_str = f"{tick.price:,.2f}"
        if tick.change_pct is not None:
            sign = "+" if tick.change_pct >= 0 else ""
            css = "snapshot-change-pos" if tick.change_pct >= 0 else "snapshot-change-neg"
            change_html = f"<span class='{css}'>{sign}{tick.change_pct:.2f}%</span>"
        else:
            change_html = "<span class='snapshot-change-flat'>—</span>"
        items.append(
            f"<div class='snapshot-tick'>"
            f"<div class='snapshot-label'>{_e(tick.label)}</div>"
            f"<div class='snapshot-price'>{price_str}</div>"
            f"{change_html}"
            f"</div>"
        )
    if not items:
        return ""
    return "<div class='market-snapshot'>" + "".join(items) + "</div>"


# ── Section Renderers ─────────────────────────────────────────────────────────

def _render_header(briefing_date: date) -> str:
    formatted = briefing_date.strftime("%A, %d %B %Y").upper()
    return (
        "<div class='briefing-header'>"
        "<div class='briefing-title'>Daily Brief</div>"
        f"<div class='briefing-date'>{formatted}</div>"
        "</div>"
    )


def _render_epigraph(opening: OpeningSections) -> str:
    return (
        f"<blockquote class='epigraph'>{_e(opening.epigraph)}"
        f"<span class='epigraph-attribution'>&mdash; {_e(opening.epigraph_attribution)}</span>"
        "</blockquote>"
    )


def _render_developing_situations(opening: OpeningSections) -> str:
    parts = [
        "<div class='section-wrapper' id='developing-situations'>",
        "<h2>Developing Situations</h2>",
    ]
    for sit in opening.developing_situations:
        trajectory_block = (
            "<div style='margin-top:14px;'>"
            "<span class='label'>Trajectory</span>"
            f"<p style='margin:4px 0 0;'>{_e(sit.trajectory)}</p>"
            "</div>"
        )
        entity_block = ""
        if sit.entities_involved:
            entity_block += "<span class='label' style='margin-top:12px;'>Entities</span>" + _tags(sit.entities_involved)
        if sit.geographies:
            entity_block += "<span class='label' style='margin-top:8px;'>Geographies</span>" + _tags(sit.geographies)
        parts.append(
            f"<div class='card card-gold'>"
            f"<h3>{_e(sit.title)}</h3>"
            f"<p>{_e(sit.narrative)}</p>"
            f"<span class='label' style='margin-top:14px;'>Intelligence Data Points</span>"
            + _dp_list(sit.key_data_points)
            + trajectory_block
            + (f"<div style='margin-top:12px;'>{entity_block}</div>" if entity_block else "")
            + "</div>"
        )
    parts.append("</div>")
    return "\n".join(parts)


_CAL_DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _cal_dow(date_str: str) -> str:
    """Return abbreviated day-of-week for an ISO date string, e.g. 'Wed'."""
    try:
        from datetime import date as _date
        d = _date.fromisoformat(date_str)
        return _CAL_DOW[d.weekday()][:3]
    except Exception:
        return ""


_MONTHS_SHORT = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


def _fmt_date(date_str: str) -> str:
    """Return '25 Mar 2026' for an ISO date string."""
    try:
        from datetime import date as _date
        d = _date.fromisoformat(date_str)
        return f"{d.day} {_MONTHS_SHORT[d.month - 1]} {d.year}"
    except Exception:
        return date_str


_TOD_ABBR = {
    "before market open": "BMO",
    "after market close": "AMC",
    "during market hours": "DMH",
}


def _fmt_tod(tod: str | None) -> str:
    """Abbreviate time-of-day: 'Before Market Open' → 'BMO'."""
    if not tod:
        return ""
    return _TOD_ABBR.get(tod.strip().lower(), tod)


_NO_CALENDAR_MSG = (
    "<p style='font-family:var(--font-sans);font-size:13px;color:var(--text-muted);'>"
    "No high-impact US macroeconomic or earnings events scheduled for this window.</p>"
)


def _render_calendar(opening: OpeningSections) -> str:
    if not opening.calendar_events:
        return (
            "<div class='section-wrapper' id='forward-calendar'>"
            "<h2>Forward Calendar</h2>"
            + _NO_CALENDAR_MSG
            + "</div>"
        )
    rows = []
    for ev in opening.calendar_events:
        date_cell = (
            f"<td class='cal-date'>{_e(_fmt_date(ev.date_str))}"
            + (f"<span class='cal-time'>{_e(ev.time_et)} ET</span>" if ev.time_et else "")
            + "</td>"
        )
        prev_cell = f"<td class='cal-prev'>{_e(ev.previous) if ev.previous else '—'}</td>"
        fcst_cell = f"<td class='cal-fcst'>{_e(ev.forecast) if ev.forecast else '—'}</td>"
        rows.append(
            f"<tr>"
            f"{date_cell}"
            f"<td>{_e(ev.event)}</td>"
            f"<td style='font-size:0.85em;color:var(--text-secondary);'>{_e(ev.entity)}</td>"
            f"{prev_cell}"
            f"{fcst_cell}"
            f"<td>{_e(ev.market_relevance)}</td>"
            f"</tr>"
        )
    return (
        "<div class='section-wrapper' id='forward-calendar'>"
        "<h2>Forward Calendar</h2>"
        "<div class='card' style='padding:0;overflow:hidden;'>"
        "<table class='cal-table'><thead><tr>"
        "<th>Date / Time</th><th>Release</th><th>Authority</th>"
        "<th>Previous</th><th>Forecast</th><th>Market Relevance</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></div>"
    )


def _render_upcoming_earnings(opening: OpeningSections) -> str:
    if not opening.upcoming_earnings:
        return (
            "<div class='section-wrapper' id='earnings-upcoming'>"
            "<h2>Earnings Calendar</h2>"
            + _NO_CALENDAR_MSG
            + "</div>"
        )
    cards = []
    for ev in opening.upcoming_earnings:
        tod = _fmt_tod(ev.time_of_day)
        date_html = _e(_fmt_date(ev.date_str))
        if tod:
            date_html += f"&ensp;<span class='earnings-tod'>{_e(tod)}</span>"
        sa_url = f"https://seekingalpha.com/symbol/{_e(ev.ticker)}/earnings"

        est_items = []
        if ev.eps_estimate:
            est_items.append(
                f"<div class='earnings-est-item'>"
                f"<span class='earnings-est-label'>EPS Est.</span>"
                f"<span class='earnings-est-value'>{_e(ev.eps_estimate)}</span>"
                f"</div>"
            )
        if ev.revenue_estimate:
            est_items.append(
                f"<div class='earnings-est-item'>"
                f"<span class='earnings-est-label'>Revenue Est.</span>"
                f"<span class='earnings-est-value'>{_e(ev.revenue_estimate)}</span>"
                f"</div>"
            )

        cards.append(
            "<div class='card' style='break-inside:avoid;page-break-inside:avoid;'>"
            # ── Header: company + ticker | date + tod
            "<div class='earnings-card-header'>"
            f"<div>"
            f"<span class='earnings-company'>{_e(ev.company)}</span>"
            f"<span class='earnings-ticker-plain'>{_e(ev.ticker)}</span>"
            f"</div>"
            f"<span class='earnings-date-plain'>{date_html}</span>"
            "</div>"
            # ── Estimates
            + (f"<div class='earnings-est-row'>{''.join(est_items)}</div>" if est_items else "")
            # ── Theme relevance
            + "<span class='earnings-section-label'>Theme Relevance</span>"
            + f"<p class='earnings-analysis'>{_e(ev.theme_relevance)}</p>"
            # ── Watch
            + "<span class='earnings-section-label' style='margin-top:8px;'>Watch</span>"
            + f"<p class='earnings-analysis'>{_e(ev.watch_analysis)}</p>"
            # ── Link
            + f"<a href='{sa_url}' class='earnings-link' target='_blank'>Seeking Alpha Earnings &#8594;</a>"
            + "</div>"
        )
    return (
        "<div class='section-wrapper' id='earnings-upcoming'>"
        "<h2>Earnings Calendar</h2>"
        + "".join(cards)
        + "</div>"
    )


def _render_recent_earnings(opening: OpeningSections) -> str:
    if not opening.recent_earnings:
        return ""

    def _badge(result: str) -> str:
        r = result.upper()
        if r == "BEAT":
            return "<span class='earnings-badge earnings-badge-beat'>Beat</span>"
        if r == "MISS":
            return "<span class='earnings-badge earnings-badge-miss'>Miss</span>"
        return "<span class='earnings-badge earnings-badge-inline'>In-Line</span>"

    def _surp_class(result: str) -> str:
        r = result.upper()
        return "earnings-beat" if r == "BEAT" else ("earnings-miss" if r == "MISS" else "")

    cards = []
    for ev in opening.recent_earnings:
        badge    = _badge(ev.result)
        s_class  = _surp_class(ev.result)
        r        = ev.result.upper()
        card_cls = "recent-card-beat" if r == "BEAT" else ("recent-card-miss" if r == "MISS" else "")
        date_label = _fmt_date(ev.date_str)
        sa_url = f"https://seekingalpha.com/symbol/{_e(ev.ticker)}/earnings"

        def _metric_cell(label: str, actual, estimate, surprise, s_cls: str) -> str:
            if actual is None and estimate is None:
                return ""
            act  = f"<span class='earnings-metric-actual {s_cls}'>{_e(actual)}</span>" if actual else ""
            est  = f"<span class='earnings-metric-vs'>est. {_e(estimate)}</span>" if estimate else ""
            surp = f"<span class='earnings-metric-surp {s_cls}'>{_e(surprise)}</span>" if surprise else ""
            return (
                f"<div class='earnings-metric-cell'>"
                f"<span class='earnings-metric-label'>{label}</span>"
                f"{act}{est}{surp}"
                f"</div>"
            )

        eps_cell = _metric_cell("EPS", ev.eps_actual, ev.eps_estimate, ev.eps_surprise, s_class)
        rev_cell = _metric_cell("Revenue", ev.revenue_actual, ev.revenue_estimate, ev.revenue_surprise, s_class)
        metrics_html = (
            f"<div class='earnings-metrics-grid'>{eps_cell}{rev_cell}</div>"
            if eps_cell or rev_cell else ""
        )

        description_html = ""
        company_desc = getattr(ev, "company_description", None)
        if company_desc:
            description_html = (
                f"<p style='font-size:0.88em;color:var(--text-muted);margin:0 0 10px;"
                f"font-family:var(--font-serif);'>{_e(company_desc)}</p>"
            )

        cards.append(
            f"<div class='card {card_cls}' style='break-inside:avoid;page-break-inside:avoid;'>"
            # ── Header: company + ticker + badge | date
            "<div class='earnings-card-header'>"
            f"<div>"
            f"<span class='earnings-company'>{_e(ev.company)}</span>"
            f"<span class='earnings-ticker-plain'>{_e(ev.ticker)}</span>"
            f"{badge}"
            f"</div>"
            f"<span class='earnings-date-plain'>{_e(date_label)}</span>"
            "</div>"
            # ── Company description
            + description_html
            # ── Metrics grid
            + metrics_html
            # ── Analysis
            + f"<p class='earnings-analysis'>{_e(ev.analysis)}</p>"
            # ── Link
            + f"<a href='{sa_url}' class='earnings-link' target='_blank'>Seeking Alpha Earnings &#8594;</a>"
            + "</div>"
        )

    return (
        "<div class='section-wrapper' id='earnings-recent'>"
        "<h2>Recent Earnings</h2>"
        + "".join(cards)
        + "</div>"
    )


def _render_priority_themes(themes: PriorityThemes) -> str:
    parts = [
        "<div class='section-wrapper' id='priority-themes'>",
        "<h2>Priority Intelligence Themes</h2>",
    ]
    for theme in themes.themes:
        risk = theme.risk_level.value if isinstance(theme.risk_level, RiskLevel) else str(theme.risk_level)
        transmission_block = (
            "<div style='margin-top:16px;border-top:1px solid var(--border);padding-top:14px;'>"
            f"<span class='label'>Risk Transmission — {_e(theme.primary_channel)}</span>"
            f"<span class='badge badge-{risk}' style='margin-left:8px;'>{risk}</span>"
            f"<p style='margin-top:8px;'>{_e(theme.transmission_narrative)}</p>"
            "<div class='overlay-grid'>"
            "<div class='overlay-box'><span class='overlay-box-label'>Second-Order Effects</span>"
            + _dp_list(theme.second_order_effects)
            + "</div>"
            + "<div class='overlay-box'><span class='overlay-box-label'>Third-Order Effects</span>"
            + _dp_list(theme.third_order_effects)
            + "</div></div>"
            + ("<span class='label' style='margin-top:10px;'>Affected Sectors</span>" + _tags(theme.affected_sectors) if theme.affected_sectors else "")
            + "</div>"
        )
        parts.append(
            "<div class='card card-gold'>"
            "<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:12px;'>"
            f"<h3 style='margin-bottom:0;border-bottom:none;flex:1;'>{_e(theme.title)}</h3>"
            f"<span class='theme-rank'>{theme.rank:02d}</span>"
            "</div>"
            f"<p>{_e(theme.narrative)}</p>"
            "<span class='label' style='margin-top:14px;'>Intelligence Data Points</span>"
            + _dp_list(theme.key_data_points)
            + _causal(theme.signal, theme.mechanism, theme.market_impact)
            + "<span class='label' style='margin-top:12px;'>Affected Instruments</span>"
            + _tags(theme.affected_instruments)
            + "<span class='label' style='margin-top:8px;'>Entities</span>"
            + _tags(theme.entities_involved)
            + (
                "<span class='label' style='margin-top:8px;'>Geographies</span>"
                + _tags(theme.geographies)
                if getattr(theme, "geographies", None) else ""
            )
            + transmission_block
            + "</div>"
        )
    parts.append("</div>")
    return "\n".join(parts)


def _render_trade_card(trade: Trade, label: str = "") -> str:
    direction  = trade.direction.value  if hasattr(trade.direction,  "value") else str(trade.direction)
    asset_class = trade.asset_class.value if hasattr(trade.asset_class, "value") else str(trade.asset_class)

    label_html = (
        f"<span class='trade-type-badge'>{_e(label)}</span>" if label else ""
    )
    strip_parts = [
        f"<span class='meta-strip-item'><span class='meta-label'>Horizon</span>&nbsp;{_e(trade.time_horizon)}</span>",
        f"<span class='meta-strip-item'><span class='meta-label'>Asset Class</span>&nbsp;{_e(asset_class)}</span>",
    ]
    if getattr(trade, "last_price", None):
        strip_parts.append(
            f"<span class='meta-strip-item'><span class='meta-label'>Last Price</span>&nbsp;"
            f"<span class='trade-last-price'>{_e(trade.last_price)}</span></span>"
        )
    if getattr(trade, "avg_daily_volume", None):
        strip_parts.append(
            f"<span class='meta-strip-item'><span class='meta-label'>ADV</span>&nbsp;{_e(trade.avg_daily_volume)}</span>"
        )
    sep = "<span class='meta-sep'>·</span>"
    meta = (
        f"<div class='trade-meta-strip'>{sep.join(strip_parts)}</div>"
        f"<p class='trade-catalyst'><span class='meta-label'>Catalyst</span>&nbsp;{_e(trade.geopolitical_catalyst)}</p>"
    )
    entry_block = ""
    if any([trade.entry_notes, trade.target_notes, trade.stop_notes]):
        items = []
        if trade.entry_notes:  items.append(f"Entry: {_e(trade.entry_notes)}")
        if trade.target_notes: items.append(f"Target: {_e(trade.target_notes)}")
        if trade.stop_notes:   items.append(f"Stop: {_e(trade.stop_notes)}")
        entry_block = (
            "<span class='label' style='margin-top:16px;'>Level Notes</span>"
            + _dp_list(items)
        )

    company = getattr(trade, "company_name", None) or ""
    ticker_html = (
        f"<span class='trade-ticker'>"
        f"<span class='trade-ticker-exchange'>{_e(trade.instrument)}</span>"
        + (
            f"<span class='trade-ticker-sep'>|</span>"
            f"<span class='trade-ticker-name'>{_e(company)}</span>"
            if company else ""
        )
        + "</span>"
    )

    return (
        "<div class='trade-card'>"
        "<div class='trade-header'>"
        "<div class='trade-badges'>"
        + ticker_html
        + f"<span class='dir-{direction}'>{direction}</span>"
        + label_html
        + "</div>"
        + "</div>"
        + "<div class='trade-body'>"
        + meta
        + f"<p>{_e(trade.rationale)}</p>"
        + "<span class='label' style='margin-top:16px;'>Risk Factors</span>"
        + _dp_list(trade.risk_factors)
        + entry_block
        + "</div></div>"
    )


def _quant_cell(label: str, value: str) -> str:
    """Render a single labelled quant data cell. Module-level so other renderers can use it."""
    return (
        f"<div class='quant-cell'>"
        f"<span class='label'>{label}</span>"
        f"<p style='margin:4px 0 0;'>{_e(value)}</p>"
        f"</div>"
    )


def _render_quant_analysis(quant) -> str:
    """Render a QuantAnalysis block for strategic/positional trades."""
    if quant is None:
        return ""
    # Scenarios
    def _scenario_row(label: str, sc, case_class: str, row_class: str) -> str:
        return (
            f"<tr class='{row_class}'>"
            f"<td><span class='{case_class}'>{label}</span></td>"
            f"<td><span class='scenario-price'>{_e(sc.target_price)}</span></td>"
            f"<td><span class='scenario-prob'>{_e(sc.probability)}</span></td>"
            f"<td><span class='scenario-logic'>{_e(sc.logic)}</span></td>"
            f"</tr>"
        )
    scenarios_html = (
        "<div style='margin:14px 0;'>"
        "<span class='label'>Scenarios</span>"
        "<table class='scenario-table'>"
        "<thead><tr>"
        "<th>Case</th><th>Target</th><th>Prob</th><th>Logic</th>"
        "</tr></thead><tbody>"
        + _scenario_row("BULL", quant.scenarios.bull, "scenario-case-bull", "scenario-bull")
        + _scenario_row("BASE", quant.scenarios.base, "scenario-case-base", "scenario-base")
        + _scenario_row("BEAR", quant.scenarios.bear, "scenario-case-bear", "scenario-bear")
        + "</tbody></table>"
        f"<div class='scenario-ev'>Expected Value: {_e(quant.scenarios.expected_value)}</div>"
        "</div>"
    )
    hedge_plan = getattr(quant, "hedge_plan", None) or ""
    instruments = getattr(quant, "instruments", []) or []
    return (
        "<div class='card' style='margin-top:6px;'>"
        "<span class='label'>Quantitative Analysis</span>"
        + scenarios_html
        + "<div class='quant-grid'>"
        + _quant_cell("Valuation", quant.valuation_summary)
        + _quant_cell("Non-Consensus View", quant.non_consensus_view)
        + _quant_cell("Invalidation", quant.invalidation)
        + _quant_cell("Crowding", quant.crowding)
        + _quant_cell("Short Interest", quant.short_interest)
        + _quant_cell("Seasonality", quant.seasonality)
        + _quant_cell("Entry Strategy", quant.entry_strategy)
        + _quant_cell("Hedge Plan", hedge_plan)
        + "</div>"
        + (
            "<div style='margin-top:14px;'>"
            "<span class='label'>Instruments</span>"
            + _dp_list(instruments)
            + "</div>"
            if instruments else ""
        )
        + "</div>"
    )


def _render_tactical_quant_block(tq) -> str:
    """Render a TacticalQuant summary block."""
    if tq is None:
        return ""
    parts = ["<div style='margin-top:8px;padding:10px 14px;background:#f0f0f0;border:1px solid #cccccc;border-radius:3px;display:flex;flex-wrap:wrap;gap:16px;'>"]
    def _tq_item(label: str, val: str) -> str:
        return (
            f"<div style='min-width:100px;'>"
            f"<span class='label' style='margin-bottom:2px;'>{label}</span>"
            f"<span style='font-family:var(--font-mono);font-size:12px;color:#000000;'>{_e(val)}</span>"
            f"</div>"
        )
    if tq.is_options_trade and tq.option_details:
        parts.append(_tq_item("Options", tq.option_details))
    parts.append(_tq_item("Expected Move", tq.expected_move))
    parts.append(_tq_item("Risk/Reward", tq.risk_reward))
    if tq.catalyst_date:
        parts.append(_tq_item("Catalyst", tq.catalyst_date))
    parts.append("</div>")
    return "".join(parts)


def _render_adversarial_assessment(adversarial: AdversarialAssessment) -> str:
    """Render the adversarial scenario assessment section."""
    if adversarial is None:
        return ""

    scenario_cards = []
    for s in adversarial.scenarios:
        scenario_cards.append(
            "<div class='adversarial-card' style='border-left:3px solid var(--red);'>"
            f"<div style='font-family:var(--font-sans);font-size:13px;font-weight:700;"
            f"color:var(--red);margin-bottom:6px;'>{_e(s.title)}</div>"
            "<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;'>"
            + _quant_cell("Consensus View", s.consensus_view)
            + _quant_cell("Blind Spot", s.blind_spot)
            + "</div>"
            "<span class='label'>Adversarial Case</span>"
            f"<p style='margin-top:6px;'>{_e(s.adversarial_case)}</p>"
            "<span class='label' style='margin-top:10px;'>Trigger Conditions</span>"
            + _dp_list(s.trigger_conditions)
            + "<span class='label' style='margin-top:10px;'>Market Implications</span>"
            f"<p style='margin-top:6px;font-family:var(--font-mono);font-size:12px;'>{_e(s.market_implications)}</p>"
            "</div>"
        )

    return (
        "<div class='section-wrapper' id='adversarial-assessment'>"
        "<h2>Adversarial Assessment</h2>"
        "<div style='font-family:var(--font-sans);font-size:11px;color:var(--text-muted);"
        "margin-bottom:12px;'>Red-cell analysis: blind spots and contrarian risks in the priority themes.</div>"
        + "".join(scenario_cards)
        + "<div class='adversarial-card' style='border-left:3px solid var(--mck-navy);'>"
        "<span class='label' style='color:var(--mck-navy);'>Systemic Meta-Risk</span>"
        f"<p style='margin-top:8px;'>{_e(adversarial.meta_risk)}</p>"
        "</div>"
        "</div>"
    )


def _render_trade_logic_review(review: TradeLogicReview) -> str:
    """Render the trade logic review block inside the strategic trade section."""
    if review is None:
        return ""

    fallacies_html = (
        _dp_list(review.fallacies_identified)
        if review.fallacies_identified
        else "<p style='color:var(--text-muted);font-family:var(--font-sans);font-size:12px;'>No material fallacies identified.</p>"
    )

    adjustment_color = {
        "maintain": "var(--green)",
        "upgrade": "var(--green)",
        "downgrade": "var(--red)",
    }.get(review.conviction_adjustment.lower().split()[0], "var(--text-primary)")

    return (
        "<div class='logic-review' style='border-left:3px solid #888;'>"
        "<span class='label'>Logic Review</span>"
        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px;'>"
        "<div>"
        "<span class='label' style='font-size:9px;'>Fallacies Identified</span>"
        + fallacies_html
        + "</div>"
        "<div>"
        "<span class='label' style='font-size:9px;'>Analytical Gaps</span>"
        + _dp_list(review.analytical_gaps)
        + "</div>"
        + "</div>"
        + _quant_cell("Steelman (Counter-Argument)", review.steelman)
        + _quant_cell("Verdict", review.verdict)
        + f"<div style='margin-top:10px;font-family:var(--font-sans);font-size:12px;'>"
        f"<strong>Conviction Adjustment:</strong> "
        f"<span style='color:{adjustment_color};font-weight:700;'>{_e(review.conviction_adjustment)}</span>"
        f"</div>"
        "</div>"
    )


def _render_strategic_trade(strategic: StrategicTrade, quant=None, logic_review=None) -> str:
    return (
        "<div class='section-wrapper trade-page' id='strategic-trade'>"
        + _render_trade_card(strategic.trade, label="STRATEGIC")
        + "<div class='card' style='margin-top:6px;'>"
        "<span class='label'>Macro Thesis</span>"
        f"<p style='margin-top:8px;'>{_e(strategic.macro_thesis)}</p>"
        f"<p style='margin-top:12px;'><strong>Conviction:</strong> {_e(strategic.conviction_level)} &nbsp;&bull;&nbsp; <strong>Horizon:</strong> {strategic.time_horizon_months} months</p>"
        "<span class='label' style='margin-top:8px;'>Historical Precedents</span>"
        + _dp_list(strategic.historical_precedents)
        + "</div>"
        + _render_quant_analysis(quant)
        + _render_trade_logic_review(logic_review)
        + "</div>"
    )


def _render_positional_trades(positional: PositionalTradeSet, quants: list = None) -> str:
    quants = quants or []
    parts = []
    for i, trade in enumerate(positional.trades, 1):
        q = quants[i - 1] if i - 1 < len(quants) else None
        parts.append(
            f"<div class='section-wrapper trade-page' id='positional-trade-{i}'>"
            + _render_trade_card(trade, label=f"POSITIONAL {i}")
            + _render_quant_analysis(q)
            + "</div>"
        )
    return "\n".join(parts)


def _render_tactical_trades(tactical: TacticalTradeSet, quants: list = None) -> str:
    quants = quants or []
    parts = []
    for i, trade in enumerate(tactical.trades, 1):
        tq = quants[i - 1] if i - 1 < len(quants) else None
        parts.append(
            f"<div class='section-wrapper trade-page' id='tactical-trade-{i}'>"
            + _render_trade_card(trade, label=f"TACTICAL {i}")
            + _render_tactical_quant_block(tq)
            + "</div>"
        )
    return "\n".join(parts)


def _render_appendix(appendix: AppendixOutput) -> str:

    # Book recommendations (rendered if available)
    books_html = ""
    if hasattr(appendix, "book_recommendations") and appendix.book_recommendations:
        def _book_amazon_url(title: str, author: str) -> str:
            import urllib.parse
            q = urllib.parse.quote_plus(f"{title} {author}")
            return f"https://www.amazon.com/s?k={q}"

        def _book_synopsis(book) -> str:
            synopsis = getattr(book, "synopsis", None)
            conn     = getattr(book, "briefing_connection", None)
            # Fall back to legacy `relevance` field if new fields absent
            if not synopsis and not conn:
                legacy = getattr(book, "relevance", "")
                return (
                    f"<div class='book-rel' style='font-size:13px;color:var(--text-secondary);"
                    f"font-family:var(--font-sans);margin-top:4px;'>{_e(legacy)}</div>"
                )
            parts = []
            if synopsis:
                parts.append(
                    f"<div class='book-rel' style='font-size:13px;color:var(--text-secondary);"
                    f"font-family:var(--font-sans);margin-top:4px;line-height:1.6;'>{_e(synopsis)}</div>"
                )
            if conn:
                parts.append(
                    f"<div style='font-size:12px;color:var(--mck-blue);font-family:var(--font-sans);"
                    f"margin-top:4px;font-style:italic;'>{_e(conn)}</div>"
                )
            return "".join(parts)

        book_items = "".join(
            "<div class='book-item'>"
            f"<div class='book-category'>{_e(book.category.value if hasattr(book.category, 'value') else str(book.category))}</div>"
            f"<div class='book-title'>{_e(book.title)}</div>"
            f"<div class='book-author'>{_e(book.author)}</div>"
            + _book_synopsis(book)
            + f"<div style='margin-top:6px;'><a href='{_book_amazon_url(book.title, book.author)}' style='font-family:var(--font-sans);font-size:11px;color:var(--mck-blue);' target='_blank'>Buy on Amazon →</a></div>"
            "</div>"
            for book in appendix.book_recommendations
        )
        books_html = (
            "<div class='section-wrapper'>"
            "<h2>Recommended Reading</h2>"
            "<div class='card' style='padding:0;overflow:hidden;'>"
            + book_items
            + "</div></div>"
        )

    return (
        "<div class='section-wrapper' id='reference-materials'>"
        + books_html
        + "</div>"
    )


def _render_intelligence_summary(appendix: AppendixOutput) -> str:
    return (
        "<div class='section-wrapper'>"
        "<h2>Intelligence Summary</h2>"
        f"<p style='color:var(--text-secondary);'>{_e(appendix.intelligence_summary)}</p>"
        "</div>"
    )


def _render_correlation_matrix(matrix_html: str) -> str:
    """Render the 30-day price correlation matrix as a styled HTML table."""
    if not matrix_html or matrix_html.startswith("Insufficient"):
        return ""
    return (
        "<div class='section-wrapper' id='correlation-matrix'>"
        "<h2>Instrument Price Correlations (30-Day)</h2>"
        "<div class='card'>"
        + matrix_html
        + "</div>"
        "</div>"
    )



# ── Public Assembly Functions ─────────────────────────────────────────────────

def compile_html_document(
    opening:   OpeningSections,
    themes:    PriorityThemes,
    appendix:  AppendixOutput,
    strategic: Optional[StrategicTrade] = None,
    positional: Optional[PositionalTradeSet] = None,
    tactical:  Optional[TacticalTradeSet] = None,
    briefing_date: Optional[date] = None,
    market_snapshot: Optional[list] = None,
    strategic_quant=None,
    positional_quants: Optional[list] = None,
    tactical_quants: Optional[list] = None,
    adversarial: Optional[AdversarialAssessment] = None,
    logic_review: Optional[TradeLogicReview] = None,
    correlation_matrix: Optional[str] = None,
) -> str:
    """
    Concatenate all rendered HTML sections into a single styled document.

    Returns a complete HTML string ready to write to disk.
    """
    if briefing_date is None:
        briefing_date = date.today()

    sections = [_render_header(briefing_date)]
    sections += [
        _render_market_snapshot(market_snapshot or []),
        _render_intelligence_summary(appendix),
        _render_epigraph(opening),
        _render_calendar(opening),
        _render_upcoming_earnings(opening),
        _render_recent_earnings(opening),
        _render_priority_themes(themes),
        _render_adversarial_assessment(adversarial),
    ]

    if strategic is not None:
        sections.append(_render_strategic_trade(strategic, quant=strategic_quant, logic_review=logic_review))
    if positional is not None:
        sections.append(_render_positional_trades(positional, quants=positional_quants or []))
    if tactical is not None:
        sections.append(_render_tactical_trades(tactical, quants=tactical_quants or []))

    if correlation_matrix:
        sections.append(_render_correlation_matrix(correlation_matrix))

    sections.append(_render_appendix(appendix))

    body = "\n".join(sections)

    return (
        "<!DOCTYPE html>\n"
        "<html lang='en'>\n"
        "<head>\n"
        "  <meta charset='UTF-8'>\n"
        "  <meta name='viewport' content='width=device-width, initial-scale=1.0'>\n"
        f"  <title>Daily Brief &mdash; {briefing_date.strftime('%m%d%y')}</title>\n"
        + _STYLES
        + "</head>\n<body>\n"
        + body
        + "\n</body>\n</html>"
    )


def aggregate_json_output(
    opening:    OpeningSections,
    themes:     PriorityThemes,
    appendix:   AppendixOutput,
    strategic:  Optional[StrategicTrade] = None,
    positional: Optional[PositionalTradeSet] = None,
    tactical:   Optional[TacticalTradeSet] = None,
    briefing_date: Optional[date] = None,
    correlation_matrix: Optional[str] = None,
) -> dict:
    """
    Aggregate all validated Pydantic objects into a single master dictionary
    suitable for JSON serialization and longitudinal storage.

    Transmission overlay data is embedded within each theme in the
    priority_themes field (unified model).  Entity registry and situation
    index are preserved here for longitudinal memory even though they are
    no longer rendered in the HTML output.
    """
    if briefing_date is None:
        briefing_date = date.today()

    return {
        "date":              briefing_date.isoformat(),
        "opening_sections":  opening.model_dump(),
        "priority_themes":   themes.model_dump(),
        "strategic_trade":    strategic.model_dump() if strategic is not None else None,
        "positional_trades":  positional.model_dump() if positional is not None else None,
        "tactical_trades":    tactical.model_dump() if tactical is not None else None,
        "appendix":           appendix.model_dump(),
        "correlation_matrix": correlation_matrix,
    }
