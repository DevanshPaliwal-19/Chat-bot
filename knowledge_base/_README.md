# Knowledge base

Put the REAL Osiyatech documents here (PDF, Markdown, TXT, HTML). Subfolders are fine; the first folder name is used as the
service category. This project ships **no company content on purpose**: the assistant may only answer from what you place here.

Optional front matter (Markdown/TXT) – only these keys are read, nothing is guessed:

```
---
title: AI Chatbot Development
source_url: https://ayushitated.com/...
approval_status: approved        # approved | draft | archived ...
last_updated: 2026-05-01
service_category: AI Services
---
```

Then run `python -m app.rag.cli ingest`. Files or folders whose names start with `_` or `.` (like this one) are ignored by ingestion.
