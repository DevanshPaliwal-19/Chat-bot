"""Tests for Phase 1: Knowledge Governance, Ingestion, Tier Filtering, and Evidence Isolation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.rag.chunking import chunk_document
from app.rag.embeddings import HashingEmbedder
from app.rag.ingest import Ingestor, determine_service_category, is_internal_document
from app.rag.parsers import parse_docx, parse_file, parse_jsonl, parse_xlsx
from app.rag.retriever import Retriever
from app.rag.types import Chunk, ParsedDocument, ParsedPage

KB_FINAL = Path("knowledge_base") / "Osiyatech_Chatbot_final"


def test_docx_parser_extracts_tables_and_headings():
    docx_path = KB_FINAL / "01_company_profile.docx"
    assert docx_path.exists(), "01_company_profile.docx must be present in knowledge base"
    doc = parse_docx(docx_path)
    assert len(doc.pages) >= 1
    text = doc.full_text
    assert "Osiyatech" in text
    assert "Document control" in text or "Document ID" in text
    assert "|" in text, "Tables in docx should be converted to markdown tables"
    assert doc.meta.get("approval_status") == "approved"
    assert doc.meta.get("trust_tier") == "tier_1"


def test_xlsx_faq_parser_extracts_all_questions_and_metadata():
    faq_path = KB_FINAL / "04_faqs.xlsx"
    assert faq_path.exists(), "04_faqs.xlsx must be present in knowledge base"
    doc = parse_xlsx(faq_path)
    # 137 distinct FAQ rows in sheet 1
    assert len(doc.pages) >= 130, f"Expected at least 130 FAQs, got {len(doc.pages)}"
    first_faq = doc.pages[0]
    assert "# FAQ:" in first_faq.text
    assert "**Answer:**" in first_faq.text
    assert first_faq.meta.get("approval_status") == "approved"
    assert first_faq.meta.get("trust_tier") == "tier_1"
    assert first_faq.meta.get("service_category") != ""


def test_xlsx_portfolio_parser_extracts_all_projects():
    port_path = KB_FINAL / "03_portfolio.xlsx"
    assert port_path.exists(), "03_portfolio.xlsx must be present in knowledge base"
    doc = parse_xlsx(port_path)
    assert len(doc.pages) >= 36, f"Expected at least 36 portfolio projects, got {len(doc.pages)}"
    first_proj = doc.pages[0]
    assert "# Project:" in first_proj.text
    assert first_proj.meta.get("approval_status") == "approved"
    assert first_proj.meta.get("trust_tier") == "tier_1"


def test_internal_tier_3_documents_are_flagged_and_excluded():
    bad_files = [
        "Osiyatech_Chatbot_final/07_missing_and_unverified_information.md",
        "Osiyatech_Chatbot_final/08_website_review_report.md",
        "internal_audit_notes.txt",
        "_archive/old_faq.md",
    ]
    for bf in bad_files:
        assert is_internal_document(bf) is True, f"File {bf} should be identified as internal Tier 3"

    good_files = [
        "00_Osiyatech_Website_AI_Chatbot_Knowledge_Base.pdf",
        "Osiyatech_Chatbot_final/01_company_profile.docx",
        "Osiyatech_Chatbot_final/04_faqs.xlsx",
        "ai-ml-development/service.md",
    ]
    for gf in good_files:
        assert is_internal_document(gf) is False, f"File {gf} should NOT be internal"


def test_category_extraction_fixes_parent_folder_corruption():
    # Should not be 'Osiyatech_Chatbot_final'
    cat1 = determine_service_category("Osiyatech_Chatbot_final/02_services/web_development/service.md")
    assert cat1 == "Web Development"

    cat2 = determine_service_category("Osiyatech_Chatbot_final/02_services/ai_ml_development/faqs.md")
    assert cat2 == "AI & Machine Learning"

    cat3 = determine_service_category("ai-agents-automation/service.md")
    assert cat3 == "AI Agents & Workflow Automation"

    cat4 = determine_service_category("Osiyatech_Chatbot_final/01_company_profile.docx")
    assert cat4 == "Company Information"


def test_retriever_context_budget_does_not_break_on_large_chunk(make_settings):
    """When a candidate exceeds remaining budget, smaller subsequent chunks should still be packed."""
    # Chunk 1: 500 chars (fits in 1000 budget, 500 left)
    # Chunk 2: 700 chars (exceeds 500 remaining budget -> skipped with continue)
    # Chunk 3: 300 chars (fits in 500 remaining budget -> packed!)
    c1 = Chunk("c1", "d1", "A" * 500, "k1", "h1", "doc1", "text", "Title 1", approval_status="approved")
    c2 = Chunk("c2", "d1", "B" * 700, "k2", "h2", "doc1", "text", "Title 2", approval_status="approved")
    c3 = Chunk("c3", "d1", "C" * 300, "k3", "h3", "doc1", "text", "Title 3", approval_status="approved")

    # Mock index
    import numpy as np
    from app.rag.index import KnowledgeIndex
    idx = KnowledgeIndex([c1, c2, c3], np.zeros((3, 512), dtype="float32"), "hashing-512")

    s = make_settings(context_char_budget=1000, retrieval_top_k=3, min_confidence=0.0)
    retriever = Retriever(idx, HashingEmbedder(), s)
    # Both c1 and c3 should be retrieved because c2 was skipped with continue rather than breaking the loop
    res = retriever.retrieve("Title")
    retrieved_ids = [rc.chunk.chunk_id for rc in res.chunks]
    assert "c1" in retrieved_ids
    assert "c3" in retrieved_ids
    assert "c2" not in retrieved_ids


def test_retriever_rejects_unapproved_and_internal_chunks(make_settings):
    c_approved = Chunk("c1", "d1", "Approved text", "k1", "h1", "doc1", "text", approval_status="approved", trust_tier="tier_1")
    c_unapproved = Chunk("c2", "d1", "Unapproved text", "k2", "h2", "doc1", "text", approval_status=None, trust_tier="tier_2")
    c_internal = Chunk("c3", "d1", "Internal text", "k3", "h3", "08_website_review_report.md", "text", approval_status="approved", trust_tier="tier_3")

    import numpy as np
    from app.rag.index import KnowledgeIndex
    idx = KnowledgeIndex([c_approved, c_unapproved, c_internal], np.zeros((3, 512), dtype="float32"), "hashing-512")

    s = make_settings(require_approved_only=True)
    retriever = Retriever(idx, HashingEmbedder(), s)
    assert retriever._allowed(0, None) is True
    assert retriever._allowed(1, None) is False
    assert retriever._allowed(2, None) is False
