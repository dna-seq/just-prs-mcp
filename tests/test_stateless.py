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


async def test_genomes_resource_listed(essentials_client):
    resources = {str(r.uri) for r in await essentials_client.list_resources()}
    assert "resource://prs/genomes" in resources


async def test_genomes_resource_mirrors_list_genomes(tmp_path):
    """The genomes resource returns the same inventory (JSON) as list_genomes."""
    import json

    from fastmcp.client import Client

    from just_prs_mcp.server import build_server
    from just_prs_mcp.settings import Settings

    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "antonkulaga.vcf").write_bytes(b"fake vcf content")

    settings = Settings(cache_dir=str(tmp_path))
    server = build_server(mode="essentials", settings=settings)
    async with Client(transport=server) as client:
        tool_cat = (await client.call_tool("list_genomes", {})).data
        res = await client.read_resource("resource://prs/genomes")
        payload = json.loads(res[0].text)

    assert payload["cache_dir"] == str(tmp_path)
    assert [e["path"] for e in payload["downloaded"]] == [e.path for e in tool_cat.downloaded]
    assert payload["downloaded"][0]["filename"] == "antonkulaga.vcf"
    assert payload["downloaded"][0]["sample_alias"] == "anton"
