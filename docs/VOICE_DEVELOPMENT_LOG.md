# Voice Development Log & Engineering Evidence

## [2026-11-18] — Milestone M0: Codebase Forensic Audit

### Objective
Perform an exhaustive, non-destructive forensic audit of the entire Osiyatech AI Assistant codebase across backend, frontend, audio pipelines, models, and test suites, without modifying any application code.

### Files Inspected
- `app/main.py`: Lifespan, FastAPI app configuration, middleware, error handlers, router inclusions.
- `app/api/routes.py`: REST routes for health, config, chat, audio transcribe/synthesize/process, leads, and RAG ingestion.
- `app/api/deps.py`: Rate limiting, container dependency injection, admin authentication.
- `app/config.py`: Centralized configuration settings and environment variable bindings.
- `app/container.py`: Service wiring and container assembly.
- `app/core/engine.py`: Single conversation engine, intent analysis, RAG retrieval, grounding gate, generation, validation, memory.
- `app/core/analysis.py`: Intent classification and entity/slot extraction.
- `app/core/prompts.py`: System prompt builders for text, voice, and avatar modes.
- `app/core/validator.py`: Answer validation, citation verification, hallucination checks.
- `app/speech/providers.py`: `FasterWhisperSTT`, `PiperTTS`, `SpeechService`.
- `app/speech/audio_utils.py`: Magic-byte format sniffing, PCM/WAV duration and RMS energy validation.
- `app/memory/sessions.py`: SQLite-backed session store, conversation models, turn records.
- `app/rag/embeddings.py`: SentenceTransformer and HashingEmbedder implementations.
- `app/rag/retriever.py`: Hybrid BM25 + FAISS retrieval with Reciprocal Rank Fusion.
- `app/llm/providers.py`: Ollama and OpenAI-compatible HTTP providers.
- `.env` & `.env.example`: Current environment configuration.
- `frontend-react/src/App.tsx`: Main React application component, session management, audio playback.
- `frontend-react/src/components/VoicePanel.tsx`: Voice and avatar UI, audio wave bars, recording controls.
- `frontend-react/src/components/Header.tsx`, `Sidebar.tsx`, `MessageItem.tsx`: Complementary UI components.
- `frontend-react/src/hooks/useAudioRecorder.ts`: MediaRecorder-based audio recording hook.
- `frontend-react/src/services/api.ts`: Frontend HTTP client for backend endpoints.
- `frontend-react/package.json`: Frontend dependencies.
- `tests/conftest.py`, `tests/test_units.py`, `tests/test_speech.py`, `tests/test_rag.py`, `tests/test_leads.py`: Pytest test suite.

### Files Modified
- None (M0 is strictly read-only for application code).

### Files Created
- `docs/VOICE_SYSTEM_FORENSIC_AUDIT.md`: Forensic audit report.
- `docs/VOICE_TARGET_ARCHITECTURE.md`: Target multi-layer architecture document.
- `docs/VOICE_PROTOCOL_SPEC.md`: WebSocket protocol specification (v1).
- `docs/VOICE_IMPLEMENTATION_ROADMAP.md`: Step-by-step milestone plan.
- `docs/VOICE_TEST_STRATEGY.md`: Multi-tier quality assurance and test execution matrix.
- `docs/VOICE_RISK_REGISTER.md`: Risk identification and mitigation strategies.
- `docs/VOICE_DEVELOPMENT_LOG.md`: Chronological log of development and findings.

### Files Deleted
- None.

### Backups Created
- None required for M0 (read-only audit).

### Dependencies Verified
- Python: 3.11.9
- PyTorch: `2.15.0+cpu` (CPU mode verified)
- faster-whisper: `1.2.1` installed
- Piper TTS: `models/piper/piper/piper.exe` and `models/piper/en_US-lessac-medium.onnx` present and verified
- Ollama: Installed in local user programs (`C:\Users\Asus\AppData\Local\Programs\Ollama\ollama.exe`)
- Node.js & Vite: React 18.3.1, TypeScript 5.6.3, Vite 5.4.11

### Commands Executed
- Directory inspection: `Get-ChildItem -Force`, `Get-ChildItem app`, `Get-ChildItem frontend-react`
- Search commands: Scanned for WebSocket references (0 found in existing code)
- Python environment verification: `.venv\Scripts\python.exe -c "import faster_whisper, torch; print(...)"`
- Model path checks: Checked Piper executable and ONNX weights on disk
- Pytest execution:
  - `pytest tests/test_rag.py`: 27 passed in 1.64s
  - `pytest tests/test_speech.py -k "test_sniff_formats"`: 1 passed in 0.06s
  - Discovered test runner deadlock in `test_units.py` due to synchronous `httpx.MockTransport` passed to `httpx.AsyncClient`.

### Known Limitations Discovered
1. Existing voice interaction is purely turn-based HTTP POST (`/api/v1/audio/process`). No streaming or WebSocket support exists.
2. Avatar is a 2D photograph (`/avatar.jpg`) wrapped in a circular CSS container with simple border pulse animations; no lip-sync or facial expressions currently exist.
3. Assistant speech must be synthesized in its entirety before any audio can be played to the user (high latency).
4. Full-duplex interruption does not exist; user must manually click "Stop" to interrupt speech.

### Next Pending Action
- Request stakeholder approval for M0 audit findings and M1 Architecture & State Machine Plan.
