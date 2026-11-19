# PROJECT WORK LOG – Osiyatech AI Assistant

Environment of all executed tests: Linux cloud sandbox, Python 3.13, headless Chromium (Playwright). **Not** the target
machine (Windows 10/11, PowerShell 5.1, GTX 1650 4 GB, real Ollama).

## Milestone 0 – Repository audit
* **NOT DONE as specified.** The requested folder `D:\osiya_tech project\chat_bot` is on the user's machine and was not reachable
  from the sandbox. The user then asked for the project to be built from scratch, so there was no existing code to audit.
  Nothing from the original repository was reused or checked.

## CHANGES MADE (milestones 1–9, built from scratch)
1. **Foundation:** pydantic settings (`OSIYA_*`), structured/redacting logging, error envelope, `.env.example`.
2. **RAG:** parsers (PDF/MD/TXT/HTML), chunking, hashing + sentence-transformers embedders, FAISS+BM25+RRF retrieval, confidence
   score, dedupe, incremental versioned ingestion with atomic writes, quarantine of injection-like chunks, CLI, evaluation module.
3. **LLM layer:** Ollama + OpenAI-compatible providers, retries/timeouts, typed errors, concurrency semaphore.
4. **Conversation engine:** turn analysis, grounding gate, grounded generation, post-validator, session memory with summaries,
   idempotent `client_message_id`, per-conversation lock.
5. **Leads/handoff:** SQLite store, consent, validation, scoring, honest handoff (no claim of notification unless a webhook delivers).
6. **API:** FastAPI app factory, CORS allow-list, size/rate limits, session ownership checks, admin-key endpoints.
7. **Speech:** faster-whisper STT and Piper TTS providers (optional), audio validation, off-event-loop execution.
8. **Frontend:** Streamlit UI (text/voice/avatar), retry-safe message queue, lead form, custom mic/avatar component.
9. **Tests, docs, scripts:** pytest suite, Playwright e2e, `scripts/eval_rag.py`, PowerShell scripts, README, API and troubleshooting docs.

## TESTS EXECUTED
| Suite | Result |
|---|---|
| `pytest -m "not e2e"` (units, engine, API, RAG, leads, speech, frontend AppTest) | 254 passed |
| `pytest tests/e2e` (real Chromium, fake mic, stand-in LLM/STT/TTS) | 14 passed |
| Full `pytest` | 268 passed |
| `scripts/eval_rag.py --sweep` on the bundled fictional "Fixture Corp" set, hashing embedder | recall@4 1.0, MRR 1.0, fact coverage 1.0; unsupported-reject 0.83 at threshold 0.30 and 1.0 at ≥0.35 |
| Empty knowledge base ingest | runs cleanly, 0 chunks; eval script exits with failures as expected |
| Piper CLI argument contract (`--model`, `--output_file`, stdin) | checked against the real `piper-tts` package |

## ACTUAL RESULTS
* All automated tests above pass. Screenshots from the browser run were inspected: text chat with citations/sources, and the
  avatar speaking with the status text and the "not phoneme-accurate" note.
* Bugs found by running real-browser tests and fixed: mic level loop started before the listening state (every recording rejected),
  mouth stuck open after speech, component iframe remounting mid-turn, prompt-leak false positive replacing a correct refusal
  (now needs ≥20 consecutive words of the system prompt), chat input silently swallowing a message when the backend had died.
* The 0.83 unsupported-reject at threshold 0.30 means 1 of 6 out-of-scope questions in the fixture set would still be answered
  from weak context with the hashing embedder; the sweep shows 0.35 rejects all of them without losing supported questions on this
  tiny set. **This does not transfer to real documents or real embeddings** – re-tune with your own set.

## KNOWN ISSUES / NOT TESTED
* **NOT TESTED:** real Ollama `llama3.2:3b` generation (the e2e/dev LLM is `scripts/mock_ollama.py`, which just echoes context). Answer
  quality, JSON-analysis reliability on a 3B model and the validator's behaviour on real output are unknown.
* **NOT TESTED:** real sentence-transformers embeddings (model could not be downloaded in the sandbox); all retrieval numbers use the
  hashing fallback.
* **NOT TESTED:** real faster-whisper transcription and real Piper voices (weights unreachable). Only the code paths, the Piper
  command-line contract and a stand-in STT/TTS were exercised. Accuracy, latency and audio quality are unknown.
* **NOT TESTED:** Windows / PowerShell 5.1 execution of `scripts\*.ps1`, GTX 1650 performance, memory use, response latency, load.
* **NOT TESTED:** Safari / Firefox microphone capture; only Chromium. Mic requires `localhost` or HTTPS.
* Avatar lip movement follows loudness only (not phoneme/viseme lip-sync).
* No company content is included; the assistant will answer "not found" for everything until real documents are ingested.
* Sessions are in-memory and single-process; rate limiting is per-process; no OCR for scanned PDFs; validator is regex-based.
* `requirements*.txt` use lower bounds only (not pinned); run `pip freeze` after a successful install on your machine to pin.
* Handoff is honest-by-default: with no webhook configured the assistant cannot notify the team.

## NEXT ACTION
1. Unzip on your machine, run `scripts\setup_windows.ps1`, `ollama pull llama3.2:3b`.
2. Copy real Osiyatech documents into `knowledge_base\`, ingest, and test 10–20 real questions; note wrong or ungrounded answers.
3. Build a real eval set, run `scripts\eval_rag.py --sweep` with sentence-transformers, set `OSIYA_MIN_CONFIDENCE`.
4. Install voice extras + a Piper voice, test Voice/Avatar on Chrome/Edge, record latency, and update this log.
