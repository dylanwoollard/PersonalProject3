"""
storage.py — SQLite longitudinal memory and file I/O (Level 1).

Functions:
  init_database()               — Create schema if not present
  commit_to_longitudinal_memory — Write today's data to SQLite
  purge_expired_memory          — Delete records older than N days
  save_html_briefing            — Write compiled HTML to disk
  save_json_data                — Write aggregated JSON to disk
"""

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import aiofiles

import config

DB_PATH    = config.DB_PATH
OUTPUT_DIR = config.OUTPUT_DIR


# ── Schema ────────────────────────────────────────────────────────────────────

def init_database() -> None:
    """Initialize the SQLite database and create the briefings table if absent."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS briefings (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                date                        TEXT    NOT NULL UNIQUE,
                raw_json                    TEXT    NOT NULL,
                entities                    TEXT    NOT NULL DEFAULT '[]',
                situations                  TEXT    NOT NULL DEFAULT '[]',
                themes                      TEXT    NOT NULL DEFAULT '[]',
                strategic_trade_instrument  TEXT,
                created_at                  TEXT    NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_briefings_date ON briefings (date)"
        )
        conn.commit()


# ── Write / Purge ─────────────────────────────────────────────────────────────

def commit_to_longitudinal_memory(briefing_date: str, briefing_json: dict) -> None:
    """
    Write today's structured briefing data to the SQLite longitudinal memory database.
    Performs an upsert so re-running a day's briefing overwrites the prior record.
    """
    appendix     = briefing_json.get("appendix", {})
    themes_data  = briefing_json.get("priority_themes", {}).get("themes", [])
    # strategic_trade is None when GENERATE_TRADES=False — guard with `or {}`
    strategic    = (briefing_json.get("strategic_trade") or {}).get("trade", {})

    entities   = appendix.get("key_entities", [])
    situations = appendix.get("key_situations", [])
    themes     = [t.get("title", "") for t in themes_data]
    instrument = strategic.get("instrument", "")

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO briefings
                (date, raw_json, entities, situations, themes,
                 strategic_trade_instrument, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                briefing_date,
                json.dumps(briefing_json, ensure_ascii=False),
                json.dumps(entities),
                json.dumps(situations),
                json.dumps(themes),
                instrument,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()


def purge_expired_memory(days: int = 180) -> int:
    """
    Delete all briefing records older than `days` days.
    Returns the number of records deleted.
    """
    if not DB_PATH.exists():
        return 0

    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM briefings WHERE date < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount


# ── File Output ───────────────────────────────────────────────────────────────

async def save_html_briefing(
    html_content: str,
    briefing_date: Optional[date] = None,
) -> Path:
    """Save the compiled HTML briefing string to briefing_YYYY_MM_DD.html."""
    if briefing_date is None:
        briefing_date = date.today()

    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = OUTPUT_DIR / f"dailybrief{briefing_date.strftime('%m%d%y')}.html"

    async with aiofiles.open(filename, "w", encoding="utf-8") as f:
        await f.write(html_content)

    return filename


async def save_json_data(
    json_data: dict,
    briefing_date: Optional[date] = None,
) -> Path:
    """Save the aggregated Pydantic JSON output to data_YYYY_MM_DD.json."""
    if briefing_date is None:
        briefing_date = date.today()

    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = OUTPUT_DIR / f"data_{briefing_date.strftime('%Y_%m_%d')}.json"

    async with aiofiles.open(filename, "w", encoding="utf-8") as f:
        await f.write(json.dumps(json_data, indent=2, ensure_ascii=False))

    return filename
