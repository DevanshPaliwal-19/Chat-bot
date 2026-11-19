"""Per-connection voice session state container.

Each WebSocket connection gets exactly one VoiceSession instance that owns:
  - Monotonic turn counter.
  - PCM audio ring-buffer for the current utterance.
  - Cancellation token for the active processing task.
  - Lightweight lock protecting turn transitions.

Design rules
------------
* No I/O happens inside VoiceSession.  It is a pure data-holder.
* The WebSocket handler in ws.py drives all state transitions.
* The turn_id starts at 1 and is incremented on every new user utterance.
"""
from __future__ import annotations

import asyncio
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Binary frame header (8 bytes, little-endian)
#   Offset 0-3  uint32  turn_id   – monotonic, matches JSON turn_id
#   Offset 4-7  uint32  seq       – per-turn frame sequence number
# ---------------------------------------------------------------------------
_HEADER_FMT = "<II"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # always 8

SAMPLE_RATE = 16_000          # Hz — Faster-Whisper expects 16 kHz
FRAME_MS = 20                 # ms per PCM frame
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 320 samples
FRAME_BYTES = FRAME_SAMPLES * 2                   # int16 → 2 bytes/sample
MAX_FRAME_BYTES = 65_536      # hard cap per binary message (spec §7)
MAX_UTTERANCE_BYTES = 5 * 1024 * 1024  # 5 MB ≈ 10 s at 16 kHz int16
MAX_FRAMES_PER_SECOND = 200   # rate-limit guard


def parse_audio_header(data: bytes) -> tuple[int, int, bytes]:
    """Extract (turn_id, seq, pcm_payload) from a raw binary WebSocket frame.

    Raises ValueError if the frame is malformed.
    """
    if len(data) < _HEADER_SIZE:
        raise ValueError(f"Binary frame too short: {len(data)} bytes, need ≥ {_HEADER_SIZE}")
    if len(data) > MAX_FRAME_BYTES:
        raise ValueError(f"Binary frame too large: {len(data)} bytes, max {MAX_FRAME_BYTES}")
    turn_id, seq = struct.unpack_from(_HEADER_FMT, data, 0)
    payload = data[_HEADER_SIZE:]
    if len(payload) % 2 != 0:
        raise ValueError("PCM payload length is odd — must be a multiple of 2 (int16)")
    return turn_id, seq, payload


@dataclass
class VoiceSession:
    """Runtime state for a single WebSocket voice connection."""

    session_id: str
    conversation_id: Optional[str] = None

    # --- turn management ---
    _turn_id: int = field(default=0, init=False, repr=False)
    _turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    # --- audio buffer for the current utterance ---
    _audio_chunks: list[bytes] = field(default_factory=list, init=False, repr=False)
    _audio_bytes: int = field(default=0, init=False, repr=False)
    _frame_count: int = field(default=0, init=False, repr=False)
    _last_frame_window_start: float = field(default=0.0, init=False, repr=False)
    _frames_this_window: int = field(default=0, init=False, repr=False)

    # --- cancellation of the active processing task ---
    _active_task: Optional[asyncio.Task] = field(default=None, init=False, repr=False)

    # --- session lifecycle ---
    created_at: float = field(default_factory=time.monotonic, init=False, repr=False)
    last_activity: float = field(default_factory=time.monotonic, init=False, repr=False)

    # -----------------------------------------------------------------
    # Turn management
    # -----------------------------------------------------------------

    @property
    def turn_id(self) -> int:
        return self._turn_id

    async def next_turn(self) -> int:
        """Atomically cancel any in-flight task and increment the turn counter.

        Returns the new turn_id.
        """
        async with self._turn_lock:
            self._cancel_active_task()
            self._turn_id += 1
            self._reset_audio_buffer()
            self.last_activity = time.monotonic()
            return self._turn_id

    # -----------------------------------------------------------------
    # Audio buffering
    # -----------------------------------------------------------------

    def accept_audio_frame(self, turn_id: int, seq: int, payload: bytes) -> bool:
        """Buffer a PCM payload for the current turn.

        Returns True if the frame was accepted, False if it was stale
        (turn_id mismatch) or the buffer is full.

        Stale frames are silently dropped — this is intentional.
        """
        self.last_activity = time.monotonic()

        # --- rate-limit guard ---
        now = time.monotonic()
        if now - self._last_frame_window_start >= 1.0:
            self._last_frame_window_start = now
            self._frames_this_window = 0
        self._frames_this_window += 1
        if self._frames_this_window > MAX_FRAMES_PER_SECOND:
            return False  # caller should send error.rate_limit

        if turn_id != self._turn_id:
            return False  # stale — drop silently

        if self._audio_bytes + len(payload) > MAX_UTTERANCE_BYTES:
            return False  # overflow — ignore (VAD should have fired long ago)

        self._audio_chunks.append(payload)
        self._audio_bytes += len(payload)
        self._frame_count += 1
        return True

    def drain_audio(self) -> bytes:
        """Return the complete accumulated PCM bytes and reset the buffer."""
        data = b"".join(self._audio_chunks)
        self._reset_audio_buffer()
        return data

    def _reset_audio_buffer(self) -> None:
        self._audio_chunks = []
        self._audio_bytes = 0
        self._frame_count = 0

    # -----------------------------------------------------------------
    # Active task management
    # -----------------------------------------------------------------

    def set_active_task(self, task: asyncio.Task) -> None:
        self._cancel_active_task()
        self._active_task = task

    def _cancel_active_task(self) -> None:
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        self._active_task = None

    async def cancel_and_wait(self) -> None:
        """Cancel the active processing task and await its completion."""
        async with self._turn_lock:
            task = self._active_task
            self._active_task = None
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    # -----------------------------------------------------------------
    # Idle detection
    # -----------------------------------------------------------------

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity
