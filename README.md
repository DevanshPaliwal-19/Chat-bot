# Osiyatech AI Assistant

A website sales and customer-engagement chatbot for https://ayushitated.com/ with **three interaction modes — Text, Voice, Avatar —
that all run through one shared conversation engine**, one knowledge base, one session memory and one lead-qualification flow.

* **Backend:** FastAPI · **Frontend:** Streamlit (+ one small custom component for the microphone/avatar)
* **LLM:** Ollama, default `llama3.2:3b` (any OpenAI-compatible server also works)
* **Retrieval:** FAISS (dense) + BM25 (lexical), fused with RRF; sentence-transformers embeddings
* **Optional speech:** faster-whisper (speech-to-text), Piper (text-to-speech)

> **Read first – what is and is not verified.** See [docs/PROJECT_WORK_LOG.md](docs/PROJECT_WORK_LOG.md). The automated suite
> (268 tests) passes in the development sandbox, but that sandbox had **no Ollama, no sentence-transformers weights, no Whisper
> model and no Piper voice**, and ran on Linux, not Windows. Those parts are covered with stand-ins and still need your own
> run on your machine (checklist in §10).

## 1. What it does

* Answers company/service questions **only from your documents** (no hard-coded company answers, no keyword routing). When the
  documents do not support an answer it says so and offers the contact options. Answers carry numbered citations and sources.
* Rewrites follow-up questions ("and pricing?") using conversation memory before retrieval.
* Qualifies leads conversationally, **asks consent** before saving contact details, validates email/phone, stores to SQLite.
* **Honest human handoff:** by default the assistant cannot notify anyone and says so. It never claims the team was contacted
  unless a configured webhook actually delivered the request. Output that falsely claims this is removed.
* Security: CORS allow-list, request-size and rate limits, input sanitising, prompt-injection resistance (retrieved text is
  treated as untrusted data; suspicious chunks are quarantined at ingest), session isolation, privacy-aware (redacted) logs,
  sanitised error responses, admin endpoints protected by a key.

## 2. Architecture

```
Streamlit UI ──HTTP──► FastAPI ──► ConversationEngine ──► LLM provider (Ollama / OpenAI-compatible)
 text | voice | avatar     │            │  1 sanitise  2 analyse turn (intent, standalone query, lead fields)
 mic → 16 kHz WAV          │            │  3 hybrid retrieval  4 grounding gate  5 grounded generation
                           │            │  6 post-validation (figures, false claims, citations, prompt leak)
 /audio/process ─► STT ────┘            │  7 lead / handoff signals  8 memory update
                  TTS ◄── answer text   ├─► KnowledgeIndex (FAISS + BM25, versioned, atomic)
                                        ├─► SessionStore (per-conversation lock, idempotency, TTL, summaries)
                                        └─► LeadService → SQLite · HandoffService → (optional webhook)
```

Voice and Avatar are thin layers: speech-to-text produces a message for the same engine; the text reply is optionally turned
into audio. The avatar is an SVG whose mouth follows **audio loudness** – this is approximate movement, **not phoneme lip-sync**.

```
app/            backend (config, api, core, rag, llm, memory, leads, speech, security)
frontend/       Streamlit app + components (voice_avatar_component = custom component)
knowledge_base/ put your real documents here
scripts/        start/setup (PowerShell), eval_rag.py, mock_ollama.py (dev only)
tests/          unit, API, engine, RAG, frontend (AppTest) and real-browser e2e tests
docs/           API.md, TROUBLESHOOTING.md, PROJECT_WORK_LOG.md
```

## 3. Prerequisites (Windows 10/11, PowerShell 5.1)

* Python 3.10+ (developed on 3.13) · Git optional
* [Ollama for Windows](https://ollama.com/download)
* GTX 1650 4 GB / 16 GB RAM is the target: `llama3.2:3b` (~2 GB) fits; concurrency is limited to 1 generation at a time.

## 4. Install

```powershell
cd "D:\osiya_tech project\chat_bot"          # or wherever you unpacked the project
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
.\.venv\Scripts\Activate.ps1
# optional, for voice:
pip install -r requirements-voice.txt
```

Manual equivalent: `python -m venv .venv`, activate, `pip install -r requirements.txt`, `Copy-Item .env.example .env`.

## 5. Configure

Edit `.env` (every variable is prefixed `OSIYA_`; see `.env.example` – no secrets are committed). Common settings:

| Variable | Purpose | Default |
|---|---|---|
| `OSIYA_LLM_MODEL` | Ollama model | `llama3.2:3b` |
| `OSIYA_LLM_BASE_URL` | Ollama URL | `http://localhost:11434` |
| `OSIYA_MIN_CONFIDENCE` | grounding threshold (tune with `scripts/eval_rag.py`) | `0.30` |
| `OSIYA_ADMIN_API_KEY` | enables `/knowledge/*` and `GET /leads` | unset = disabled |
| `OSIYA_CORS_ORIGINS` | allowed browser origins | localhost:8501 |
| `OSIYA_CONTACT_EMAIL` / `OSIYA_CONTACT_PHONE` | shown for handoff **only if you set them** | unset |
| `OSIYA_HANDOFF_WEBHOOK_URL` | optional live notification | unset (handoff is honest "can't notify") |
| `OSIYA_PIPER_BINARY`, `OSIYA_PIPER_MODEL_PATH` | text-to-speech | unset (voice replies are text-only) |
| `OSIYA_STT_MODEL` | Whisper size (`base.en`, `small.en`, `base`…) | `base.en` |

## 6. Ollama

```powershell
ollama pull llama3.2:3b
ollama list            # confirm the model is installed
```

`GET /health` reports `model_missing` or `unavailable` if this is not in place.

## 7. Knowledge base

Place **real** Osiyatech documents (PDF, MD, TXT, HTML) in `knowledge_base\` – the project ships none and will not invent
company facts. Optional front matter keys: `title, source_url, approval_status, last_updated, service_category`
(see `knowledge_base\_README.md`). Then:

```powershell
python -m app.rag.cli ingest          # incremental: new/changed/removed files; --full to rebuild
python -m app.rag.cli status
python -m app.rag.cli search "What services do you offer?"   # shows confidence + matched chunks
```

Ingestion reports added/updated/removed/duplicate/skipped files and quarantined suspicious chunks. A file that fails to parse
keeps its last good version in the index. Changing the embedding model triggers an automatic full rebuild.

## 8. Run

Two terminals (venv active in both):

```powershell
.\scripts\start_backend.ps1      # http://127.0.0.1:8000  (docs at /docs)
.\scripts\start_frontend.ps1     # http://localhost:8501
```

The browser microphone works on `http://localhost` or HTTPS only. Safari/`MediaRecorder` support is untested (see limitations).

## 9. Test

```powershell
pip install -r requirements-dev.txt
python -m pytest -q -m "not e2e"            # fast suite (no browser)
python -m playwright install chromium       # once, for browser tests
python -m pytest tests/e2e -q               # real-browser tests (fake microphone)
python scripts\eval_rag.py tests\eval\rag_eval.json --sweep   # retrieval metrics + threshold sweep
```

Write your own evaluation set from your **real** documents (format in the docstring of `scripts/eval_rag.py`) and tune
`OSIYA_MIN_CONFIDENCE` from the sweep. The bundled eval set is for a fictional company and only exercises the code.

## 10. Verify on your machine (not yet done by the developer)

1. `ollama pull llama3.2:3b`, ingest real documents, ask 10–20 real questions in Text mode; check citations against the sources.
2. Run `eval_rag.py` with real sentence-transformers embeddings and a dataset from your documents.
3. Install `requirements-voice.txt` + a Piper voice; test Voice and Avatar on Chrome/Edge; note latency on the GTX 1650.
4. Confirm the handoff wording matches your reality (set `OSIYA_HANDOFF_WEBHOOK_URL` only if it really notifies someone).

## 11. Limitations

* Not run against real Ollama / sentence-transformers / Whisper / Piper / Windows in development (see work log).
* A 3B model can still answer imperfectly; the grounding gate and validator reduce but cannot eliminate this.
* Avatar mouth movement is loudness-driven, not lip-sync. TTS audio is generated after the full answer (no streaming).
* Sessions are in-memory: a backend restart ends conversations (leads in SQLite persist). Single-process; no horizontal scaling.
* Rate limiting is per process. No user authentication for visitors. Validator is heuristic (regex) – it can miss or over-remove.
* Scanned/image-only PDFs have no text and are skipped (no OCR).

## 12. Future improvements

Streaming responses, OCR for scanned PDFs, a reranker, persistent sessions (Redis), SSO-protected admin UI, real lip-sync
(viseme timings from TTS), multilingual support, CRM integration for handoff, load testing on target hardware.
