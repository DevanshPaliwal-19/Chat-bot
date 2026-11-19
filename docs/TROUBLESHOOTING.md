# Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| UI says it can't reach the assistant service | Backend not running or `OSIYA_BACKEND_URL` wrong. Start `scripts\start_backend.ps1`; open `http://127.0.0.1:8000/health`. |
| `/health` → `unavailable` | Ollama not running. Start the Ollama app; check `OSIYA_LLM_BASE_URL`. |
| `/health` → `model_missing` | `ollama pull llama3.2:3b` (or set `OSIYA_LLM_MODEL` to an installed model from `ollama list`). |
| Every answer is "could not find verified information" | Knowledge base empty/not ingested (`python -m app.rag.cli status`), or threshold too high. Use `app.rag.cli search "..."` to see confidence; tune with `scripts\eval_rag.py --sweep`. |
| Log: "falling back to the lexical hashing embedder" | sentence-transformers missing or model not downloadable. Install it and ensure internet access for the first model download; retrieval is weaker until then. |
| Slow replies / 503 "busy" | Small GPU: generation is serial by design (`OSIYA_LLM_CONCURRENCY=1`). Keep a short model, lower `OSIYA_LLM_MAX_TOKENS`, set `OSIYA_LLM_WARMUP=true`. |
| Voice option missing | `features.voice_input` false: install `requirements-voice.txt`; STT model downloads on first use. |
| Replies are text only in Voice mode | Piper not configured: set `OSIYA_PIPER_BINARY` and `OSIYA_PIPER_MODEL_PATH`. |
| Browser: microphone blocked / unavailable | Use `http://localhost` or HTTPS; allow the mic in site settings; use Chrome/Edge. The audio-file upload fallback still works. |
| "I couldn't hear anything" | Silent recording (wrong input device). Check the OS input level. |
| CORS error from a custom frontend | Add its origin to `OSIYA_CORS_ORIGINS`. |
| 429 Too many requests | Per-session limit (`OSIYA_RATE_LIMIT_REQUESTS` per window). |
| Admin endpoint returns 403 | `OSIYA_ADMIN_API_KEY` not set, or header `X-Admin-Key` missing/wrong. |
| Ingest reports errors for a PDF | Encrypted/corrupt or scanned (no text). The previous good version stays indexed. |
