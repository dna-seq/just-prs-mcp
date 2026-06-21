"""EXTENDED — heavier / opt-in tools (registered only when mode == "extended").

Consumer-array normalization, scoring-file and bulk catalog downloads, the
HuggingFace catalog upload, prevalence-prior inspection, and multi-method
absolute risk. Long-running operations run as background tasks; download/upload
tools return a typed ``OpResult`` so partial-success and missing-credential
states are data, not exceptions.

Opt in via ``PRS_MCP_MODE=extended`` / ``--mode extended``.
"""

from __future__ import annotations

import os
from pathlib import Path

from anyio.to_thread import run_sync
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from just_prs_mcp import client
from just_prs_mcp.logging_setup import get_logger
from just_prs_mcp.models import (
    AbsoluteRiskBundle,
    NormalizeResult,
    OpResult,
    PrevalenceInfo,
    PrevalenceRow,
)
from just_prs_mcp.settings import Settings

log = get_logger()

_PREVALENCE_FIELDS = (
    "efo_id",
    "trait_label",
    "prevalence",
    "prevalence_lower",
    "prevalence_upper",
    "prevalence_type",
    "sex",
    "ancestry",
    "age_range",
    "source",
    "source_detail",
    "xref_mondo",
    "xref_icd10",
    "confidence",
)


def _count_rows(parquet_path: Path) -> int:
    import polars as pl

    return int(pl.scan_parquet(parquet_path).select(pl.len()).collect().item())


def _prevalence_row(row: dict) -> PrevalenceRow:
    return PrevalenceRow(**{field: row.get(field) for field in _PREVALENCE_FIELDS})


def register_extended(mcp: FastMCP, settings: Settings) -> None:
    """Register the extended-only tools."""

    @mcp.tool(
        task=True,
        tags={"extended"},
        annotations=ToolAnnotations(
            title="Normalize array", readOnlyHint=False, idempotentHint=True
        ),
    )
    async def normalize_array(
        array_path: str,
        ctx: Context,
        output_path: str | None = None,
        genome_build: str = "GRCh37",
        array_format: str | None = None,
    ) -> NormalizeResult:
        """Normalize a 23andMe / AncestryDNA raw file to a genotype Parquet (background task).

        Output schema matches ``normalize_vcf`` so it is a drop-in genotype source
        for ``compute_prs`` / ``compute_prs_batch``. Genome build defaults to
        GRCh37 (typical for consumer arrays); ``array_format`` forces '23andme' or
        'ancestrydna' instead of auto-detection.
        """
        from just_prs.arrays import normalize_array as _normalize_array

        src = Path(array_path).expanduser()
        if not src.exists():
            raise ToolError(f"Array file not found: {array_path}")
        if output_path:
            out = Path(output_path).expanduser()
        else:
            out_dir = client.resolved_cache_dir(settings) / "normalized"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / (src.name.split(".")[0] + ".parquet")

        await ctx.info(f"Normalizing array {src.name} -> {out.name}")
        result_path = await run_sync(
            lambda: _normalize_array(src, out, genome_build=genome_build, array_format=array_format)
        )
        n = await run_sync(lambda: _count_rows(result_path))
        return NormalizeResult(
            output_path=str(result_path),
            n_variants=n,
            message=f"Normalized {n} variants to {result_path}.",
        )

    @mcp.tool(
        tags={"extended"},
        annotations=ToolAnnotations(
            title="Download scoring file", readOnlyHint=False, openWorldHint=True
        ),
    )
    def download_scoring_file(
        pgs_id: str,
        output_dir: str | None = None,
        genome_build: str | None = None,
    ) -> OpResult:
        """Download a harmonized PGS scoring file (.txt.gz) from EBI FTP."""
        from just_prs.scoring import download_scoring_file as _download

        out = (
            Path(output_dir).expanduser()
            if output_dir
            else client.resolved_cache_dir(settings) / "scores"
        )
        out.mkdir(parents=True, exist_ok=True)
        try:
            path = _download(pgs_id, out, genome_build=client.build(settings, genome_build))
        except Exception as exc:  # noqa: BLE001
            return OpResult(success=False, message=f"Download failed for {pgs_id}: {exc}")
        return OpResult(
            success=True,
            message=f"Downloaded {pgs_id} scoring file.",
            data={"pgs_id": pgs_id, "path": str(path)},
        )

    @mcp.tool(
        tags={"extended"},
        annotations=ToolAnnotations(title="List PGS IDs", readOnlyHint=True, openWorldHint=True),
    )
    def list_pgs_ids() -> list[str]:
        """List every PGS Catalog score ID available on the EBI FTP server."""
        from just_prs.ftp import list_all_pgs_ids

        try:
            return list_all_pgs_ids()
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Failed to list PGS IDs: {exc}") from exc

    @mcp.tool(
        task=True,
        tags={"extended"},
        annotations=ToolAnnotations(
            title="Download all metadata", readOnlyHint=False, openWorldHint=True
        ),
    )
    async def download_all_metadata(
        ctx: Context,
        output_dir: str | None = None,
        overwrite: bool = False,
    ) -> OpResult:
        """Download all PGS Catalog metadata sheets as Parquet (background task)."""
        from just_prs.ftp import download_all_metadata as _download_meta

        out = (
            Path(output_dir).expanduser()
            if output_dir
            else client.resolved_cache_dir(settings) / "pgs_metadata"
        )
        out.mkdir(parents=True, exist_ok=True)
        await ctx.info(f"Downloading PGS Catalog metadata sheets -> {out}")
        try:
            sheets = await run_sync(lambda: _download_meta(out, overwrite=overwrite))
        except Exception as exc:  # noqa: BLE001
            return OpResult(success=False, message=f"Metadata download failed: {exc}")
        return OpResult(
            success=True,
            message=f"Downloaded {len(sheets)} metadata sheet(s) to {out}.",
            data={"output_dir": str(out), "sheets": sorted(sheets)},
        )

    @mcp.tool(
        task=True,
        tags={"extended"},
        annotations=ToolAnnotations(
            title="Bulk download scores", readOnlyHint=False, openWorldHint=True
        ),
    )
    async def bulk_download_scores(
        ctx: Context,
        output_dir: str | None = None,
        genome_build: str | None = None,
        ids: list[str] | None = None,
        overwrite: bool = False,
    ) -> OpResult:
        """Download many (or all ~5,000+) PGS scoring files as Parquet (background task).

        Omit ``ids`` to fetch the entire catalog — this is a long, network-bound
        operation. Returns the count and output directory.
        """
        from just_prs.ftp import bulk_download_scoring_parquets

        out = (
            Path(output_dir).expanduser()
            if output_dir
            else client.resolved_cache_dir(settings) / "scores"
        )
        out.mkdir(parents=True, exist_ok=True)
        await ctx.info(f"Downloading {'all' if not ids else len(ids)} scoring file(s) -> {out}")
        try:
            paths = await run_sync(
                lambda: bulk_download_scoring_parquets(
                    out,
                    genome_build=client.build(settings, genome_build),
                    pgs_ids=ids,
                    overwrite=overwrite,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return OpResult(success=False, message=f"Bulk download failed: {exc}")
        return OpResult(
            success=True,
            message=f"Downloaded {len(paths)} scoring file(s) to {out}.",
            data={"output_dir": str(out), "n_files": len(paths)},
        )

    @mcp.tool(
        tags={"extended"},
        annotations=ToolAnnotations(
            title="Prevalence prior", readOnlyHint=True, openWorldHint=True
        ),
    )
    def prevalence_info(
        pgs_id: str | None = None,
        trait_id: str | None = None,
    ) -> PrevalenceInfo:
        """Inspect the population-prevalence prior just-prs uses for absolute risk.

        ``absolute_risk`` reports the prior it applied, but only as a side effect of
        a risk calc that needs a z-score. This surfaces the prior directly: pass a
        ``trait_id`` (EFO or MONDO) or a ``pgs_id`` (resolved to its EFO trait IDs),
        and get the matching rows from just-prs's prevalence table — value, bounds,
        type, sex/ancestry/age scope, source, and confidence. Returns no rows (not
        an error) when the catalog has no prior for the trait.
        """
        if not pgs_id and not trait_id:
            raise ToolError("Provide pgs_id or trait_id.")
        import polars as pl
        from just_prs.ontology import expand_trait_ids_from_alias_columns

        cat = client.make_catalog(settings)
        efo_ids: list[str] = []
        if trait_id:
            efo_ids.append(trait_id.strip())
        if pgs_id:
            try:
                info = cat.score_info_row(pgs_id)
            except Exception as exc:  # noqa: BLE001
                raise ToolError(f"Score lookup failed for {pgs_id}: {exc}") from exc
            if info is None:
                raise ToolError(f"Unknown PGS ID '{pgs_id}'.")
            raw = info.get("trait_efo_id")
            if raw:
                efo_ids.extend(e.strip() for e in str(raw).split(",") if e.strip())
        efo_ids = list(dict.fromkeys(e for e in efo_ids if e))
        if not efo_ids:
            raise ToolError(f"Could not resolve any trait ontology ID for {pgs_id or trait_id}.")

        try:
            df = cat.prevalence_table().collect()
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Prevalence table unavailable: {exc}") from exc

        if df.height:
            expanded = expand_trait_ids_from_alias_columns(efo_ids, df)
            cols = df.columns
            mask = pl.col("efo_id").is_in(expanded)
            if "xref_mondo" in cols:
                mask = mask | pl.col("xref_mondo").is_in(expanded)
            rows = [_prevalence_row(r) for r in df.filter(mask).to_dicts()]
        else:
            expanded = efo_ids
            rows = []

        return PrevalenceInfo(
            query=pgs_id or trait_id or "",
            resolved_efo_ids=expanded,
            n_matches=len(rows),
            rows=rows,
            message=(
                f"Found {len(rows)} prevalence prior row(s) for "
                f"{', '.join(expanded)}."
                if rows
                else f"No prevalence prior in the catalog for {', '.join(expanded)}."
            ),
        )

    @mcp.tool(
        tags={"extended"},
        annotations=ToolAnnotations(
            title="Absolute risk (all methods)", readOnlyHint=True, openWorldHint=True
        ),
    )
    def absolute_risk_bundle(
        pgs_id: str,
        z_score: float,
        sex: str | None = None,
    ) -> AbsoluteRiskBundle:
        """Compute every available absolute-risk estimate for a score, with agreement.

        Unlike the single-method ``absolute_risk`` (essentials), this runs every
        method the data supports — OR-per-SD and AUC-bivariate (from best
        performance) and h²-liability (per ancestry/source from the heritability
        table) — and returns all estimates, a best pick, and how well they agree.
        Each estimate carries the population-prevalence prior it used. ``z_score``
        is the PRS in SDs from the population mean. Returns an empty bundle when the
        prevalence prior is unavailable.
        """
        try:
            return client.make_catalog(settings).absolute_risk_bundle(pgs_id, z_score, sex=sex)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Absolute-risk bundle failed for {pgs_id}: {exc}") from exc

    @mcp.tool(
        task=True,
        tags={"extended"},
        annotations=ToolAnnotations(
            title="Push catalog to HuggingFace", readOnlyHint=False, openWorldHint=True
        ),
    )
    async def push_catalog_to_hf(ctx: Context, repo_id: str | None = None) -> OpResult:
        """Upload cleaned metadata + scoring parquets to a HuggingFace dataset (background task).

        Requires a token: set ``PRS_MCP_HF_TOKEN`` or the native ``HF_TOKEN`` env
        var. A maintainer operation — defaults to the just-dna-seq/pgs-catalog repo.
        """
        token = settings.hf_token or os.getenv("HF_TOKEN")
        if not token:
            return OpResult(
                success=False,
                message="No HuggingFace token. Set PRS_MCP_HF_TOKEN or HF_TOKEN to push.",
            )
        cat = client.make_catalog(settings)
        await ctx.info("Pushing cleaned catalog to HuggingFace...")
        try:
            await run_sync(lambda: cat.push_to_hf(token=settings.hf_token, repo_id=repo_id))
        except Exception as exc:  # noqa: BLE001
            return OpResult(success=False, message=f"HuggingFace push failed: {exc}")
        return OpResult(success=True, message="Catalog pushed to HuggingFace.")
