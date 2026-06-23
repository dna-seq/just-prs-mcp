"""Agentic integration tests — an LLM agent drives the PRS tools end-to-end.

Modelled on the ensembl-mcp agent tests: each test fires a natural-language
query through the Agno agent and asserts expected keywords in the response.

Requirements:
- ``uv sync --dev`` (installs agno + model backend).
- An API key in the environment (``PRS_MCP_AGENT_API_KEY``, ``GEMINI_API_KEY``,
  or ``GOOGLE_API_KEY``). Tests skip when no key is found.
- Network access to the PGS Catalog REST API and (for the heavy tests) Zenodo.

Run:
    uv run pytest tests/test_agent.py -m integration          # catalog-only tests
    uv run pytest tests/test_agent.py -m "integration and slow"  # full comparison
"""

from __future__ import annotations

import pytest

from just_prs_mcp.agent import get_agent_api_key, run_agent_query
from just_prs_mcp.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def agent_settings() -> Settings:
    return Settings()


def _require_agent_key(settings: Settings) -> None:
    if get_agent_api_key(settings) is None:
        pytest.skip(
            "Set PRS_MCP_AGENT_API_KEY, GEMINI_API_KEY, or GOOGLE_API_KEY "
            "to run agent integration tests."
        )


# ------------------------------------------------------------------
# Lightweight tests (catalog/REST only — no genome files needed)
# ------------------------------------------------------------------


def test_agent_searches_traits(agent_settings: Settings) -> None:
    """Agent can discover trait IDs from a natural-language query."""
    _require_agent_key(agent_settings)

    response = run_agent_query(
        "What PGS Catalog traits are available for intelligence? "
        "List the trait IDs and their labels.",
        agent_settings,
    )

    assert "intelligence" in response.lower() or "cognitive" in response.lower()


def test_agent_looks_up_score(agent_settings: Settings) -> None:
    """Agent can retrieve metadata for a specific PGS ID."""
    _require_agent_key(agent_settings)

    response = run_agent_query(
        "What trait does PGS000001 measure? Give me its name and how many "
        "variants it has.",
        agent_settings,
    )

    assert "PGS000001" in response


def test_agent_lists_available_genomes(agent_settings: Settings) -> None:
    """Agent can report which sample genomes are available."""
    _require_agent_key(agent_settings)

    response = run_agent_query(
        "What sample genomes are available for PRS analysis? "
        "List their names and whether they are already downloaded.",
        agent_settings,
    )

    lower = response.lower()
    assert "anton" in lower or "livia" in lower


# ------------------------------------------------------------------
# Full end-to-end comparison (requires genome downloads + network)
# ------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.network
def test_agent_compares_intelligence_and_thrombosis(agent_settings: Settings) -> None:
    """Agent compares PRS between two individuals for multiple traits.

    This is the flagship agentic test: the agent downloads genomes (if not
    cached), normalizes them, finds relevant PGS models, computes scores,
    and compares Anton vs Livia on intelligence and thrombosis.

    Requires network access and may take 10+ minutes on first run (genome
    downloads are ~800 MB total). Subsequent runs reuse cached files.
    """
    _require_agent_key(agent_settings)

    response = run_agent_query(
        "Compare who has higher genetic predisposition for intelligence and "
        "who has lower risk of deep vein thrombosis (DVT) between Anton and "
        "Livia. Download and normalize their genomes if needed. For each "
        "trait, use the best available PGS model (check performance/AUROC). "
        "Report the percentiles and explain who 'wins' for each trait.",
        agent_settings,
    )

    lower = response.lower()
    assert "anton" in lower, "Response should mention Anton"
    assert "livia" in lower, "Response should mention Livia"
    assert (
        "intelligence" in lower or "cognitive" in lower
    ), "Response should discuss intelligence"
    assert (
        "thrombosis" in lower or "dvt" in lower or "venous" in lower
    ), "Response should discuss thrombosis/DVT"
