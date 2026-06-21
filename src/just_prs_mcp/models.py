"""Pydantic models used as structured tool inputs/outputs.

Returning Pydantic models from tools gives clients a typed output schema
(``result.data`` on the client side) instead of opaque text.

Where ``just-prs`` already exposes a complete, JSON-serializable model we reuse
it directly (``PRSResult``, ``AbsoluteRisk``, and the REST ``ScoreInfo`` /
``TraitInfo``) rather than re-deriving it. The models defined here cover the
cases just-prs returns as polars frames / dicts, which need summarizing into a
fixed schema for MCP.
"""

from __future__ import annotations

# Re-exported just-prs models — used directly as tool return types.
from just_prs.models import (  # noqa: F401
    AbsoluteRisk,
    PRSResult,
    ScoreInfo,
    TraitInfo,
)
from pydantic import BaseModel, Field


class OpResult(BaseModel):
    """Generic success/failure envelope for fallible tools.

    Tools return this (with ``success=False``) instead of raising, so an agent
    gets an actionable message rather than a protocol-level error.
    """

    success: bool = Field(description="Whether the operation succeeded.")
    message: str = Field(description="Human-readable summary or error.")
    data: dict | None = Field(default=None, description="Optional payload.")


class ScoreSummary(BaseModel):
    """A row from the cleaned PGS Catalog scores metadata."""

    pgs_id: str = Field(description="PGS Catalog Score ID, e.g. 'PGS000001'.")
    name: str | None = Field(default=None, description="Score name.")
    trait_reported: str | None = Field(default=None, description="Reported trait.")
    trait_efo: str | None = Field(default=None, description="EFO trait label(s).")
    genome_build: str | None = Field(default=None, description="Original genome build.")
    variants_number: int | None = Field(default=None, description="Number of variants.")
    weight_type: str | None = Field(default=None, description="Weight type (beta/OR/HR).")
    is_harmonized: bool | None = Field(
        default=None,
        description="True when the score's native build differs from the queried build.",
    )
    quality_label: str | None = Field(
        default=None, description="Synthetic quality label, when available."
    )


class PerformanceSummary(BaseModel):
    """Best evaluation performance for a score (largest sample, EUR-preferred)."""

    pgs_id: str = Field(description="PGS Catalog Score ID.")
    found: bool = Field(description="Whether a performance row was found.")
    n_individuals: int | None = Field(default=None, description="Evaluation sample size.")
    ancestry_broad: str | None = Field(default=None, description="Evaluation cohort ancestry.")
    or_estimate: float | None = Field(default=None, description="Odds ratio per SD.")
    hr_estimate: float | None = Field(default=None, description="Hazard ratio per SD.")
    beta_estimate: float | None = Field(default=None, description="Beta per SD.")
    auroc_estimate: float | None = Field(default=None, description="AUROC.")
    cindex_estimate: float | None = Field(default=None, description="Harrell's C-index.")
    effect_size: str = Field(
        default="", description="Formatted effect size, e.g. 'OR=1.55 [1.52-1.58]'."
    )
    classification: str = Field(
        default="", description="Formatted classification, e.g. 'AUROC=0.72'."
    )


class NormalizeResult(BaseModel):
    """Outcome of a VCF / array normalization."""

    output_path: str = Field(description="Path to the normalized Parquet file.")
    n_variants: int = Field(description="Number of variant rows written.")
    genome_build: str | None = Field(
        default=None,
        description="Effective genome build assumed for downstream scoring.",
    )
    message: str = Field(description="Human-readable summary.")


class PercentileResult(BaseModel):
    """Estimated population percentile for a PRS score."""

    pgs_id: str = Field(description="PGS Catalog Score ID.")
    prs_score: float = Field(description="The PRS value that was scored.")
    percentile: float | None = Field(
        default=None, description="Estimated percentile (0-100), or null if unavailable."
    )
    method: str = Field(
        description="'reference_panel', 'theoretical', 'auroc_approx', or 'unavailable'."
    )
    ancestry: str = Field(description="1000G superpopulation used (AFR/AMR/EAS/EUR/SAS).")
    reliable: bool = Field(
        default=True,
        description="False when the percentile should be treated as caveated or unreliable.",
    )
    caveat: str | None = Field(
        default=None,
        description="Human-readable warning explaining why the percentile is caveated.",
    )


class TraitSummary(BaseModel):
    """Compact trait search result for keeping search payloads small."""

    id: str = Field(description="Trait ontology ID, e.g. 'EFO_0001645' or 'MONDO_0005148'.")
    label: str = Field(description="Trait label.")
    description: str | None = Field(default=None, description="Trait description, when available.")
    trait_categories: list[str] = Field(
        default_factory=list, description="PGS Catalog trait category labels."
    )
    trait_synonyms: list[str] = Field(default_factory=list, description="Known trait synonyms.")
    n_associated: int = Field(description="Number of directly associated PGS IDs.")
    n_child_associated: int = Field(description="Number of PGS IDs associated via child traits.")


class TraitScoreRow(BaseModel):
    """One score row in a trait-level PRS report."""

    pgs_id: str = Field(description="PGS Catalog Score ID.")
    status: str = Field(description="'scored' or 'failed'.")
    score: float | None = Field(default=None, description="Computed PRS value.")
    variants_matched: int | None = Field(default=None, description="Matched scoring variants.")
    variants_total: int | None = Field(default=None, description="Total scoring variants.")
    match_rate: float | None = Field(default=None, description="Matched / total scoring variants.")
    percentile: float | None = Field(default=None, description="Optional percentile estimate.")
    percentile_method: str | None = Field(default=None, description="Method used for percentile.")
    percentile_reliable: bool | None = Field(
        default=None, description="Reliability flag from percentile estimation."
    )
    percentile_caveat: str | None = Field(
        default=None, description="Warning attached to the percentile estimate."
    )
    quality_label: str | None = Field(default=None, description="Optional quality label.")
    quality_summary: str | None = Field(default=None, description="Optional quality summary.")
    effect_size: str | None = Field(
        default=None, description="Formatted best performance effect size."
    )
    auroc_estimate: float | None = Field(default=None, description="Best available AUROC estimate.")
    error: str | None = Field(default=None, description="Error for failed score rows.")


class TraitPRSReport(BaseModel):
    """Aggregate result for computing many PRS scores associated with one trait."""

    trait_id: str = Field(description="Trait ontology ID used for lookup.")
    label: str = Field(description="Trait label.")
    genome_build: str = Field(description="Effective genome build used for scoring.")
    n_requested: int = Field(description="Number of PGS IDs selected for scoring.")
    n_scored: int = Field(description="Number of scores computed successfully.")
    n_failed: int = Field(description="Number of scores that failed.")
    n_skipped: int = Field(description="Number of associated PGS IDs skipped by the limit.")
    rows: list[TraitScoreRow] = Field(description="Per-score results.")
    summary: str = Field(description="Human-readable summary.")


class QualityAssessment(BaseModel):
    """Quality classification + interpretation for a PRS result (pure logic, no I/O)."""

    quality_label: str = Field(description="High / Moderate / Low / Very Low.")
    quality_color: str = Field(description="Semantic color token for the label.")
    summary: str = Field(description="Human-readable interpretation.")


class DistributionRow(BaseModel):
    """Per-superpopulation PRS distribution statistics."""

    pgs_id: str | None = Field(default=None, description="PGS Catalog Score ID.")
    superpopulation: str = Field(description="1000G superpopulation (AFR/AMR/EAS/EUR/SAS).")
    mean: float = Field(description="Mean PRS in this group.")
    std: float = Field(description="Standard deviation of PRS in this group.")
    n: int = Field(description="Number of reference individuals.")
    median: float | None = Field(default=None)
    p5: float | None = Field(default=None)
    p25: float | None = Field(default=None)
    p75: float | None = Field(default=None)
    p95: float | None = Field(default=None)


class ReferenceScoreSummary(BaseModel):
    """Result of scoring a single PGS against a reference / pgen panel."""

    pgs_id: str = Field(description="PGS Catalog Score ID.")
    panel: str = Field(description="Reference panel or pgen directory identifier.")
    n_samples: int = Field(description="Number of scored samples.")
    distributions: list[DistributionRow] = Field(
        default_factory=list, description="Per-superpopulation distribution stats."
    )


class BatchScoringSummary(BaseModel):
    """Summary of a batch reference-scoring run (DataFrame fields omitted)."""

    panel: str = Field(description="Reference panel identifier.")
    n_requested: int = Field(description="PGS IDs requested.")
    n_scored: int = Field(description="PGS IDs successfully scored.")
    n_failed: int = Field(description="PGS IDs that failed.")
    outcomes: list[dict] = Field(
        default_factory=list, description="Per-ID {pgs_id, status, error} records."
    )
    distributions: list[DistributionRow] = Field(
        default_factory=list, description="Aggregated per-superpopulation distributions."
    )
