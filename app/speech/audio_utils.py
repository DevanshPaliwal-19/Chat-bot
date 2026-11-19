"""Audio validation that works without any speech library (magic-byte sniffing + WAV analysis)."""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

from ..config import Settings
from ..errors import AppError, PayloadTooLarge, UnsupportedMedia


class AudioError(AppError):
    status_code, code = 422, "invalid_audio"
    default_message = "That audio could not be used. Please try recording again."


@dataclass
class AudioInfo:
    format: str
    duration_s: float | None = None
    rms: float | None = None


def sniff_format(data: bytes) -> str | None:
    head = data[:16]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[4:8] == b"ftyp":
        return "m4a"
    return None


def wav_stats(data: bytes) -> tuple[float, float | None]:
    """Return (duration_seconds, rms in 0..1 or None if not 16-bit PCM)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            frames, rate, width, channels = wf.getnframes(), wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
            if rate <= 0:
                raise AudioError("The audio file looks corrupted.", code="invalid_audio")
            duration = frames / rate
            raw = wf.readframes(min(frames, rate * 120))
    except (wave.Error, EOFError) as exc:
        raise AudioError("The audio file looks corrupted.", code="invalid_audio") from exc
    rms = None
    if width == 2 and raw:
        samples = np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype="<i2").astype("float32") / 32768.0
        if channels > 1:
            samples = samples[: len(samples) - len(samples) % channels].reshape(-1, channels).mean(axis=1)
        rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
    return duration, rms


def validate_audio(data: bytes, settings: Settings) -> AudioInfo:
    if not data:
        raise AudioError("No audio was received. Please record again.", code="empty_audio")
    if len(data) > settings.max_audio_bytes:
        raise PayloadTooLarge("That recording is too large.", code="audio_too_large")
    fmt = sniff_format(data)
    if fmt is None:
        raise UnsupportedMedia("Unsupported audio format. Please use WAV, MP3, M4A, OGG, FLAC or WebM.", code="unsupported_audio_format")
    info = AudioInfo(format=fmt)
    if fmt == "wav":
        info.duration_s, info.rms = wav_stats(data)
        if info.duration_s < settings.min_audio_seconds:
            raise AudioError("That recording was too short. Please hold the button a little longer.", code="audio_too_short")
        if info.duration_s > settings.max_audio_seconds:
            raise AudioError(f"Please keep recordings under {int(settings.max_audio_seconds)} seconds.", code="audio_too_long")
        if info.rms is not None and info.rms < 0.003:
            raise AudioError("I couldn't hear anything in that recording. Please check your microphone and try again.", code="no_speech")
    return info
