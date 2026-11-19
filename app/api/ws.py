"""WebSocket voice endpoint.

Route:   ws://host/ws/voice?session_id=<id>[&conversation_id=<id>]

Protocol version: 1.0  (see docs/VOICE_PROTOCOL_SPEC.md)

Responsibilities (M2 scope)
----------------------------
* Accept WebSocket upgrade, validate session_id.
* Send session.ack with initial turn_id.
* Handle heartbeat ping/pong to keep the connection alive.
* Accept binary PCM frames and buffer them in VoiceSession.
* Accept JSON control messages (speech.started, speech.ended, session.stop).
* On speech.ended: drain the audio buffer, dispatch to STT → Engine → TTS pipeline
  (implemented progressively in M3-M6; in M2 a stub returns a placeholder response).
* On interrupt: cancel the active task, acknowledge.
* Enforce payload limits, rate-limiting and origin validation.
* Clean up the VoiceSession on disconnect.

Design decisions
----------------
* We inherit the existing app.state.container for all services — zero new DI.
* The WebSocket handler is a single async coroutine; long-running work (STT, LLM,
  TTS) is offloaded to asyncio.Task so the receive-loop stays responsive.
* All JSON messages follow the BaseMessage schema (see VOICE_PROTOCOL_SPEC.md).
* Binary frames carry an 8-byte header parsed by voice_session.parse_audio_header.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from starlette.websockets import WebSocketState

from ..config import Settings
from ..container import Container
from .voice_session import VoiceSession, parse_audio_header

logger = logging.getLogger(__name__)

voice_router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = "1.0"
HEARTBEAT_TIMEOUT_S = 35.0   # close if no message in this window
SESSION_IDLE_TIMEOUT_S = 300  # 5 min inactivity → clean shutdown
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def _msg(type_: str, turn_id: int, **extra: Any) -> str:
    return json.dumps({"type": type_, "protocol_version": PROTOCOL_VERSION,
                       "turn_id": turn_id, **extra})


def _error_msg(code: int, message: str, turn_id: int = 0) -> str:
    return json.dumps({"type": "error", "protocol_version": PROTOCOL_VERSION,
                       "turn_id": turn_id, "code": code, "message": message})


# ---------------------------------------------------------------------------
# Origin validation
# ---------------------------------------------------------------------------

def _check_origin(websocket: WebSocket, settings: Settings) -> bool:
    origin = websocket.headers.get("origin", "")
    if not origin:
        # No Origin header — allow in development, block in production.
        return settings.app_env != "production"
    allowed = [o.rstrip("/") for o in settings.cors_origin_list]
    return origin.rstrip("/") in allowed


# ---------------------------------------------------------------------------
# Main WebSocket handler
# ---------------------------------------------------------------------------

@voice_router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket):
    """Entry point for the continuous-voice WebSocket session."""
    container: Container = websocket.app.state.container
    settings = container.settings

    # --- origin check ---
    if not _check_origin(websocket, settings):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        logger.warning("voice_ws rejected — bad origin: %s",
                       websocket.headers.get("origin", ""))
        return

    # --- session_id validation ---
    session_id = websocket.query_params.get("session_id", "")
    conversation_id = websocket.query_params.get("conversation_id") or None
    if not _ID_PATTERN.match(session_id):
        await websocket.accept()
        await websocket.send_text(_error_msg(401, "Invalid or missing session_id"))
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        logger.warning("voice_ws rejected — invalid session_id")
        return

    await websocket.accept()

    vsession = VoiceSession(session_id=session_id, conversation_id=conversation_id)
    request_id = uuid.uuid4().hex[:12]

    logger.info("voice_ws connected session=%s request=%s", session_id, request_id)

    # Initial ack — turn_id=0 means "not in a turn yet"
    await websocket.send_text(_msg("session.ack", turn_id=0,
                                   server_timestamp=_iso_now()))

    try:
        await _run_session(websocket, vsession, container, request_id)
    except WebSocketDisconnect:
        logger.info("voice_ws disconnected session=%s", session_id)
    except Exception as exc:
        logger.exception("voice_ws unhandled error session=%s: %s", session_id, exc)
        await _safe_close(websocket, status.WS_1011_INTERNAL_ERROR)
    finally:
        await vsession.cancel_and_wait()
        logger.info("voice_ws cleaned up session=%s turns=%d", session_id,
                    vsession.turn_id)


async def _run_session(
    ws: WebSocket,
    vsession: VoiceSession,
    container: Container,
    request_id: str,
) -> None:
    """Main receive-loop.  Runs until the client disconnects or idles out.

    Heartbeat enforcement runs as a parallel asyncio.Task so the receive-loop
    uses a plain ``await ws.receive()`` with no nested ``asyncio.wait_for``.
    This avoids deadlocks inside the Starlette TestClient (anyio threadpool)
    while keeping full heartbeat timeout support in production.
    """
    closed = asyncio.Event()

    async def _heartbeat_watchdog() -> None:
        while not closed.is_set():
            await asyncio.sleep(HEARTBEAT_TIMEOUT_S)
            if closed.is_set():
                return
            if vsession.idle_seconds() >= HEARTBEAT_TIMEOUT_S:
                logger.warning("voice_ws heartbeat timeout session=%s",
                               vsession.session_id)
                await _safe_close(ws, status.WS_1001_GOING_AWAY)
                closed.set()
                return

    watchdog = asyncio.create_task(_heartbeat_watchdog())
    try:
        while not closed.is_set():
            raw = await ws.receive()
            vsession.last_activity = time.monotonic()

            msg_type = raw.get("type")

            # --- binary audio frame ---
            if msg_type == "websocket.receive" and raw.get("bytes") is not None:
                await _handle_binary(ws, vsession, raw["bytes"])
                continue

            # --- text JSON message ---
            if msg_type == "websocket.receive" and raw.get("text") is not None:
                await _handle_text(ws, vsession, container, raw["text"], request_id)
                continue

            # --- disconnect ---
            if msg_type == "websocket.disconnect":
                raise WebSocketDisconnect(raw.get("code", 1000))
    finally:
        closed.set()
        watchdog.cancel()


# ---------------------------------------------------------------------------
# Binary frame handler
# ---------------------------------------------------------------------------

async def _handle_binary(ws: WebSocket, vsession: VoiceSession, data: bytes) -> None:
    try:
        turn_id, seq, payload = parse_audio_header(data)
    except ValueError as exc:
        await ws.send_text(_error_msg(422, f"Bad audio frame: {exc}",
                                      vsession.turn_id))
        return

    accepted = vsession.accept_audio_frame(turn_id, seq, payload)
    if not accepted and turn_id == vsession.turn_id:
        # Rate-limited or buffer overflow
        await ws.send_text(_error_msg(429, "Audio frame rate exceeded or buffer full",
                                      vsession.turn_id))


# ---------------------------------------------------------------------------
# JSON message handler
# ---------------------------------------------------------------------------

async def _handle_text(
    ws: WebSocket,
    vsession: VoiceSession,
    container: Container,
    text: str,
    request_id: str,
) -> None:
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        await ws.send_text(_error_msg(400, "Invalid JSON", vsession.turn_id))
        return

    msg_type = msg.get("type", "")
    pv = msg.get("protocol_version", "")

    # Minimal version check — reject unknown major versions
    if pv and not pv.startswith("1."):
        await ws.send_text(_error_msg(400,
            f"Unsupported protocol_version '{pv}'; server supports 1.x",
            vsession.turn_id))
        return

    if msg_type == "heartbeat":
        await ws.send_text(_msg("heartbeat", vsession.turn_id,
                                timestamp=_iso_now()))

    elif msg_type == "speech.started":
        new_turn = await vsession.next_turn()
        logger.debug("voice_ws speech.started session=%s turn=%d",
                     vsession.session_id, new_turn)
        # Acknowledge so the client knows which turn_id the server is tracking
        await ws.send_text(_msg("speech.started.ack", turn_id=new_turn))

    elif msg_type == "speech.ended":
        client_turn = msg.get("turn_id", vsession.turn_id)
        if client_turn != vsession.turn_id:
            # Stale event — ignore
            return
        audio = vsession.drain_audio()
        logger.debug("voice_ws speech.ended session=%s turn=%d bytes=%d",
                     vsession.session_id, vsession.turn_id, len(audio))
        # Dispatch to processing pipeline (M3-M6 will fill this in).
        # In M2 we confirm receipt and echo a placeholder transcript.
        await ws.send_text(_msg("transcript.final", vsession.turn_id,
                                text="[M2 stub — STT not yet wired]",
                                confidence=None))
        await ws.send_text(_msg("assistant.thinking", vsession.turn_id))
        # Kick off the (currently stub) processing task
        task = asyncio.create_task(
            _process_turn_stub(ws, vsession, container, vsession.turn_id,
                               audio, request_id)
        )
        vsession.set_active_task(task)

    elif msg_type == "assistant.interrupt":
        await _handle_interrupt(ws, vsession)

    elif msg_type == "session.stop":
        await ws.send_text(_msg("session.stopped", vsession.turn_id,
                                server_timestamp=_iso_now()))
        await _safe_close(ws, status.WS_1000_NORMAL_CLOSURE)

    else:
        await ws.send_text(_error_msg(400,
            f"Unknown message type: '{msg_type}'", vsession.turn_id))


# ---------------------------------------------------------------------------
# Interrupt handler
# ---------------------------------------------------------------------------

async def _handle_interrupt(ws: WebSocket, vsession: VoiceSession) -> None:
    logger.info("voice_ws interrupt session=%s turn=%d",
                vsession.session_id, vsession.turn_id)
    await vsession.cancel_and_wait()
    await ws.send_text(_msg("assistant.interrupted", vsession.turn_id))


# ---------------------------------------------------------------------------
# Processing stub (M2) — replaced progressively in M3-M6
# ---------------------------------------------------------------------------

async def _process_turn_stub(
    ws: WebSocket,
    vsession: VoiceSession,
    container: Container,
    turn_id: int,
    audio: bytes,
    request_id: str,
) -> None:
    """M2 stub: simulates a 500 ms think + speaking state, then completes the turn.

    This coroutine will be replaced by the real STT → Engine → TTS pipeline
    in milestones M3-M6.  It is intentionally minimal so M2 can be tested end-to-end.
    """
    try:
        await asyncio.sleep(0.5)  # simulate processing latency

        # Guard: if the turn changed while we were "processing", discard.
        if vsession.turn_id != turn_id:
            return

        await _safe_send(ws, _msg("assistant.speaking", turn_id))
        await asyncio.sleep(0.3)  # simulate TTS streaming

        if vsession.turn_id != turn_id:
            return

        await _safe_send(ws, _msg("turn.complete", turn_id,
                                  answer="[M2 stub — full pipeline in M3-M6]"))
    except asyncio.CancelledError:
        # Task was cancelled by an interrupt or new turn — expected.
        logger.debug("voice_ws processing cancelled turn=%d", turn_id)
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _safe_send(ws: WebSocket, text: str) -> None:
    """Send a text message if the socket is still open."""
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_text(text)
    except Exception:
        pass


async def _safe_close(ws: WebSocket, code: int) -> None:
    try:
        if ws.client_state != WebSocketState.DISCONNECTED:
            await ws.close(code=code)
    except Exception:
        pass


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
