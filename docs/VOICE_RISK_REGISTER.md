# Voice System Risk Register & Mitigation Strategy

**Project:** Osiyatech AI Assistant  
**Date:** 2026-11-18  
**Document Version:** 1.1.0  
**Status:** M1 Finalized Specification  

---

## 1. Risk Register

| ID | Risk Description | Severity | Probability | Impact | Concrete M1 Architectural Mitigation | Status |
|---|---|---|---|---|---|---|
| **RSK-01** | **Acoustic Feedback / Echo Barge-In:** Assistant speech from laptop speakers re-enters microphone, falsely triggering user interruption. | HIGH | HIGH | Assistant constantly interrupts itself. | 1. Browser audio constraints `{ echoCancellation: true, noiseSuppression: true }`.<br>2. Dynamic VAD gating: client raises barge-in threshold by $+6.8\text{ dB}$ (2.2x) during `ASSISTANT_SPEAKING`.<br>3. Require continuous speech (>200ms) to trigger barge-in. | **Mitigated in Design** |
| **RSK-02** | **Inference Concurrency & Compute Overload (4GB VRAM):** Running Ollama LLM, Faster-Whisper, and Piper TTS concurrently causes CPU/VRAM saturation. | HIGH | MEDIUM | High latency spikes or process crashes. | 1. Serialized turn lifecycle: STT, LLM, and TTS run in sequential stages per turn.<br>2. Global concurrency semaphores (`stt_concurrency=1`, `llm_concurrency=1`).<br>3. Keep context bounded (`llm_voice_max_tokens=220`, `llm_num_ctx=2048`). | **Mitigated in Design** |
| **RSK-03** | **Stale Audio / Out-of-Order Packet Delivery:** Late audio chunks from Turn N play after Turn N+1 has started. | HIGH | MEDIUM | User hears overlapping or confusing answers. | Every turn is tagged with a monotonic `turn_id`. Client binary audio player strictly discards chunks where `chunk.turn_id < current_turn_id`. | **Mitigated in Design** |
| **RSK-04** | **Microphone Privacy Leakage:** Microphone remains recording after user stops session or navigates away. | HIGH | LOW | Privacy violation, browser alert. | Idempotent `session.stop` handler immediately stops all `MediaStreamTrack` instances. Visual red recording pill visible in UI while active. | **Mitigated in Design** |
| **RSK-05** | **Premature Endpointing on Natural Speech Pauses:** User pauses for breath (400ms) and assistant cuts them off. | MEDIUM | HIGH | User frustration. | Configurable silence endpointing timeout set to 700ms - 800ms with a 250ms pre-roll audio buffer. | **Mitigated in Design** |
| **RSK-06** | **Network Jitter / WebSocket Drops:** Temporary Wi-Fi disconnect drops continuous voice session. | MEDIUM | MEDIUM | Conversation aborted. | Automatic WebSocket reconnection with exponential backoff. Session state and turns persisted in SQLite, enabling seamless resumption. | **Mitigated in Design** |
| **RSK-07** | **Avatar UI Frame Drops:** High-frequency audio analysis ticks force React re-renders. | MEDIUM | MEDIUM | Stuttering UI. | Visual loop and canvas mouth movement powered directly by `requestAnimationFrame`, completely outside React component render tree. | **Mitigated in Design** |
| **RSK-08** | **Answer Hallucination in Voice Mode:** LLM invents facts or services when answering via voice. | CRITICAL | LOW | Inaccurate company representation. | All voice transcripts route through the identical `ConversationEngine` and `validate_answer` as text chat. No ungrounded partial claims are spoken. | **Mitigated in Design** |
