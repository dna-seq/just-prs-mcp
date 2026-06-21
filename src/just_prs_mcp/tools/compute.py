"""ESSENTIALS — PRS computation and analysis tools.

The core workflow: normalize a VCF to Parquet (a long-running background task),
compute a polygenic score against it, then interpret the result (percentile,
absolute risk, quality). Computation tools take **local file paths** on the
server's filesystem (VCF / normalized Parquet); the analysis tools
(``percentile``, ``absolute_risk``, ``assess_quality``) need no files.

Bad/missing input paths raise ``ToolError`` (the template's sanctioned channel
for malformed input); everything else returns a typed model.
"""

from __future__ import annotations

from pathlib import Path

from anyio.to_thread import run_sync
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from just_prs_mcp import client
from just_prs_mcp.logging_setup import get_logger
from just_prs_mcp.models import (
    AbsoluteRisk,
    NormalizeResult,
    PercentileResult,
    PerformanceSummary,
    PRSResult,
    QualityAssessment,
    TraitPRSReport,
    TraitScoreRow,
)
from just_prs_mcp.settings import Settings

log = get_logger()


def _require_file(path: str, kind: str) -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise ToolError(f"{kind} not found: {path}")
    return p


def _count_rows(parquet_path: Path) -> int:
    import polars as pl

    return int(pl.scan_parquet(parquet_path).select(pl.len()).collect().item())


def _percentile_result(
    settings: Settings,
    prs_score: float,
    pgs_id: str,
    superpopulation: str,
    panel: str | None,
    match_rate: float | None = None,
) -> PercentileResult:
    pct, method = client.make_catalog(settings).percentile(
        prs_score=prs_score,
        pgs_id=pgs_id,
        ancestry=superpopulation,
        panel=client.panel(settings, panel),
    )
    reliable = True
    caveat = None
    if match_rate is not None and match_rate < 0.9:
        reliable = False
        caveat = (
            f"Match rate is {match_rate:.1%}; percentile may be a coverage artifact. "
            "Confirm genome build and missing/ref-call handling before interpreting."
        )
    elif pct in (0, 100):
        reliable = False
        caveat = (
            "Extreme percentile returned. Interpret cautiously unless the scoring variant "
            "match rate is high and the genome build is confirmed."
        )
    return PercentileResult(
        pgs_id=pgs_id,
        prs_score=prs_score,
        percentile=pct,
        method=method,
        ancestry=superpopulation,
        reliable=reliable,
        caveat=caveat,
    )


def _best_performance_summary(settings: Settings, pgs_id: str) -> PerformanceSummary:
    from just_prs.quality import format_classification, format_effect_size

    df = client.make_catalog(settings).best_performance(pgs_id=pgs_id).collect()
    if df.height == 0:
        return PerformanceSummary(pgs_id=pgs_id, found=False)
    row = df.row(0, named=True)
    effect_size = format_effect_size(row) or None
    return PerformanceSummary(
        pgs_id=pgs_id,
        found=True,
        n_individuals=row.get("n_individuals"),
        ancestry_broad=row.get("ancestry_broad"),
        or_estimate=row.get("or_estimate"),
        hr_estimate=row.get("hr_estimate"),
        beta_estimate=row.get("beta_estimate"),
        auroc_estimate=row.get("auroc_estimate"),
        cindex_estimate=row.get("cindex_estimate"),
        effect_size=effect_size or "",
        classification=format_classification(row),
    )


def _trait_score_row(
    settings: Settings,
    result: PRSResult,
    interpret: bool,
    superpopulation: str,
    panel: str | None,
) -> TraitScoreRow:
    percentile_result = None
    quality = None
    performance = None
    if interpret:
        try:
            percentile_result = _percentile_result(
                settings=settings,
                prs_score=result.score,
                pgs_id=result.pgs_id,
                superpopulation=superpopulation,
                panel=panel,
                match_rate=result.match_rate,
            )
        except Exception:  # noqa: BLE001 - interpretation is best-effort in aggregate reports
            percentile_result = None
        try:
            performance = _best_performance_summary(settings, result.pgs_id)
        except Exception:  # noqa: BLE001 - interpretation is best-effort in aggregate reports
            performance = None
        quality = _quality_assessment(
            match_rate=result.match_rate,
            auroc=performance.auroc_estimate if performance else None,
            percentile=percentile_result.percentile if percentile_result else result.percentile,
        )

    return TraitScoreRow(
        pgs_id=result.pgs_id,
        status="scored",
        score=result.score,
        variants_matched=result.variants_matched,
        variants_total=result.variants_total,
        match_rate=result.match_rate,
        percentile=percentile_result.percentile if percentile_result else result.percentile,
        percentile_method=(
            percentile_result.method if percentile_result else result.percentile_method
        ),
        percentile_reliable=percentile_result.reliable if percentile_result else None,
        percentile_caveat=percentile_result.caveat if percentile_result else None,
        quality_label=quality.quality_label if quality else None,
        quality_summary=quality.summary if quality else None,
        effect_size=(
            None
            if not performance or not performance.effect_size
            else performance.effect_size
        ),
        auroc_estimate=performance.auroc_estimate if performance else None,
    )


def _quality_assessment(
    match_rate: float,
    auroc: float | None = None,
    percentile: float | None = None,
) -> QualityAssessment:
    from just_prs.quality import interpret_prs_result

    interp = interpret_prs_result(percentile=percentile, match_rate=match_rate, auroc=auroc)
    summary = interp.get("summary", "")
    if percentile is not None and "percentile not available" in summary.lower():
        summary = (
            f"Percentile {percentile:.1f} was provided separately; "
            "use percentile reliability caveats for availability and coverage context. "
            f"{summary}"
        )
    return QualityAssessment(
        quality_label=interp.get("quality_label", ""),
        quality_color=interp.get("quality_color", ""),
        summary=summary,
    )


def register_compute(mcp: FastMCP, settings: Settings) -> None:
    """Register the always-on compute + analysis tools, a resource, and a prompt."""

    @mcp.tool(
        task=True,
        annotations=ToolAnnotations(title="Normalize VCF", readOnlyHint=False, idempotentHint=True),
    )
    async def normalize_vcf(
        vcf_path: str,
        ctx: Context,
        output_path: str | None = None,
        pass_filters: list[str] | None = None,
        min_depth: int | None = None,
        min_qual: float | None = None,
        sex: str | None = None,
        genome_build: str | None = None,
    ) -> NormalizeResult:
        """Normalize a VCF to a quality-filtered genotype Parquet (background task).

        Strips the chr prefix, renames id→rsid, computes genotype from GT, applies
        optional quality filters (FILTER allow-list, min DP, min QUAL), and writes
        zstd-compressed Parquet. The output is a drop-in genotype source for
        ``compute_prs`` / ``compute_prs_batch`` (pass it as ``genotypes_path``),
        so a VCF is normalized once and reused across many scores.

        Runs as a real MCP background task, though some clients transparently
        collapse the task/poll handshake and return the final result inline.
        Normalization is the slow step (seconds to minutes depending on VCF size).
        The result echoes the effective genome build assumed for downstream PRS
        scoring; build inference from VCF contigs is deferred to just-prs.
        """
        from just_prs.normalize import VcfFilterConfig
        from just_prs.normalize import normalize_vcf as _normalize_vcf

        src = _require_file(vcf_path, "VCF")
        b = client.build(settings, genome_build)
        if output_path:
            out = Path(output_path).expanduser()
        else:
            out_dir = client.resolved_cache_dir(settings) / "normalized"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / (src.name.split(".")[0] + ".parquet")

        config = None
        if any(v is not None for v in (pass_filters, min_depth, min_qual, sex)):
            config = VcfFilterConfig(
                pass_filters=pass_filters, min_depth=min_depth, min_qual=min_qual, sex=sex
            )

        await ctx.info(f"Normalizing {src.name} -> {out.name}")
        await ctx.report_progress(progress=0, total=1)
        result_path = await run_sync(lambda: _normalize_vcf(src, out, config=config))
        n = await run_sync(lambda: _count_rows(result_path))
        await ctx.report_progress(progress=1, total=1)
        log.info("Normalized %s (%d variants) -> %s", src.name, n, result_path)
        return NormalizeResult(
            output_path=str(result_path),
            n_variants=n,
            genome_build=b,
            message=f"Normalized {n} variants to {result_path}.",
        )

    @mcp.tool(
        annotations=ToolAnnotations(title="Compute PRS", readOnlyHint=True, openWorldHint=True)
    )
    def compute_prs(
        vcf_path: str,
        pgs_id: str,
        genome_build: str | None = None,
        genotypes_path: str | None = None,
    ) -> PRSResult:
        """Compute a polygenic risk score for one VCF against one PGS score.

        Downloads the harmonized scoring file (cached) and scores the genotypes.
        Pass ``genotypes_path`` to reuse a normalized Parquet from ``normalize_vcf``
        (avoids re-reading the VCF); otherwise the VCF is read directly. Returns
        the score, match rate, variant counts, trait, and (when data permits) a
        theoretical percentile.
        """
        b = client.build(settings, genome_build)
        cat = client.make_catalog(settings)
        try:
            if genotypes_path:
                import polars as pl
                from just_prs.prs import compute_prs as _compute_prs

                gpath = _require_file(genotypes_path, "Genotypes Parquet")
                info = cat.score_info_row(pgs_id)
                raw_trait = info.get("trait_reported") if info else None
                trait = str(raw_trait) if raw_trait is not None else None
                return _compute_prs(
                    vcf_path=vcf_path,
                    scoring_file=pgs_id,
                    genome_build=b,
                    cache_dir=client.resolved_cache_dir(settings) / "scores",
                    pgs_id=pgs_id,
                    trait_reported=trait,
                    genotypes_lf=pl.scan_parquet(gpath),
                )
            _require_file(vcf_path, "VCF")
            return cat.compute_prs(vcf_path=vcf_path, pgs_id=pgs_id, genome_build=b)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"PRS computation failed for {pgs_id}: {exc}") from exc

    @mcp.tool(
        task=True,
        annotations=ToolAnnotations(title="Compute PRS (batch)", readOnlyHint=True),
    )
    async def compute_prs_batch(
        vcf_path: str,
        pgs_ids: list[str],
        ctx: Context,
        genome_build: str | None = None,
    ) -> list[PRSResult]:
        """Compute PRS for one VCF against many PGS scores (background task).

        Memory-safe DuckDB engine with spill-to-disk; reuses the parsed VCF and
        scoring caches across scores. Returns one result per PGS ID.
        """
        b = client.build(settings, genome_build)
        cat = client.make_catalog(settings)
        _require_file(vcf_path, "VCF")

        await ctx.info(f"Batch-scoring {len(pgs_ids)} PGS IDs against {Path(vcf_path).name}")
        results = await run_sync(
            lambda: cat.compute_prs_batch(vcf_path=vcf_path, pgs_ids=pgs_ids, genome_build=b)
        )
        await ctx.info(f"Computed {len(results)} score(s)")
        return results

    @mcp.tool(
        task=True,
        annotations=ToolAnnotations(title="Compute PRS by trait", readOnlyHint=True),
    )
    async def compute_prs_by_trait(
        trait_id: str,
        vcf_path: str,
        ctx: Context,
        genotypes_path: str | None = None,
        genome_build: str | None = None,
        include_children: bool = False,
        limit: int | None = None,
        interpret: bool = False,
        superpopulation: str = "EUR",
        panel: str | None = None,
    ) -> TraitPRSReport:
        """Compute all directly associated PRS scores for a trait ontology ID.

        ``trait_id`` may be an EFO or MONDO identifier. Set ``include_children``
        to also score PGS IDs associated through descendant traits. ``limit`` caps
        the number scored and reports skipped IDs explicitly.
        """
        b = client.build(settings, genome_build)
        try:
            with client.make_rest_client() as rest:
                trait = rest.get_trait(trait_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Trait lookup failed for {trait_id}: {exc}") from exc

        pgs_ids = list(dict.fromkeys(trait.associated_pgs_ids))
        if include_children:
            pgs_ids = list(dict.fromkeys([*pgs_ids, *trait.child_associated_pgs_ids]))
        n_total = len(pgs_ids)
        selected_ids = pgs_ids[:limit] if limit is not None and limit >= 0 else pgs_ids
        n_skipped = max(0, n_total - len(selected_ids))

        await ctx.info(
            f"Trait {trait.id} has {n_total} candidate PGS ID(s); "
            f"scoring {len(selected_ids)}"
        )
        await ctx.report_progress(progress=0, total=max(1, len(selected_ids)))

        rows: list[TraitScoreRow] = []
        if genotypes_path:
            import polars as pl
            from just_prs.prs import compute_prs as _compute_prs

            gpath = _require_file(genotypes_path, "Genotypes Parquet")
            genotypes_lf = pl.scan_parquet(gpath)
            cat = client.make_catalog(settings)
            for idx, pgs_id in enumerate(selected_ids, start=1):
                try:
                    info = cat.score_info_row(pgs_id)
                    raw_trait = info.get("trait_reported") if info else None
                    result = await run_sync(
                        lambda pgs_id=pgs_id, raw_trait=raw_trait: _compute_prs(
                            vcf_path=vcf_path,
                            scoring_file=pgs_id,
                            genome_build=b,
                            cache_dir=client.resolved_cache_dir(settings) / "scores",
                            pgs_id=pgs_id,
                            trait_reported=str(raw_trait) if raw_trait is not None else None,
                            genotypes_lf=genotypes_lf,
                        )
                    )
                    rows.append(
                        _trait_score_row(
                            settings=settings,
                            result=result,
                            interpret=interpret,
                            superpopulation=superpopulation,
                            panel=panel,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    rows.append(TraitScoreRow(pgs_id=pgs_id, status="failed", error=str(exc)))
                await ctx.report_progress(progress=idx, total=max(1, len(selected_ids)))
        else:
            _require_file(vcf_path, "VCF")
            try:
                results = await run_sync(
                    lambda: client.make_catalog(settings).compute_prs_batch(
                        vcf_path=vcf_path,
                        pgs_ids=selected_ids,
                        genome_build=b,
                    )
                )
                by_id = {result.pgs_id: result for result in results}
                for pgs_id in selected_ids:
                    result = by_id.get(pgs_id)
                    if result is None:
                        rows.append(
                            TraitScoreRow(
                                pgs_id=pgs_id,
                                status="failed",
                                error="No result returned by batch scoring.",
                            )
                        )
                    else:
                        rows.append(
                            _trait_score_row(
                                settings=settings,
                                result=result,
                                interpret=interpret,
                                superpopulation=superpopulation,
                                panel=panel,
                            )
                        )
                await ctx.report_progress(
                    progress=len(selected_ids),
                    total=max(1, len(selected_ids)),
                )
            except Exception as exc:  # noqa: BLE001
                rows = [
                    TraitScoreRow(pgs_id=pgs_id, status="failed", error=str(exc))
                    for pgs_id in selected_ids
                ]

        n_scored = sum(1 for row in rows if row.status == "scored")
        n_failed = sum(1 for row in rows if row.status == "failed")
        return TraitPRSReport(
            trait_id=trait.id,
            label=trait.label or trait.id,
            genome_build=b,
            n_requested=len(selected_ids),
            n_scored=n_scored,
            n_failed=n_failed,
            n_skipped=n_skipped,
            rows=rows,
            summary=(
                f"Scored {n_scored}/{len(selected_ids)} PGS ID(s) for {trait.label or trait.id} "
                f"on {b}; {n_failed} failed, {n_skipped} skipped by limit."
            ),
        )

    @mcp.tool(
        annotations=ToolAnnotations(title="PRS percentile", readOnlyHint=True, openWorldHint=True)
    )
    def percentile(
        prs_score: float,
        pgs_id: str,
        superpopulation: str = "EUR",
        panel: str | None = None,
        match_rate: float | None = None,
    ) -> PercentileResult:
        """Estimate the population percentile (0-100) for a computed PRS value.

        Uses the 3-tier fallback: precomputed reference-panel distributions
        (best), then a theoretical distribution, then an AUROC approximation.
        ``superpopulation`` is a 1000G code (AFR/AMR/EAS/EUR/SAS). Pass
        ``match_rate`` from ``compute_prs`` so low-coverage or extreme percentile
        outputs can be flagged as unreliable instead of presented bare.
        """
        try:
            return _percentile_result(
                settings=settings,
                prs_score=prs_score,
                pgs_id=pgs_id,
                superpopulation=superpopulation,
                panel=panel,
                match_rate=match_rate,
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Percentile estimation failed for {pgs_id}: {exc}") from exc

    @mcp.tool(
        annotations=ToolAnnotations(title="Absolute risk", readOnlyHint=True, openWorldHint=True)
    )
    def absolute_risk(pgs_id: str, z_score: float, sex: str | None = None) -> AbsoluteRisk:
        """Estimate absolute disease risk from a PRS z-score and population prevalence.

        Joins the score's trait to prevalence + effect-size data. ``z_score`` is
        the PRS in standard deviations from the population mean. Raises if the
        required prevalence / effect-size data is unavailable for this score.
        """
        try:
            risk = client.make_catalog(settings).absolute_risk(pgs_id, z_score, sex=sex)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Absolute-risk estimation failed for {pgs_id}: {exc}") from exc
        if risk is None:
            raise ToolError(
                f"No absolute-risk estimate available for {pgs_id} "
                "(missing prevalence or effect-size data)."
            )
        return risk

    @mcp.tool(annotations=ToolAnnotations(title="Assess quality", readOnlyHint=True))
    def assess_quality(
        match_rate: float,
        auroc: float | None = None,
        percentile: float | None = None,
    ) -> QualityAssessment:
        """Classify and interpret a PRS result's quality (pure logic — no I/O).

        ``match_rate`` is the fraction of scoring variants matched (0-1). Returns
        a quality label/color and a human-readable interpretation combining match
        rate, AUROC, and (optionally) the result percentile.
        """
        return _quality_assessment(match_rate=match_rate, auroc=auroc, percentile=percentile)

    @mcp.resource("resource://prs/panels")
    def panels() -> str:
        """Reference panels, supported genome builds, and the active cache directory."""
        from just_prs.reference import REFERENCE_PANELS

        lines = ["# just-prs server", ""]
        lines.append(f"- default genome build: **{settings.default_genome_build}**")
        lines.append(f"- default reference panel: **{settings.default_panel}**")
        lines.append(f"- cache dir: `{client.resolved_cache_dir(settings)}`")
        lines.append("")
        lines.append("## Reference panels")
        for name in REFERENCE_PANELS:
            lines.append(f"- `{name}`")
        return "\n".join(lines)

    @mcp.prompt
    def compute_prs_for_trait(trait: str, vcf_path: str = "<path/to/sample.vcf.gz>") -> str:
        """Prompt template: find and compute PRS for a trait against a VCF."""
        return (
            f"I want to compute polygenic risk scores for '{trait}' from the VCF at "
            f"{vcf_path}. Search the PGS Catalog for relevant scores, normalize the "
            "VCF, compute the PRS for the most relevant score(s), and interpret the "
            "results (percentile and quality)."
        )
