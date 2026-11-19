"""E2E backend: the REAL app, real OllamaProvider (talking to scripts/mock_ollama.py over HTTP) and the fixture KB.

Speech engines are stand-ins because Whisper/Piper model files can't be downloaded in CI:
  * STT returns a fixed transcript   * TTS returns a short tone (so playback / mouth animation are observable)
A debug route exists ONLY in this harness so tests can read server-side state.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.rag.embeddings import HashingEmbedder  # noqa: E402
from app.rag.ingest import Ingestor  # noqa: E402
from tests.conftest import FakeSTT, FakeTTS, make_wav  # noqa: E402


def syllable_wav(seconds: float = 3.0, rate: int = 16000) -> bytes:
    """A tone whose loudness rises and falls ~3 times a second, like syllables, so mouth movement is observable."""
    import io, wave
    import numpy as np
    t = np.arange(int(seconds * rate)) / rate
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 3.0 * t - np.pi / 2))          # 0..1 at 3 Hz
    samples = (0.35 * envelope * np.sin(2 * np.pi * 180 * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1), wf.setsampwidth(2), wf.setframerate(rate), wf.writeframes(samples.tobytes())
    return buf.getvalue()


class ToneTTS(FakeTTS):
    def synthesize_sync(self, text: str):
        self.last_text = text
        return syllable_wav(3.0), "audio/wav"


def build():
    data_dir = Path(os.environ["E2E_DATA_DIR"])
    s = Settings(_env_file=None, company_name="Fixture Corp", assistant_name="Fixie", company_website="https://fixture.example/",
                 kb_dir=ROOT / "tests/fixtures/kb", data_dir=data_dir, embedding_provider="hashing", log_level="WARNING",
                 llm_base_url=os.environ["E2E_OLLAMA_URL"], llm_concurrency=1, rate_limit_requests=1000,
                 cors_origins=os.environ.get("E2E_CORS", "http://localhost:8766,http://127.0.0.1:8766"))
    Ingestor(s, HashingEmbedder()).run()
    app = create_app(s, embedder=HashingEmbedder(), stt=FakeSTT(), tts=ToneTTS())

    @app.get("/__debug/conversation/{session_id}")
    async def debug(session_id: str):
        sessions = app.state.container.sessions._sessions.get(session_id)
        if not sessions:
            return {"conversations": []}
        return {"conversations": [{"id": c.id, "turns": [{"role": t.role, "mode": t.mode, "interrupted": t.audio_interrupted, "text": t.content}
                                                          for t in c.turns]} for c in sessions.conversations.values()]}

    @app.get("/__debug/sessions")
    async def debug_sessions():
        return {"sessions": list(app.state.container.sessions._sessions)}

    return app


if __name__ == "__main__":
    uvicorn.run(build(), host="127.0.0.1", port=int(os.environ.get("E2E_BACKEND_PORT", "8765")), log_level="warning")
