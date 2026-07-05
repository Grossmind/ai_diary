"""Chat router — multi-turn conversation with the diary assistant (Phase 2).

Endpoints:
  POST /api/chat                       — start new conversation, get welcome msg
  GET  /api/chat/{id}                  — fetch full conversation (rehydration)
  POST /api/chat/{id}/message          — send user msg; SSE-stream assistant reply
  POST /api/chat/{id}/end              — end conversation, extract, save diary entry

Conversations are persisted in the `conversations` table so refreshing the
page mid-conversation doesn't lose the thread.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List

from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse

from app.database import get_connection
from app.models import ChatMemoryModeRequestSchema, ChatMessageRequestSchema, MessageSchema
from app.responses import err, ok
from app.services.extract import extract_from_conversation
from app.services.llm import LLMError, stream_chat
from app.services.recall import build_recall_context
from app.services import vector
from app.services.weather import fetch_weather

logger = logging.getLogger("diary.chat")

router = APIRouter(prefix="/api/chat", tags=["chat"])

WELCOME_MESSAGE = (
    "你好！今天想记录些什么？随便聊聊，我从旁陪你。"
)

# System prompt that sets the assistant's persona. Concise and warm; the
# model is told to mirror the user's language.
SYSTEM_PROMPT = """你是一个温暖、贴心的个人日记助理，任务是陪用户把今天的事情说出来。

行为准则：
- 用与用户相同的语言回复（中文用中文，英文用英文，其他语言同理）
- 回复简短，1-3 句话为主
- 多用开放式提问，引导用户继续说、想得更深
- 不要长篇大论分析、总结或给建议（那是结束对话时模型的事）
- 不要复述用户说的话，让用户感觉被倾听而非被审视
- 语气温柔、好奇、像老朋友聊天
"""


# ---- Helpers ----------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_conversation(row) -> Dict[str, Any]:
    """Convert a conversations-table row to a JSON-friendly dict."""
    raw = row["messages"]
    try:
        messages = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        messages = []
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status": row["status"],
        "memory_mode": bool(row["memory_mode"]),
        "diary_entry_id": row["diary_entry_id"],
        "messages": messages,
    }


def _build_llm_messages(conv: Dict[str, Any]) -> List[Dict[str, str]]:
    """Convert a persisted conversation to the LLM's expected message format."""
    out: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in conv["messages"]:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


def _build_llm_messages_with_recall(
    conv: Dict[str, Any],
    user_query: str,
) -> List[Dict[str, str]]:
    """Same as `_build_llm_messages` but prepends RAG context if memory_mode is on.

    The recall context is injected as a `system` message right after the base
    system prompt so the model sees it before any user/assistant turns.
    """
    out = _build_llm_messages(conv)
    if not conv.get("memory_mode"):
        return out
    try:
        block = build_recall_context(user_query, n_recent=5, k_fts=3)
    except Exception as exc:
        logger.warning("RAG context build failed: %s", exc)
        return out
    if not block:
        return out
    # Insert after the base system prompt (index 1).
    out.insert(1, {"role": "system", "content": block})
    logger.info("chat.rag conv=%s ctx_chars=%d", conv["id"][:8], len(block))
    return out


def _user_text(conv: Dict[str, Any]) -> str:
    """Concatenate user turns — used as `raw_text` on the saved diary entry."""
    return "\n".join(m["content"] for m in conv["messages"] if m.get("role") == "user")


# ---- Routes -----------------------------------------------------------------
@router.post("", status_code=201)
async def start_conversation():
    """Create a new conversation with a welcome message and return its id."""
    cid = str(uuid.uuid4())
    welcome = {"role": "assistant", "content": WELCOME_MESSAGE, "ts": _now_iso()}
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO conversations (id, messages, status, memory_mode) "
                "VALUES (?, ?, 'active', 0)",
                (cid, json.dumps([welcome], ensure_ascii=False)),
            )
            conn.commit()
        return ok({
            "conversation_id": cid,
            "welcome_message": WELCOME_MESSAGE,
            "memory_mode": False,
        }, status_code=201)
    except Exception as exc:
        logger.exception("start_conversation failed")
        return err(f"DB error: {exc}", "DB_ERROR", status_code=500)


@router.patch("/{conversation_id}")
async def update_conversation(
    payload: ChatMemoryModeRequestSchema,
    conversation_id: str = Path(...),
):
    """Toggle flags on a conversation. Currently only `memory_mode` is supported."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                return err(f"Conversation {conversation_id} not found", "NOT_FOUND", status_code=404)
            conn.execute(
                "UPDATE conversations SET memory_mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (1 if payload.memory_mode else 0, conversation_id),
            )
            conn.commit()
        return ok({"memory_mode": payload.memory_mode})
    except Exception as exc:
        logger.exception("update_conversation failed")
        return err(f"DB error: {exc}", "DB_ERROR", status_code=500)


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str = Path(...)):
    """Fetch a conversation for rehydrating the frontend after a refresh."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            return err(f"Conversation {conversation_id} not found", "NOT_FOUND", status_code=404)
        return ok(_row_to_conversation(row))
    except Exception as exc:
        logger.exception("get_conversation failed")
        return err(f"DB error: {exc}", "DB_ERROR", status_code=500)


@router.post("/{conversation_id}/message")
async def send_message(
    payload: ChatMessageRequestSchema,
    conversation_id: str = Path(...),
):
    """Append a user message and stream the assistant reply as SSE.

    SSE format:
      data: {"delta": "<chunk>"}            — repeated for each text chunk
      event: done\\ndata: {"length": N}      — sent once at the end
      event: error\\ndata: {"error": "..."}  — on failure
    """
    # ---- Pre-stream: load + persist user turn ----------------------------
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                return err(
                    f"Conversation {conversation_id} not found", "NOT_FOUND", status_code=404
                )
            if row["status"] != "active":
                return err(
                    f"Conversation is {row['status']}; start a new one",
                    "CONVERSATION_CLOSED",
                    status_code=409,
                )
            conv = _row_to_conversation(row)
            conv["messages"].append(
                {"role": "user", "content": payload.content, "ts": _now_iso()}
            )
            conn.execute(
                "UPDATE conversations SET messages = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(conv["messages"], ensure_ascii=False), conversation_id),
            )
            conn.commit()
            # If memory_mode is on, prepend RAG context from past entries.
            llm_messages = _build_llm_messages_with_recall(conv, payload.content)
    except Exception as exc:
        logger.exception("send_message pre-stream failed")
        return err(f"DB error: {exc}", "DB_ERROR", status_code=500)

    # ---- Stream ---------------------------------------------------------
    async def event_stream() -> AsyncIterator[bytes]:
        accumulated: List[str] = []
        try:
            async for delta in stream_chat(llm_messages):
                accumulated.append(delta)
                line = json.dumps({"delta": delta}, ensure_ascii=False)
                yield f"data: {line}\n\n".encode("utf-8")

            full_text = "".join(accumulated)
            # Persist the assistant turn.
            try:
                with get_connection() as conn:
                    row2 = conn.execute(
                        "SELECT messages FROM conversations WHERE id = ?",
                        (conversation_id,),
                    ).fetchone()
                    if row2 is not None:
                        msgs = json.loads(row2["messages"])
                        msgs.append(
                            {"role": "assistant", "content": full_text, "ts": _now_iso()}
                        )
                        conn.execute(
                            "UPDATE conversations SET messages = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (json.dumps(msgs, ensure_ascii=False), conversation_id),
                        )
                        conn.commit()
            except Exception as exc:
                logger.exception("failed to persist assistant message for %s", conversation_id)

            yield f"event: done\ndata: {json.dumps({'length': len(full_text)}, ensure_ascii=False)}\n\n".encode(
                "utf-8"
            )
        except LLMError as exc:
            err_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {err_payload}\n\n".encode("utf-8")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering (Phase 5: Cloudflare)
        },
    )


@router.post("/{conversation_id}/end")
async def end_conversation(conversation_id: str = Path(...)):
    """End the conversation, run LLM extraction, and persist a diary entry."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            return err(f"Conversation {conversation_id} not found", "NOT_FOUND", status_code=404)
        if row["status"] == "saved":
            return err(
                "Conversation already saved",
                "ALREADY_SAVED",
                status_code=409,
            )
        conv = _row_to_conversation(row)
    except Exception as exc:
        logger.exception("end_conversation load failed")
        return err(f"DB error: {exc}", "DB_ERROR", status_code=500)

    # ---- Extract --------------------------------------------------------
    raw_text = _user_text(conv)
    msgs_for_llm = [
        MessageSchema(role=m["role"], content=m.get("content", ""))
        for m in conv["messages"]
    ]
    try:
        extracted = await extract_from_conversation(msgs_for_llm, raw_text)
    except Exception as exc:
        logger.exception("extraction failed; using fallback")
        extracted = {
            "summary": (raw_text[:200] if raw_text else "(empty entry)"),
            "mood": None,
            "events": [],
            "people": [],
            "follow_ups": [],
        }

    # ---- Persist diary entry + mark conversation saved -------------------
    # Try to attach a weather snapshot if the user provided a location via
    # the request. The frontend passes {lat, lon} in the body; if not present
    # we skip silently (weather is best-effort).
    weather_snapshot = None
    try:
        body_loc = None  # we don't read the body here; the frontend passes weather pre-fetched
    except Exception:
        pass

    try:
        with get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO diary_entries
                       (raw_text, summary, conversation, mood, events, people, follow_ups, weather)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    raw_text,
                    extracted.get("summary"),
                    json.dumps(conv["messages"], ensure_ascii=False),
                    extracted.get("mood"),
                    json.dumps(extracted.get("events") or [], ensure_ascii=False),
                    json.dumps(extracted.get("people") or [], ensure_ascii=False),
                    json.dumps(extracted.get("follow_ups") or [], ensure_ascii=False),
                    json.dumps(weather_snapshot) if weather_snapshot else None,
                ),
            )
            entry_id = cur.lastrowid
            conn.execute(
                """UPDATE conversations
                      SET status = 'saved',
                          diary_entry_id = ?,
                          updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?""",
                (entry_id, conversation_id),
            )
            conn.commit()
    except Exception as exc:
        logger.exception("end_conversation insert failed")
        return err(f"DB error: {exc}", "DB_ERROR", status_code=500)

    # Vectorize the new entry for future RAG. Non-fatal if it fails.
    try:
        vector.add_entry(
            entry_id=entry_id,
            text=raw_text,
            created_at=_now_iso(),
            summary=extracted.get("summary"),
            mood=extracted.get("mood"),
        )
    except Exception as exc:
        logger.warning("vectorize on end_conversation failed: %s", exc)

    return ok({
        "diary_entry_id": entry_id,
        "summary": extracted.get("summary"),
        "mood": extracted.get("mood"),
        "events": extracted.get("events", []),
        "people": extracted.get("people", []),
        "follow_ups": extracted.get("follow_ups", []),
    })
