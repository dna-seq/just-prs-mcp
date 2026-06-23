"""Mode gating: which tools exist in essentials vs extended."""

from __future__ import annotations

ESSENTIALS = {
    "search_scores",
    "score_info",
    "best_performance",
    "search_traits",
    "trait_info",
    "normalize_vcf",
    "download_sample_genome",
    "list_genomes",
    "compute_prs",
    "compute_prs_batch",
    "compute_prs_by_trait",
    "percentile",
    "absolute_risk",
    "assess_quality",
}

EXTENDED_ONLY = {
    "normalize_array",
    "download_scoring_file",
    "list_pgs_ids",
    "download_all_metadata",
    "bulk_download_scores",
    "prevalence_info",
    "absolute_risk_bundle",
    "push_catalog_to_hf",
    "download_reference_panel",
    "reference_score",
    "reference_score_batch",
    "pgen_read_pvar",
    "pgen_read_psam",
    "pgen_score",
}


async def _tool_names(client) -> set[str]:
    return {t.name for t in await client.list_tools()}


async def test_essentials_surface(essentials_client):
    names = await _tool_names(essentials_client)
    assert names >= ESSENTIALS
    # Extended-only tools must NOT be present in essentials mode.
    assert not (EXTENDED_ONLY & names)


async def test_extended_is_superset(essentials_client, extended_client):
    essentials = await _tool_names(essentials_client)
    extended = await _tool_names(extended_client)
    assert essentials <= extended
    assert extended >= EXTENDED_ONLY


async def test_no_auth_tool(essentials_client, extended_client):
    """The cake auth tier was dropped — no authenticate/order tools anywhere."""
    for client in (essentials_client, extended_client):
        names = await _tool_names(client)
        assert "authenticate" not in names
        assert "order_custom_cake" not in names
