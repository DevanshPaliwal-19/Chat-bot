# Voice Test Strategy & Quality Assurance Framework

**Project:** Osiyatech AI Assistant  
**Document Version:** 1.1.0  
**Status:** M1 Finalized Specification  

---

## 1. Quality Engineering Standards

No test may be claimed as passing without concrete, timestamped execution evidence. Every capability progresses through an explicit verification ladder:
1. **Designed:** Architecturally defined in specifications.
2. **Implemented:** Code written and syntactically valid.
3. **Unit Tested:** Automated isolated unit tests passing in Pytest / Vitest.
4. **Integration Tested:** End-to-end WebSocket loopback test with mock/real audio passing.
5. **Browser Tested:** Executed inside real Chromium/Firefox browser with simulated or live Web Audio.
6. **Hardware Tested:** Executed with real laptop/desktop microphone, speakers, and local GPU/CPU inference.
7. **Production Verified:** Sustained 30+ continuous turns with zero leakage and verified rollback safety.

---

## 2. Test Execution Matrix

| Test ID | Test Category | Target Component | Scenario & Procedure | Pass Criteria | Status |
|---|---|---|---|---|---|
| **TEST-01** | State Machine | Voice FSM | Exhaustive state transitions across all 13 states; trigger invalid transitions (`IDLE -> ASSISTANT_SPEAKING`). | Valid transitions succeed; invalid transitions raise `InvalidStateTransitionError` without corrupting state. | **Designed** |
| **TEST-02** | Transport | WebSocket Handler | Client establishes connection with valid `session_id`; sends ping every 15s; triggers disconnect. | Server returns `session.ready`; replies `session.pong`; releases session resources on disconnect. | **Designed** |
| **TEST-03** | Audio Ingestion | AudioWorklet | Client microphone captures 16kHz audio; streams 50ms binary PCM frames (1,600 bytes) over WebSocket. | Server receives continuous valid PCM frames; verifies exact byte length and little-endian ordering. | **Designed** |
| **TEST-04** | VAD & Endpointing | Server VAD | Send synthetic audio: 1s silence + 2s speech + 800ms silence. | Server detects speech onset at ~1.0s, triggers `speech.ended` at ~3.7s; utterance buffer has 250ms pre-roll. | **Designed** |
| **TEST-05** | False Trigger | Noise Rejection | Send synthetic audio with short noise burst (100ms click) or low-energy hum (RMS < 0.010). | Server discards buffer without invoking STT or LLM; state returns cleanly to `LISTENING`. | **Designed** |
| **TEST-06** | STT Integration | faster-whisper | Send speech audio ("What services does Osiyatech provide?"). | Returns accurate transcript; confidence >= 0.85; language detected as "en". | **Designed** |
| **TEST-07** | Engine Integration | ConversationEngine | Pipe transcribed text to `engine.handle()`. | Returns grounded `ChatResponse`; applies `validate_answer`; updates SQLite `Turn` table with `mode="avatar"`. | **Designed** |
| **TEST-08** | Follow-Up Retain | Session Memory | Turn 1: "What AI services do you offer?" -> Turn 2: "Can you give me an example of the first one?" | Turn 2 resolves "first one" using conversational context; no request for user to repeat. | **Designed** |
| **TEST-09** | Sentence Pipelining| Piper TTS | Pass multi-sentence answer to Piper pipelined synthesizer. | Sentence 0 audio emitted in <800ms (TTFA); Sentence 1 queued gaplessly; binary header flags match. | **Designed** |
| **TEST-10** | Barge-In Interrupt | Interruption FSM | While assistant is speaking (turn 1), send user speech audio (turn 2). | Client audio playback halts in <100ms; server cancels pending Piper tasks for turn 1; processes turn 2. | **Designed** |
| **TEST-11** | Stale Audio Drop | Turn Synchronization | Simulate delayed arrival of turn 1 audio chunk after turn 2 has started. | Client Web Audio player inspects binary header; detects `chunk.turn_id (1) < current_turn_id (2)`; drops packet. | **Designed** |
| **TEST-12** | Echo Suppression | Acoustic Gating | Playback high-volume TTS through laptop speakers while continuous listening is active. | Software playback gating suppresses echo trigger; does not cut off assistant self-speech. | **Designed** |
| **TEST-13** | Teardown Safety | Browser Lifecycle | User clicks "Stop Conversation", closes browser tab, or refreshes page. | `track.stop()` cuts mic hardware immediately; server session janitor cleans orphaned state in <=60s. | **Designed** |
| **TEST-14** | Text Chat Parity | Regression Guard | Submit text message via `/api/v1/chat` during or after continuous voice session. | Text chat operates with identical accuracy and speed; shares SQLite conversation history. | **Designed** |
| **TEST-15** | Endurance Test | Stress / Longevity | Execute 30 consecutive voice dialogue turns back-to-back. | Zero memory leak; peak RAM remains stable; SQLite contains all 30 turns in correct chronological order. | **Designed** |

---

## 3. Unit Test Fixture Strategy for Async Test Runner

During M0, it was discovered that `httpx.MockTransport` in `tests/test_units.py` caused async worker deadlocks when running with `pytest-asyncio`.
- **M1 Remediation Rule:** All new WebSocket and async voice unit tests will use `pytest-asyncio` native loop fixtures with in-memory WebSocket transports (`httpx.ASGITransport` or `starlette.testclient.TestClient(app).websocket_connect`) to ensure 100% deadlock-free automated testing.
