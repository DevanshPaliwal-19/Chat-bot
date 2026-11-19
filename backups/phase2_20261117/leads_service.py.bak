"""Lead capture + honest human handoff.

There is no CRM integration here. A captured lead is stored locally (SQLite) for the team to follow up.
A live notification is only claimed when a configured notifier actually delivered it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import httpx

from ..config import Settings
from ..errors import ValidationFailed
from ..memory.sessions import Conversation
from ..schemas import LeadCreate, LeadResponse
from .rules import is_valid_email, merge_fields, normalize_phone, qualification_score
from .store import LeadStore

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    delivers: bool

    async def notify(self, payload: dict) -> bool: ...


class NullNotifier:
    """Default: nobody is notified live; the lead only sits in the follow-up queue."""

    delivers = False

    async def notify(self, payload: dict) -> bool:
        return False


class WebhookNotifier:
    delivers = True

    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = url
        self._transport = transport

    async def notify(self, payload: dict) -> bool:
        try:
            async with httpx.AsyncClient(timeout=8.0, transport=self._transport) as client:
                resp = await client.post(self._url, json=payload)
                return resp.status_code < 300
        except httpx.HTTPError as exc:
            logger.warning("handoff webhook failed: %s", type(exc).__name__)
            return False


class HandoffService:
    def __init__(self, settings: Settings, notifier: Notifier | None = None) -> None:
        self.settings = settings
        if notifier is None:
            url = settings.handoff_webhook_url
            notifier = WebhookNotifier(url.get_secret_value()) if url else NullNotifier()
        self.notifier = notifier

    def describe_contact(self) -> str:
        """Honest description of how a visitor can actually reach the team (from configuration only)."""
        s = self.settings
        parts = []
        if s.contact_email:
            parts.append(f"email {s.contact_email}")
        if s.contact_phone:
            parts.append(f"call {s.contact_phone}")
        parts.append(f"use the contact options on {s.company_website}")
        how = ", or ".join(parts)
        note = (
            "I can't contact the team myself from this chat"
            if not self.notifier.delivers
            else "I can pass your details to the team from this chat"
        )
        return f"{note}. You can {how}. If you like, I can also save your details here so the team can follow up."


class LeadService:
    def __init__(self, settings: Settings, store: LeadStore, handoff: HandoffService) -> None:
        self.settings = settings
        self.store = store
        self.handoff = handoff

    async def capture(self, req: LeadCreate, conv: Conversation | None) -> LeadResponse:
        if not req.consent:
            raise ValidationFailed("Consent is required before your details can be saved.", code="consent_required")
        if not is_valid_email(req.email):
            raise ValidationFailed("Please enter a valid email address.", code="invalid_email")
        if req.phone and not normalize_phone(req.phone):
            raise ValidationFailed("Please enter a valid phone number, or leave it blank.", code="invalid_phone")

        submitted = {k: v for k, v in req.model_dump(include={
            "name", "company", "email", "phone", "project_type", "requirements", "timeline", "budget", "preferred_contact",
        }).items() if v}
        if "phone" in submitted:
            submitted["phone"] = normalize_phone(submitted["phone"]) or ""
        fields = merge_fields(conv.lead.fields if conv else {}, submitted)  # form input wins over chat-derived guesses
        score = qualification_score(fields)
        summary = conv.summary or None if conv else None
        if conv and not summary and conv.turns:
            summary = " | ".join(t.content[:160] for t in conv.turns if t.role == "user")[-600:] or None

        lead_id, duplicate, status = await asyncio.to_thread(
            self.store.upsert, fields, score=score, follow_up=req.request_human_follow_up,
            conversation_id=conv.id if conv else req.conversation_id, summary=summary,
        )

        notified = False
        if req.request_human_follow_up:
            notified = await self.handoff.notifier.notify({
                "lead_id": lead_id, "name": fields.get("name"), "company": fields.get("company"),
                "email": fields.get("email"), "phone": fields.get("phone"), "project_type": fields.get("project_type"),
                "requirements": fields.get("requirements"), "timeline": fields.get("timeline"), "score": score,
            })

        if conv:
            conv.lead.fields = fields
            conv.lead.score = score
            conv.lead.status = "handoff_requested" if req.request_human_follow_up else "captured"

        if req.request_human_follow_up and notified:
            message = "Thanks! Your details were passed to the team for follow-up."
        elif req.request_human_follow_up:
            message = (
                "Thanks! Your request is saved in the team's follow-up queue. "
                "I can't send a live notification from here, so for anything urgent please use the contact options on "
                f"{self.settings.company_website}."
            )
        else:
            message = "Thanks! Your details are saved so the team can get in touch."
        return LeadResponse(
            lead_id=lead_id, status=status, duplicate=duplicate, qualification_score=score,
            follow_up_requested=req.request_human_follow_up, notified=notified, message=message,
        )
