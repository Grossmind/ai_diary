"""SQLite database layer.

Single-file SQLite accessed via the stdlib `sqlite3` module. Connection-per-call
is fine at this scale (single user); WAL mode would be the next step if write
contention ever appears.

Schemas are from PLAN.md §8 (diary_entries) and Phase 2 (conversations). All
JSON-shaped columns are stored as TEXT and serialized/deserialized at the
router boundary — keeps the DB dumb and the Pydantic types rich.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import DATA_DIR

DB_PATH: Path = DATA_DIR / "diary.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS diary_entries (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  raw_text      TEXT NOT NULL,
  summary       TEXT,
  conversation  JSON,
  mood          TEXT,
  events        JSON,
  people        JSON,
  follow_ups    JSON,
  audio_url     TEXT
);

CREATE INDEX IF NOT EXISTS idx_diary_entries_created_at
  ON diary_entries(created_at DESC);

CREATE TABLE IF NOT EXISTS conversations (
  id              TEXT PRIMARY KEY,
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  messages        JSON NOT NULL DEFAULT '[]',
  status          TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'saved'
  memory_mode     INTEGER NOT NULL DEFAULT 0,     -- 0 = off, 1 = RAG on
  diary_entry_id  INTEGER,
  FOREIGN KEY (diary_entry_id) REFERENCES diary_entries(id)
);

CREATE INDEX IF NOT EXISTS idx_conversations_status
  ON conversations(status, updated_at DESC);

-- Full-text search index for RAG recall. Standalone (not external-content)
-- so we can add extra fields (people, mood) without coupling to the source
-- table's column set. Updated explicitly by app.services.vector.add_entry.
CREATE VIRTUAL TABLE IF NOT EXISTS diary_fts USING fts5(
  entry_id  UNINDEXED,
  text,
  summary,
  people,
  mood,
  tokenize = 'unicode61 remove_diacritics 2'
);
"""

# Idempotent column additions for Phase 3.5 (weather, raw_metadata).
# SQLite has no `ADD COLUMN IF NOT EXISTS` so we check sqlite_master first.
_PHASE_3_5_COLUMNS = [
    ("diary_entries", "weather",       "JSON"),
    ("diary_entries", "raw_metadata",  "JSON"),
]


def _add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    """Add a column to a table if it doesn't exist. No-op if it does."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(r["name"] == col for r in rows):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def get_connection() -> sqlite3.Connection:
    """Open a new SQLite connection.

    Returns a connection with `row_factory=sqlite3.Row` so callers can address
    columns by name. WAL mode is enabled on first use so concurrent reads
    and writes don't block each other; `busy_timeout` makes us wait briefly
    for a lock rather than failing immediately.

    The caller is responsible for closing it (use as a context manager).
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Enable WAL once (idempotent; cheap if already enabled).
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    """Create the data directory and apply the schema. Idempotent."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)
        for table, col, decl in _PHASE_3_5_COLUMNS:
            _add_column_if_missing(conn, table, col, decl)
        conn.commit()
