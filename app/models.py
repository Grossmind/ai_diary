"""Pydantic schemas for the diary API.

Kept separate from the router so types can be imported anywhere without
pulling in FastAPI route registration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Diary entry schemas (Phase 1)
# =============================================================================
class ConversationTurnSchema(BaseModel):
    """One message in a multi-turn conversation (added in Phase 2)."""

    role: str = Field(..., description="'user' or 'assistant'")
    content: str
    ts: Optional[datetime] = None


class DiaryEntryCreateSchema(BaseModel):
    """Payload for POST /api/diary."""

    text: str = Field(..., min_length=1, max_length=10000, description="Raw transcript / text to store")
    conversation: Optional[List[ConversationTurnSchema]] = Field(
        default=None, description="Optional full multi-turn history (Phase 2 will populate this)"
    )
    audio_url: Optional[str] = Field(default=None, description="Optional URL/path to the original recording")
    weather: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional weather snapshot (Phase 3.5): {temp_c, condition, location, source, captured_at}",
    )
    created_at: Optional[str] = Field(
        default=None,
        description="Optional ISO timestamp; defaults to CURRENT_TIMESTAMP if omitted",
    )


class DiaryEntrySchema(BaseModel):
    """Full diary entry as returned by the API."""

    id: int
    created_at: datetime
    raw_text: str
    summary: Optional[str] = None
    conversation: Optional[List[Dict[str, Any]]] = None
    mood: Optional[str] = None
    events: Optional[List[Dict[str, Any]]] = None
    people: Optional[List[Dict[str, Any]]] = None
    follow_ups: Optional[List[Dict[str, Any]]] = None
    audio_url: Optional[str] = None
    weather: Optional[Dict[str, Any]] = None
    raw_metadata: Optional[Dict[str, Any]] = None


class DiaryListResponseSchema(BaseModel):
    """Envelope for GET /api/diary."""

    items: List[DiaryEntrySchema]
    total: int
    limit: int
    offset: int


class DiaryImportSchema(BaseModel):
    """Body of POST /api/diary/import — a list of full entries to bulk-insert."""

    entries: List[Dict[str, Any]] = Field(..., min_length=1, max_length=500)


# =============================================================================
# Chat schemas (Phase 2)
# =============================================================================
class MessageSchema(BaseModel):
    """A single message in a conversation thread (matches DB JSON shape)."""

    role: str = Field(..., description="'user' | 'assistant' | 'system'")
    content: str
    ts: Optional[str] = None  # ISO-8601 string for JSON friendliness


class ConversationSchema(BaseModel):
    """Full conversation as returned by GET /api/chat/{id}."""

    id: str
    created_at: str
    updated_at: str
    status: str
    diary_entry_id: Optional[int] = None
    messages: List[MessageSchema]


class ChatStartResponseSchema(BaseModel):
    """Response of POST /api/chat."""

    conversation_id: str
    welcome_message: str
    memory_mode: bool = False


class ChatMessageRequestSchema(BaseModel):
    """Payload for POST /api/chat/{id}/message."""

    content: str = Field(..., min_length=1, max_length=10000)


class ChatMemoryModeRequestSchema(BaseModel):
    """Payload for PATCH /api/chat/{id}."""

    memory_mode: bool


class ExtractedEntrySchema(BaseModel):
    """Structured fields the LLM extracts from a conversation."""

    summary: str = Field(..., min_length=1, max_length=500)
    mood: Optional[str] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)
    people: List[str] = Field(default_factory=list)
    follow_ups: List[Dict[str, Any]] = Field(default_factory=list)


class ChatEndResponseSchema(BaseModel):
    """Response of POST /api/chat/{id}/end."""

    diary_entry_id: int
    summary: str
    mood: Optional[str] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)
    people: List[str] = Field(default_factory=list)
    follow_ups: List[Dict[str, Any]] = Field(default_factory=list)
