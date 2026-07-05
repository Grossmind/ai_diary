"""Recall formatter — turns raw search results into LLM-friendly context.

Used by the chat router when `memory_mode` is on: top-k relevant diary entries
are prepended to the LLM message list so the assistant can answer questions
grounded in the user's past.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.services import vector

MAX_CONTEXT_CHARS = 3000  # total context budget for recall block
MAX_PER_ENTRY_CHARS = 500


def _format_entry(idx: int, item: Dict[str, Any], label: str = "") -> str:
    """Format one recall hit as a compact citation."""
    meta = item.get("metadata") or {}
    date = meta.get("created_at", "?")
    summary = meta.get("summary")
    mood = meta.get("mood")
    people = meta.get("people")
    text = (item.get("text") or "").strip().replace("\n", " ")
    if len(text) > MAX_PER_ENTRY_CHARS:
        text = text[:MAX_PER_ENTRY_CHARS] + "…"

    header = f"[日记 #{item.get('id', idx)} · {date}"
    if label:
        header += f" · {label}"
    if mood:
        header += f" · 情绪:{mood}"
    if summary:
        header += f" · 摘要:{summary[:80]}"
    if people and isinstance(people, str):
        header += f" · 人物:{people[:60]}"
    header += "]"
    return f"{header}\n{text}"


def build_recall_context(query: str, n_recent: int = 5, k_fts: int = 3) -> str:
    """Combine FTS hits + recent entries into a single context block.

    The LLM sees the most-recent entries as a "diary scroll" (good for
    time-relative questions like "上周/最近/刚才") and the top-k FTS hits
    (good for keyword queries mentioning specific people or topics). Entries
    are deduplicated by id; FTS hits appear first (more likely on-topic).
    """
    fts_hits = vector.search(query, k=k_fts) if query else []
    recent = vector.recent(n=n_recent)
    recent = [r for r in recent if r.get("distance", 0) > 0 or True]  # recent always has 0 dist

    # Dedupe, preserve order: FTS first, then recent (skip already-seen).
    seen = set()
    combined: List[Dict[str, Any]] = []
    for h in fts_hits:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        combined.append({**h, "_label": "匹配"})
    for r in recent:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        combined.append({**r, "_label": "近期"})

    if not combined:
        return ""

    return _format_combined(combined)


def _format_combined(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    header = "以下是用户的过往日记（先按关键词匹配，再补近期条目）。如果用户的问题是关于过去的，可以引用；如果无关，请忽略："
    lines = [header]
    total = len(header)
    for i, item in enumerate(items, 1):
        chunk = "\n\n" + _format_entry(i, item, label=item.get("_label", ""))
        if total + len(chunk) > MAX_CONTEXT_CHARS:
            break
        lines.append(chunk)
        total += len(chunk)
    return "\n".join(lines)


# Back-compat alias used by the chat router.
def format_recall_for_context(results: List[Dict[str, Any]]) -> str:
    """Legacy entry point — formats a pre-built result list as context."""
    if not results:
        return ""
    header = "以下是用户过往日记中可能相关的片段。如果用户的问题是关于过去的，可以引用；如果无关，请忽略："
    lines = [header]
    total = len(header)
    for i, item in enumerate(results, 1):
        chunk = "\n\n" + _format_entry(i, item)
        if total + len(chunk) > MAX_CONTEXT_CHARS:
            break
        lines.append(chunk)
        total += len(chunk)
    return "\n".join(lines)

