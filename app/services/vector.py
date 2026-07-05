"""Recall index for diary entries (RAG).

Phase 3 originally planned to use ChromaDB + ONNX embeddings, but the model
download from PyPI was prohibitively slow on this network. Replaced with
SQLite FTS5 — built into the stdlib, no model, no extra deps, and good
enough for the single-user diary use case.

How it works:
  - `diary_fts` is an FTS5 virtual table over (text, summary, people, mood)
    with `unicode61` tokenization. For CJK text this is character-level,
    which gives decent overlap on shared words.
  - `add_entry()` indexes a new row (or refreshes an existing one).
  - `search()` runs a BM25-ranked query and returns top-k.

`search()` accepts an optional `threshold` on the absolute BM25 rank — entries
with rank beyond the threshold are dropped. We default to None (no filter,
just take top-k).

We expose the SAME function signatures as the Chroma version did so the
chat router and recall formatter don't need to change.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.database import get_connection

logger = logging.getLogger("diary.vector")

# FTS5 has a few reserved characters that break the MATCH expression if
# left in. Strip anything that isn't word-ish or whitespace.
_QUERY_STRIP_RE = re.compile(r"[^\w\s一-鿿]", re.UNICODE)
_CJK_RANGE = r"[一-鿿]"


def _segment_cjk(s: str) -> str:
    """Insert spaces between adjacent CJK characters (and at CJK/ASCII
    boundaries) so the unicode61 tokenizer treats each Chinese character
    as its own token. Without this, FTS5 treats whole sentences as one
    token and per-character queries don't match.
    """
    if not s:
        return ""
    s = re.sub(rf"(?<={_CJK_RANGE})(?={_CJK_RANGE})", " ", s)
    s = re.sub(rf"(?<={_CJK_RANGE})(?=[a-zA-Z0-9])", " ", s)
    s = re.sub(rf"(?<=[a-zA-Z0-9])(?={_CJK_RANGE})", " ", s)
    return s


def _sanitize_query(query: str) -> str:
    """Strip FTS5-unfriendly characters; collapse whitespace."""
    cleaned = _QUERY_STRIP_RE.sub(" ", query)
    return " ".join(cleaned.split())


def _build_fts_match(query: str) -> Optional[str]:
    """Build a FTS5 MATCH expression from a free-text query.

    Pre-segments CJK so each character is a token, then ORs the tokens
    together so partial / single-character matches still surface. BM25
    rank pushes the most-overlapping entries to the top.
    """
    seg = _segment_cjk(_sanitize_query(query))
    if not seg:
        return None
    tokens = seg.split()
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def add_entry(
    entry_id: int,
    text: str,
    *,
    created_at: Optional[str] = None,
    summary: Optional[str] = None,
    mood: Optional[str] = None,
) -> None:
    """Add or update a diary entry in the FTS index.

    Idempotent: re-adding the same entry_id replaces the existing row.
    Also looks up `people` from the source table so the index includes it.

    The text is pre-segmented with spaces between adjacent CJK characters
    so the unicode61 tokenizer indexes each character as its own token,
    which is what we need for substring/keyword matching in Chinese.

    Note: the connection used here is committed and closed before returning,
    so callers don't need to worry about lock contention with the source
    table inserts.
    """
    if not text or not text.strip():
        return
    people_str = ""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT people FROM diary_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if row and row["people"]:
                try:
                    names = json.loads(row["people"])
                    if isinstance(names, list):
                        people_str = " ".join(str(n) for n in names)
                except (json.JSONDecodeError, TypeError):
                    people_str = str(row["people"])
            # Pre-segment: insert spaces between adjacent CJK chars and at
            # CJK↔ASCII boundaries, so unicode61 tokenizes per character.
            seg_text = _segment_cjk(text)
            seg_summary = _segment_cjk(summary or "")
            seg_mood = _segment_cjk(mood or "")
            seg_people = _segment_cjk(people_str)
            conn.execute("DELETE FROM diary_fts WHERE entry_id = ?", (entry_id,))
            conn.execute(
                "INSERT INTO diary_fts (entry_id, text, summary, people, mood) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry_id, seg_text, seg_summary, seg_people, seg_mood),
            )
            conn.commit()
        logger.info("vector.add id=%s chars=%d", entry_id, len(text))
    except Exception as exc:
        logger.warning("vector.add failed id=%s err=%s", entry_id, exc)
        return
    # Re-read the freshly-inserted text so subsequent calls return it
    # in correct display form. (FTS table holds the segmented form; we
    # always pull display text from diary_entries via _hydrate_rows.)


def search(
    query: str,
    *,
    k: int = 3,
    threshold: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Top-k diary entries most relevant to `query`.

    Args:
        query: Natural-language question or phrase.
        k: Max results to return.
        threshold: If set, drop results with abs(BM25 rank) > threshold.
            Lower rank = more relevant (BM25 is negative). Default None.

    Returns:
        List of `{"id": int, "text": str, "metadata": dict, "distance": float}`,
        ordered by ascending |distance| (most relevant first).
    """
    fts_query = _build_fts_match(query)
    if not fts_query:
        return []
    try:
        with get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM diary_fts").fetchone()[0]
            if count == 0:
                return []
            rows = conn.execute(
                "SELECT entry_id, rank "
                "FROM diary_fts "
                "WHERE diary_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (fts_query, min(k, count)),
            ).fetchall()
    except Exception as exc:
        logger.warning("vector.search failed query=%r err=%s", query, exc)
        return []

    # We need text + metadata; fetch from diary_entries (the FTS table only
    # has the segmented strings, which are hard to read for display).
    return _hydrate_rows([(r["entry_id"], abs(float(r["rank"])) if r["rank"] is not None else 0.0) for r in rows])


def recent(n: int = 5) -> List[Dict[str, Any]]:
    """Return the N most recent diary entries (newest first).

    Used as the "diary scroll" fallback in RAG: when a user asks a question
    and FTS isn't a good match (or we have few entries), the LLM gets recent
    context it can reason over.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM diary_entries ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(0, n),),
            ).fetchall()
    except Exception as exc:
        logger.warning("vector.recent failed n=%d err=%s", n, exc)
        return []
    return _hydrate_rows([(r["id"], 0.0) for r in rows])


def _hydrate_rows(id_score_pairs: List[tuple]) -> List[Dict[str, Any]]:
    """Given (entry_id, score) pairs, fetch the text + metadata."""
    if not id_score_pairs:
        return []
    eids = [eid for eid, _ in id_score_pairs]
    score_map = {eid: score for eid, score in id_score_pairs}
    try:
        with get_connection() as conn:
            placeholders = ",".join("?" * len(eids))
            rows = conn.execute(
                f"SELECT id, created_at, raw_text, summary, mood, people "
                f"FROM diary_entries WHERE id IN ({placeholders})",
                eids,
            ).fetchall()
            # Preserve the input order (caller's sort).
            by_id = {r["id"]: r for r in rows}
            ordered = [by_id[eid] for eid in eids if eid in by_id]
    except Exception as exc:
        logger.warning("vector._hydrate_rows failed err=%s", exc)
        return []
    out: List[Dict[str, Any]] = []
    for r in ordered:
        out.append({
            "id": r["id"],
            "text": r["raw_text"],
            "metadata": {
                "summary": r["summary"] or "",
                "mood": r["mood"] or "",
                "people": r["people"] or "",
                "created_at": r["created_at"] or "",
            },
            "distance": score_map.get(r["id"], 0.0),
        })
    return out


def delete_entry(entry_id: int) -> None:
    """Remove an entry from the FTS index (no-op if not present)."""
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM diary_fts WHERE entry_id = ?", (entry_id,))
            conn.commit()
    except Exception as exc:
        logger.warning("vector.delete failed id=%s err=%s", entry_id, exc)


def count() -> int:
    """Return the number of indexed entries."""
    try:
        with get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM diary_fts").fetchone()[0]
    except Exception:
        return 0
