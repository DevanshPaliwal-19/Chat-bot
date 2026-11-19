"""Frontend contract and build integration tests for React/Vite interface."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.rag.embeddings import HashingEmbedder
from tests.conftest import SESSION, chat_payload

DIST_DIR = Path(__file__).resolve().parent.parent / "frontend-react" / "dist"
SRC_DIR = Path(__file__).resolve().parent.parent / "frontend-react" / "src"


def test_react_build_artifacts_exist():
    assert DIST_DIR.exists(), "frontend-react/dist should exist after npm build"
    assert (DIST_DIR / "index.html").exists(), "dist/index.html is missing"
    html = (DIST_DIR / "index.html").read_text(encoding="utf-8")
    assert "Osiyatech" in html or "<div id=\"root\">" in html


def test_react_assets_and_icons_exist():
    assert (DIST_DIR / "favicon.svg").exists()
    assert (DIST_DIR / "avatar.jpg").exists()
    assets = list((DIST_DIR / "assets").glob("*.js"))
    assert len(assets) >= 1, "Compiled React JS bundle should exist in assets"


def test_api_config_contract_matches_react_types(client):
    res = client.get("/config")
    assert res.status_code == 200
    cfg = res.json()
    # Verified against frontend-react/src/types/index.ts (BackendConfig)
    for expected_key in ("company_name", "assistant_name", "company_website", "features", "limits", "human_handoff"):
        assert expected_key in cfg, f"Key {expected_key} missing from /config contract"
    assert "voice_input" in cfg["features"]
    assert "voice_output" in cfg["features"]
    assert "max_audio_seconds" in cfg["limits"]
    assert "live_notification" in cfg["human_handoff"]


def test_api_health_contract_matches_react_types(client):
    res = client.get("/health")
    assert res.status_code == 200
    h = res.json()
    # Verified against frontend-react/src/types/index.ts (HealthStatus)
    assert h["status"] in {"ok", "degraded", "error"}
    assert "knowledge_base" in h
    assert "ready" in h["knowledge_base"]
    assert "llm" in h


def test_chat_response_contract_matches_react_types(client):
    res = client.post("/api/v1/chat", json=chat_payload("What services do you provide?"))
    assert res.status_code == 200
    body = res.json()
    # Verified against frontend-react/src/services/api.ts chat response handling
    for key in ("conversation_id", "message_id", "answer", "sources", "suggested_actions", "response_status", "interaction_mode"):
        assert key in body, f"Key {key} missing from /api/v1/chat response"
