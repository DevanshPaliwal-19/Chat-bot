"""Phase 1 regression tests: backend output reliability, Markdown preservation, and completion checks."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.core.validator import validate_answer, SAFE_FALLBACK
from app.llm.base import LLMResult


def v(text: str, support: str = "", system: str = "x", cites: int = 5, strip: bool = False, max_chars: int = 4000):
    return validate_answer(
        text,
        supporting_text=support,
        system_prompt=system,
        valid_citations=cites,
        strip_citations=strip,
        max_chars=max_chars,
    )


# -----------------------------------------------------------------------------
# 1. Markdown Structure Preservation Tests
# -----------------------------------------------------------------------------

def test_validator_preserves_numbered_lists():
    raw = (
        "Here is the UI/UX design process:\n\n"
        "1. Discovery: Understanding goals.\n"
        "2. Research: Conducting user research.\n"
        "3. Concepting: Developing initial wireframes.\n"
        "4. Prototyping: Interactive prototypes.\n"
        "5. Testing: Usability testing.\n"
        "6. Refining: Design iterations.\n"
        "7. Handoff: Production handoff."
    )
    out = v(raw)
    assert out.issues == []
    assert out.text == raw
    assert "\n" in out.text
    assert "1. Discovery" in out.text
    assert "7. Handoff" in out.text


def test_validator_preserves_bullet_lists():
    raw = (
        "We specialize in the following technologies:\n\n"
        "* Flutter and React Native for mobile\n"
        "* React and Vue for web development\n"
        "* Python and Node.js for backend services\n"
        "* AWS and Azure for cloud infrastructure"
    )
    out = v(raw)
    assert out.issues == []
    assert out.text == raw
    assert "* Flutter" in out.text
    assert "* AWS" in out.text


def test_validator_preserves_multiple_paragraphs():
    raw = (
        "This is the introductory paragraph explaining the architecture.\n\n"
        "This is the second paragraph discussing microservices separation and containerization.\n\n"
        "This is the concluding paragraph with recommended next steps."
    )
    out = v(raw)
    assert out.issues == []
    assert out.text == raw
    paragraphs = out.text.split("\n\n")
    assert len(paragraphs) == 3


def test_validator_preserves_bold_headings():
    raw = (
        "### Assessment & Planning\n"
        "Evaluate existing architecture and database bottlenecks.\n\n"
        "### Application Decomposition\n"
        "Break monolith into independent services."
    )
    out = v(raw)
    assert out.issues == []
    assert out.text == raw
    assert "### Assessment & Planning" in out.text


def test_validator_preserves_markdown_tables():
    raw = (
        "Here is the comparison table:\n\n"
        "| Feature | Dedicated Team | Project-Based |\n"
        "|---|---|---|\n"
        "| Control | High | Milestone-based |\n"
        "| Flexibility | Full roadmap adapt | Fixed scope |"
    )
    out = v(raw)
    assert out.issues == []
    assert out.text == raw
    assert "| Feature | Dedicated Team |" in out.text


def test_validator_preserves_code_blocks_and_numbers():
    raw = (
        "You can configure the service as follows:\n\n"
        "```python\n"
        "port = 8080\n"
        "retries = 3\n"
        "```\n\n"
        "Let us know if you need assistance."
    )
    out = v(raw)
    assert out.issues == []
    assert "port = 8080" in out.text
    assert "retries = 3" in out.text


# -----------------------------------------------------------------------------
# 2. Supported & Unsupported Numerical Claims inside Structured Markdown
# -----------------------------------------------------------------------------

def test_validator_drops_unsupported_figures_inside_list_item():
    raw = (
        "1. Discovery: Understand your requirements.\n"
        "2. Architecture: Design the system. We guarantee 99% uptime.\n"
        "3. Implementation: Build the application."
    )
    out = v(raw, support="Discovery and Architecture")
    assert "unsupported_figure" in out.issues
    assert "We guarantee 99% uptime" not in out.text
    assert "1. Discovery: Understand your requirements." in out.text
    assert "2. Architecture: Design the system." in out.text
    assert "3. Implementation: Build the application." in out.text


def test_validator_keeps_supported_figures_inside_structured_text():
    support = "We work in 2-week sprint cycles with demos every 14 days."
    raw = (
        "Our Agile Methodology:\n\n"
        "* 2-week sprint cycles for iterative releases.\n"
        "* Demonstrations every 14 days to collect feedback."
    )
    out = v(raw, support=support)
    assert out.issues == []
    assert "2-week" in out.text
    assert "14 days" in out.text


# -----------------------------------------------------------------------------
# 3. Truncation and Character Limits
# -----------------------------------------------------------------------------

def test_validator_configurable_max_chars_and_boundary_trim():
    text = (
        "Sentence one is here. "
        "Sentence two is right here. "
        "Sentence three provides final thoughts."
    )
    # With a small max_chars that cuts after sentence two
    out = v(text, max_chars=55)
    assert out.is_truncated is True
    assert "truncated" in out.issues
    assert out.text.endswith(".")
    assert len(out.text) <= 55
    assert "Sentence one is here." in out.text


def test_validator_expanded_budget_does_not_truncate_normal_long_responses():
    long_response = (
        "Step 1: Conduct initial discovery and infrastructure review.\n\n"
        "Step 2: Define domain boundaries and microservices communication.\n\n"
        "Step 3: Establish database read-replicas and plan phased cutover.\n\n"
        "Step 4: Implement CI/CD deployment pipelines on AWS ECS/EKS.\n\n"
        "Step 5: Execute final validation, performance testing, and production cutover."
    )
    # Default 4000 max_chars easily accommodates full response
    out = v(long_response, max_chars=4000)
    assert out.is_truncated is False
    assert "truncated" not in out.issues
    assert len(out.text) == len(long_response)


# -----------------------------------------------------------------------------
# 4. LLM Result Termination and Completeness Metadata
# -----------------------------------------------------------------------------

def test_llm_result_records_finish_reason():
    res_normal = LLMResult(
        text="Normal answer", model="llama3.2:3b", duration_ms=10.0,
        finish_reason="stop", is_truncated=False,
    )
    assert res_normal.finish_reason == "stop"
    assert res_normal.is_truncated is False

    res_truncated = LLMResult(
        text="Cut off answer...", model="llama3.2:3b", duration_ms=10.0,
        finish_reason="length", is_truncated=True,
    )
    assert res_truncated.finish_reason == "length"
    assert res_truncated.is_truncated is True


# -----------------------------------------------------------------------------
# 5. Core Regression Scenarios (Simulated Realistic Model Outputs)
# -----------------------------------------------------------------------------

def test_scenario_1_aws_migration_complete_steps():
    raw_response = (
        "To migrate your monolithic PHP/MySQL application to AWS microservices, we follow a 5-step methodology:\n\n"
        "1. **Assessment & Domain Decomposition**: We analyze application bottlenecks and partition the monolith using domain-driven design.\n"
        "2. **API Gateway & Routing**: We set up AWS API Gateway to route traffic progressively between legacy and new services.\n"
        "3. **Microservices Containerization**: We containerize components using Docker and deploy them onto AWS ECS or EKS.\n"
        "4. **Database Decoupling**: We migrate MySQL to Amazon Aurora with read-replicas and service-specific schemas.\n"
        "5. **CI/CD & Monitoring**: We establish automated testing pipelines and CloudWatch monitoring for zero-downtime cutover."
    )
    out = v(raw_response)
    assert out.issues == []
    assert out.is_truncated is False
    assert "5. **CI/CD & Monitoring**" in out.text
    assert len(out.text.split("\n\n")) >= 2


def test_scenario_2_ui_ux_design_process_numbered_list():
    raw_response = (
        "Our UI/UX design process follows a user-centered framework:\n\n"
        "1. Discovery: Understanding business goals and user personas.\n"
        "2. Research: Market analysis and stakeholder interviews.\n"
        "3. Wireframing: Low-fidelity wireframes and user journeys.\n"
        "4. Interactive Prototyping: Clickable prototypes in Figma.\n"
        "5. Usability Testing: Validation with test users.\n"
        "6. Visual Design: High-fidelity design systems and components.\n"
        "7. Developer Handoff: Detailed design specs and asset exports."
    )
    out = v(raw_response)
    assert out.issues == []
    assert "1. Discovery:" in out.text
    assert "7. Developer Handoff:" in out.text
    # Verify that newlines between list items are intact
    lines = [l.strip() for l in out.text.split("\n") if l.strip()]
    assert len(lines) == 8


def test_scenario_3_agile_methodology_headings_and_bullets():
    raw_response = (
        "### Sprint Workflow\n\n"
        "* 2-week sprint cycles with clear deliverables.\n"
        "* Regular sprint planning and retrospective sessions.\n\n"
        "### Client Visibility\n\n"
        "* Bi-weekly sprint reviews and progress demonstrations.\n"
        "* Direct access to development staging environments and progress boards."
    )
    support = "2-week sprint cycles with bi-weekly demos."
    out = v(raw_response, support=support)
    assert out.issues == []
    assert "### Sprint Workflow" in out.text
    assert "### Client Visibility" in out.text
    assert "* 2-week sprint cycles" in out.text


def test_scenario_4_dedicated_engineering_team_comparison():
    raw_response = (
        "The difference between a Dedicated Engineering Team and Project-Based Development comes down to scope flexibility and team integration:\n\n"
        "* **Dedicated Engineering Team**: A dedicated team of engineers, architects, and QA professionals integrates directly into your workflows. You have full control over priorities, roadmap changes, and sprint rituals.\n\n"
        "* **Project-Based Development**: We work against a predefined project scope, fixed timeline, and milestone deliverables. This model is ideal for well-defined MVPs or standalone applications."
    )
    out = v(raw_response)
    assert out.issues == []
    assert not out.is_truncated
    assert "* **Dedicated Engineering Team**" in out.text
    assert "* **Project-Based Development**" in out.text


def test_scenario_5_on_premise_llm_deployment_explanation():
    raw_response = (
        "For enterprise financial transactions requiring zero cloud data leakage, we architect on-premise LLMs using the following principles:\n\n"
        "1. **Local Model Hosting**: We deploy open-weights models (such as Llama or Mistral) on air-gapped on-premise hardware.\n"
        "2. **Private Vector Search**: Embeddings and documents are stored in locally hosted vector stores without external API calls.\n"
        "3. **Role-Based Access Control**: Strict authentication gates who can query specific financial indices.\n"
        "4. **Audit Logging**: Every inference request and retrieved context is logged locally for compliance."
    )
    out = v(raw_response)
    assert out.issues == []
    assert not out.is_truncated
    assert "4. **Audit Logging**" in out.text


def test_scenario_6_short_faq_remains_concise():
    short_faq = "Yes, we build mobile apps for both iOS and Android using Flutter and React Native."
    out = v(short_faq)
    assert out.issues == []
    assert out.text == short_faq
    assert len(out.text) < 120
