"""Lead rules, persistence, consent, duplicate handling, honest handoff."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.leads.rules import (
    extract_contact_details, is_valid_email, merge_fields, normalize_phone, qualification_score,
)
from app.leads.service import HandoffService, NullNotifier, WebhookNotifier
from app.leads.store import SQLiteLeadStore
from app.main import create_app
from app.rag.embeddings import HashingEmbedder
from tests.conftest import SESSION, chat_payload


def lead_payload(**kw):
    base = {"session_id": SESSION, "name": "Jane Doe", "email": "jane@acme.io", "consent": True,
            "project_type": "HR chatbot", "requirements": "answer policy questions", "timeline": "Q1", "company": "Acme"}
    base.update(kw)
    return base


# ------------------------------------------------------------------ rules
@pytest.mark.parametrize("email,ok", [
    ("a@b.co", True), ("jane.doe+x@sub.acme.io", True), ("nope", False), ("a@b", False), ("a b@c.io", False),
    ("", False), (None, False), ("x@" + "a" * 250 + ".com", False),
])
def test_email_validation(email, ok):
    assert is_valid_email(email) is ok


@pytest.mark.parametrize("raw,norm", [("+91 98765 43210", "+919876543210"), ("(555) 123-4567", "5551234567"), ("123", None), ("abc", None)])
def test_phone_normalisation(raw, norm):
    assert normalize_phone(raw) == norm


def test_contact_extraction_from_free_text():
    d = extract_contact_details("mail me at Jane.Doe@acme.io or call +1 (415) 555-0199 please")
    assert d == {"email": "Jane.Doe@acme.io", "phone": "+14155550199"}
    assert extract_contact_details("we have 2024 plans for 15 users") == {}


def test_merge_rejects_junk_and_keeps_existing():
    out = merge_fields({"name": "Jane"}, {"name": "unknown", "email": "bad", "company": " Acme  Corp ", "budget": None, "phone": "12"})
    assert out == {"name": "Jane", "company": "Acme Corp"}


def test_qualification_score_grows_with_information():
    assert qualification_score({}) == 0
    assert qualification_score({"project_type": "x", "requirements": "y"}) == 40
    assert qualification_score({k: "v" for k in ("name", "company", "email", "phone", "project_type", "requirements", "timeline", "budget")}) == 100


# ------------------------------------------------------------------ store
@pytest.fixture
def store():
    s = SQLiteLeadStore(":memory:")
    yield s
    s.close()


def test_store_create_get_list(store):
    lid, dup, status = store.upsert({"name": "A", "email": "a@x.io"}, score=30, follow_up=False, conversation_id="c1", summary="s")
    assert (dup, status) == (False, "new")
    row = store.get(lid)
    assert row["email"] == "a@x.io" and row["score"] == 30 and row["consent_at"]
    assert [x["id"] for x in store.list()] == [lid] and store.list(status="closed") == []


def test_store_merges_duplicate_email_case_insensitively(store):
    a, *_ = store.upsert({"name": "A", "email": "a@x.io", "company": "Old"}, score=20, follow_up=False, conversation_id=None, summary=None)
    b, dup, status = store.upsert({"email": "A@X.IO", "project_type": "ERP"}, score=50, follow_up=True, conversation_id="c2", summary=None)
    assert a == b and dup and status == "handoff_requested"
    row = store.get(a)
    assert row["company"] == "Old" and row["project_type"] == "ERP" and row["score"] == 50 and row["follow_up_requested"] == 1
    assert [h["event"] for h in store.history(a)] == ["created", "updated"]
    assert len(store.list()) == 1


def test_store_status_and_retention(store):
    lid, *_ = store.upsert({"email": "a@x.io"}, score=1, follow_up=False, conversation_id=None, summary=None)
    assert store.set_status(lid, "contacted", "called") and store.get(lid)["status"] == "contacted"
    assert not store.set_status(999, "closed")
    store._db.execute("UPDATE leads SET created_at='2020-01-01T00:00:00+00:00'")
    assert store.purge_older_than(180) == 1 and store.get(lid) is None and store.history(lid) == []


def test_store_is_safe_against_sql_injection(store):
    lid, *_ = store.upsert({"name": "x'); DROP TABLE leads;--", "email": "a@x.io"}, score=1, follow_up=False, conversation_id=None, summary=None)
    assert store.get(lid)["name"].startswith("x')") and len(store.list()) == 1


# ------------------------------------------------------------------ HTTP flow
def test_lead_requires_consent(client):
    r = client.post("/api/v1/leads", json=lead_payload(consent=False))
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    assert client.app_state.container.lead_store.list() == []


@pytest.mark.parametrize("override,code", [({"email": "not-an-email"}, "invalid_email"), ({"phone": "12"}, "invalid_phone")])
def test_lead_validation(client, override, code):
    r = client.post("/api/v1/leads", json=lead_payload(**override))
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_lead_capture_without_notifier_is_honest(client):
    r = client.post("/api/v1/leads", json=lead_payload(request_human_follow_up=True))
    b = r.json()
    assert r.status_code == 200 and b["notified"] is False and b["follow_up_requested"]
    assert "can't send a live notification" in b["message"] and "fixture.example" in b["message"]
    assert b["qualification_score"] >= 60
    row = client.app_state.container.lead_store.get(b["lead_id"])
    assert row["status"] == "handoff_requested" and row["email"] == "jane@acme.io"


def test_lead_capture_without_follow_up_does_not_mention_notification(client):
    b = client.post("/api/v1/leads", json=lead_payload()).json()
    assert b["status"] == "new" and "notif" not in b["message"].lower()


def test_lead_duplicate_is_merged(client):
    a = client.post("/api/v1/leads", json=lead_payload()).json()
    b = client.post("/api/v1/leads", json=lead_payload(phone="+1 415 555 0199")).json()
    assert b["duplicate"] and a["lead_id"] == b["lead_id"]
    assert client.app_state.container.lead_store.get(a["lead_id"])["phone"] == "+14155550199"


def test_lead_merges_details_collected_in_chat(client, fake_llm):
    import json
    fake_llm.analysis_queue.append(json.dumps({"intent": "project_discussion", "standalone_query": "ERP",
                                               "lead": {"project_type": "ERP system", "timeline": "6 months"}}))
    c1 = client.post("/api/v1/chat", json=chat_payload("We need an ERP system within 6 months")).json()
    r = client.post("/api/v1/leads", json={"session_id": SESSION, "conversation_id": c1["conversation_id"],
                                           "name": "Raj", "email": "raj@corp.in", "consent": True})
    lead = client.app_state.container.lead_store.get(r.json()["lead_id"])
    assert lead["project_type"] == "ERP system" and lead["timeline"] == "6 months" and lead["name"] == "Raj"
    follow = client.post("/api/v1/chat", json=chat_payload("thanks", conversation_id=c1["conversation_id"])).json()
    assert follow["lead_status"] == "captured"


def test_lead_cannot_attach_to_another_sessions_conversation(client):
    c1 = client.post("/api/v1/chat", json=chat_payload("hello")).json()
    r = client.post("/api/v1/leads", json=lead_payload(session_id="sess_attacker1", conversation_id=c1["conversation_id"]))
    assert r.status_code == 200
    victim_conv = client.app_state.container.sessions.find_conversation(SESSION, c1["conversation_id"])
    assert victim_conv.lead.status != "captured"


def test_lead_listing_requires_admin(client):
    client.post("/api/v1/leads", json=lead_payload())
    assert client.get("/api/v1/leads").status_code == 403
    assert client.patch("/api/v1/leads/1/status", json={"status": "closed"}).status_code == 403


def test_admin_can_list_and_update_leads(make_settings, fake_llm, ingested):
    app = create_app(make_settings(admin_api_key="admin-key-123456"), llm=fake_llm, embedder=HashingEmbedder())
    h = {"X-Admin-Key": "admin-key-123456"}
    with TestClient(app) as c:
        lid = c.post("/api/v1/leads", json=lead_payload()).json()["lead_id"]
        assert c.get("/api/v1/leads", headers=h).json()["count"] == 1
        assert c.patch(f"/api/v1/leads/{lid}/status", json={"status": "contacted"}, headers=h).status_code == 200
        assert c.patch("/api/v1/leads/999/status", json={"status": "closed"}, headers=h).status_code == 404
        assert c.patch(f"/api/v1/leads/{lid}/status", json={"status": "bogus"}, headers=h).status_code == 422
        assert c.get("/api/v1/leads?status=contacted", headers=h).json()["count"] == 1


# ------------------------------------------------------------------ handoff notifier
async def test_webhook_notifier_success_and_failure():
    sent = []

    def ok(request: httpx.Request):
        sent.append(request.content)
        return httpx.Response(200)

    assert await WebhookNotifier("http://hook.test", httpx.MockTransport(ok)).notify({"a": 1}) is True and sent
    assert await WebhookNotifier("http://hook.test", httpx.MockTransport(lambda r: httpx.Response(500))).notify({}) is False

    def down(request):
        raise httpx.ConnectError("refused")

    assert await WebhookNotifier("http://hook.test", httpx.MockTransport(down)).notify({}) is False


def test_notified_message_only_when_delivery_succeeds(make_settings, fake_llm, ingested):
    class Ok:
        delivers = True
        async def notify(self, payload): return True

    class Down:
        delivers = True
        async def notify(self, payload): return False

    for notifier, expected in ((Ok(), True), (Down(), False)):
        app = create_app(make_settings(), llm=fake_llm, embedder=HashingEmbedder(), notifier=notifier)
        with TestClient(app) as c:
            b = c.post("/api/v1/leads", json=lead_payload(request_human_follow_up=True)).json()
        assert b["notified"] is expected
        assert ("passed to the team" in b["message"]) is expected


def test_describe_contact_uses_configuration_only(make_settings):
    s = make_settings(contact_email="hello@fixture.example")
    text = HandoffService(s, NullNotifier()).describe_contact()
    assert "hello@fixture.example" in text and "can't contact the team myself" in text
    assert "phone" not in text.lower() and "call" not in text.lower()  # no invented phone number
    assert "can pass your details" in HandoffService(s, WebhookNotifier("http://x")).describe_contact()
