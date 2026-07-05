"""Diary CRUD router (Phase 1: minimal write + read).

Endpoints:
  POST /api/diary          — create entry
  GET  /api/diary          — paginated list (newest first)
  GET  /api/diary/{id}     — single entry

All responses use the standard envelope (see app.responses).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.database import get_connection
from app.models import DiaryEntryCreateSchema, DiaryImportSchema
from app.responses import err, ok
from app.services import vector

logger = logging.getLogger("diary.diary")

router = APIRouter(prefix="/api/diary", tags=["diary"])


# ---- Helpers ----------------------------------------------------------------
def _maybe_json(raw: Optional[str]) -> Any:
    """Decode a JSON-encoded TEXT column, returning None for NULL/empty."""
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw  # not JSON — return as-is rather than masking corruption


def _row_to_entry(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a sqlite3.Row into a JSON-safe dict matching DiaryEntrySchema."""
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "raw_text": row["raw_text"],
        "summary": row["summary"],
        "conversation": _maybe_json(row["conversation"]),
        "mood": row["mood"],
        "events": _maybe_json(row["events"]),
        "people": _maybe_json(row["people"]),
        "follow_ups": _maybe_json(row["follow_ups"]),
        "audio_url": row["audio_url"],
        "weather": _maybe_json(row["weather"]) if "weather" in row.keys() else None,
        "raw_metadata": _maybe_json(row["raw_metadata"]) if "raw_metadata" in row.keys() else None,
    }


# ---- Routes -----------------------------------------------------------------
@router.post("", status_code=201)
async def create_entry(payload: DiaryEntryCreateSchema):
    """Insert a new diary entry. Returns the full row on success."""
    conversation_json: Optional[str] = None
    if payload.conversation:
        conversation_json = json.dumps([t.model_dump(mode="json") for t in payload.conversation])

    try:
        with get_connection() as conn:
            # Use the provided created_at if given (import path), else default
            # to CURRENT_TIMESTAMP via the table default.
            if payload.created_at:
                cur = conn.execute(
                    """INSERT INTO diary_entries
                           (raw_text, conversation, audio_url, weather, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        payload.text,
                        conversation_json,
                        payload.audio_url,
                        json.dumps(payload.weather) if payload.weather else None,
                        payload.created_at,
                    ),
                )
            else:
                cur = conn.execute(
                    """INSERT INTO diary_entries
                           (raw_text, conversation, audio_url, weather)
                       VALUES (?, ?, ?, ?)""",
                    (
                        payload.text,
                        conversation_json,
                        payload.audio_url,
                        json.dumps(payload.weather) if payload.weather else None,
                    ),
                )
            conn.commit()
            new_id = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM diary_entries WHERE id = ?", (new_id,)
            ).fetchone()
        # Vectorize for RAG. Non-fatal if it fails.
        try:
            vector.add_entry(
                entry_id=new_id,
                text=payload.text,
                created_at=row["created_at"],
            )
        except Exception as exc:
            logger.warning("vectorize on create_entry failed: %s", exc)
        return ok(_row_to_entry(row), status_code=201)
    except sqlite3.Error as exc:
        return err(f"Database error: {exc}", "DB_ERROR", status_code=500)


@router.get("")
async def list_entries(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Return newest entries first, plus total count for pagination UIs."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM diary_entries ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM diary_entries").fetchone()[0]
            return ok(
                {
                    "items": [_row_to_entry(r) for r in rows],
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                }
            )
    except sqlite3.Error as exc:
        return err(f"Database error: {exc}", "DB_ERROR", status_code=500)


@router.get("/{entry_id}")
async def get_entry(entry_id: int):
    """Fetch a single entry by id."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM diary_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                return err(f"Entry {entry_id} not found", "NOT_FOUND", status_code=404)
            return ok(_row_to_entry(row))
    except sqlite3.Error as exc:
        return err(f"Database error: {exc}", "DB_ERROR", status_code=500)


@router.patch("/{entry_id}")
async def update_entry(entry_id: int, payload: Dict[str, Any]):
    """Partially update an entry. Only provided fields are written.

    Supported fields: raw_text, summary, mood, events, people, follow_ups,
    weather. The FTS index is re-built from the new `raw_text` (if changed).
    """
    if not payload:
        return err("Empty update body", "EMPTY_UPDATE", status_code=400)

    # Whitelist of editable columns (everything else is ignored).
    editable = {"raw_text", "summary", "mood", "events", "people",
                "follow_ups", "weather"}
    fields = {k: v for k, v in payload.items() if k in editable}
    if not fields:
        return err("No editable fields provided", "NO_EDITABLE_FIELDS", status_code=400)

    # JSON-encode the list/dict fields so they land in the right SQLite
    # affinity. `weather` and `mood` are scalars — leave as-is.
    encoded: Dict[str, Any] = {}
    for k, v in fields.items():
        if k in ("raw_text", "summary", "mood"):
            encoded[k] = v
        elif k == "weather":
            encoded[k] = json.dumps(v) if v is not None else None
        else:
            encoded[k] = json.dumps(v, ensure_ascii=False) if v is not None else None

    set_clause = ", ".join(f"{k} = ?" for k in encoded.keys())
    params = list(encoded.values()) + [entry_id]

    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM diary_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                return err(f"Entry {entry_id} not found", "NOT_FOUND", status_code=404)
            conn.execute(
                f"UPDATE diary_entries SET {set_clause} WHERE id = ?", params
            )
            conn.commit()
            new_row = conn.execute(
                "SELECT * FROM diary_entries WHERE id = ?", (entry_id,)
            ).fetchone()
    except sqlite3.Error as exc:
        return err(f"Database error: {exc}", "DB_ERROR", status_code=500)

    # Re-vectorize if raw_text (or summary/mood, which we also index)
    # changed. Best-effort.
    try:
        vector.add_entry(
            entry_id=entry_id,
            text=new_row["raw_text"],
            created_at=new_row["created_at"],
            summary=new_row["summary"],
            mood=new_row["mood"],
        )
    except Exception as exc:
        logger.warning("vectorize on update_entry failed: %s", exc)

    return ok(_row_to_entry(new_row))


@router.delete("/{entry_id}")
async def delete_entry(entry_id: int):
    """Delete an entry (and remove it from the FTS index)."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM diary_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                return err(f"Entry {entry_id} not found", "NOT_FOUND", status_code=404)
            conn.execute("DELETE FROM diary_entries WHERE id = ?", (entry_id,))
            # Also clear the conversation link if it pointed here.
            conn.execute(
                "UPDATE conversations SET diary_entry_id = NULL "
                "WHERE diary_entry_id = ?",
                (entry_id,),
            )
            conn.commit()
    except sqlite3.Error as exc:
        return err(f"Database error: {exc}", "DB_ERROR", status_code=500)

    # Remove from FTS. Best-effort.
    try:
        vector.delete_entry(entry_id)
    except Exception as exc:
        logger.warning("vector.delete on delete_entry failed: %s", exc)

    return ok({"deleted": entry_id})


@router.post("/import", status_code=201)
async def import_entries(payload: DiaryImportSchema):
    """Bulk-insert diary entries (typically from a markdown import).

    Each entry dict should include `raw_text` (required) and any of
    `created_at`, `summary`, `mood`, `events`, `people`, `follow_ups`,
    `weather`, `conversation`. The full dict is also stored in
    `raw_metadata` so a re-export preserves the original payload.
    """
    inserted_ids: list[int] = []
    try:
        with get_connection() as conn:
            for entry in payload.entries:
                text = (entry.get("raw_text") or "").strip()
                if not text:
                    continue
                # Build column list dynamically so we can omit created_at
                # when not provided (and let the table default kick in).
                cols = ["raw_text", "summary", "mood", "events", "people",
                        "follow_ups", "weather", "raw_metadata"]
                vals = [
                    text,
                    entry.get("summary"),
                    entry.get("mood"),
                    json.dumps(entry.get("events") or [], ensure_ascii=False),
                    json.dumps(entry.get("people") or [], ensure_ascii=False),
                    json.dumps(entry.get("follow_ups") or [], ensure_ascii=False),
                    json.dumps(entry.get("weather")) if entry.get("weather") else None,
                    json.dumps(entry, ensure_ascii=False),
                ]
                if entry.get("created_at"):
                    cols.append("created_at")
                    vals.append(entry["created_at"])
                placeholders = ",".join("?" * len(cols))
                cur = conn.execute(
                    f"INSERT INTO diary_entries ({','.join(cols)}) VALUES ({placeholders})",
                    vals,
                )
                inserted_ids.append(cur.lastrowid)
            conn.commit()
    except sqlite3.Error as exc:
        return err(f"Database error: {exc}", "DB_ERROR", status_code=500)

    # Vectorize each new entry for RAG. Best-effort.
    try:
        with get_connection() as conn:
            for eid in inserted_ids:
                row = conn.execute(
                    "SELECT raw_text, created_at, summary, mood, people FROM diary_entries WHERE id = ?",
                    (eid,),
                ).fetchone()
                if row is None:
                    continue
                vector.add_entry(
                    entry_id=eid,
                    text=row["raw_text"],
                    created_at=row["created_at"],
                    summary=row["summary"],
                    mood=row["mood"],
                )
    except Exception as exc:
        logger.warning("vectorize after import failed: %s", exc)

    return ok({"inserted": len(inserted_ids), "ids": inserted_ids}, status_code=201)
