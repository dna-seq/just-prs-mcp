"""Regression coverage for MCP wrapper fixes from dogfooding."""

from __future__ import annotations

import pytest
from just_prs.models import TraitInfo


class FakeRestClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def search_traits(self, term: str, limit: int = 25) -> list[TraitInfo]:
        self.calls.append(term)
        if term == "diabetes mellitus type 2":
            return []
        if term in {"diabetes mellitus, type 2", "diabetes"}:
            return [_trait()]
        return []

    def get_trait(self, efo_id: str) -> TraitInfo:
        return _trait(trait_id=efo_id)


class FakeCatalog:
    def percentile_full(
        self,
        prs_score: float,
        pgs_id: str,
        ancestry: str = "EUR",
        mean: float = 0.0,
        std: float | None = None,
        panel: str = "1000g",
        weight_mass_coverage: float | None = None,
    ):
        from just_prs.models import PercentileResult

        # Mirror the library: a low C_wt flips reliability and attaches a caveat.
        reliable = True
        caveat = ""
        if weight_mass_coverage is not None and weight_mass_coverage < 0.20:
            reliable = False
            caveat = (
                f"Only {weight_mass_coverage * 100:.0f}% of this score's effect-weight "
                "mass was matched in this genome (C_wt)."
            )
        return PercentileResult(
            percentile=0.0,
            method="reference_panel",
            z_score=-3.5,
            reference_mean=0.0,
            reference_std=1.0,
            reliable=reliable,
            caveat=caveat,
            ancestry=ancestry.upper(),
            panel=panel,
        )


def _trait(trait_id: str = "MONDO_0005148") -> TraitInfo:
    return TraitInfo(
        id=trait_id,
        label="type 2 diabetes mellitus",
        description="A type 2 diabetes trait.",
        url="https://example.test/trait",
        trait_categories=["Disease"],
        trait_synonyms=["diabetes mellitus, type 2"],
        associated_pgs_ids=["PGS000001", "PGS000002"],
        child_associated_pgs_ids=["PGS000003"],
    )


async def test_trait_info_accepts_trait_id_and_efo_id(essentials_client, monkeypatch):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_rest_client", FakeRestClient)

    by_trait_id = await essentials_client.call_tool("trait_info", {"trait_id": "MONDO_0005148"})
    by_efo_id = await essentials_client.call_tool("trait_info", {"efo_id": "EFO_0001645"})

    assert by_trait_id.data.id == "MONDO_0005148"
    assert by_efo_id.data.id == "EFO_0001645"


async def test_search_traits_defaults_to_counts_and_retries_empty_query(
    essentials_client,
    monkeypatch,
):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_rest_client", FakeRestClient)

    result = await essentials_client.call_tool(
        "search_traits", {"term": "diabetes mellitus type 2"}
    )

    assert result.data[0].id == "MONDO_0005148"
    assert result.data[0].n_associated == 2
    assert result.data[0].n_child_associated == 1
    assert not hasattr(result.data[0], "associated_pgs_ids")


async def test_search_traits_can_return_full_pgs_id_arrays(essentials_client, monkeypatch):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_rest_client", FakeRestClient)

    result = await essentials_client.call_tool(
        "search_traits", {"term": "diabetes", "include_pgs_ids": True}
    )

    assert result.data[0].associated_pgs_ids == ["PGS000001", "PGS000002"]
    assert result.data[0].child_associated_pgs_ids == ["PGS000003"]


async def test_percentile_low_coverage_is_unreliable(essentials_client, monkeypatch):
    """F9/F20: a low weight-mass coverage (C_wt) flags the percentile unreliable,
    and the true z-score / reference stats are surfaced (F12)."""
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_catalog", lambda settings: FakeCatalog())

    result = await essentials_client.call_tool(
        "percentile",
        {"prs_score": 14.944, "pgs_id": "PGS000014", "weight_mass_coverage": 0.15},
    )

    assert result.data.percentile == 0.0
    assert result.data.reliable is False
    assert "C_wt" in result.data.caveat
    assert result.data.z_score == -3.5
    assert result.data.reference_std == 1.0
    # F19: the reference-panel ancestry actually used is surfaced.
    assert result.data.reference_panel_ancestry == "EUR"
    assert result.data.reference_panel == "1000g"


class FakeReportRest:
    """Rest client whose trait carries three associated PGS IDs."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get_trait(self, trait_id: str) -> TraitInfo:
        return TraitInfo(
            id=trait_id,
            label="type 2 diabetes mellitus",
            description=None,
            url="https://example.test/trait",
            trait_categories=[],
            trait_synonyms=[],
            associated_pgs_ids=["PGS000001", "PGS000002", "PGS000003"],
            child_associated_pgs_ids=[],
        )


def _batch_result(results):
    """Wrap PRSResults in a PRSBatchResult, mirroring just-prs's batch return shape."""
    from just_prs.models import PRSBatchResult

    return PRSBatchResult(
        results=results,
        outcomes=[],
        n_total=len(results),
        n_ok=len(results),
        n_failed=0,
        failed_ids=[],
    )


class FakeBatchCatalog:
    """Catalog whose batch scoring returns deterministic, varied match rates."""

    _RATES = {"PGS000001": 0.95, "PGS000002": 0.40, "PGS000003": 0.70}

    def compute_prs_batch(
        self, vcf_path, pgs_ids, genome_build, genotypes_lf=None, attach_performance=False
    ):
        from just_prs.models import PRSResult

        return _batch_result(
            [
                PRSResult(
                    pgs_id=pgs_id,
                    score=1.0,
                    variants_matched=int(1000 * self._RATES[pgs_id]),
                    variants_total=1000,
                    match_rate=self._RATES[pgs_id],
                )
                for pgs_id in pgs_ids
            ]
        )


async def test_compute_prs_by_trait_top_n_trims_and_ranks(essentials_client, monkeypatch, tmp_path):
    """F14: top_n returns the best-covered rows, accounts for omissions, aggregates all."""
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_rest_client", FakeReportRest)
    monkeypatch.setattr(mcp_client, "make_catalog", lambda settings: FakeBatchCatalog())

    vcf = tmp_path / "sample.vcf"
    vcf.write_text("##fileformat=VCFv4.2\n")

    result = await essentials_client.call_tool(
        "compute_prs_by_trait",
        {"trait_id": "MONDO_0005148", "vcf_path": str(vcf), "top_n": 2},
    )
    report = result.data

    assert report.n_scored == 3
    assert report.n_returned == 2
    assert report.n_omitted == 1
    # Ranked best-coverage first; the 0.40 score is trimmed, not an arbitrary one.
    assert [row.pgs_id for row in report.rows] == ["PGS000001", "PGS000003"]
    assert report.mean_match_rate == pytest.approx((0.95 + 0.40 + 0.70) / 3)


class FakePerfCatalog:
    """Batch catalog that attaches PerformanceInfo when asked; no best_performance method,
    so any per-score round-trip fallback would raise (and surface as a regression)."""

    def compute_prs_batch(
        self, vcf_path, pgs_ids, genome_build, genotypes_lf=None, attach_performance=False
    ):
        from just_prs.models import EffectSizeInfo, PerformanceInfo, PRSResult

        out = []
        for pgs_id in pgs_ids:
            perf = None
            if attach_performance:
                perf = PerformanceInfo(
                    ppm_id="PPM1",
                    effect_sizes=[
                        EffectSizeInfo(name_short="OR", estimate=1.55, ci_lower=1.50, ci_upper=1.60)
                    ],
                    class_acc=[EffectSizeInfo(name_short="AUROC", estimate=0.78)],
                    sample_number=1000,
                    ancestry_broad="European",
                )
            out.append(
                PRSResult(
                    pgs_id=pgs_id,
                    score=1.0,
                    variants_matched=900,
                    variants_total=1000,
                    match_rate=0.9,
                    weight_mass_coverage=0.85,
                    performance=perf,
                    detected_genome_build="GRCh38",
                )
            )
        return _batch_result(out)

    def percentile_full(
        self,
        prs_score,
        pgs_id,
        ancestry="EUR",
        mean=0.0,
        std=None,
        panel="1000g",
        weight_mass_coverage=None,
    ):
        from just_prs.models import PercentileResult

        return PercentileResult(
            percentile=82.0,
            method="reference_panel",
            z_score=0.9,
            reference_mean=0.0,
            reference_std=1.0,
            reliable=True,
            caveat="",
            ancestry=ancestry.upper(),
            panel=panel,
        )


async def test_compute_prs_by_trait_attaches_performance(essentials_client, monkeypatch, tmp_path):
    """F11: interpret=True reads the batch-attached performance (auroc + effect size) and
    C_wt (F9/F20) without a per-score best_performance round-trip; the report surfaces the
    detected genome build (F4) and rows carry the reference-panel ancestry (F19)."""
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_rest_client", FakeReportRest)
    monkeypatch.setattr(mcp_client, "make_catalog", lambda settings: FakePerfCatalog())

    vcf = tmp_path / "sample.vcf"
    vcf.write_text("##fileformat=VCFv4.2\n")

    result = await essentials_client.call_tool(
        "compute_prs_by_trait",
        {"trait_id": "MONDO_0005148", "vcf_path": str(vcf), "interpret": True},
    )
    report = result.data
    rows = report.rows

    assert rows and all(r.auroc_estimate == 0.78 for r in rows)
    assert all(r.effect_size == "OR=1.55 [1.50-1.60]" for r in rows)
    assert all(r.weight_mass_coverage == 0.85 for r in rows)
    assert all(r.percentile == 82.0 and r.percentile_reliable for r in rows)
    # F19: reference-panel ancestry surfaced per row.
    assert all(r.reference_panel_ancestry == "EUR" for r in rows)
    # F4: detected build surfaced on the report, no mismatch when it matches the scoring build.
    assert report.detected_genome_build == "GRCh38"
    assert report.build_mismatch is False


async def test_compute_prs_genotypes_path_attaches_performance(
    essentials_client, monkeypatch, tmp_path
):
    """F23: the single-score genotypes_path branch now routes through PRSCatalog.compute_prs
    (genotypes_lf + attach_performance) — no low-level free function."""
    from just_prs_mcp import client as mcp_client

    captured: dict = {}

    class FakeSingleCatalog:
        def compute_prs(
            self,
            vcf_path,
            pgs_id,
            genome_build="GRCh38",
            attach_performance=False,
            genotypes_lf=None,
        ):
            from just_prs.models import PRSResult

            captured["genotypes_lf_is_set"] = genotypes_lf is not None
            captured["attach_performance"] = attach_performance
            return PRSResult(
                pgs_id=pgs_id,
                score=2.0,
                variants_matched=800,
                variants_total=1000,
                match_rate=0.8,
                weight_mass_coverage=0.7,
            )

    monkeypatch.setattr(mcp_client, "make_catalog", lambda settings: FakeSingleCatalog())

    vcf = tmp_path / "sample.vcf"
    vcf.write_text("##fileformat=VCFv4.2\n")
    parquet = tmp_path / "geno.parquet"
    parquet.write_bytes(b"PAR1")  # only needs to exist; scan is never executed by the fake

    result = await essentials_client.call_tool(
        "compute_prs",
        {
            "vcf_path": str(vcf),
            "pgs_id": "PGS000014",
            "genotypes_path": str(parquet),
            "attach_performance": True,
        },
    )

    assert result.data.pgs_id == "PGS000014"
    assert captured == {"genotypes_lf_is_set": True, "attach_performance": True}


class FakeRiskFromScoreCatalog:
    """Records how absolute_risk_bundle routed: raw score vs z-score."""

    def __init__(self) -> None:
        self.from_score_args: dict | None = None

    def absolute_risk_from_score(
        self, pgs_id, score, ancestry="EUR", sex=None, weight_mass_coverage=None, panel="1000g"
    ):
        from just_prs.models import AbsoluteRiskBundle

        self.from_score_args = {
            "pgs_id": pgs_id,
            "score": score,
            "ancestry": ancestry,
            "weight_mass_coverage": weight_mass_coverage,
        }
        return AbsoluteRiskBundle(agreement="single")


async def test_absolute_risk_bundle_from_raw_score(extended_client, monkeypatch):
    """F12: a raw score routes through absolute_risk_from_score (true-z chain), no
    caller-supplied z-score needed."""
    from just_prs_mcp import client as mcp_client

    fake = FakeRiskFromScoreCatalog()
    monkeypatch.setattr(mcp_client, "make_catalog", lambda settings: fake)

    result = await extended_client.call_tool(
        "absolute_risk_bundle",
        {"pgs_id": "PGS000014", "score": 14.9, "weight_mass_coverage": 0.85},
    )

    assert result.data.agreement == "single"
    assert fake.from_score_args == {
        "pgs_id": "PGS000014",
        "score": 14.9,
        "ancestry": "EUR",
        "weight_mass_coverage": 0.85,
    }


async def test_absolute_risk_bundle_requires_score_or_z(extended_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await extended_client.call_tool("absolute_risk_bundle", {"pgs_id": "PGS000014"})


async def test_download_sample_genome_unknown_sample_is_recoverable(essentials_client):
    """Bad sample alias returns an OpResult, never a protocol error, with no network."""
    result = await essentials_client.call_tool(
        "download_sample_genome", {"sample": "not-a-real-person"}
    )

    assert result.data.success is False
    assert "Unknown sample" in result.data.message


# --- F25: download_sample_genome is idempotent (no re-download when cached) ---

_FAKE_VCF_BYTES = b"##fileformat=VCFv4.2\n" + b"x" * 200


class _FakeStream:
    """Async context manager mimicking httpx's streaming response."""

    def __init__(self, counter: dict) -> None:
        self._counter = counter

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    @property
    def headers(self) -> dict:
        return {"content-length": str(len(_FAKE_VCF_BYTES))}

    async def aiter_bytes(self, chunk_size: int = 1 << 20):
        self._counter["downloads"] += 1
        yield _FAKE_VCF_BYTES


class _FakeMetaResp:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "files": [
                {
                    "key": "antonkulaga.vcf",
                    "size": len(_FAKE_VCF_BYTES),
                    "links": {"content": "https://example.test/antonkulaga.vcf"},
                }
            ]
        }


def _fake_async_client_factory(counter: dict):
    class _FakeAsyncClient:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def get(self, url: str):
            return _FakeMetaResp()

        def stream(self, method: str, url: str):
            return _FakeStream(counter)

    return _FakeAsyncClient


async def test_download_sample_genome_idempotent(tmp_path, monkeypatch):
    """A second call reuses the cached VCF instead of re-streaming it (F25)."""
    import httpx
    from fastmcp.client import Client

    from just_prs_mcp.server import build_server
    from just_prs_mcp.settings import Settings

    counter = {"downloads": 0}
    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client_factory(counter))

    settings = Settings(cache_dir=str(tmp_path))
    server = build_server(mode="essentials", settings=settings)
    # auto_normalize defaults to True; pin it off here to isolate download idempotency
    # (the fake VCF bytes aren't a real VCF, so normalization isn't under test).
    base = {"sample": "anton", "auto_normalize": False}
    async with Client(transport=server) as client:
        first = (await client.call_tool("download_sample_genome", base)).data
        assert first.success is True
        assert first.data["reused_cache"] is False
        assert first.data["downloaded_bytes"] == len(_FAKE_VCF_BYTES)
        assert counter["downloads"] == 1

        second = (await client.call_tool("download_sample_genome", base)).data
        assert second.success is True
        assert second.data["reused_cache"] is True
        assert second.data["downloaded_bytes"] == 0
        assert second.data["bytes"] == len(_FAKE_VCF_BYTES)
        # No second stream — the big download was skipped.
        assert counter["downloads"] == 1

        forced = (
            await client.call_tool(
                "download_sample_genome",
                {**base, "force": True},
            )
        ).data
        assert forced.data["reused_cache"] is False
        assert counter["downloads"] == 2


async def test_download_sample_genome_auto_normalizes_by_default(tmp_path, monkeypatch):
    """A bare download normalizes in the same call (default auto_normalize=True)."""
    import httpx
    import just_prs.normalize as jn
    from fastmcp.client import Client

    from just_prs_mcp.server import build_server
    from just_prs_mcp.settings import Settings
    from just_prs_mcp.tools import compute as compute_mod

    counter = {"downloads": 0}
    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client_factory(counter))

    def _fake_normalize(src, out, config=None):
        from pathlib import Path

        Path(out).write_bytes(b"normalized")
        return Path(out)

    monkeypatch.setattr(jn, "normalize_vcf", _fake_normalize)
    monkeypatch.setattr(compute_mod, "_count_rows", lambda p: 7)

    settings = Settings(cache_dir=str(tmp_path))
    server = build_server(mode="essentials", settings=settings)
    async with Client(transport=server) as client:
        # No auto_normalize passed -> default True -> one-call compute-ready Parquet.
        res = (await client.call_tool("download_sample_genome", {"sample": "anton"})).data
        assert res.success is True
        assert res.data["normalized_path"].endswith("antonkulaga.parquet")
        assert res.data["n_variants"] == 7


# --- F28: normalize_vcf is idempotent (reuses an existing Parquet) ---


async def test_normalize_vcf_idempotent(tmp_path, monkeypatch):
    """An existing Parquet is reused; force and custom filters re-normalize (F28)."""
    import just_prs.normalize as jn
    from fastmcp.client import Client

    from just_prs_mcp.server import build_server
    from just_prs_mcp.settings import Settings
    from just_prs_mcp.tools import compute as compute_mod

    samples = tmp_path / "samples"
    samples.mkdir()
    vcf = samples / "antonkulaga.vcf"
    vcf.write_bytes(b"##fileformat=VCFv4.2\n")
    norm_dir = tmp_path / "normalized"
    norm_dir.mkdir()
    (norm_dir / "antonkulaga.parquet").write_bytes(b"cached parquet")

    calls = {"normalize": 0}

    def _fake_normalize(src, out, config=None):
        calls["normalize"] += 1
        from pathlib import Path

        Path(out).write_bytes(b"fresh parquet")
        return Path(out)

    monkeypatch.setattr(jn, "normalize_vcf", _fake_normalize)
    monkeypatch.setattr(compute_mod, "_count_rows", lambda p: 42)

    settings = Settings(cache_dir=str(tmp_path))
    server = build_server(mode="essentials", settings=settings)
    async with Client(transport=server) as client:
        # Cache hit — no re-normalization.
        first = (await client.call_tool("normalize_vcf", {"vcf_path": str(vcf)})).data
        assert first.reused_cache is True
        assert first.n_variants == 42
        assert calls["normalize"] == 0

        # force=True re-normalizes.
        forced = (
            await client.call_tool("normalize_vcf", {"vcf_path": str(vcf), "force": True})
        ).data
        assert forced.reused_cache is False
        assert calls["normalize"] == 1

        # Custom filters bypass the cache (cached Parquet may not reflect them).
        filtered = (
            await client.call_tool("normalize_vcf", {"vcf_path": str(vcf), "min_depth": 10})
        ).data
        assert filtered.reused_cache is False
        assert calls["normalize"] == 2


class FakePrevalenceCatalog:
    """Catalog exposing a tiny in-memory prevalence table + score->EFO mapping."""

    def score_info_row(self, pgs_id: str) -> dict:
        return {"pgs_id": pgs_id, "trait_efo_id": "EFO_0001360"}

    def prevalence_table(self):
        import polars as pl

        return pl.LazyFrame(
            {
                "efo_id": ["EFO_0001360", "EFO_9999"],
                "trait_label": ["type 2 diabetes mellitus", "other"],
                "prevalence": [0.09, 0.01],
                "prevalence_type": ["lifetime", "point"],
                "source": ["seed", "pgs_eval"],
                "confidence": ["high", "low"],
                "xref_mondo": ["MONDO_0005148", None],
            }
        )


async def test_prevalence_info_by_pgs_id_surfaces_the_prior(extended_client, monkeypatch):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_catalog", lambda settings: FakePrevalenceCatalog())

    result = await extended_client.call_tool("prevalence_info", {"pgs_id": "PGS000014"})

    assert result.data.resolved_efo_ids == ["EFO_0001360"]
    assert result.data.n_matches == 1
    assert result.data.rows[0].prevalence == 0.09
    assert result.data.rows[0].confidence == "high"


async def test_prevalence_info_by_mondo_trait_id(extended_client, monkeypatch):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_catalog", lambda settings: FakePrevalenceCatalog())

    result = await extended_client.call_tool("prevalence_info", {"trait_id": "MONDO_0005148"})

    assert result.data.n_matches == 1
    assert result.data.rows[0].xref_mondo == "MONDO_0005148"


async def test_prevalence_info_requires_an_id(extended_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await extended_client.call_tool("prevalence_info", {})


class FakeBundleCatalog:
    def absolute_risk_bundle(self, pgs_id: str, z_score: float, sex=None):
        from just_prs.models import AbsoluteRiskBundle, AbsoluteRiskEstimate

        est = AbsoluteRiskEstimate(
            absolute_risk=0.14,
            population_prevalence=0.09,
            risk_ratio=1.55,
            method="or_per_sd",
            method_label="OR per SD",
            confidence="moderate",
            prevalence_source="seed",
            prevalence_type="lifetime",
        )
        return AbsoluteRiskBundle(
            estimates=[est],
            best_estimate=est,
            agreement="single",
            heritability_status="unavailable",
            heritability_detail="no h2 row",
            heritability_trait_ids=[],
        )


async def test_absolute_risk_bundle_returns_all_estimates(extended_client, monkeypatch):
    from just_prs_mcp import client as mcp_client

    monkeypatch.setattr(mcp_client, "make_catalog", lambda settings: FakeBundleCatalog())

    result = await extended_client.call_tool(
        "absolute_risk_bundle", {"pgs_id": "PGS000014", "z_score": 1.2}
    )

    assert len(result.data.estimates) == 1
    assert result.data.best_estimate.population_prevalence == 0.09
    assert result.data.estimates[0].method == "or_per_sd"


def test_zenodo_helpers_resolve_samples_and_pick_vcf():
    from just_prs_mcp.tools.compute import _pick_vcf_file, _zenodo_api_url

    anton_url, anton_label = _zenodo_api_url("anton", None)
    assert anton_url.endswith("/records/18370498")
    assert "Anton" in anton_label

    by_url, _ = _zenodo_api_url(None, "https://zenodo.org/records/19487816/")
    assert by_url.endswith("/records/19487816")

    files = [
        {"key": "readme.txt", "size": 10, "links": {"self": "u1"}},
        {"key": "small.vcf.gz", "size": 100, "links": {"self": "u2"}},
        {"key": "genome.vcf.gz", "size": 9000, "links": {"content": "u3"}},
    ]
    largest = _pick_vcf_file(files, None)
    assert largest is not None and largest["key"] == "genome.vcf.gz"
    named = _pick_vcf_file(files, "small.vcf.gz")
    assert named is not None and named["key"] == "small.vcf.gz"
    assert _pick_vcf_file([{"key": "x.txt", "size": 1, "links": {}}], None) is None
