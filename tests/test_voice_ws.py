"""M2 Integration Tests — WebSocket Voice Transport.

Tests the /ws/voice endpoint for:
  - Successful handshake and session.ack.
  - Heartbeat ping/pong.
  - Binary audio frame acceptance (valid + malformed).
  - speech.started / speech.ended turn lifecycle (M2 stub response).
  - Interruption flow (assistant.interrupt → assistant.interrupted).
  - session.stop graceful closure.
  - Origin and session_id validation.
  - Protocol version rejection.

All tests use Starlette's TestClient in WebSocket mode; no real audio or
model inference is performed.
"""
from __future__ import annotations

import json
import struct
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# conftest.py is auto-discovered by pytest — fixtures (client, etc.) come from there.


# ---------------------------------------------------------------------------
# Helper: build a valid binary frame with the 8-byte header
# ---------------------------------------------------------------------------

def _make_frame(turn_id: int, seq: int, pcm_bytes: bytes = b"\x00\x01" * 160) -> bytes:
    """Return a correctly formatted binary audio frame (header + PCM payload)."""
    header = struct.pack("<II", turn_id, seq)
    return header + pcm_bytes


def _decode(text: str) -> dict:
    return json.loads(text)


# ---------------------------------------------------------------------------
# Shared session_id
# ---------------------------------------------------------------------------
VALID_SID = "sess_ws_m2test"   # 14 chars — matches ID_PATTERN


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHandshake:
    """Session establishment and ack."""

    def test_valid_session_id_receives_ack(self, client: TestClient):
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            msg = _decode(ws.receive_text())
            assert msg["type"] == "session.ack"
            assert msg["protocol_version"] == "1.0"
            assert msg["turn_id"] == 0
            assert "server_timestamp" in msg

    def test_missing_session_id_rejected(self, client: TestClient):
        with client.websocket_connect("/ws/voice") as ws:
            msg = _decode(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == 401

    def test_short_session_id_rejected(self, client: TestClient):
        with client.websocket_connect("/ws/voice?session_id=abc") as ws:
            msg = _decode(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == 401

    def test_conversation_id_accepted(self, client: TestClient):
        """Optional conversation_id query param must not break the handshake."""
        with client.websocket_connect(
            f"/ws/voice?session_id={VALID_SID}&conversation_id=conv_12345678"
        ) as ws:
            msg = _decode(ws.receive_text())
            assert msg["type"] == "session.ack"


class TestHeartbeat:
    def test_heartbeat_pong(self, client: TestClient):
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # consume ack
            ws.send_text(json.dumps({
                "type": "heartbeat",
                "protocol_version": "1.0",
                "turn_id": 0,
                "timestamp": "2026-11-18T00:00:00Z",
            }))
            msg = _decode(ws.receive_text())
            assert msg["type"] == "heartbeat"
            assert "timestamp" in msg


class TestBinaryFrames:
    def _connect_and_start_turn(self, ws) -> int:
        ws.receive_text()  # ack
        ws.send_text(json.dumps({
            "type": "speech.started", "protocol_version": "1.0", "turn_id": 0
        }))
        ack = _decode(ws.receive_text())
        assert ack["type"] == "speech.started.ack"
        return ack["turn_id"]

    def test_valid_frame_accepted_silently(self, client: TestClient):
        """A well-formed binary frame must not trigger any error response."""
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            turn_id = self._connect_and_start_turn(ws)
            frame = _make_frame(turn_id, 0)
            ws.send_bytes(frame)
            # No immediate error should come back — send a heartbeat and check
            ws.send_text(json.dumps({
                "type": "heartbeat", "protocol_version": "1.0", "turn_id": turn_id
            }))
            msg = _decode(ws.receive_text())
            assert msg["type"] == "heartbeat"  # no error was queued first

    def test_too_short_frame_returns_error(self, client: TestClient):
        """A binary frame shorter than 8 bytes (header size) must return a 422 error."""
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            ws.send_bytes(b"\x00\x01\x02")  # only 3 bytes — no valid header
            msg = _decode(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == 422

    def test_odd_payload_length_returns_error(self, client: TestClient):
        """PCM payload with an odd number of bytes is invalid int16."""
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            header = struct.pack("<II", 1, 0)
            ws.send_bytes(header + b"\x00\x01\x02")  # 3 odd bytes of PCM
            msg = _decode(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == 422

    def test_stale_turn_id_frame_dropped(self, client: TestClient):
        """A frame carrying a turn_id lower than the current turn must be silently dropped."""
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            # Advance to turn 1
            ws.send_text(json.dumps({
                "type": "speech.started", "protocol_version": "1.0", "turn_id": 0
            }))
            ws.receive_text()  # speech.started.ack
            # Send a frame with turn_id=0 (stale)
            stale_frame = _make_frame(0, 99)
            ws.send_bytes(stale_frame)
            # No error — heartbeat should come back clean
            ws.send_text(json.dumps({
                "type": "heartbeat", "protocol_version": "1.0", "turn_id": 1
            }))
            msg = _decode(ws.receive_text())
            assert msg["type"] == "heartbeat"


class TestTurnLifecycle:
    def test_full_turn_stub_response(self, client: TestClient):
        """speech.started → audio frames → speech.ended should produce stub responses."""
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            # Start turn
            ws.send_text(json.dumps({
                "type": "speech.started", "protocol_version": "1.0", "turn_id": 0
            }))
            ack = _decode(ws.receive_text())
            assert ack["type"] == "speech.started.ack"
            turn_id = ack["turn_id"]
            assert turn_id == 1

            # Send a few audio frames
            for seq in range(3):
                ws.send_bytes(_make_frame(turn_id, seq))

            # End the turn
            ws.send_text(json.dumps({
                "type": "speech.ended", "protocol_version": "1.0",
                "turn_id": turn_id, "reason": "silence"
            }))

            # Expect: transcript.final, then assistant.thinking, then (async) speaking + complete
            transcript_msg = _decode(ws.receive_text())
            assert transcript_msg["type"] == "transcript.final"
            assert transcript_msg["turn_id"] == turn_id

            thinking_msg = _decode(ws.receive_text())
            assert thinking_msg["type"] == "assistant.thinking"

            # Wait for the stub async task to fire (≤ 2 s)
            speaking_msg = _decode(ws.receive_text(timeout=2))
            assert speaking_msg["type"] == "assistant.speaking"

            complete_msg = _decode(ws.receive_text(timeout=2))
            assert complete_msg["type"] == "turn.complete"

    def test_second_turn_increments_turn_id(self, client: TestClient):
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            for expected_turn in [1, 2]:
                ws.send_text(json.dumps({
                    "type": "speech.started", "protocol_version": "1.0", "turn_id": 0
                }))
                ack = _decode(ws.receive_text())
                assert ack["turn_id"] == expected_turn


class TestInterruption:
    def test_interrupt_acknowledged(self, client: TestClient):
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            ws.send_text(json.dumps({
                "type": "speech.started", "protocol_version": "1.0", "turn_id": 0
            }))
            ws.receive_text()  # speech.started.ack
            ws.send_text(json.dumps({
                "type": "assistant.interrupt", "protocol_version": "1.0", "turn_id": 1
            }))
            msg = _decode(ws.receive_text())
            assert msg["type"] == "assistant.interrupted"


class TestSessionStop:
    def test_session_stop_closes_cleanly(self, client: TestClient):
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            ws.send_text(json.dumps({
                "type": "session.stop", "protocol_version": "1.0", "turn_id": 0
            }))
            msg = _decode(ws.receive_text())
            assert msg["type"] == "session.stopped"


class TestProtocolValidation:
    def test_unknown_message_type_returns_error(self, client: TestClient):
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            ws.send_text(json.dumps({
                "type": "nonsense.type", "protocol_version": "1.0", "turn_id": 0
            }))
            msg = _decode(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == 400

    def test_unsupported_protocol_version_rejected(self, client: TestClient):
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            ws.send_text(json.dumps({
                "type": "heartbeat", "protocol_version": "9.0", "turn_id": 0
            }))
            msg = _decode(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == 400

    def test_invalid_json_returns_error(self, client: TestClient):
        with client.websocket_connect(f"/ws/voice?session_id={VALID_SID}") as ws:
            ws.receive_text()  # ack
            ws.send_text("{not valid json")
            msg = _decode(ws.receive_text())
            assert msg["type"] == "error"
            assert msg["code"] == 400
