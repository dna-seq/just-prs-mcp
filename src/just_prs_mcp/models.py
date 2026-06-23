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
    AbsoluteRiskBundle,
    AbsoluteRiskEstimate,
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
    reused_cache: bool = Field(
        default=False,
        description="True if an existing Parquet was reused instead of re-normalizing.",
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
    ancestry: str = Field(description="Requested 1000G superpopulation (AFR/AMR/EAS/EUR/SAS).")
    reliable: bool = Field(
        default=True,
        description="False when the percentile should be treated as caveated or unreliable.",
    )
    caveat: str | None = Field(
        default=None,
        description="Human-readable warning explaining why the percentile is caveated.",
    )
    z_score: float | None = Field(
        default=None,
        description="True z-score ((score − reference_mean)/reference_std) used for this "
        "percentile — feed directly to absolute_risk instead of inverting the percentile.",
    )
    reference_mean: float | None = Field(
        default=None, description="Reference-distribution mean used, when known."
    )
    reference_std: float | None = Field(
        default=None, description="Reference-distribution SD used, when known."
    )
    reference_panel_ancestry: str | None = Field(
        default=None,
        description="Superpopulation of the reference panel actually used (reference_panel "
        "method only) — check it matches the sample's ancestry before trusting the percentile.",
    )
    reference_panel: str | None = Field(
        default=None, description="Reference panel identifier used (reference_panel method only)."
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
    weight_mass_coverage: float | None = Field(
        default=None,
        description="Fraction of the score's effect-weight mass (Σ|β|) matched in this genome "
        "(C_wt) — the scale-free coverage signal; rank/gate on this, not match_rate.",
    )
    percentile: float | None = Field(default=None, description="Optional percentile estimate.")
    percentile_method: str | None = Field(default=None, description="Method used for percentile.")
    percentile_reliable: bool | None = Field(
        default=None, description="Reliability flag from percentile estimation."
    )
    percentile_caveat: str | None = Field(
        default=None, description="Warning attached to the percentile estimate."
    )
    reference_panel_ancestry: str | None = Field(
        default=None,
        description="Ancestry of the reference panel used for this score's percentile — flag "
        "when it disagrees with the sample's ancestry (reference_panel method only).",
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
    detected_genome_build: str | None = Field(
        default=None,
        description="Genome build detected from the VCF (contigs/##reference), or null if "
        "undetectable (e.g. pre-normalized input).",
    )
    build_mismatch: bool = Field(
        default=False,
        description="True when the detected VCF build disagrees with the scoring build — "
        "treat coverage/percentiles as unreliable until resolved.",
    )
    n_requested: int = Field(description="Number of PGS IDs selected for scoring.")
    n_scored: int = Field(description="Number of scores computed successfully.")
    n_failed: int = Field(description="Number of scores that failed.")
    n_skipped: int = Field(description="Number of associated PGS IDs skipped by the limit.")
    n_reliable: int = Field(
        default=0,
        description="Scores whose percentile passed the reliability check (≥90% coverage).",
    )
    mean_match_rate: float | None = Field(
        default=None, description="Mean scoring-variant match rate across computed scores."
    )
    n_returned: int = Field(
        default=0, description="Number of per-score rows included in this response."
    )
    n_omitted: int = Field(
        default=0,
        description="Rows computed but trimmed from the response by ``top_n`` "
        "(trait-level counts still reflect all scores).",
    )
    rows: list[TraitScoreRow] = Field(
        description="Per-score results (ranked best-coverage first; trimmed to ``top_n`` when set)."
    )
    summary: str = Field(description="Human-readable summary.")
    genome_label: str | None = Field(
        default=None,
        description="Label identifying which genome was scored (e.g. 'anton', 'livia', or a "
        "filename stem). Set automatically when the result is saved to disk.",
    )
    result_path: str | None = Field(
        default=None,
        description="Path where this result was auto-saved as JSON. Pass to ``compare_genomes`` "
        "to build a cross-genome comparison without re-serializing the full report.",
    )


class PrevalenceRow(BaseModel):
    """One population-prevalence prior row used for absolute-risk estimation."""

    efo_id: str | None = Field(default=None, description="EFO trait ID this prior is keyed on.")
    trait_label: str | None = Field(default=None, description="Human-readable trait label.")
    prevalence: float | None = Field(
        default=None, description="Population prevalence (fraction, 0-1) — the prior."
    )
    prevalence_lower: float | None = Field(default=None, description="Lower bound, if known.")
    prevalence_upper: float | None = Field(default=None, description="Upper bound, if known.")
    prevalence_type: str | None = Field(
        default=None, description="e.g. 'lifetime', 'point', 'period'."
    )
    sex: str | None = Field(default=None, description="Sex the prior applies to, if specific.")
    ancestry: str | None = Field(default=None, description="Ancestry the prior applies to.")
    age_range: str | None = Field(default=None, description="Age range the prior applies to.")
    source: str | None = Field(default=None, description="Provenance tier / source.")
    source_detail: str | None = Field(default=None, description="Citation or source detail.")
    xref_mondo: str | None = Field(default=None, description="Cross-referenced MONDO ID.")
    xref_icd10: str | None = Field(default=None, description="Cross-referenced ICD-10 code.")
    confidence: str | None = Field(default=None, description="high / moderate / low.")


class PrevalenceInfo(BaseModel):
    """Prevalence priors just-prs would apply for a score or trait."""

    query: str = Field(description="The pgs_id or trait_id that was looked up.")
    resolved_efo_ids: list[str] = Field(
        default_factory=list,
        description="EFO trait IDs the query resolved to (after alias expansion).",
    )
    n_matches: int = Field(description="Number of matching prevalence rows.")
    rows: list[PrevalenceRow] = Field(
        default_factory=list, description="Matching prevalence prior rows."
    )
    message: str = Field(description="Human-readable summary.")


class GenomeEntry(BaseModel):
    """One genome file discovered in the cache directory."""

    filename: str = Field(description="File name (e.g. 'antonkulaga.vcf').")
    path: str = Field(description="Absolute path on the server filesystem.")
    size_bytes: int = Field(description="File size in bytes.")
    stage: str = Field(
        description="'downloaded' (raw VCF in samples/) or 'normalized' (Parquet in normalized/)."
    )
    sample_alias: str | None = Field(
        default=None,
        description="Pre-configured sample alias ('anton', 'livia') if this file matches one, "
        "else null.",
    )


class GenomeCatalog(BaseModel):
    """Inventory of genomes present in the server's cache directory."""

    cache_dir: str = Field(description="Root cache directory path.")
    downloaded: list[GenomeEntry] = Field(
        default_factory=list,
        description="Raw VCF files in <cache_dir>/samples/.",
    )
    normalized: list[GenomeEntry] = Field(
        default_factory=list,
        description="Normalized Parquet files in <cache_dir>/normalized/.",
    )
    available_samples: list[dict] = Field(
        default_factory=list,
        description="Pre-configured sample genomes that can be downloaded via "
        "download_sample_genome (name, description, size, license).",
    )
    message: str = Field(description="Human-readable summary.")


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


# ---------------------------------------------------------------------------
# Cross-genome comparison models
# ---------------------------------------------------------------------------


class GenomeRanking(BaseModel):
    """One genome's position in a per-trait ranking."""

    genome_label: str = Field(description="Label identifying the genome (e.g. 'anton').")
    best_pgs_id: str | None = Field(
        default=None, description="PGS ID of the highest-coverage reliable model used."
    )
    percentile: float | None = Field(
        default=None, description="Best-model percentile (0-100), or null if none reliable."
    )
    n_models_scored: int = Field(description="Total models scored for this genome × trait.")
    n_reliable: int = Field(description="Models with reliable percentiles.")
    rank: int = Field(
        description="1-based rank among compared genomes, sorted HIGH percentile first. "
        "No directionality judgment — the LLM decides whether high is good or bad for this trait."
    )


class TraitComparison(BaseModel):
    """Cross-genome comparison for a single trait."""

    trait_id: str = Field(description="Trait ontology ID.")
    label: str = Field(description="Trait label.")
    rankings: list[GenomeRanking] = Field(
        description="Genomes ranked by best-model percentile, highest first."
    )
    percentile_spread: float | None = Field(
        default=None,
        description="Max minus min best-model percentile across genomes — magnitude of divergence.",
    )
    model_consistency: str = Field(
        description="'consistent' if all genomes' reliable models agree on rank order, "
        "'mixed' otherwise."
    )


class GenomeComparison(BaseModel):
    """Structured comparison of PRS results across multiple genomes."""

    genome_labels: list[str] = Field(description="Labels of the compared genomes, in input order.")
    n_traits: int = Field(description="Number of traits compared.")
    traits: list[TraitComparison] = Field(description="Per-trait comparison details.")
    most_divergent_traits: list[str] = Field(
        description="Trait labels sorted by percentile_spread descending — traits where the "
        "genomes differ most, for the LLM to highlight."
    )
    summary: str = Field(description="Human-readable summary of the comparison.")
