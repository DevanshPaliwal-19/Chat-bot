"""All HTTP routes. Text, voice and avatar all end up in ConversationEngine.handle()."""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Literal

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from pydantic import BaseModel

from .. import __version__
from ..errors import AppError, GatewayTimeout, NotFound, PayloadTooLarge, ValidationFailed
from ..rag.index import IndexUnavailable, KnowledgeIndex
from ..rag.ingest import Ingestor
from ..schemas import (
    ChatRequest, ChatResponse, ConversationView, LeadCreate, LeadResponse, SynthesizeRequest,
    TranscribeResponse, TurnView, VoiceChatResponse,
)
from ..speech.audio_utils import validate_audio
from .deps import check_id, enforce_rate_limit, get_container, require_admin

logger = logging.getLogger(__name__)
root = APIRouter()
v1 = APIRouter(prefix="/api/v1")


# ------------------------------------------------------------------ meta
@root.get("/health")
async def health(request: Request) -> dict:
    c = get_container(request)
    llm = await c.llm.health()
    idx = c.retriever.index
    kb = {
        "ready": c.retriever.ready,
        "chunks": len(idx.chunks) if idx else 0,
        "embedder": c.embedder.name,
        "index_version": idx.manifest.get("index_version") if idx else None,
    }
    ok = llm.get("status") == "ok" and kb["ready"]
    return {
        "status": "ok" if ok else "degraded",
        "version": __version__,
        "llm": llm,
        "knowledge_base": kb,
        "speech": {
            "stt": {"available": c.speech.stt.available, "reason": c.speech.stt.reason or None},
            "tts": {"available": c.speech.tts.available, "reason": c.speech.tts.reason or None},
        },
        "active_sessions": len(c.sessions),
    }


@root.get("/config")
async def public_config(request: Request) -> dict:
    s = get_container(request).settings
    c = get_container(request)
    return {
        "company_name": s.company_name,
        "assistant_name": s.assistant_name,
        "company_website": s.company_website,
        "interaction_modes": ["text", "voice", "avatar"],
        "model": s.llm_model,
        "limits": {"max_message_chars": s.max_message_chars, "max_audio_seconds": s.max_audio_seconds, "max_audio_mb": s.max_audio_mb},
        "features": {"voice_input": c.speech.stt.available, "voice_output": c.speech.tts.available},
        "human_handoff": {"live_notification": c.handoff.notifier.delivers, "contact_email": s.contact_email},
    }


# ------------------------------------------------------------------ chat
@v1.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    c = get_container(request)
    enforce_rate_limit(request, "chat", req.session_id)
    try:
        return await asyncio.wait_for(c.engine.handle(req, request.state.request_id), c.settings.request_timeout_s)
    except asyncio.TimeoutError as exc:
        raise GatewayTimeout() from exc


@v1.get("/conversations/{conversation_id}", response_model=ConversationView)
async def get_conversation(conversation_id: str, request: Request) -> ConversationView:
    c = get_container(request)
    check_id(conversation_id, "conversation_id")
    sid = check_id(request.headers.get("x-session-id"), "X-Session-ID")
    conv = c.sessions.find_conversation(sid, conversation_id)  # type: ignore[arg-type]
    if conv is None:
        raise NotFound("Conversation not found.", code="conversation_not_found")
    return ConversationView(
        conversation_id=conv.id,
        turns=[TurnView(message_id=t.message_id, role=t.role, content=t.content, ts=t.ts, mode=t.mode) for t in conv.turns],  # type: ignore[arg-type]
        lead_status=conv.lead.status,  # type: ignore[arg-type]
    )


@v1.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request) -> Response:
    c = get_container(request)
    check_id(conversation_id, "conversation_id")
    sid = check_id(request.headers.get("x-session-id"), "X-Session-ID")
    if not c.sessions.delete_conversation(sid, conversation_id):  # type: ignore[arg-type]
        raise NotFound("Conversation not found.", code="conversation_not_found")
    return Response(status_code=204)


# ------------------------------------------------------------------ audio
async def _read_upload(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge("That recording is too large.", code="audio_too_large")
    return data


@v1.post("/audio/transcribe", response_model=TranscribeResponse)
async def transcribe(request: Request, file: UploadFile = File(...), session_id: str = Form(...)) -> TranscribeResponse:
    c = get_container(request)
    check_id(session_id, "session_id")
    enforce_rate_limit(request, "audio", session_id)
    data = await _read_upload(file, c.settings.max_audio_bytes)
    info = validate_audio(data, c.settings)
    res = await c.speech.transcribe(data, info.format)
    low = res.confidence is not None and res.confidence < c.settings.low_transcript_confidence
    return TranscribeResponse(transcript=res.text, language=res.language, duration_s=round(res.duration_s, 2),
                              confidence=None if res.confidence is None else round(res.confidence, 3), low_confidence=low)


@v1.post("/audio/synthesize")
async def synthesize(req: SynthesizeRequest, request: Request) -> Response:
    c = get_container(request)
    enforce_rate_limit(request, "audio", req.session_id)
    audio, mime = await c.speech.synthesize(req.text)
    return Response(content=audio, media_type=mime, headers={"Cache-Control": "no-store"})


@v1.post("/audio/process", response_model=VoiceChatResponse)
async def process_audio(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(...),
    conversation_id: str | None = Form(None),
    interaction_mode: Literal["voice", "avatar"] = Form("voice"),
    client_message_id: str | None = Form(None),
    with_audio: bool = Form(True),
    confirm_low_confidence: bool = Form(True),
    interrupted_previous: bool = Form(False),
) -> VoiceChatResponse:
    """Voice turn: STT -> shared conversation engine -> optional TTS."""
    c = get_container(request)
    check_id(session_id, "session_id")
    check_id(conversation_id, "conversation_id", required=False)
    check_id(client_message_id, "client_message_id", required=False)
    enforce_rate_limit(request, "audio", session_id)

    data = await _read_upload(file, c.settings.max_audio_bytes)
    info = validate_audio(data, c.settings)
    res = await c.speech.transcribe(data, info.format)
    low = res.confidence is not None and res.confidence < c.settings.low_transcript_confidence
    base = dict(transcript=res.text, transcript_confidence=None if res.confidence is None else round(res.confidence, 3), low_confidence=low)
    if low and confirm_low_confidence:
        return VoiceChatResponse(**base, needs_confirmation=True)

    text = res.text[: c.settings.max_message_chars]
    chat_req = ChatRequest(
        session_id=session_id, conversation_id=conversation_id, message=text, interaction_mode=interaction_mode,
        transcript_confidence=base["transcript_confidence"], client_message_id=client_message_id,
        interrupted_previous=interrupted_previous,
    )
    try:
        chat_resp = await asyncio.wait_for(c.engine.handle(chat_req, request.state.request_id), c.settings.request_timeout_s)
    except asyncio.TimeoutError as exc:
        raise GatewayTimeout() from exc

    out = VoiceChatResponse(**base, chat=chat_resp)
    if with_audio:
        try:
            audio, mime = await c.speech.synthesize(chat_resp.answer)
            out.audio_base64, out.audio_mime = base64.b64encode(audio).decode("ascii"), mime
        except AppError as exc:  # the text answer is still returned
            out.tts_error = exc.code
    return out


# ------------------------------------------------------------------ leads
class StatusUpdate(BaseModel):
    status: Literal["new", "handoff_requested", "contacted", "closed"]
    note: str | None = None


@v1.post("/leads", response_model=LeadResponse)
async def create_lead(req: LeadCreate, request: Request) -> LeadResponse:
    c = get_container(request)
    enforce_rate_limit(request, "lead", req.session_id)
    conv = c.sessions.find_conversation(req.session_id, req.conversation_id) if req.conversation_id else None
    return await c.lead_service.capture(req, conv)


@v1.get("/leads")
async def list_leads(request: Request, status: str | None = None, limit: int = 100) -> dict:
    require_admin(request)
    c = get_container(request)
    rows = await asyncio.to_thread(c.lead_store.list, status, max(1, min(limit, 500)))
    return {"leads": rows, "count": len(rows)}


@v1.patch("/leads/{lead_id}/status")
async def update_lead_status(lead_id: int, body: StatusUpdate, request: Request) -> dict:
    require_admin(request)
    c = get_container(request)
    if not await asyncio.to_thread(c.lead_store.set_status, lead_id, body.status, body.note):
        raise NotFound("Lead not found.", code="lead_not_found")
    return {"lead_id": lead_id, "status": body.status}


# ------------------------------------------------------------------ knowledge
class IngestRequest(BaseModel):
    full_rebuild: bool = False


@v1.post("/knowledge/ingest")
async def ingest(req: IngestRequest, request: Request) -> dict:
    require_admin(request)
    c = get_container(request)
    if c.ingest_lock.locked():
        raise ValidationFailed("An ingestion is already running.", code="ingest_in_progress", status_code=409)
    async with c.ingest_lock:
        report = await asyncio.to_thread(Ingestor(c.settings, c.embedder).run, req.full_rebuild)
        c.reload_index()
    return report.to_dict()


@v1.get("/knowledge/status")
async def knowledge_status(request: Request) -> dict:
    require_admin(request)
    c = get_container(request)
    try:
        idx = KnowledgeIndex.load(c.settings.index_dir)
    except IndexUnavailable as exc:
        return {"indexed": False, "detail": str(exc), "loaded_in_memory": c.retriever.ready}
    m = idx.manifest
    return {
        "indexed": True, "loaded_in_memory": c.retriever.ready, "index_version": m.get("index_version"),
        "embedder": m.get("embedder"), "chunk_count": m.get("chunk_count"), "built_at": m.get("built_at"),
        "documents": {k: {"chunks": v["chunk_count"], "type": v["source_type"]} for k, v in m.get("documents", {}).items()},
    }
