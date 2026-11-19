"""Speech-to-text (faster-whisper) and text-to-speech (Piper CLI) providers, both optional."""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from ..config import Settings
from ..errors import ServiceUnavailable
from .audio_utils import AudioError

logger = logging.getLogger(__name__)


@dataclass
class TranscriptResult:
    text: str
    language: str | None
    duration_s: float
    confidence: float | None


class STTProvider(ABC):
    available: bool = False
    reason: str = ""

    @abstractmethod
    def transcribe_sync(self, data: bytes, fmt: str) -> TranscriptResult: ...


class UnavailableSTT(STTProvider):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def transcribe_sync(self, data: bytes, fmt: str) -> TranscriptResult:
        raise ServiceUnavailable("Voice input is not available right now. You can type your question instead.", code="stt_unavailable")


class FasterWhisperSTT(STTProvider):
    available = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._lock = Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self.settings.stt_model, device=self.settings.stt_device, compute_type=self.settings.stt_compute_type)
            return self._model

    def transcribe_sync(self, data: bytes, fmt: str) -> TranscriptResult:
        model = self._load()
        fd, name = tempfile.mkstemp(suffix=f".{fmt}")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            try:
                segments, info = model.transcribe(name, vad_filter=True, beam_size=1)
                segs = list(segments)
            except Exception as exc:  # undecodable / corrupt audio
                raise AudioError("The audio could not be decoded. Please try recording again.", code="invalid_audio") from exc
        finally:
            try:
                os.remove(name)
            except OSError:
                pass
        if info.duration > self.settings.max_audio_seconds:
            raise AudioError(f"Please keep recordings under {int(self.settings.max_audio_seconds)} seconds.", code="audio_too_long")
        text = " ".join(s.text.strip() for s in segs).strip()
        if not text:
            raise AudioError("I couldn't make out any speech. Please try again, or type your question.", code="no_speech")
        confs = [math.exp(min(0.0, s.avg_logprob)) * (1.0 - min(1.0, max(0.0, s.no_speech_prob))) for s in segs]
        return TranscriptResult(text, getattr(info, "language", None), float(info.duration), sum(confs) / len(confs) if confs else None)


def build_stt(settings: Settings) -> STTProvider:
    if settings.stt_provider == "none":
        return UnavailableSTT("disabled by configuration")
    if importlib.util.find_spec("faster_whisper") is None:
        return UnavailableSTT("faster-whisper is not installed (pip install -r requirements-voice.txt)")
    return FasterWhisperSTT(settings)


# ---------------------------------------------------------------- TTS
class TTSProvider(ABC):
    available: bool = False
    reason: str = ""

    @abstractmethod
    def synthesize_sync(self, text: str) -> tuple[bytes, str]:
        """Return (audio_bytes, mime_type)."""


class UnavailableTTS(TTSProvider):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def synthesize_sync(self, text: str) -> tuple[bytes, str]:
        raise ServiceUnavailable("Voice replies are not available right now.", code="tts_unavailable")


class PiperTTS(TTSProvider):
    available = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def synthesize_sync(self, text: str) -> tuple[bytes, str]:
        s = self.settings
        fd, out = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            proc = subprocess.run(
                [s.piper_binary, "--model", str(s.piper_model_path), "--output_file", out],
                input=text.encode("utf-8"), capture_output=True, timeout=s.tts_timeout_s,
            )
            if proc.returncode != 0:
                logger.warning("piper exited with %s", proc.returncode)
                raise ServiceUnavailable("Voice replies are not available right now.", code="tts_failed")
            audio = Path(out).read_bytes()
            if len(audio) < 100:
                raise ServiceUnavailable("Voice replies are not available right now.", code="tts_failed")
            return audio, "audio/wav"
        except subprocess.TimeoutExpired as exc:
            raise ServiceUnavailable("Voice reply took too long to generate.", code="tts_timeout") from exc
        except OSError as exc:
            raise ServiceUnavailable("Voice replies are not available right now.", code="tts_unavailable") from exc
        finally:
            try:
                os.remove(out)
            except OSError:
                pass


def build_tts(settings: Settings) -> TTSProvider:
    if settings.tts_provider == "none":
        return UnavailableTTS("disabled by configuration")
    if not settings.piper_model_path or not Path(settings.piper_model_path).exists():
        return UnavailableTTS("OSIYA_PIPER_MODEL_PATH is not set or the model file does not exist")
    if shutil.which(settings.piper_binary) is None and not Path(settings.piper_binary).exists():
        return UnavailableTTS(f"Piper binary '{settings.piper_binary}' was not found")
    return PiperTTS(settings)


_URL = re.compile(r"https?://\S+")
_MD = re.compile(r"(\*\*|__|`|^#+\s*|^\s*[-*•]\s+|\[\d{1,2}\])", re.M)
_SENT_END = re.compile(r"(?<=[.!?])\s")


def prepare_tts_text(text: str, max_chars: int) -> str:
    text = _MD.sub("", _URL.sub("", text))
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    ends = [m.end() for m in _SENT_END.finditer(cut)]
    return cut[: ends[-1]].strip() if ends and ends[-1] > max_chars // 3 else cut.rstrip()


class SpeechService:
    """Runs blocking STT/TTS work off the event loop with concurrency and time limits."""

    def __init__(self, settings: Settings, stt: STTProvider, tts: TTSProvider) -> None:
        self.settings = settings
        self.stt = stt
        self.tts = tts
        self._stt_slots = asyncio.Semaphore(max(1, settings.stt_concurrency))
        self._tts_slots = asyncio.Semaphore(1)

    async def transcribe(self, data: bytes, fmt: str) -> TranscriptResult:
        async with self._stt_slots:
            try:
                return await asyncio.wait_for(asyncio.to_thread(self.stt.transcribe_sync, data, fmt), self.settings.stt_timeout_s)
            except asyncio.TimeoutError as exc:
                raise ServiceUnavailable("Transcription took too long. Please try a shorter recording.", code="stt_timeout") from exc

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        prepared = prepare_tts_text(text, self.settings.tts_max_chars)
        if not prepared:
            raise ServiceUnavailable("There is nothing to speak.", code="tts_empty")
        async with self._tts_slots:
            return await asyncio.to_thread(self.tts.synthesize_sync, prepared)
