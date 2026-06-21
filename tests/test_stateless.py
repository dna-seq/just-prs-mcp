"""Stateless analysis tools: pure logic, no network — safe in CI."""

from __future__ import annotations


async def test_assess_quality_high(essentials_client):
    result = await essentials_client.call_tool(
        "assess_quality", {"match_rate": 0.98, "auroc": 0.80, "percentile": 95.0}
    )
    assert result.data.quality_label
    assert result.data.summary


async def test_assess_quality_low_match(essentials_client):
    """A poor match rate should not classify as the top quality tier."""
    high = await essentials_client.call_tool("assess_quality", {"match_rate": 0.99, "auroc": 0.85})
    low = await essentials_client.call_tool("assess_quality", {"match_rate": 0.10, "auroc": 0.85})
    assert low.data.quality_label != high.data.quality_label


async def test_assess_quality_minimal_args(essentials_client):
    """auroc/percentile are optional."""
    result = await essentials_client.call_tool("assess_quality", {"match_rate": 0.9})
    assert isinstance(result.data.quality_label, str)


async def test_panels_resource(essentials_client):
    resources = {str(r.uri) for r in await essentials_client.list_resources()}
    assert "resource://prs/panels" in resources
