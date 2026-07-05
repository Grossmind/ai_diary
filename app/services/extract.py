"""LLM-based structured extraction for diary conversations.

Takes a list of messages and returns a dict matching `ExtractedEntrySchema`,
suitable for direct insertion into `diary_entries`. Falls back gracefully if
the LLM call or JSON parsing fails — the user always gets a saved entry.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.models import ExtractedEntrySchema, MessageSchema
from app.services.llm import LLMError, complete_chat

logger = logging.getLogger("diary.extract")

EXTRACTION_SYSTEM_PROMPT = """你是一个日记结构化提取助手。给定用户与日记助理的对话，输出严格的 JSON 对象（不要 Markdown 代码块、不要解释、不要多余文本）。

输出格式必须完全匹配：
{
  "summary": "<一句话总结，<= 30 字，与用户语言相同>",
  "mood": "<一个情绪标签，如：开心/难过/焦虑/平静/兴奋/疲惫/反思/愤怒/other；不确定时为 null>",
  "events": [{"description": "<发生的事>", "time_anchor": "<时间锚点，如 '今天'、'上周'、'2026-06-03'，不确定时为空字符串>"}],
  "people": ["<人名1>", "<人名2>"],
  "follow_ups": [{"description": "<用户提到要做的事>", "due": "<截止时间，如 '下周'、'2026-06-10'，不确定时为空字符串>"}]
}

规则：
1. 所有 key 必须存在；为空时用 [] 或 null
2. summary 必须非空
3. events/people/follow_ups 中没提到就返回 []
4. people 仅包含人名（不要头衔、关系）
5. 始终用 JSON 输出，无任何额外字符
"""


# Match ```json ... ``` or ``` ... ``` blocks.
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
# Match the first {...} block (greedy from first { to last }).
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """Try to parse JSON out of a string, handling markdown fences and prose."""
    s = text.strip()
    if not s:
        return None
    # 1. Direct parse.
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 2. Strip a leading "Here is the JSON:" or similar prefix.
    if "{" in s:
        candidate = s[s.index("{") :]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # 3. Fenced block.
    m = _FENCE_RE.search(s)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 4. First {...} block.
    m = _JSON_RE.search(s)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _fallback_extraction(raw_text: str) -> Dict[str, Any]:
    """Last-resort record when extraction can't produce a valid result."""
    trimmed = (raw_text or "").strip()
    summary = trimmed[:120].rsplit(" ", 1)[0] if trimmed else "(empty entry)"
    return {
        "summary": summary or "(empty entry)",
        "mood": None,
        "events": [],
        "people": [],
        "follow_ups": [],
    }


def _build_llm_messages(messages: List[MessageSchema]) -> List[Dict[str, str]]:
    """Build the LLM message list for extraction: system + full conversation."""
    out: List[Dict[str, str]] = [{"role": "system", "content": EXTRACTION_SYSTEM_PROMPT}]
    for m in messages:
        if m.role in ("user", "assistant", "system"):
            out.append({"role": m.role, "content": m.content})
    return out


async def extract_from_conversation(
    messages: List[MessageSchema],
    raw_text_fallback: str,
) -> Dict[str, Any]:
    """Run LLM extraction. Always returns a dict; falls back on failure.

    Args:
        messages: All turns in the conversation (incl. assistant, user, system).
        raw_text_fallback: Concatenated user text, used in fallback `summary`.

    Returns:
        Dict with keys: summary, mood, events, people, follow_ups.
    """
    if not messages:
        return _fallback_extraction(raw_text_fallback)

    chat_messages = _build_llm_messages(messages)
    last_error: Optional[str] = None

    for attempt in range(2):
        try:
            text = await complete_chat(
                chat_messages,
                temperature=0.2,
                max_tokens=800,
                json_mode=True,
            )
            parsed = _try_parse_json(text)
            if parsed is None:
                last_error = f"could not parse JSON from: {text[:200]!r}"
                logger.warning("extract parse fail attempt=%d: %s", attempt, last_error)
                continue
            try:
                obj = ExtractedEntrySchema.model_validate(parsed)
            except ValidationError as ve:
                last_error = f"validation error: {ve}"
                logger.warning("extract validation fail attempt=%d: %s", attempt, last_error)
                continue
            return obj.model_dump()

        except LLMError as exc:
            last_error = str(exc)
            logger.warning("extract LLM error attempt=%d: %s", attempt, exc)
            # Don't retry on persistent LLM errors — the retry inside
            # `complete_chat` already handled 5xx/429.

    logger.error("extract failed after attempts: %s; using fallback", last_error)
    return _fallback_extraction(raw_text_fallback)
