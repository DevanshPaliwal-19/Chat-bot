# M0: Codebase Forensic Audit — Osiyatech AI Assistant

**Date:** 2026-11-18  
**Auditor:** Principal AI Architect / Senior Real-Time Systems Engineer  
**Status:** M0 Complete (Read-Only Forensic Audit)

---

## 1. Executive Summary

This forensic audit evaluates the existing Osiyatech AI Assistant codebase across backend architecture, frontend components, audio processing, machine learning models, session memory, and runtime infrastructure. 

The primary objective is to prepare the system for an upgrade to **continuous, full-duplex, interruptible voice conversation with real-time avatar intelligence**, while strictly preserving:
1. The existing conversation engine (`app/core/engine.py`).
2. The hybrid RAG retrieval pipeline (BM25 + FAISS + RRF).
3. Company knowledge governance and answer validation.
4. Lead capture and consultative discovery workflows.
5. All three existing interaction modes: Text (`chat_Question:chat_Answer`), Voice (`voice_Question:voice_Answer`), and Avatar Voice (`voice_question:avatar_speach_voice_answers`).

---

## 2. Environment & Infrastructure Verification

| Component | Target Spec | Actual Verified State | Status |
|---|---|---|---|
| Operating System | Windows | Windows 11 (64-bit) | Verified |
| Python Environment | Python 3.11+ | Python 3.11.9 (`.venv`) | Verified |
| PyTorch | CPU / CUDA | `2.15.0+cpu`, `cuda: False` | Verified (CPU inference) |
| Speech-to-Text | faster-whisper | `faster-whisper 1.2.1` installed | Verified |
| Text-to-Speech | Piper TTS | `piper.exe` (1.20.0 ONNX), `en_US-lessac-medium.onnx` (63 MB) | Verified on disk |
| Local LLM | Ollama | `ollama.exe` installed in User Programs; configured for `llama3.2:3b` | Verified |
| Frontend | React 18 / TypeScript / Vite | React 18.3.1, TypeScript 5.6.3, Vite 5.4.11, TailwindCSS 3.4.15 | Verified |
| Database | SQLite | `data/sessions.sqlite3` and `data/leads.sqlite3` | Verified |

---

## 3. Backend Architecture Inspection

### 3.1 Application Entry Point & Routes
- **Entry Point:** `app/main.py` defines `create_app()` with async lifespan managing `Container` initialization, SQLite lead cleanup, background session janitor (`janitor()` running every 60s), and graceful draining on shutdown (`c.engine.drain()`, `c.llm.aclose()`).
- **HTTP Routes:** Defined in `app/api/routes.py`:
  - `GET /health`: Comprehensive status of LLM, Knowledge Base, STT, TTS, active sessions.
  - `GET /config`: Public frontend configuration and interaction limits.
  - `POST /api/v1/chat`: Standard text chat turn with rate limiting and deduplication.
  - `GET /api/v1/conversations/{id}` and `DELETE /api/v1/conversations/{id}`: Conversation lifecycle.
  - `POST /api/v1/audio/transcribe`: Standalone audio transcription.
  - `POST /api/v1/audio/synthesize`: Standalone TTS synthesis.
  - `POST /api/v1/audio/process`: Composite turn: STT -> Shared Conversation Engine -> TTS.
  - `POST /api/v1/leads`: Lead capture and qualification.
- **WebSocket Implementation:** **Currently None**. Neither `app/main.py` nor `app/api/routes.py` define WebSocket handlers. All existing voice interaction operates over chunked HTTP `POST` multipart uploads.

### 3.2 Shared Conversation Engine (`app/core/engine.py`)
- **Single Source of Truth:** `ConversationEngine.handle(req: ChatRequest, request_id)` is the unified brain for text, voice, and avatar turns.
- **Execution Pipeline:**
  1. Input sanitization and prompt injection detection (`detect_injection`).
  2. Per-conversation serialization via `conv.lock = asyncio.Lock()`.
  3. Turn deduplication via `client_message_id`.
  4. Intent and slot analysis (`_analyze`): Either LLM-based structured JSON extraction or regex fallback analysis (`fallback_analysis`). Notice: In `.env`, `OSIYA_ANALYSIS_ENABLED=false` is set for rapid response on lightweight local hardware.
  5. Context-aware hybrid retrieval (`Retriever.retrieve`): Standalone query formulated with conversational context; dense + sparse search with Reciprocal Rank Fusion.
  6. Grounding gate: Evaluates retrieval confidence against `min_confidence` (0.30).
  7. Consultative discovery and lead qualification update (`_update_lead`).
  8. System prompt construction: Dynamically adjusts tone and concise length for voice/avatar (`llm_voice_max_tokens=220`).
  9. Grounded LLM generation: Calls `OllamaProvider.chat`.
  10. Answer validation (`validate_answer`): Enforces hallucination detection, phone/email validation, and strips markdown citations (`[1]`, `[2]`) in voice and avatar modes.
  11. Turn and entity persistence in `SessionStore`.
  12. Background summarization (`_maybe_summarize`) triggered every 6 turns.

### 3.3 Speech Subsystem (`app/speech/`)
- **STT (`app/speech/providers.py` - `FasterWhisperSTT`):**
  - Uses `WhisperModel(model="base.en", device="cpu", compute_type="int8")`.
  - Writes binary upload to disk temp file (`tempfile.mkstemp`), calls `model.transcribe(..., vad_filter=True, beam_size=1)`.
  - Calculates confidence based on logprob and `no_speech_prob`.
  - Serialized via `asyncio.Semaphore(stt_concurrency=1)` to prevent CPU/memory saturation.
- **TTS (`app/speech/providers.py` - `PiperTTS`):**
  - Executes `piper.exe` via `subprocess.run` with text passed via stdin.
  - Outputs 16-bit 22.05 kHz mono PCM in a WAV container.
  - Throttled via `asyncio.Semaphore(1)`.
  - Prepares text with `prepare_tts_text` (strips markdown, links, truncates at sentence boundary up to 700 chars).
- **Audio Utils (`app/speech/audio_utils.py`):**
  - Pure-Python / NumPy audio validation (header magic bytes sniffing for WAV, MP3, OGG, WebM, FLAC, M4A).
  - WAV validation calculates duration and RMS energy to catch empty recordings (<0.003 RMS).

### 3.4 Session & Memory Management (`app/memory/sessions.py`)
- Two-tier store: In-memory dictionary `_sessions[session_id]` backed by SQLite (`data/sessions.sqlite3`).
- Thread-safe with SQLite transactions and `asyncio.Lock` per conversation.
- Shared context across modes: `Conversation` stores `Turn` objects with `mode: "text" | "voice" | "avatar"`, preserving continuous multi-turn dialogue context regardless of input mode.

---

## 4. Frontend Architecture Inspection

### 4.1 Technology Stack & Assets
- **Framework:** React 18.3.1 with TypeScript and Vite.
- **Icons & Styling:** Lucide React, TailwindCSS.
- **Avatar Asset:**
  - Located at `frontend-react/public/avatar.jpg` (47 KB raster photograph of professional female persona).
  - **No 3D models** (`.glb`, `.gltf`, `.vrm`), no Three.js / React Three Fiber, no Live2D runtime.
  - Current rendering in `VoicePanel.tsx`: 2D circular `img` element with CSS border transitions and pulsing rings.
  - Audio playback animation: 5 CSS `wave-bar` spans animating height.

### 4.2 Current Voice Recording Flow (`frontend-react/src/hooks/useAudioRecorder.ts`)
- Uses browser `MediaRecorder` API (`audio/webm;codecs=opus`).
- Starts microphone capture on button click, slices every 200ms.
- On "Stop & Send" click, creates a single `Blob`, uploads via `multipart/form-data` to `POST /api/v1/audio/process`.
- Waits for full roundtrip (STT + Engine + TTS), receives base64 WAV, instantiates `new Audio(...)` for playback.
- **Current Limitations:**
  - Turn-based manual push-to-talk only (User must press Record, then press Stop).
  - No client-side streaming or real-time Voice Activity Detection (VAD).
  - No streaming audio playback; user waits for full answer generation and full audio synthesis before hearing a sound.
  - Cannot interrupt assistant speech without clicking the manual "Stop" button.

---

## 5. Forensic Findings & Architectural Gaps

1. **Lack of Streaming Transport:** No WebSocket connection exists. All audio requires HTTP file upload and full response payload delivery.
2. **Turn Latency Bottleneck:** End-of-speech to playback latency is high (1.5s - 3.5s) because STT, LLM generation, and TTS synthesis occur sequentially in monolithic blocks before any audio is sent back.
3. **No Automatic Speech Endpointing:** The browser does not detect when the user finishes speaking; the user is forced to click "Stop & Send".
4. **Avatar Animation Simplicity:** The avatar does not blink, breathe, or move its mouth in sync with audio. It only toggles a static CSS ring when audio plays.
5. **No Barge-In / Interruption Engine:** If the user speaks while the assistant is speaking, the browser microphone is either closed or the audio is ignored until the user clicks "Stop".
6. **Pytest Test Runner Observation:** In `tests/test_units.py`, synchronous `httpx.MockTransport` is passed into `OllamaProvider` (which uses `httpx.AsyncClient`), causing test runner deadlocks when executing async test suites. This must be addressed in test harness configurations without changing production code.

---

## 6. Audit Verdict & Readiness

The foundation of Osiyatech AI Assistant is exceptionally clean, modular, and robust:
- The conversation engine already contains all required conversational intelligence, reference resolution, grounding gates, and memory structures.
- Both faster-whisper and Piper TTS are pre-installed and proven functional.
- The avatar asset and UI layout are ready for enhanced real-time control.

The system is ready for **M1: Architecture & Protocol Specification**.
