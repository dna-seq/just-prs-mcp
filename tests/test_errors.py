"""Error handling: bad inputs surface cleanly, never crash the server."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError


async def test_compute_prs_missing_vcf(essentials_client):
    with pytest.raises(ToolError):
        await essentials_client.call_tool(
            "compute_prs", {"vcf_path": "/no/such/file.vcf.gz", "pgs_id": "PGS000001"}
        )


async def test_compute_prs_missing_genotypes(essentials_client):
    with pytest.raises(ToolError):
        await essentials_client.call_tool(
            "compute_prs",
            {
                "vcf_path": "/no/such/file.vcf.gz",
                "pgs_id": "PGS000001",
                "genotypes_path": "/no/such/genotypes.parquet",
            },
        )


async def test_normalize_vcf_missing_file(essentials_client):
    with pytest.raises(ToolError):
        await essentials_client.call_tool("normalize_vcf", {"vcf_path": "/no/such/file.vcf.gz"})


async def test_pgen_read_pvar_missing_file(extended_client):
    with pytest.raises(ToolError):
        await extended_client.call_tool("pgen_read_pvar", {"pvar_path": "/no/such/file.pvar.zst"})


async def test_push_to_hf_without_token(extended_client, monkeypatch):
    """No HF token configured -> friendly OpResult, not an exception (offline)."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    result = await extended_client.call_tool("push_catalog_to_hf", {})
    assert result.data.success is False
    assert "token" in result.data.message.lower()
