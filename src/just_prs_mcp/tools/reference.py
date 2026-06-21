"""EXTENDED — reference-panel and PLINK2-pgen scoring tools.

These score PGS models against population reference panels (1000G / HGDP+1kGP)
or arbitrary ``.pgen`` datasets to build per-ancestry distributions. The scoring
path depends on the optional native ``pgenlib`` dependency (Linux/WSL only —
install with ``uv sync --extra reference``); when it is missing, scoring tools
raise a ``ToolError`` with the install hint. Panel download and ``.pvar``/``.psam``
inspection do not need pgenlib.

All are registered only in ``extended`` mode and long ops run as background tasks.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from anyio.to_thread import run_sync
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from just_prs_mcp import client
from just_prs_mcp.logging_setup import get_logger
from just_prs_mcp.models import (
    BatchScoringSummary,
    DistributionRow,
    OpResult,
    ReferenceScoreSummary,
)
from just_prs_mcp.settings import Settings

if TYPE_CHECKING:
    import polars as pl

log = get_logger()

_PGENLIB_HINT = (
    "Reference/pgen scoring needs the native 'pgenlib' dependency "
    "(Linux/WSL only): uv sync --extra reference."
)


def _dist_rows(df: pl.DataFrame) -> list[DistributionRow]:
    rows: list[DistributionRow] = []
    for r in df.to_dicts():
        rows.append(
            DistributionRow(
                pgs_id=r.get("pgs_id"),
                superpopulation=str(r.get("superpopulation", r.get("superpop", ""))),
                mean=float(r.get("mean", 0.0)),
                std=float(r.get("std", 0.0)),
                n=int(r.get("n", 0)),
                median=r.get("median"),
                p5=r.get("p5"),
                p25=r.get("p25"),
                p75=r.get("p75"),
                p95=r.get("p95"),
            )
        )
    return rows


def _score_against_dir(settings: Settings, pgs_id: str, ref_dir: Path, build: str):
    """Download the scoring file and score a PGS against a pgen/reference directory."""
    from just_prs.reference import aggregate_distributions, compute_reference_prs_polars
    from just_prs.scoring import download_scoring_file

    cache = client.resolved_cache_dir(settings)
    scores_dir = cache / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    out_dir = cache / "reference_scores"
    out_dir.mkdir(parents=True, exist_ok=True)

    scoring_file = download_scoring_file(pgs_id, scores_dir, genome_build=build)
    scores_df = compute_reference_prs_polars(
        pgs_id, scoring_file, ref_dir, out_dir, genome_build=build
    )
    dist_df = aggregate_distributions(scores_df)
    return scores_df.height, _dist_rows(dist_df)


def register_reference(mcp: FastMCP, settings: Settings) -> None:
    """Register the extended-only reference-panel / pgen tools."""

    @mcp.tool(
        task=True,
        tags={"extended", "reference"},
        annotations=ToolAnnotations(
            title="Download reference panel", readOnlyHint=False, openWorldHint=True
        ),
    )
    async def download_reference_panel(
        ctx: Context, panel: str | None = None, overwrite: bool = False
    ) -> OpResult:
        """Download and extract a population reference panel (background task).

        Panels: '1000g' (default) or 'hgdp_1kg'. Large (~7-15 GB) one-time download
        used by ``reference_score`` / ``reference_score_batch``.
        """
        from just_prs.reference import download_reference_panel as _download

        p = client.panel(settings, panel)
        await ctx.info(f"Downloading reference panel '{p}' (large, one-time)...")
        try:
            path = await run_sync(
                lambda: _download(
                    cache_dir=client.cache_dir(settings), overwrite=overwrite, panel=p
                )
            )
        except Exception as exc:  # noqa: BLE001
            return OpResult(success=False, message=f"Panel download failed: {exc}")
        return OpResult(
            success=True,
            message=f"Reference panel '{p}' ready.",
            data={"panel": p, "path": str(path)},
        )

    @mcp.tool(
        task=True,
        tags={"extended", "reference"},
        annotations=ToolAnnotations(title="Reference score", readOnlyHint=True, openWorldHint=True),
    )
    async def reference_score(
        pgs_id: str,
        ctx: Context,
        panel: str | None = None,
        genome_build: str | None = None,
    ) -> ReferenceScoreSummary:
        """Score one PGS against a downloaded reference panel (background task).

        Returns per-superpopulation distribution statistics (mean/std/quantiles).
        The panel must already be present (see ``download_reference_panel``).
        """
        from just_prs.reference import reference_panel_dir

        p = client.panel(settings, panel)
        b = client.build(settings, genome_build)
        ref_dir = reference_panel_dir(client.cache_dir(settings), panel=p)
        if not ref_dir.exists():
            raise ToolError(
                f"Reference panel '{p}' not found at {ref_dir}. Run download_reference_panel first."
            )
        await ctx.info(f"Scoring {pgs_id} against panel '{p}'...")
        try:
            n_samples, dist = await run_sync(
                lambda: _score_against_dir(settings, pgs_id, ref_dir, b)
            )
        except ImportError as exc:
            raise ToolError(_PGENLIB_HINT) from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Reference scoring failed for {pgs_id}: {exc}") from exc
        return ReferenceScoreSummary(
            pgs_id=pgs_id, panel=p, n_samples=n_samples, distributions=dist
        )

    @mcp.tool(
        task=True,
        tags={"extended", "reference"},
        annotations=ToolAnnotations(
            title="Reference score (batch)", readOnlyHint=True, openWorldHint=True
        ),
    )
    async def reference_score_batch(
        ctx: Context,
        pgs_ids: list[str] | None = None,
        limit: int = 0,
        panel: str | None = None,
        genome_build: str | None = None,
    ) -> BatchScoringSummary:
        """Score many PGS IDs against a reference panel to build distributions (background task).

        Omit ``pgs_ids`` to score the entire catalog (very long — hours). ``limit``
        caps the number scored (0 = no limit). Per-ID errors are tracked, not fatal.
        """
        from just_prs.ftp import list_all_pgs_ids
        from just_prs.reference import compute_reference_prs_batch, reference_panel_dir

        p = client.panel(settings, panel)
        b = client.build(settings, genome_build)
        ref_dir = reference_panel_dir(client.cache_dir(settings), panel=p)
        if not ref_dir.exists():
            raise ToolError(
                f"Reference panel '{p}' not found at {ref_dir}. Run download_reference_panel first."
            )

        ids = pgs_ids or await run_sync(list_all_pgs_ids)
        if limit and limit > 0:
            ids = ids[:limit]
        await ctx.info(f"Batch-scoring {len(ids)} PGS IDs against panel '{p}'...")

        try:
            result = await run_sync(
                lambda: compute_reference_prs_batch(
                    ids, ref_dir, client.resolved_cache_dir(settings), genome_build=b, panel=p
                )
            )
        except ImportError as exc:
            raise ToolError(_PGENLIB_HINT) from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Batch reference scoring failed: {exc}") from exc

        outcomes = [
            {"pgs_id": o.pgs_id, "status": o.status, "error": o.error} for o in result.outcomes
        ]
        n_ok = sum(1 for o in result.outcomes if o.status == "ok")
        return BatchScoringSummary(
            panel=result.panel,
            n_requested=len(ids),
            n_scored=n_ok,
            n_failed=len(ids) - n_ok,
            outcomes=outcomes,
            distributions=_dist_rows(result.distributions_df),
        )

    @mcp.tool(
        tags={"extended", "reference"},
        annotations=ToolAnnotations(title="Read .pvar", readOnlyHint=True),
    )
    def pgen_read_pvar(pvar_path: str, limit: int = 20) -> list[dict]:
        """Read a PLINK2 ``.pvar`` / ``.pvar.zst`` variant table (first ``limit`` rows)."""
        from just_prs.reference import parse_pvar

        path = Path(pvar_path).expanduser()
        if not path.exists():
            raise ToolError(f".pvar file not found: {pvar_path}")
        try:
            df = parse_pvar(path)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Failed to parse .pvar: {exc}") from exc
        return df.head(limit).to_dicts() if limit and limit > 0 else df.to_dicts()

    @mcp.tool(
        tags={"extended", "reference"},
        annotations=ToolAnnotations(title="Read .psam", readOnlyHint=True),
    )
    def pgen_read_psam(psam_path: str, limit: int = 20) -> list[dict]:
        """Read a PLINK2 ``.psam`` sample table (iid/superpop/population; first ``limit`` rows)."""
        from just_prs.reference import parse_psam

        path = Path(psam_path).expanduser()
        if not path.exists():
            raise ToolError(f".psam file not found: {psam_path}")
        try:
            df = parse_psam(path)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Failed to parse .psam: {exc}") from exc
        return df.head(limit).to_dicts() if limit and limit > 0 else df.to_dicts()

    @mcp.tool(
        task=True,
        tags={"extended", "reference"},
        annotations=ToolAnnotations(
            title="Score against .pgen dir", readOnlyHint=True, openWorldHint=True
        ),
    )
    async def pgen_score(
        pgs_id: str,
        pgen_dir: str,
        ctx: Context,
        genome_build: str | None = None,
    ) -> ReferenceScoreSummary:
        """Score a PGS against any ``.pgen``/``.pvar.zst``/``.psam`` directory (background task).

        Returns per-superpopulation distribution statistics. Needs ``pgenlib``.
        """
        b = client.build(settings, genome_build)
        ref_dir = Path(pgen_dir).expanduser()
        if not ref_dir.exists():
            raise ToolError(f"pgen directory not found: {pgen_dir}")
        await ctx.info(f"Scoring {pgs_id} against {ref_dir}...")
        try:
            n_samples, dist = await run_sync(
                lambda: _score_against_dir(settings, pgs_id, ref_dir, b)
            )
        except ImportError as exc:
            raise ToolError(_PGENLIB_HINT) from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"pgen scoring failed for {pgs_id}: {exc}") from exc
        return ReferenceScoreSummary(
            pgs_id=pgs_id, panel=str(ref_dir), n_samples=n_samples, distributions=dist
        )
