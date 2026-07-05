"""LLM client (DeepSeek, OpenAI-compatible).

Two functions:
  - `stream_chat(messages)` for chat UI (SSE-yielded token deltas)
  - `complete_chat(messages, json_mode=True)` for one-shot extraction

Both retry on transient errors (5xx, 429, connect/read timeouts) with
exponential backoff up to 3 attempts. Auth comes from `app.config`.

Provider is swappable via env (DEEPSEEK_BASE_URL + DEEPSEEK_MODEL) — the
code is plain OpenAI-compatible and works with any provider exposing the
same /chat/completions shape (DeepSeek, Moonshot, GLM, SiliconFlow, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Dict, List, Optional

import httpx

from app.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

logger = logging.getLogger("diary.llm")

DEFAULT_TIMEOUT = 60.0
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 500

# Status codes that warrant a retry.
_RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """Raised when the LLM call fails after retries."""


def _headers() -> Dict[str, str]:
    if not DEEPSEEK_API_KEY:
        raise LLMError("DEEPSEEK_API_KEY is not set in .env (copy .env.example to .env)")
    return {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }


def _url() -> str:
    return f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"


async def stream_chat(
    messages: List[Dict[str, str]],
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    model: Optional[str] = None,
) -> AsyncIterator[str]:
    """Yield text chunks from a streaming chat completion.

    Args:
        messages: OpenAI-style `[{role, content}, ...]` list.
        temperature: Sampling temperature. 0.7 is a reasonable default.
        max_tokens: Cap on tokens in the response.
        model: Override the configured model; defaults to `DEEPSEEK_MODEL`.

    Yields:
        Successive text deltas as strings (NOT JSON).

    Raises:
        LLMError: If all retries fail or the response is non-200.
    """
    payload = {
        "model": model or DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                async with client.stream("POST", _url(), json=payload, headers=_headers()) as resp:
                    if resp.status_code in _RETRY_STATUS:
                        last_exc = LLMError(f"LLM transient error {resp.status_code}")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise LLMError(
                            f"LLM error {resp.status_code}: {body.decode(errors='ignore')[:300]}"
                        )

                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        content = delta.get("content")
                        if content:
                            yield content
                    return
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_exc = LLMError(f"LLM connection error: {exc}")
            await asyncio.sleep(2 ** attempt)
            continue
        except LLMError:
            raise
        except Exception as exc:  # last-resort safety net
            raise LLMError(f"Unexpected LLM error: {exc}") from exc

    raise last_exc or LLMError("LLM stream failed after retries")


async def complete_chat(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 800,
    json_mode: bool = False,
    model: Optional[str] = None,
) -> str:
    """Single-shot chat completion. Returns the full assistant text.

    Args:
        messages: OpenAI-style message list.
        temperature: Sampling temperature. 0.2 keeps extraction stable.
        max_tokens: Cap on output tokens.
        json_mode: If True, sets `response_format={"type":"json_object"}` to
            nudge the model to return valid JSON. Models that don't support
            it will ignore the field; the caller should still validate output.
        model: Override the configured model.

    Returns:
        The full assistant text content.

    Raises:
        LLMError: If all retries fail or the response is non-200.
    """
    payload: Dict[str, object] = {
        "model": model or DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # NOTE: do NOT set `response_format={"type":"json_object"}` — some
    # OpenAI-compatible providers reject it. The extraction service handles
    # JSON via prompt + parser instead.

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.post(_url(), json=payload, headers=_headers())
                if resp.status_code in _RETRY_STATUS:
                    last_exc = LLMError(f"LLM transient error {resp.status_code}")
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status_code != 200:
                    raise LLMError(
                        f"LLM error {resp.status_code}: {resp.text[:300]}"
                    )
                data = resp.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
                if content is None:
                    raise LLMError(f"LLM response missing content: {data!r}")
                return content
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_exc = LLMError(f"LLM connection error: {exc}")
            await asyncio.sleep(2 ** attempt)
            continue
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Unexpected LLM error: {exc}") from exc

    raise last_exc or LLMError("LLM completion failed after retries")
