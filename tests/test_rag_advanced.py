"""Unit tests for Milestone 2: Advanced RAG Pipeline.
Tests JSONL parser, acronym query expansion, evidence scoring/reranking, and metadata filtering.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.rag.index import KnowledgeIndex
from app.rag.parsers import parse_file, parse_jsonl
from app.rag.retriever import Retriever, expand_tokens
from app.rag.types import Chunk


def test_expand_tokens():
    tokens = ["we", "need", "ai", "and", "erp", "solutions"]
    expanded = expand_tokens(tokens)
    assert "artificial" in expanded and "intelligence" in expanded
    assert "enterprise" in expanded and "resource" in expanded and "planning" in expanded
    assert expanded.count("ai") == 1  # No duplicate tokens


def test_parse_jsonl(tmp_path):
    jsonl_file = tmp_path / "test_units.jsonl"
    records = [
        {
            "document_id": "UNIT-001",
            "category": "AI Services",
            "subcategory": "Chatbots",
            "title": "Osiyatech AI Chatbot Architecture",
            "content": "Osiyatech designs production-grade conversational AI chatbots using RAG and hybrid FAISS retrieval.",
            "related_questions": ["What chatbots do you build?", "Do you support RAG?"],
            "source_url": "https://osiyatech.example/ai-chatbots",
            "verification_status": "Verified on website",
        },
        {
            "document_id": "UNIT-002",
            "category": "Cloud Services",
            "subcategory": "DevOps",
            "title": "Cloud Infrastructure Automation",
            "content": "We deploy scalable Kubernetes clusters and automated CI/CD pipelines across AWS and Azure.",
            "related_questions": ["Do you support Kubernetes?", "What cloud providers?"],
            "source_url": "https://osiyatech.example/cloud-devops",
            "verification_status": "Verified",
        },
    ]
    jsonl_file.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    parsed = parse_file(jsonl_file)
    assert parsed.source_type == "jsonl"
    assert len(parsed.pages) == 2
    assert "Osiyatech AI Chatbot Architecture" in parsed.pages[0].text
    assert "**Category:** AI Services - Chatbots" in parsed.pages[0].text
    assert "**Source:** https://osiyatech.example/ai-chatbots" in parsed.pages[0].text
    assert "Cloud Infrastructure Automation" in parsed.pages[1].text


def test_reranking_and_evidence_scoring(make_settings):
    settings = make_settings()
    c1 = Chunk(
        chunk_id="c1", document_id="d1", embed_key="k1", content_hash="h1", source_type="markdown",
        text="We build AI Chatbots with deep conversational intelligence.",
        source_document="doc1.md", title="AI Chatbot Engineering", section="Overview",
        service_category="AI Services", approval_status="approved",
    )
    c2 = Chunk(
        chunk_id="c2", document_id="d2", embed_key="k2", content_hash="h2", source_type="markdown",
        text="General software consulting for startups and companies.",
        source_document="doc2.md", title="General Consulting", section="Services",
        service_category="Consulting", approval_status="draft",
    )
    # Dummy 4-dim vectors
    v1 = np.array([1.0, 0.0, 0.0, 0.0], dtype="float32")
    v2 = np.array([0.0, 1.0, 0.0, 0.0], dtype="float32")
    vectors = np.stack([v1, v2])

    index = KnowledgeIndex([c1, c2], vectors, "dummy-embedder")

    class DummyEmbedder:
        name = "dummy-embedder"
        dim = 4

        def embed(self, texts):
            return np.array([[0.9, 0.1, 0.0, 0.0]], dtype="float32")

    retriever = Retriever(index, DummyEmbedder(), settings)
    res = retriever.retrieve("AI chatbot engineering", top_k=2)

    assert len(res.chunks) >= 1
    # c1 should score significantly higher due to title match, dense match, and approved status
    top_chunk = res.chunks[0].chunk
    assert top_chunk.chunk_id == "c1"
    assert res.chunks[0].score > 0.5


def test_metadata_filtering(make_settings):
    settings = make_settings(require_approved_only=True)
    c_approved = Chunk(
        chunk_id="c1", document_id="d1", embed_key="k1", content_hash="h1", source_type="markdown",
        text="Approved AI chatbot documentation.",
        source_document="approved.md", approval_status="approved", service_category="AI Services",
    )
    c_draft = Chunk(
        chunk_id="c2", document_id="d2", embed_key="k2", content_hash="h2", source_type="markdown",
        text="Draft unverified notes on chatbots.",
        source_document="draft.md", approval_status="draft", service_category="AI Services",
    )
    vectors = np.eye(2, dtype="float32")
    index = KnowledgeIndex([c_approved, c_draft], vectors, "dummy")

    class DummyEmbedder:
        name = "dummy"
        dim = 2

        def embed(self, texts):
            return np.array([[1.0, 1.0]], dtype="float32")

    retriever = Retriever(index, DummyEmbedder(), settings)
    res = retriever.retrieve("chatbots")
    assert all(rc.chunk.approval_status == "approved" for rc in res.chunks)
    assert not any(rc.chunk.chunk_id == "c2" for rc in res.chunks)
