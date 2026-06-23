"""Tests for the list_genomes tool."""

from __future__ import annotations

from fastmcp.client import Client

from just_prs_mcp.server import build_server
from just_prs_mcp.settings import Settings


async def test_list_genomes_empty_cache(tmp_path):
    """An empty cache dir returns zero entries and lists available samples."""
    settings = Settings(cache_dir=str(tmp_path))
    server = build_server(mode="essentials", settings=settings)
    async with Client(transport=server) as client:
        result = await client.call_tool("list_genomes", {})
        cat = result.data
        assert cat.cache_dir == str(tmp_path)
        assert cat.downloaded == []
        assert cat.normalized == []
        assert len(cat.available_samples) == 2
        names = {s["name"] for s in cat.available_samples}
        assert names == {"anton", "livia"}
        for s in cat.available_samples:
            assert s["already_downloaded"] is False
            assert s["already_normalized"] is False


async def test_list_genomes_with_downloaded_vcf(tmp_path):
    """A VCF in samples/ is reported as downloaded."""
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    vcf = samples_dir / "antonkulaga.vcf"
    vcf.write_bytes(b"fake vcf content")

    settings = Settings(cache_dir=str(tmp_path))
    server = build_server(mode="essentials", settings=settings)
    async with Client(transport=server) as client:
        result = await client.call_tool("list_genomes", {})
        cat = result.data
        assert len(cat.downloaded) == 1
        assert cat.downloaded[0].filename == "antonkulaga.vcf"
        assert cat.downloaded[0].sample_alias == "anton"
        assert cat.downloaded[0].stage == "downloaded"
        anton = next(s for s in cat.available_samples if s["name"] == "anton")
        assert anton["already_downloaded"] is True


async def test_list_genomes_with_normalized_parquet(tmp_path):
    """A Parquet in normalized/ is reported as normalized."""
    norm_dir = tmp_path / "normalized"
    norm_dir.mkdir()
    pq = norm_dir / "antonkulaga.parquet"
    pq.write_bytes(b"fake parquet")

    settings = Settings(cache_dir=str(tmp_path))
    server = build_server(mode="essentials", settings=settings)
    async with Client(transport=server) as client:
        result = await client.call_tool("list_genomes", {})
        cat = result.data
        assert len(cat.normalized) == 1
        assert cat.normalized[0].filename == "antonkulaga.parquet"
        assert cat.normalized[0].sample_alias == "anton"
        assert cat.normalized[0].stage == "normalized"
        anton = next(s for s in cat.available_samples if s["name"] == "anton")
        assert anton["already_normalized"] is True


async def test_list_genomes_unknown_vcf(tmp_path):
    """A VCF that doesn't match a known sample has sample_alias=None."""
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    vcf = samples_dir / "custom_genome.vcf.gz"
    vcf.write_bytes(b"custom")

    settings = Settings(cache_dir=str(tmp_path))
    server = build_server(mode="essentials", settings=settings)
    async with Client(transport=server) as client:
        result = await client.call_tool("list_genomes", {})
        cat = result.data
        assert len(cat.downloaded) == 1
        assert cat.downloaded[0].sample_alias is None


async def test_list_genomes_in_essentials(essentials_client):
    """list_genomes is available in essentials mode."""
    names = {t.name for t in await essentials_client.list_tools()}
    assert "list_genomes" in names
