"""Tests for the compare_genomes tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from just_prs_mcp.models import TraitPRSReport, TraitScoreRow


def _make_report(
    genome_label: str,
    trait_id: str = "EFO_0001645",
    label: str = "venous thromboembolism",
    pgs_id: str = "PGS000043",
    percentile: float = 72.0,
    match_rate: float = 0.95,
) -> TraitPRSReport:
    return TraitPRSReport(
        trait_id=trait_id,
        label=label,
        genome_build="GRCh38",
        n_requested=1,
        n_scored=1,
        n_failed=0,
        n_skipped=0,
        n_reliable=1,
        mean_match_rate=match_rate,
        n_returned=1,
        rows=[
            TraitScoreRow(
                pgs_id=pgs_id,
                status="scored",
                score=0.5,
                variants_matched=100,
                variants_total=110,
                match_rate=match_rate,
                percentile=percentile,
                percentile_method="reference_panel",
                percentile_reliable=True,
                quality_label="High",
            )
        ],
        summary="test",
        genome_label=genome_label,
    )


async def test_compare_genomes_basic(essentials_client, tmp_path):
    """Two genomes, one trait — should produce a ranked comparison."""
    r1 = _make_report("anton", percentile=72.0)
    r2 = _make_report("livia", percentile=45.0)

    p1 = tmp_path / "anton_EFO_0001645.json"
    p2 = tmp_path / "livia_EFO_0001645.json"
    p1.write_text(r1.model_dump_json(indent=2))
    p2.write_text(r2.model_dump_json(indent=2))

    result = await essentials_client.call_tool(
        "compare_genomes",
        {"result_paths": [str(p1), str(p2)]},
    )

    comp = result.data
    assert comp.n_traits == 1
    assert comp.genome_labels == ["anton", "livia"]
    assert len(comp.traits) == 1

    tc = comp.traits[0]
    assert tc.trait_id == "EFO_0001645"
    assert tc.rankings[0].genome_label == "anton"
    assert tc.rankings[0].rank == 1
    assert tc.rankings[1].genome_label == "livia"
    assert tc.rankings[1].rank == 2
    assert tc.percentile_spread == pytest.approx(27.0)
    assert tc.model_consistency == "consistent"


async def test_compare_genomes_label_override(essentials_client, tmp_path):
    """genome_labels parameter overrides the labels in the saved reports."""
    r1 = _make_report("file1", percentile=60.0)
    r2 = _make_report("file2", percentile=80.0)

    p1 = tmp_path / "a.json"
    p2 = tmp_path / "b.json"
    p1.write_text(r1.model_dump_json(indent=2))
    p2.write_text(r2.model_dump_json(indent=2))

    result = await essentials_client.call_tool(
        "compare_genomes",
        {
            "result_paths": [str(p1), str(p2)],
            "genome_labels": ["Person A", "Person B"],
        },
    )

    comp = result.data
    assert comp.genome_labels == ["Person A", "Person B"]
    assert comp.traits[0].rankings[0].genome_label == "Person B"


async def test_compare_genomes_multiple_traits(essentials_client, tmp_path):
    """Four files covering two traits — should produce two TraitComparisons."""
    for genome, dvt_pct, iq_pct in [("anton", 72.0, 65.0), ("livia", 45.0, 78.0)]:
        r_dvt = _make_report(genome, trait_id="EFO_0001645", label="DVT", percentile=dvt_pct)
        r_iq = _make_report(
            genome, trait_id="EFO_0004337", label="intelligence", percentile=iq_pct,
            pgs_id="PGS000777",
        )
        (tmp_path / f"{genome}_dvt.json").write_text(r_dvt.model_dump_json(indent=2))
        (tmp_path / f"{genome}_iq.json").write_text(r_iq.model_dump_json(indent=2))

    paths = [
        str(tmp_path / "anton_dvt.json"),
        str(tmp_path / "livia_dvt.json"),
        str(tmp_path / "anton_iq.json"),
        str(tmp_path / "livia_iq.json"),
    ]
    result = await essentials_client.call_tool(
        "compare_genomes", {"result_paths": paths},
    )

    comp = result.data
    assert comp.n_traits == 2
    assert len(comp.most_divergent_traits) == 2
    assert comp.most_divergent_traits[0] == "DVT"


async def test_compare_genomes_rejects_single_path(essentials_client, tmp_path):
    """Should error with fewer than 2 paths."""
    r = _make_report("solo")
    p = tmp_path / "solo.json"
    p.write_text(r.model_dump_json(indent=2))

    with pytest.raises(Exception, match="at least 2"):
        await essentials_client.call_tool(
            "compare_genomes", {"result_paths": [str(p)]},
        )
