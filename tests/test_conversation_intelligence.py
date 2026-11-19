"""Unit tests for Milestone 1: Core Intelligence & Conversation Engine.
Tests intent detection, reference resolution, query normalization, and conversation state.
"""
from __future__ import annotations

import pytest

from app.core.analysis import (
    TurnAnalysis,
    detect_service_mention,
    extract_options_from_text,
    fallback_analysis,
    normalize_query,
    resolve_references,
)
from app.memory.sessions import Conversation, Turn


def test_normalize_query():
    assert normalize_query("Can you please tell me about AI chatbots?", strip_punctuation=True) == "AI chatbots"
    assert normalize_query("What is cloud migration?", strip_punctuation=True) == "cloud migration"
    assert normalize_query("Could you explain web development???", strip_punctuation=True) == "web development"
    assert normalize_query("Tell me more about mobile apps.", strip_punctuation=True) == "mobile apps"
    assert normalize_query("plain query") == "plain query"


def test_extract_options_from_text():
    text = (
        "We offer several AI options:\n"
        "1. Website Chatbots for customer support\n"
        "2. Internal Business Assistants for staff\n"
        "3. Custom Software Integration"
    )
    options = extract_options_from_text(text)
    assert len(options) == 3
    assert "Website Chatbots for customer support" in options[0]
    assert "Internal Business Assistants for staff" in options[1]
    assert "Custom Software Integration" in options[2]


def test_resolve_ordinal_references():
    options = [
        "Website Chatbots",
        "Mobile App Development",
        "Cloud Migration",
    ]
    # "the second option"
    resolved, svc = resolve_references("I am interested in the second option", last_options=options)
    assert "Mobile App Development" in resolved
    assert svc == "Mobile App Development"

    # "the first one"
    resolved, svc = resolve_references("Tell me about the first one", last_options=options)
    assert "Website Chatbots" in resolved
    assert svc == "AI Chatbots & Conversational Agents"

    # "last option"
    resolved, svc = resolve_references("How about the last option?", last_options=options)
    assert "Cloud Migration" in resolved
    assert svc == "Database & Cloud Migration"


def test_resolve_anaphoric_references():
    # User asks "What technologies do you use in that?" after discussing AI Chatbots
    resolved, svc = resolve_references(
        "What technologies do you use in that?",
        selected_service="AI Chatbots & Conversational Agents",
        active_topic="AI Chatbots & Conversational Agents",
    )
    assert "AI Chatbots & Conversational Agents" in resolved
    assert svc == "AI Chatbots & Conversational Agents"
    assert "in that" not in resolved.lower()

    # User asks "tell me more"
    resolved, svc = resolve_references(
        "Tell me more",
        previous_assistant_message="We build custom enterprise software and ERP solutions.",
    )
    assert "ERP" in resolved or "Enterprise" in resolved


def test_fallback_analysis_intents():
    # Greeting
    a_greet = fallback_analysis("Hello there!")
    assert a_greet.intent == "greeting"
    assert a_greet.stage == "greeting"

    # Closing
    a_close = fallback_analysis("Thank you, that's all for today.")
    assert a_close.intent == "closing"
    assert a_close.stage == "concluded"

    # Human request
    a_human = fallback_analysis("Can I speak with someone from your team?")
    assert a_human.intent == "human_request"
    assert a_human.wants_human is True

    # Pricing
    a_price = fallback_analysis("How much does a mobile app cost?")
    assert a_price.intent == "pricing"
    assert a_price.selected_service == "Mobile App Development"

    # Website navigation
    a_nav = fallback_analysis("Where can I find your AI services page?")
    assert a_nav.intent == "website_navigation"
    assert a_nav.wants_navigation is True

    # Project discussion
    a_proj = fallback_analysis("I want to build an AI chatbot for my ecommerce store")
    assert a_proj.intent == "project_discussion"
    assert a_proj.stage == "discovery"
    assert a_proj.selected_service == "AI Chatbots & Conversational Agents"


def test_conversation_state_preservation():
    conv = Conversation(id="conv_test_1")
    assert conv.stage == "greeting"
    assert conv.selected_service is None
    assert conv.last_options_presented == []

    # Update state
    conv.selected_service = "AI Chatbots & Conversational Agents"
    conv.stage = "discovery"
    conv.last_options_presented = ["Website Chatbot", "WhatsApp Bot"]

    assert conv.selected_service == "AI Chatbots & Conversational Agents"
    assert conv.stage == "discovery"
    assert len(conv.last_options_presented) == 2
