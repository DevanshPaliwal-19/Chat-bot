"""HTTP-level behaviour: contracts, validation, errors, security headers, isolation, limits, admin."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMUnavailable
from app.main import create_app
from app.rag.embeddings import HashingEmbedder
from tests.conftest import SESSION, chat_payload


def test_health_reports_components(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["llm"]["status"] == "ok"
    assert body["knowledge_base"]["ready"] and body["knowledge_base"]["chunks"] >= 8
    assert body["speech"]["stt"]["available"] and "version" in body


def test_health_degraded_without_index(make_settings, fake_llm):
    app = create_app(make_settings(data_dir=make_settings().data_dir / "empty"), llm=fake_llm, embedder=HashingEmbedder())
    with TestClient(app) as c:
        body = c.get("/health").json()
    assert body["status"] == "degraded" and body["knowledge_base"]["ready"] is False


def test_config_exposes_no_secrets(client):
    body = client.get("/config").json()
    flat = str(body).lower()
    assert body["assistant_name"] == "Fixie" and body["interaction_modes"] == ["text", "voice", "avatar"]
    for forbidden in ("api_key", "admin", "password", "secret", "webhook"):
        assert forbidden not in flat


def test_chat_contract(client):
    r = client.post("/api/v1/chat", json=chat_payload("Can you build an iOS app?"))
    assert r.status_code == 200
    b = r.json()
    for key in ("conversation_id", "message_id", "answer", "response_status", "sources", "confidence",
                "suggested_actions", "lead_status", "interaction_mode", "timings_ms"):
        assert key in b
    assert b["sources"][0]["source_document"] == "web-services/mobile-apps.html"
    assert "X-Request-ID" in r.headers and r.headers["X-Content-Type-Options"] == "nosniff"


def test_chat_multi_turn_over_http(client):
    b1 = client.post("/api/v1/chat", json=chat_payload("Do you build chatbots?")).json()
    b2 = client.post("/api/v1/chat", json=chat_payload("and how does a project start?", conversation_id=b1["conversation_id"])).json()
    assert b2["conversation_id"] == b1["conversation_id"]
    r = client.get(f"/api/v1/conversations/{b1['conversation_id']}", headers={"X-Session-ID": SESSION})
    assert [t["role"] for t in r.json()["turns"]] == ["user", "assistant", "user", "assistant"]


def test_conversation_access_requires_owning_session(client):
    b = client.post("/api/v1/chat", json=chat_payload("hello")).json()
    cid = b["conversation_id"]
    other = {"X-Session-ID": "sess_zzzzzzzz"}
    assert client.get(f"/api/v1/conversations/{cid}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/conversations/{cid}", headers=other).status_code == 404
    assert client.get(f"/api/v1/conversations/{cid}").status_code == 422  # header missing
    assert client.delete(f"/api/v1/conversations/{cid}", headers={"X-Session-ID": SESSION}).status_code == 204
    assert client.get(f"/api/v1/conversations/{cid}", headers={"X-Session-ID": SESSION}).status_code == 404


@pytest.mark.parametrize("payload", [
    {}, {"message": "hi"}, {"session_id": "short", "message": "hi"}, {"session_id": "bad id!!!!", "message": "hi"},
    {"session_id": SESSION, "message": ""}, {"session_id": SESSION, "message": "   "},
    {"session_id": SESSION, "message": "hi", "conversation_id": "../../etc/passwd"},
    {"session_id": SESSION, "message": "hi", "interaction_mode": "telepathy"},
    {"session_id": SESSION, "message": "hi", "client_metadata": {"k": "v" * 1000}},
    {"session_id": SESSION, "message": "x" * 9000},
])
def test_invalid_chat_requests_return_422(client, payload):
    r = client.post("/api/v1/chat", json=payload)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_failed" and err["request_id"]


def test_message_over_configured_limit_is_422_with_code(client):
    r = client.post("/api/v1/chat", json=chat_payload("x" * 2500))
    assert r.status_code == 422 and r.json()["error"]["code"] == "message_too_long"


def test_malformed_json_and_wrong_method(client):
    assert client.post("/api/v1/chat", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert client.get("/api/v1/chat").status_code == 405
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_oversized_body_rejected_before_parsing(client):
    r = client.post("/api/v1/chat", content=b"x" * (70 * 1024), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_llm_outage_is_503_without_internal_details(client, fake_llm):
    fake_llm.fail_with = LLMUnavailable("connect ECONNREFUSED 127.0.0.1:11434 secret-host")
    r = client.post("/api/v1/chat", json=chat_payload("Do you build chatbots?"))
    assert r.status_code == 503
    text = r.text.lower()
    assert "econnrefused" not in text and "127.0.0.1" not in text and "secret-host" not in text
    assert r.json()["error"]["code"] == "llm_unavailable"


def test_unexpected_exception_is_sanitised(client, monkeypatch):
    c = client.app_state.container

    async def boom(*a, **k):
        raise RuntimeError("password=hunter2 /srv/app/secret.py")

    monkeypatch.setattr(c.engine, "handle", boom)
    r = client.post("/api/v1/chat", json=chat_payload("hello"))
    assert r.status_code == 500 and "hunter2" not in r.text and "/srv/app" not in r.text
    assert r.json()["error"]["code"] == "internal_error"


def test_rate_limiting(make_settings, fake_llm, ingested):
    app = create_app(make_settings(rate_limit_requests=3, rate_limit_window_s=60), llm=fake_llm, embedder=HashingEmbedder())
    with TestClient(app) as c:
        codes = [c.post("/api/v1/chat", json=chat_payload("hello")).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        r = c.post("/api/v1/chat", json=chat_payload("hello"))
        assert "Retry-After" in r.headers and r.json()["error"]["code"] == "rate_limited"
        # a different session has its own budget
        assert c.post("/api/v1/chat", json=chat_payload("hello", session="sess_other_01")).status_code == 200


def test_cors_allows_only_configured_origins(client):
    ok = client.options("/api/v1/chat", headers={"Origin": "http://localhost:8501", "Access-Control-Request-Method": "POST"})
    bad = client.options("/api/v1/chat", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8501"
    assert "access-control-allow-origin" not in bad.headers


def test_duplicate_client_message_id_over_http(client, fake_llm):
    p = chat_payload("Do you build chatbots?", client_message_id="msg_dup_0001")
    a = client.post("/api/v1/chat", json=p).json()
    n = len(fake_llm.calls)
    b = client.post("/api/v1/chat", json={**p, "conversation_id": a["conversation_id"]}).json()
    assert b["duplicate"] and b["message_id"] == a["message_id"] and len(fake_llm.calls) == n


# ------------------------------------------------------------------ admin endpoints
@pytest.fixture
def admin_client(make_settings, fake_llm, ingested):
    s = make_settings(admin_api_key="super-secret-admin-key")
    app = create_app(s, llm=fake_llm, embedder=HashingEmbedder())
    with TestClient(app) as c:
        yield c


def test_admin_endpoints_disabled_without_key(client):
    for method, path in [("get", "/api/v1/knowledge/status"), ("get", "/api/v1/leads")]:
        r = getattr(client, method)(path, headers={"X-Admin-Key": "anything"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "admin_disabled"
    assert client.post("/api/v1/knowledge/ingest", json={}).status_code == 403


def test_admin_endpoints_reject_wrong_key_and_accept_right_one(admin_client):
    assert admin_client.get("/api/v1/knowledge/status").status_code == 403
    assert admin_client.get("/api/v1/knowledge/status", headers={"X-Admin-Key": "wrong"}).status_code == 403
    ok = admin_client.get("/api/v1/knowledge/status", headers={"X-Admin-Key": "super-secret-admin-key"})
    assert ok.status_code == 200 and ok.json()["indexed"] and "ai-services/chatbots.md" in ok.json()["documents"]


def test_ingest_endpoint_reports_and_reloads(admin_client):
    h = {"X-Admin-Key": "super-secret-admin-key"}
    r = admin_client.post("/api/v1/knowledge/ingest", json={"full_rebuild": True}, headers=h)
    assert r.status_code == 200 and r.json()["full_rebuild"] and r.json()["chunk_count"] >= 8
    assert admin_client.get("/health").json()["knowledge_base"]["ready"]
    r2 = admin_client.post("/api/v1/knowledge/ingest", json={}, headers=h)
    assert r2.json()["unchanged"] and not r2.json()["added"]


def test_ingest_builds_index_from_scratch_and_chat_then_works(make_settings, fake_llm):
    s = make_settings(admin_api_key="k" * 20)
    app = create_app(s, llm=fake_llm, embedder=HashingEmbedder())
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "degraded"
        assert c.post("/api/v1/chat", json=chat_payload("Do you build chatbots?")).json()["response_status"] == "no_knowledge"
        assert c.post("/api/v1/knowledge/ingest", json={}, headers={"X-Admin-Key": "k" * 20}).status_code == 200
        assert c.post("/api/v1/chat", json=chat_payload("Do you build chatbots?")).json()["response_status"] == "answered"
