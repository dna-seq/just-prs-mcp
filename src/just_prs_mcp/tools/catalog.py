"""ESSENTIALS — PGS Catalog lookup tools (read-only, no local files needed).

These wrap ``PRSCatalog`` (cleaned bulk metadata, no per-call REST traffic) and
the ``PGSCatalogClient`` REST client (for trait search). They are registered in
every mode: a small, always-on surface for discovering scores and traits.

First use triggers just-prs's 3-tier metadata fetch (local cache -> HuggingFace
-> EBI FTP); subsequent calls are served from the on-disk cache. Catalog
unavailability surfaces as a ``ToolError`` with the underlying reason.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from just_prs_mcp import client
from just_prs_mcp.logging_setup import get_logger
from just_prs_mcp.models import (
    PerformanceSummary,
    ScoreSummary,
    TraitInfo,
)
from just_prs_mcp.settings import Settings

log = get_logger()

_READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True}


def _score_summary(row: dict) -> ScoreSummary:
    return ScoreSummary(
        pgs_id=str(row.get("pgs_id", "")),
        name=row.get("name"),
        trait_reported=row.get("trait_reported"),
        trait_efo=row.get("trait_efo"),
        genome_build=row.get("genome_build"),
        variants_number=row.get("variants_number"),
        weight_type=row.get("weight_type"),
        is_harmonized=row.get("is_harmonized"),
        quality_label=row.get("quality_label"),
    )


def register_catalog(mcp: FastMCP, settings: Settings) -> None:
    """Register the always-on PGS Catalog lookup tools."""

    @mcp.tool(annotations=ToolAnnotations(title="Search PGS scores", **_READ_ONLY))
    def search_scores(
        query: str,
        genome_build: str | None = None,
        limit: int = 25,
    ) -> list[ScoreSummary]:
        """Search the PGS Catalog for polygenic scores by free text.

        Case-insensitive substring match across PGS ID, score name, reported
        trait, and EFO trait. Optionally filter to a genome build (GRCh37 /
        GRCh38, harmonized cross-build scores included). Returns up to ``limit``
        matches with their key metadata.
        """
        try:
            lf = client.make_catalog(settings).search(query, genome_build=genome_build)
            rows = client.records(lf, limit)
        except Exception as exc:  # noqa: BLE001 — surface a clean message to the agent
            raise ToolError(f"PGS Catalog search failed: {exc}") from exc
        return [_score_summary(r) for r in rows]

    @mcp.tool(annotations=ToolAnnotations(title="Score metadata", **_READ_ONLY))
    def score_info(pgs_id: str) -> ScoreSummary:
        """Get cleaned metadata for a single PGS score by its ID (e.g. 'PGS000001')."""
        try:
            row = client.make_catalog(settings).score_info_row(pgs_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"PGS Catalog lookup failed: {exc}") from exc
        if row is None:
            raise ToolError(f"Unknown PGS ID '{pgs_id}'.")
        return _score_summary(row)

    @mcp.tool(annotations=ToolAnnotations(title="Best performance", **_READ_ONLY))
    def best_performance(pgs_id: str) -> PerformanceSummary:
        """Look up the best evaluation performance for a score (largest sample, EUR-preferred).

        Returns parsed effect sizes (OR/HR/Beta) and classification metrics
        (AUROC/C-index) plus pre-formatted display strings.
        """
        from just_prs.quality import format_classification, format_effect_size

        try:
            df = client.make_catalog(settings).best_performance(pgs_id=pgs_id).collect()
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"PGS Catalog lookup failed: {exc}") from exc
        if df.height == 0:
            return PerformanceSummary(pgs_id=pgs_id, found=False)
        row = df.row(0, named=True)
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
            effect_size=format_effect_size(row),
            classification=format_classification(row),
        )

    @mcp.tool(annotations=ToolAnnotations(title="Search traits", **_READ_ONLY))
    def search_traits(term: str, limit: int = 25) -> list[TraitInfo]:
        """Search the PGS Catalog REST API for traits by term (e.g. 'type 2 diabetes')."""
        try:
            with client.make_rest_client() as rest:
                return rest.search_traits(term, limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Trait search failed: {exc}") from exc

    @mcp.tool(annotations=ToolAnnotations(title="Trait info", **_READ_ONLY))
    def trait_info(efo_id: str) -> TraitInfo:
        """Fetch a trait by EFO ID (e.g. 'EFO_0001645') with its associated PGS IDs."""
        try:
            with client.make_rest_client() as rest:
                return rest.get_trait(efo_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Trait lookup failed: {exc}") from exc
