"""Typed request/response contracts shared by text, voice and avatar modes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
InteractionMode = Literal["text", "voice", "avatar"]
ResponseStatus = Literal["answered", "low_confidence", "no_knowledge", "needs_clarification", "handoff_offered", "incomplete"]
LeadStatus = Literal["none", "exploring", "qualified", "captured", "handoff_requested"]


class Source(BaseModel):
    index: int
    title: str | None = None
    source_document: str
    source_url: str | None = None
    page_number: int | None = None
    service_category: str | None = None
    snippet: str = ""


class Confidence(BaseModel):
    level: Literal["high", "medium", "low", "none"]
    score: float = Field(ge=0.0, le=1.0)


class SuggestedAction(BaseModel):
    type: Literal["open_lead_form", "request_human_handoff"]
    label: str


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=ID_PATTERN)
    conversation_id: str | None = Field(default=None, pattern=ID_PATTERN)
    message: str = Field(min_length=1, max_length=8000)
    interaction_mode: InteractionMode = "text"
    transcript_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    client_message_id: str | None = Field(default=None, pattern=ID_PATTERN)
    interrupted_previous: bool = False
    client_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be blank")
        return v

    @field_validator("client_metadata")
    @classmethod
    def _small_metadata(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 20 or any(len(k) > 64 or len(x) > 256 for k, x in v.items()):
            raise ValueError("client_metadata too large")
        return v


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    response_status: ResponseStatus
    sources: list[Source] = Field(default_factory=list)
    confidence: Confidence
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    lead_status: LeadStatus = "none"
    interaction_mode: InteractionMode = "text"
    conversation_restarted: bool = False
    duplicate: bool = False
    timings_ms: dict[str, float] = Field(default_factory=dict)
    is_truncated: bool = False
    finish_reason: str | None = None


class TurnView(BaseModel):
    message_id: str
    role: Literal["user", "assistant"]
    content: str
    ts: str
    mode: InteractionMode = "text"


class ConversationView(BaseModel):
    conversation_id: str
    turns: list[TurnView]
    lead_status: LeadStatus = "none"


class LeadCreate(BaseModel):
    session_id: str = Field(pattern=ID_PATTERN)
    conversation_id: str | None = Field(default=None, pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    company: str | None = Field(default=None, max_length=160)
    project_type: str | None = Field(default=None, max_length=200)
    requirements: str | None = Field(default=None, max_length=2000)
    timeline: str | None = Field(default=None, max_length=200)
    budget: str | None = Field(default=None, max_length=200)
    preferred_contact: str | None = Field(default=None, max_length=100)
    consent: bool = False
    request_human_follow_up: bool = False


class LeadResponse(BaseModel):
    lead_id: int
    status: str
    duplicate: bool
    qualification_score: int
    follow_up_requested: bool
    notified: bool
    message: str


class TranscribeResponse(BaseModel):
    transcript: str
    language: str | None = None
    duration_s: float
    confidence: float | None = None
    low_confidence: bool = False


class VoiceChatResponse(BaseModel):
    transcript: str
    transcript_confidence: float | None = None
    low_confidence: bool = False
    needs_confirmation: bool = False
    chat: ChatResponse | None = None
    audio_base64: str | None = None
    audio_mime: str | None = None
    tts_error: str | None = None


class SynthesizeRequest(BaseModel):
    session_id: str = Field(pattern=ID_PATTERN)
    text: str = Field(min_length=1, max_length=4000)
