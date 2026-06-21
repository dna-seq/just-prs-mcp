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
    PRSResult,
    QualityAssessment,
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
    ) -> NormalizeResult:
        """Normalize a VCF to a quality-filtered genotype Parquet (background task).

        Strips the chr prefix, renames id→rsid, computes genotype from GT, applies
        optional quality filters (FILTER allow-list, min DP, min QUAL), and writes
        zstd-compressed Parquet. The output is a drop-in genotype source for
        ``compute_prs`` / ``compute_prs_batch`` (pass it as ``genotypes_path``),
        so a VCF is normalized once and reused across many scores.

        Runs as a real MCP background task: the client gets a task id immediately
        and polls for the result. Normalization is the slow step (seconds to
        minutes depending on VCF size).
        """
        from just_prs.normalize import VcfFilterConfig
        from just_prs.normalize import normalize_vcf as _normalize_vcf

        src = _require_file(vcf_path, "VCF")
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
        annotations=ToolAnnotations(title="PRS percentile", readOnlyHint=True, openWorldHint=True)
    )
    def percentile(
        prs_score: float,
        pgs_id: str,
        superpopulation: str = "EUR",
        panel: str | None = None,
    ) -> PercentileResult:
        """Estimate the population percentile (0-100) for a computed PRS value.

        Uses the 3-tier fallback: precomputed reference-panel distributions
        (best), then a theoretical distribution, then an AUROC approximation.
        ``superpopulation`` is a 1000G code (AFR/AMR/EAS/EUR/SAS).
        """
        try:
            pct, method = client.make_catalog(settings).percentile(
                prs_score=prs_score,
                pgs_id=pgs_id,
                ancestry=superpopulation,
                panel=client.panel(settings, panel),
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Percentile estimation failed for {pgs_id}: {exc}") from exc
        return PercentileResult(
            pgs_id=pgs_id,
            prs_score=prs_score,
            percentile=pct,
            method=method,
            ancestry=superpopulation,
        )

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
        from just_prs.quality import interpret_prs_result

        interp = interpret_prs_result(percentile=percentile, match_rate=match_rate, auroc=auroc)
        return QualityAssessment(
            quality_label=interp.get("quality_label", ""),
            quality_color=interp.get("quality_color", ""),
            summary=interp.get("summary", ""),
        )

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
