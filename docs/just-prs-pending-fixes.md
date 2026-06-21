# Pending just-prs fixes from MCP dogfooding

These findings came from `docs/dogfooding.md`. They need changes in the
underlying `just-prs` library or real-data verification before the MCP wrapper can
fully resolve them. The wrapper now includes defensive mitigations where possible.

## F2 — Trait search should be tokenized/fuzzy

`PGSCatalogClient.search_traits("diabetes mellitus type 2")` misses
`MONDO_0005148` because the REST search is exact-substring and the synonym is
punctuated as `diabetes mellitus, type 2`.

Wrapper mitigation: `search_traits` retries normalized/order-adjusted terms and
filters broad fallback matches by tokens.

Library fix: make the REST/client trait search token-aware or fuzzy enough that
punctuation and word order are not load-bearing. Ideally return did-you-mean
candidates when no exact substring matches.

## F4 — Genome build inference and echo-back

The WGS VCF had no `##reference` header; GRCh38 was only inferable from contig
lengths. `just-prs` currently relies on the caller/config default, and a build
mismatch surfaces indirectly as poor match rate.

Wrapper mitigation: normalization and trait reports now echo the effective
`genome_build` assumed by the MCP tool.

Library fix: infer genome build from contig metadata where possible, include the
effective build in `PRSResult`, and warn when the inferred VCF build disagrees
with the scoring build.

## F9 — Low-coverage percentiles need normalization or reliability metadata

A PRS with `match_rate=0.374` returned `percentile=0` from a reference-panel
comparison. The raw score is deflated when unmatched loci are treated as zero, so
the percentile can become an artifact of coverage rather than biology.

Wrapper mitigation: the MCP `percentile` tool accepts `match_rate` and returns
`reliable=false` plus a caveat for low match rates or bare extreme percentiles.

Library fix: either normalize raw scores by scoring-variant coverage before
reference-panel comparison, or return explicit reliability/caveat metadata from
`PRSCatalog.percentile`. Avoid emitting authoritative `0`/`100` percentiles for
low-coverage scores.

## F10 — Quality interpretation should know percentile method availability

`interpret_prs_result` can say a percentile is unavailable because allele
frequencies are missing, while `PRSCatalog.percentile` returns a reference-panel
percentile for the same score.

Wrapper mitigation: the MCP quality path avoids treating percentile availability
as authoritative when a percentile was supplied separately; the percentile tool is
the source of reliability/caveat messaging.

Library fix: update `interpret_prs_result` to accept the percentile method and
reliability/caveat state, or narrow it to quality classification only and leave
availability statements to percentile computation.

## F11 — PRS computation should attach best performance when available

`compute_prs(PGS000014)` returned `performance=null`, while
`best_performance(PGS000014)` found AUROC data. Users currently need a separate
call and manual handoff into quality assessment.

Wrapper mitigation: trait-level reports optionally fetch best performance per
score when `interpret=true`.

Library fix: optionally embed best-performance metadata in `PRSResult`, or expose
a convenience path that returns score, percentile, performance, and quality as one
composed result. Also prefer `null` over an empty `effect_size` string when no
effect-size estimate exists.

## F12 — Absolute risk needs a raw-score to z-score path

`absolute_risk` requires a z-score, but `compute_prs` returns a raw score and
`percentile` does not expose the z-score or reference mean/std it used.

Wrapper mitigation: no reliable wrapper-only z-score can be produced without the
library exposing reference distribution parameters.

Library fix: return the derived `z_score` plus reference mean/std from
`percentile`, or add `absolute_risk_from_score` that chains raw PRS to z-score to
absolute risk.

## F13 — RefCall/hom-ref handling — TESTED & RULED OUT

Status: **resolved by testing (2026-06-21).** Re-normalized the dogfooding VCF
**without** `pass_filters` (6,139,024 variants retained vs 4,725,262 with PASS)
and re-scored `PGS000014`: match_rate **0.3768** (unfiltered) vs **0.3744**
(PASS) — effectively identical (2,606,490 vs 2,590,167 matched). Dropping the
~1.41M `RefCall`/hom-ref records is **not** the cause of low coverage. No
library change needed for this hypothesis; the percentile reliability flag (F9)
is the relevant safeguard. The real driver of low coverage is F15.

## F15 — Genome-wide scores only overlap ~50% of a full WGS callset (root cause of low coverage) — PRIORITY

This is the dominant unresolved upstream issue and the reason the dogfooding
trait report is non-interpretable.

Observed (full 220-score T2D panel, `compute_prs_by_trait`, GRCh38, WGS sample):
- Mean variant coverage across 220 scores is **48%**; the 117 genome-wide scores
  (>500k variants) matched only ~50% of their variants each.
- Only **1 of 220** scores reached `percentile_reliable=true` (≥90% coverage).
- Percentiles for the *same sample on the same trait* span the full **0→100**
  range across genome-wide scores (e.g. PGS000807 AUROC 0.84 → 0th pct;
  PGS005334 AUROC 0.81 → 100th pct). Classic coverage artifact.
- Ruled out: PASS/RefCall filtering (F13) and genome build (GRCh38 confirmed from
  contig lengths and passed explicitly).

Likely upstream causes to investigate in `just-prs`: allele/strand harmonization
of scoring files against the genotype source, REF/ALT or multiallelic matching,
rsID-vs-position join logic, and whether unmatched loci are imputed/handled vs
silently treated as zero dose. A ~50% miss on a full WGS callset points to a
systematic matching/harmonization gap, not sample quality.

Library fix: audit the variant-matching path in PRS computation for genome-wide
scores; confirm whether the harmonized scoring file is being keyed correctly
against the normalized genotypes; surface a per-score breakdown of *why* variants
were unmatched (position miss vs allele mismatch vs filtered) so coverage gaps are
diagnosable rather than opaque.

## F16 — `get_trait` associated-ID count vs `compute_prs_by_trait` denominator (minor / deferred)

`trait_info(MONDO_0005148)` returned ~195 `associated_pgs_ids`, but
`compute_prs_by_trait` (no children) scored **220**. The two counts come from
different retrieval paths; reconcile so the trait's directly-associated score
count is consistent across tools. Low priority — defer until the above land.
