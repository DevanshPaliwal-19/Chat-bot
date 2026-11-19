"""Shared fixtures: fake LLM / STT / TTS (no network, no models) over a fictional test knowledge base."""
from __future__ import annotations

import io
import json
import re
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.errors import ServiceUnavailable
from app.llm.base import LLMProvider, LLMResult
from app.main import create_app
from app.rag.embeddings import HashingEmbedder
from app.rag.ingest import Ingestor
from app.speech.providers import STTProvider, TranscriptResult, TTSProvider

FIXTURE_KB = Path(__file__).parent / "fixtures" / "kb"


# ----------------------------------------------------------------------------- fakes
class FakeLLM(LLMProvider):
    """Scriptable stand-in for Ollama. Generation echoes the first retrieved sentence, like a faithful model."""

    name, model = "fake", "fake-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.analysis_queue: list[str] = []
        self.generation_queue: list[str] = []
        self.fail_with: Exception | None = None
        self.delay: float = 0.0

    @staticmethod
    def _visitor_message(messages) -> str:
        m = re.search(r"<visitor_message>\n(.*?)\n</visitor_message>", messages[-1]["content"], re.S)
        return m.group(1) if m else messages[-1]["content"]

    async def chat(self, messages, *, json_mode=False, max_tokens=None, temperature=None) -> LLMResult:
        import asyncio

        if self.delay:
            await asyncio.sleep(self.delay)
        sys_prompt = messages[0]["content"]
        kind = "analysis" if sys_prompt.startswith("You analyse ONE") else "summary" if sys_prompt.startswith("Update a running") else "generation"
        self.calls.append({"kind": kind, "messages": messages, "max_tokens": max_tokens, "json_mode": json_mode})
        if self.fail_with:
            raise self.fail_with
        if kind == "analysis":
            text = self.analysis_queue.pop(0) if self.analysis_queue else json.dumps(
                {"intent": "other", "standalone_query": self._visitor_message(messages), "wants_human": False,
                 "needs_clarification": False, "lead": {}}
            )
        elif kind == "summary":
            text = "Visitor asked several questions about services."
        elif self.generation_queue:
            text = self.generation_queue.pop(0)
        else:
            ctx = re.search(r"\[1\] \(source:[^\n]*\)\n(.*?)(?:\n\n\[2\]|\n</context>)", messages[-1]["content"], re.S)
            if ctx:
                text = ctx.group(1).strip().split(". ")[0].rstrip(".") + ". [1]"
            elif "NO VERIFIED CONTEXT" in messages[-1]["content"]:
                text = "I could not find verified information about that in the available documentation. I can help you connect with the team."
            else:
                text = "Hello! How can I help you today?"
        return LLMResult(text=text, model=self.model, duration_ms=1.0)

    async def health(self) -> dict:
        return {"status": "ok", "provider": self.name, "model": self.model}


class FakeSTT(STTProvider):
    available = True

    def __init__(self) -> None:
        self.result = TranscriptResult("what services do you offer", "en", 1.0, 0.9)
        self.error: Exception | None = None
        self.calls = 0

    def transcribe_sync(self, data: bytes, fmt: str) -> TranscriptResult:
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class FakeTTS(TTSProvider):
    available = True

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.last_text = ""

    def synthesize_sync(self, text: str):
        self.last_text = text
        if self.error:
            raise self.error
        return b"RIFF" + b"\x00" * 200, "audio/wav"


def make_wav(seconds: float = 1.0, amp: float = 0.3, rate: int = 16000, freq: float = 440.0) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    samples = (amp * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


# ----------------------------------------------------------------------------- fixtures
@pytest.fixture
def make_settings(tmp_path):
    def _make(**overrides) -> Settings:
        base = dict(
            _env_file=None, company_name="Fixture Corp", assistant_name="Fixie", company_website="https://fixture.example/",
            kb_dir=FIXTURE_KB, data_dir=tmp_path / "data", embedding_provider="hashing", log_level="WARNING",
            llm_concurrency=2, rate_limit_requests=1000,
        )
        base.update(overrides)
        return Settings(**base)

    return _make


@pytest.fixture
def settings(make_settings) -> Settings:
    return make_settings()


@pytest.fixture
def ingested(settings):
    Ingestor(settings, HashingEmbedder()).run()
    return settings


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_stt() -> FakeSTT:
    return FakeSTT()


@pytest.fixture
def fake_tts() -> FakeTTS:
    return FakeTTS()


@pytest.fixture
def container(ingested, fake_llm, fake_stt, fake_tts):
    return build_container(ingested, llm=fake_llm, embedder=HashingEmbedder(), stt=fake_stt, tts=fake_tts)


@pytest.fixture
def engine(container):
    return container.engine


@pytest.fixture
def client(ingested, fake_llm, fake_stt, fake_tts):
    app = create_app(ingested, llm=fake_llm, embedder=HashingEmbedder(), stt=fake_stt, tts=fake_tts)
    # raise_server_exceptions=False: behave like a real HTTP client, so tests see the sanitised 500 response
    with TestClient(app, raise_server_exceptions=False) as c:
        c.app_state = app.state
        yield c


SESSION = "sess_aaaaaaaa"


def chat_payload(message: str, session: str = SESSION, **extra) -> dict:
    return {"session_id": session, "message": message, **extra}


@pytest.fixture
def tmp_kb(tmp_path, make_settings):
    """An empty, writable knowledge base for ingestion edge cases."""
    kb = tmp_path / "kb"
    kb.mkdir()
    return kb, make_settings(kb_dir=kb)
