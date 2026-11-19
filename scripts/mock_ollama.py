"""A tiny Ollama-compatible server for smoke tests and demos WITHOUT a GPU or real model.

    python scripts/mock_ollama.py --port 11435

It implements GET /api/tags and POST /api/chat. Replies are NOT intelligent: the "model" repeats the first
sentence of the retrieved context (a perfectly faithful, perfectly boring model). Use it to exercise the whole
stack; never to judge answer quality.
"""
from __future__ import annotations

import argparse
import json
import re

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="mock-ollama")
MODEL = "llama3.2:3b"


@app.get("/api/tags")
async def tags() -> dict:
    return {"models": [{"name": MODEL}]}


def _visitor(content: str) -> str:
    m = re.search(r"<visitor_message>\n(.*?)\n</visitor_message>", content, re.S)
    return (m.group(1) if m else content).strip()


@app.post("/api/chat")
async def chat(request: Request) -> dict:
    body = await request.json()
    messages = body.get("messages", [])
    system = messages[0]["content"] if messages else ""
    last = messages[-1]["content"] if messages else ""
    if system.startswith("You analyse ONE"):
        text = json.dumps({"intent": "other", "standalone_query": _visitor(last), "wants_human": bool(re.search(r"\b(human|person|agent)\b", _visitor(last), re.I)),
                           "needs_clarification": False, "lead": {}})
    elif system.startswith("Update a running"):
        text = "The visitor asked about services."
    else:
        ctx = re.search(r"\[1\] \(source:[^\n]*\)\n(.*?)(?:\n\n\[2\]|\n</context>)", last, re.S)
        if ctx:
            text = ctx.group(1).strip().split(". ")[0].rstrip(".") + ". [1]"
        elif "NO VERIFIED CONTEXT" in last:
            text = "I could not find verified information about that in the available documentation. I can help you connect with the team."
        else:
            text = "Hello! How can I help you today?"
    return {"model": body.get("model", MODEL), "message": {"role": "assistant", "content": text}, "done": True,
            "prompt_eval_count": 10, "eval_count": 10}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=11435)
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
