"""SQLite schema and connection helper. Money is stored as REAL cents-precision values and converted to Decimal in code."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    sku                TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    cost_price         REAL NOT NULL CHECK (cost_price > 0),
    min_margin_percent REAL NOT NULL CHECK (min_margin_percent >= 0 AND min_margin_percent < 100),
    current_price      REAL NOT NULL CHECK (current_price > 0),
    stock_count        INTEGER NOT NULL CHECK (stock_count >= 0),
    is_synthetic       INTEGER NOT NULL DEFAULT 1,
    updated_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS competitor_scrapes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sku          TEXT NOT NULL REFERENCES inventory(sku),
    competitor   TEXT NOT NULL,
    raw_text     TEXT NOT NULL,
    scraped_at   TEXT NOT NULL,
    is_synthetic INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS extracted_offers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scrape_id     INTEGER NOT NULL REFERENCES competitor_scrapes(id),
    sku           TEXT NOT NULL REFERENCES inventory(sku),
    competitor    TEXT NOT NULL,
    base_price    REAL NOT NULL CHECK (base_price > 0),
    coupon_amount REAL NOT NULL CHECK (coupon_amount >= 0),
    final_price   REAL NOT NULL CHECK (final_price >= 0),
    extractor     TEXT NOT NULL CHECK (extractor IN ('llm', 'regex')),
    confidence    REAL NOT NULL,
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    sku            TEXT NOT NULL REFERENCES inventory(sku),
    current_price  REAL NOT NULL,
    competitor_low REAL,
    floor_price    REAL NOT NULL,
    proposed_price REAL NOT NULL,
    action         TEXT NOT NULL CHECK (action IN ('reprice', 'hold')),
    status         TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'superseded', 'hold')),
    reason         TEXT NOT NULL,
    flags          TEXT NOT NULL DEFAULT '[]',
    reviewer       TEXT,
    review_note    TEXT,
    created_at     TEXT NOT NULL,
    decided_at     TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    request_id        TEXT,
    actor             TEXT NOT NULL,
    event             TEXT NOT NULL,
    sku               TEXT,
    recommendation_id INTEGER,
    details           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_offers_latest ON extracted_offers (sku, competitor, id);
CREATE INDEX IF NOT EXISTS idx_recs_status ON recommendations (status, sku);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db(path: Path | None = None):
    """Commit on success, roll back on error, always close."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    with db(path) as conn:
        conn.executescript(SCHEMA)
