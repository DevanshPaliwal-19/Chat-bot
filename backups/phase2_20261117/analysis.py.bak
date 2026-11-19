"""Structured understanding of a visitor turn (produced by the LLM or robust deterministic fallback)."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..leads.rules import LEAD_FIELDS, clean_field

INTENTS = (
    "greeting", "company_info", "service_inquiry", "project_discussion", "pricing",
    "human_request", "website_navigation", "closing", "smalltalk", "off_topic", "other",
)
NO_RETRIEVAL_INTENTS = frozenset({"greeting", "closing", "smalltalk"})

_NAV_PATTERNS = re.compile(
    r"\b(?:where (?:can i find|is)|link to|url (?:for|to)|show me the page|go to page|take me to|website page|navigat(?:e|ion))\b",
    re.I,
)

_PROJECT_SIGNALS = re.compile(
    r"\b(?:i want to build|we want to build|looking to build|looking for|planning (?:to|a)|need a|need an|can you develop|we need|our project|help us build|create an app|create a website|build a chatbot|develop a)\b",
    re.I,
)

_SERVICE_KEYWORDS: list[tuple[str, str]] = [
    ("cloud migration", "Database & Cloud Migration"),
    ("database migration", "Database & Cloud Migration"),
    ("data analytics", "BI & Data Analytics"),
    ("machine learning", "AI & Machine Learning"),
    ("artificial intelligence", "AI & Machine Learning"),
    ("workflow automation", "AI Agents & Workflow Automation"),
    ("ai agents", "AI Agents & Workflow Automation"),
    ("ai/ml", "AI & Machine Learning"),
    ("chatbot", "AI Chatbots & Conversational Agents"),
    ("agent", "AI Agents & Workflow Automation"),
    ("web development", "Web Development & Web Applications"),
    ("web application", "Web Development & Web Applications"),
    ("website", "Web Development & Web Applications"),
    ("web", "Web Development & Web Applications"),
    ("mobile app", "Mobile App Development"),
    ("mobile development", "Mobile App Development"),
    ("ios", "Mobile App Development (iOS & Android)"),
    ("android", "Mobile App Development (iOS & Android)"),
    ("mobile", "Mobile App Development"),
    ("app", "Mobile App Development"),
    ("ui/ux", "UI/UX Design & Prototyping"),
    ("design", "UI/UX Design & Prototyping"),
    ("testing", "QA & Automated Testing"),
    ("qa", "QA & Automated Testing"),
    ("devops", "Cloud & DevOps"),
    ("cloud", "Cloud & DevOps"),
    ("cybersecurity", "Cybersecurity Services"),
    ("security", "Cybersecurity Services"),
    ("migration", "Database & Cloud Migration"),
    ("erp", "ERP & CRM Solutions"),
    ("crm", "ERP & CRM Solutions"),
    ("enterprise software", "Enterprise Software Development"),
    ("enterprise", "Enterprise Software Development"),
    ("bi", "BI & Data Analytics"),
]

_FILLERS = re.compile(
    r"^(?:can you\s+(?:please\s+)?tell me\s+(?:about|more about)?|"
    r"could you\s+(?:please\s+)?explain|"
    r"i(?:'d| would)\s+like to know\s+(?:about)?|"
    r"please\s+(?:tell me|provide information on|explain)|"
    r"tell me\s+(?:more\s+)?about|"
    r"what\s+(?:do you know|can you tell me)\s+about|"
    r"do you\s+(?:offer|provide|support)|"
    r"what is\s+|what are\s+)\s*",
    re.I,
)

_ORDINAL_MAP: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\b(?:the\s+)?(?:first|1st|number\s+one|#1)\b", re.I), 0),
    (re.compile(r"\b(?:the\s+)?(?:second|2nd|number\s+two|#2)\b", re.I), 1),
    (re.compile(r"\b(?:the\s+)?(?:third|3rd|number\s+three|#3)\b", re.I), 2),
    (re.compile(r"\b(?:the\s+)?(?:fourth|4th|number\s+four|#4)\b", re.I), 3),
    (re.compile(r"\b(?:the\s+)?(?:fifth|5th|number\s+five|#5)\b", re.I), 4),
    (re.compile(r"\b(?:the\s+)?(?:last|final)\s+(?:one|option)\b", re.I), -1),
]

_ANAPHORA_PATTERNS = re.compile(
    r"\b(?:that|it|that one|its|the service|that service|those|this|the second option|the first option)\b",
    re.I,
)


def normalize_query(query: str, strip_punctuation: bool = False) -> str:
    """Normalize query for retrieval by trimming filler phrases and extra spaces."""
    cleaned = _FILLERS.sub("", query.strip())
    if strip_punctuation:
        cleaned = re.sub(r"[?!.]+$", "", cleaned).strip()
    return " ".join(cleaned.split()) if cleaned else query.strip()


def extract_options_from_text(text: str) -> list[str]:
    """Extract numbered or bulleted options from assistant text."""
    options: list[str] = []
    # Match numbered items: "1. Option A" or "1) Option A"
    numbered = re.findall(r"(?:^|\n)\s*(?:\d+[\.\)]|\-|\*)\s+([^\n]+)", text)
    for opt in numbered:
        cleaned = re.sub(r"^[*_#]+|[*_#]+$", "", opt).strip()
        if len(cleaned) > 2 and len(cleaned) < 120:
            options.append(cleaned)
    return options


def detect_service_mention(text: str) -> str | None:
    """Detect if a specific Osiyatech service category is mentioned in text."""
    lower = text.lower()
    for kw, service in _SERVICE_KEYWORDS:
        # Use word boundaries for short acronyms like 'bi', 'qa', 'app'
        if len(kw) <= 3:
            if re.search(rf"\b{re.escape(kw)}\b", lower):
                return service
        elif kw in lower:
            return service
    return None


def resolve_references(
    message: str,
    previous_assistant_message: str | None = None,
    previous_user_message: str | None = None,
    last_options: list[str] | None = None,
    active_topic: str | None = None,
    selected_service: str | None = None,
) -> tuple[str, str | None]:
    """Resolve anaphoric and ordinal references in user queries.
    Returns (resolved_query, resolved_service)."""
    msg_strip = message.strip()
    detected_svc = detect_service_mention(msg_strip)
    options = list(last_options or [])
    if not options and previous_assistant_message:
        options = extract_options_from_text(previous_assistant_message)

    # 1. Check for ordinal choice: "the second option", "2nd one", "first"
    for pattern, idx in _ORDINAL_MAP:
        if pattern.search(msg_strip):
            if options:
                try:
                    chosen = options[idx]
                    resolved = f"{chosen} {msg_strip}"
                    svc = detect_service_mention(chosen) or selected_service
                    return normalize_query(resolved), svc
                except IndexError:
                    pass

    # 2. Check for demonstrative / continuation follow-up: "tell me more about that", "how does that work", "its pricing"
    has_pronoun = bool(_ANAPHORA_PATTERNS.search(msg_strip))
    is_short = len(msg_strip.split()) <= 6

    context_target = (
        selected_service
        or active_topic
        or (detect_service_mention(previous_assistant_message or "") if previous_assistant_message else None)
        or (detect_service_mention(previous_user_message or "") if previous_user_message else None)
    )

    if has_pronoun and context_target:
        # If user asks "What technologies do you use in that?", replace "that" with the context target
        replaced = _ANAPHORA_PATTERNS.sub(context_target, msg_strip, count=1)
        return normalize_query(replaced), context_target

    # 3. Fall back to previous user message if short
    if previous_user_message and is_short:
        svc = detected_svc or selected_service or (detect_service_mention(previous_user_message) if previous_user_message else None)
        return f"{previous_user_message} {msg_strip}", svc

    if is_short and context_target:
        return normalize_query(f"{context_target} {msg_strip}"), context_target

    svc = detected_svc or selected_service
    return msg_strip, svc


class TurnAnalysis(BaseModel):
    intent: str = "other"
    standalone_query: str | None = None
    wants_human: bool = False
    needs_clarification: bool = False
    clarifying_question: str | None = None
    lead: dict[str, str] = Field(default_factory=dict)
    active_topic: str | None = None
    selected_service: str | None = None
    stage: str | None = None
    wants_navigation: bool = False
    source: str = "llm"

    @field_validator("intent", mode="before")
    @classmethod
    def _intent(cls, v: Any) -> str:
        v = str(v or "other").strip().lower().replace(" ", "_")
        return v if v in INTENTS else "other"

    @field_validator("wants_human", "needs_clarification", "wants_navigation", mode="before")
    @classmethod
    def _bool(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in {"true", "yes", "1"}
        return bool(v)

    @field_validator("standalone_query", "clarifying_question", "active_topic", "selected_service", "stage", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str | None:
        if not isinstance(v, str):
            return None if v is None else str(v)[:300]
        v = " ".join(v.split())[:300]
        return v or None

    @field_validator("lead", mode="before")
    @classmethod
    def _lead(cls, v: Any) -> dict[str, str]:
        if not isinstance(v, dict):
            return {}
        out: dict[str, str] = {}
        for key in LEAD_FIELDS:
            cleaned = clean_field(key, v.get(key))
            if cleaned:
                out[key] = cleaned
        return out


def parse_analysis(raw: str) -> TurnAnalysis:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in analysis output")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("analysis JSON is not an object")
    return TurnAnalysis.model_validate(data)


def fallback_analysis(
    message: str,
    previous_user_message: str | None = None,
    previous_assistant_message: str | None = None,
    last_options: list[str] | None = None,
    active_topic: str | None = None,
    selected_service: str | None = None,
) -> TurnAnalysis:
    """Robust deterministic turn analysis used when LLM analysis is disabled or fails.
    Performs reference resolution, intent classification, service extraction, and navigation detection."""
    msg = message.strip()
    resolved_query, detected_svc = resolve_references(
        msg,
        previous_assistant_message=previous_assistant_message,
        previous_user_message=previous_user_message,
        last_options=last_options,
        active_topic=active_topic,
        selected_service=selected_service,
    )

    # 1. Detect greetings and closings
    lower = msg.lower()
    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "hi there", "hello there"}
    closings = {"bye", "goodbye", "see you", "thanks", "thank you", "that's all", "no thank you"}

    if lower in greetings or any(lower.startswith(g + " ") for g in greetings):
        return TurnAnalysis(
            intent="greeting",
            standalone_query=resolved_query,
            source="fallback",
            stage="greeting",
            selected_service=detected_svc,
        )

    if any(lower == c or lower.startswith(c + " ") or lower.startswith(c + ",") or lower.startswith(c + ".") for c in closings):
        return TurnAnalysis(
            intent="closing",
            standalone_query=resolved_query,
            source="fallback",
            stage="concluded",
            selected_service=detected_svc,
        )

    # 2. Detect human request
    human_signals = {"speak with someone", "human", "contact a person", "talk to human", "sales representative", "speak with your team"}
    if any(h in lower for h in human_signals):
        return TurnAnalysis(
            intent="human_request",
            standalone_query=resolved_query,
            wants_human=True,
            source="fallback",
            stage="qualifying",
            selected_service=detected_svc,
        )

    # 3. Detect pricing intent
    if any(p in lower for p in {"price", "pricing", "cost", "how much", "rates", "quote"}):
        return TurnAnalysis(
            intent="pricing",
            standalone_query=resolved_query,
            source="fallback",
            active_topic="pricing",
            selected_service=detected_svc,
            stage="discovery",
        )

    # 4. Detect website navigation intent
    if _NAV_PATTERNS.search(msg) or "where can i find" in lower or "url" in lower:
        return TurnAnalysis(
            intent="website_navigation",
            standalone_query=resolved_query,
            wants_navigation=True,
            source="fallback",
            selected_service=detected_svc,
            active_topic=detected_svc or "website_navigation",
        )

    # 5. Detect project discussion & requirements
    if _PROJECT_SIGNALS.search(msg):
        svc = detected_svc or detect_service_mention(msg)
        return TurnAnalysis(
            intent="project_discussion",
            standalone_query=resolved_query,
            source="fallback",
            stage="discovery",
            selected_service=svc,
            active_topic=svc or "project_discussion",
        )

    # 6. Service inquiry
    if detected_svc or "service" in lower or "what do you do" in lower:
        return TurnAnalysis(
            intent="service_inquiry",
            standalone_query=resolved_query,
            source="fallback",
            stage="exploring",
            selected_service=detected_svc,
            active_topic=detected_svc or "services",
        )

    return TurnAnalysis(
        intent="other",
        standalone_query=resolved_query,
        source="fallback",
        selected_service=detected_svc,
        active_topic=active_topic,
    )
