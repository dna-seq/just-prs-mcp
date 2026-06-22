"""ESSENTIALS — PGS Catalog lookup tools (read-only, no local files needed).

These wrap ``PRSCatalog`` (cleaned bulk metadata, no per-call REST traffic) and
the ``PGSCatalogClient`` REST client (for trait search). They are registered in
every mode: a small, always-on surface for discovering scores and traits.

First use triggers just-prs's 3-tier metadata fetch (local cache -> HuggingFace
-> EBI FTP); subsequent calls are served from the on-disk cache. Catalog
unavailability surfaces as a ``ToolError`` with the underlying reason.
"""

from __future__ import annotations

import re

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from just_prs_mcp import client
from just_prs_mcp.logging_setup import get_logger
from just_prs_mcp.models import (
    PerformanceSummary,
    ScoreSummary,
    TraitInfo,
    TraitSummary,
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


def _trait_text(value: TraitInfo) -> str:
    parts = [value.id, value.label, value.description or "", *value.trait_synonyms]
    return " ".join(parts).lower()


def _normalized_tokens(term: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", term.lower()) if token]


def _trait_matches_tokens(value: TraitInfo, tokens: list[str]) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", _trait_text(value))
    return all(token in text for token in tokens)


def _trait_summary(value: TraitInfo) -> TraitSummary:
    return TraitSummary(
        id=value.id,
        label=value.label or value.id,
        description=value.description,
        trait_categories=value.trait_categories,
        trait_synonyms=value.trait_synonyms,
        n_associated=len(value.associated_pgs_ids),
        n_child_associated=len(value.child_associated_pgs_ids),
    )


def _trait_search_terms(term: str) -> list[str]:
    tokens = _normalized_tokens(term)
    candidates = [term.strip()]
    type_match = re.search(r"\btype\s+(\d+)\b", term, flags=re.IGNORECASE)
    if type_match:
        type_text = type_match.group(0).lower()
        without_type = re.sub(r"\btype\s+\d+\b", "", term, flags=re.IGNORECASE).strip(" ,")
        if without_type:
            candidates.append(f"{without_type}, {type_text}")
            candidates.append(f"{type_text} {without_type}")
    if len(tokens) > 1:
        candidates.extend(tokens)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _search_traits_forgiving(rest, term: str, limit: int) -> list[TraitInfo]:
    results = rest.search_traits(term, limit=limit)
    if results:
        return results

    tokens = _normalized_tokens(term)
    seen: dict[str, TraitInfo] = {}
    for candidate in _trait_search_terms(term)[1:]:
        candidate_results = rest.search_traits(candidate, limit=max(limit, 100))
        for value in candidate_results:
            if not tokens or _trait_matches_tokens(value, tokens):
                seen[value.id] = value
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]


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
            effect_size=format_effect_size(row) or "",
            classification=format_classification(row) or "",
        )

    @mcp.tool(annotations=ToolAnnotations(title="Search traits", **_READ_ONLY))
    def search_traits(
        term: str,
        limit: int = 25,
        include_pgs_ids: bool = False,
    ) -> list[TraitSummary] | list[TraitInfo]:
        """Search the PGS Catalog REST API for traits by term.

        Upstream matching is exact-substring over labels and synonyms, so this
        wrapper retries a few punctuation/order variants when the first query is
        empty. By default, results include counts of directly associated PGS IDs
        and child-trait PGS IDs; set ``include_pgs_ids`` for the full arrays.
        """
        try:
            with client.make_rest_client() as rest:
                results = _search_traits_forgiving(rest, term, limit)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Trait search failed: {exc}") from exc
        if include_pgs_ids:
            return results
        return [_trait_summary(value) for value in results]

    @mcp.tool(annotations=ToolAnnotations(title="Trait info", **_READ_ONLY))
    def trait_info(trait_id: str | None = None, efo_id: str | None = None) -> TraitInfo:
        """Fetch a trait by ontology ID (EFO or MONDO) with its associated PGS IDs."""
        resolved_id = trait_id or efo_id
        if resolved_id is None:
            raise ToolError("Provide trait_id (preferred) or efo_id.")
        try:
            with client.make_rest_client() as rest:
                return rest.get_trait(resolved_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Trait lookup failed: {exc}") from exc
