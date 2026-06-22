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

**Status: addressed in `just-prs` (2026-06-22, `scoring-foundations` branch).**
`PRSResult` now carries weight-mass coverage `C_wt` (`weight_mass_coverage`), and
`PRSCatalog.percentile_full()` attaches a `reliable` flag + `caveat` driven by `C_wt`:
below `MIN_RELIABLE_WEIGHT_MASS_COVERAGE` (0.20) the percentile is kept but flagged
not-reliable, so a deflated low-coverage score no longer emits a bare authoritative
0/100. The existing count-based `MIN_PERCENTILE_MATCH_RATE` gate is unchanged (the
C_wt reliability verdict is additive). Full coverage *recovery* (FASTA reference-allele
resolution) remains deferred to the `refcall-resolution` branch.

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

**Status: addressed in `just-prs` (2026-06-22).** `interpret_prs_result` now accepts
`percentile_method`, `reliable`, and `caveat`. It describes how the percentile was
derived (reference panel / theoretical / AUROC-approx) instead of always attributing
it to scoring-file allele frequencies, and the percentile-unavailable message is now
generic rather than blaming missing allele frequencies.

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

**Status: addressed in `just-prs` (2026-06-22).** `PRSCatalog.compute_prs` and
`compute_prs_batch` now accept `attach_performance=True` to populate
`PRSResult.performance` from `best_performance()`. `format_effect_size` /
`format_classification` return `None` (not `""`) when no estimate exists, so callers
can distinguish unavailable from empty.

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

**Status: addressed in `just-prs` (2026-06-22).** `PRSCatalog.percentile_full()`
returns a `PercentileResult` exposing the **true** `z_score`, `reference_mean`, and
`reference_std` (instead of discarding them), and `PRSResult` now carries `z_score` /
`reference_mean` / `reference_std` for the theoretical path. A new
`PRSCatalog.absolute_risk_from_score(pgs_id, score, ...)` chains raw score → true z →
`absolute_risk_bundle`. `enrich.py` now feeds the true z into absolute risk instead of
inverting the percentile (which collapsed to z=0 at the 0/100 extremes).

`absolute_risk` requires a z-score, but `compute_prs` returns a raw score and
`percentile` does not expose the z-score or reference mean/std it used.

Wrapper mitigation: no reliable wrapper-only z-score can be produced without the
library exposing reference distribution parameters. The *prevalence prior* side of
absolute risk is now fully exposed in extended mode — `prevalence_info` surfaces
just-prs's prevalence table for a score/trait (value, bounds, type, scope, source,
confidence) without needing a z-score, and `absolute_risk_bundle` returns every
method's estimate with the prior it used. The remaining gap is purely the
raw-score → z-score step.

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
- **Leading lead — see F22:** a gVCF test points at unexpanded hom-ref reference
  blocks. The missing ~50% are likely positions where the sample is confidently
  hom-ref but the normalizer never emits a dose-0 genotype for them.

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

## F22 — gVCF `END` reference blocks are not expanded; gVCF input gives no coverage benefit — leading F15 lead

Status: **tested (2026-06-22).** Re-ran two genome-wide T2D scores against the
sample's **gVCF** (`newton_winter.g.vcf`, DeepVariant, BGZF) instead of its VCF:

| score | variants | VCF match_rate | gVCF match_rate | Δ |
|-------|----------|----------------|-----------------|---|
| PGS000014 | 6,917,436 | 0.3744 | 0.3795 | +0.5 pp |
| PGS003114 |   555,528 | ~0.490 | 0.4966 | +0.6 pp |

`normalize_vcf` on the gVCF produced **21,070,113** rows vs 6.14M (unfiltered VCF),
yet match_rate barely moved. The reason: the gVCF's 21M records are `END`-spanning
**reference blocks** (sampled 2M records cover 277 Mbp → ~139 bp/record, 33% span
>50 bp; the full set blankets ~2.9 Gbp of confident hom-ref). **Normalization keeps
one row per record at the block's start `POS` and does not expand the `END` span**,
so a scoring variant landing *inside* a ref block — confidently hom-ref in this
sample — is still counted "unmatched". Only variants hitting an exact block-start
position were recovered (the +0.5 pp).

Why this matters for F15: "scoring variant is confidently hom-ref but sits inside
an unexpanded reference block" is now the leading explanation for the ~50% gap, and
it is **recoverable** — the gVCF already carries the dose-0 information. It also
means feeding a gVCF today confers essentially no benefit over a plain VCF, which
is the surprising part.

Library fix (upstream `just-prs` normalize/VCF reader): when input is a gVCF,
expand `END`-spanned reference blocks into per-position dose-0 genotypes (or make
`compute_prs` treat a scoring position falling inside a confident ref block as
dose-0), gated on the block's GT being `0/0` and its `MIN_DP`/`GQ` clearing a
threshold. Not-yet-confirmed: the unmatched remainder could still include
allele-mismatch and genuinely-uncovered positions — the definitive test is to
expand the blocks and re-score (expect coverage to jump toward ~100% for
genome-wide scores if this is the dominant cause).

Wrapper note: until upstream lands, document that **gVCF input is not yet
advantageous** — `normalize_vcf` treats it like a VCF and silently yields the same
low coverage rather than the near-complete coverage a gVCF should enable.

## F16 — `get_trait` associated-ID count vs `compute_prs_by_trait` denominator (minor / deferred)

`trait_info(MONDO_0005148)` returned ~195 `associated_pgs_ids`, but
`compute_prs_by_trait` (no children) scored **220**. The two counts come from
different retrieval paths; reconcile so the trait's directly-associated score
count is consistent across tools. Low priority — defer until the above land.

## F18 — Genome build of origin does NOT predict coverage — RULED OUT as the F15 driver

Status: **ruled out by sampling (2026-06-21).** Tested the hypothesis that the
~50% genome-wide coverage (F15) is liftover/harmonization loss on GRCh37→GRCh38
uplifts. Pulled `genome_build` for 18 T2D scores spanning the coverage range:
15 GRCh37, 2 GRCh38, 1 "NR". The two **GRCh38-native** scores (PGS004840, PGS005032)
landed at match_rate **0.37** and **0.47** — squarely inside the GRCh37 cloud
(0.25–0.57), not above it. Build does not separate high- from low-coverage scores.

Consequence: a "prefer GRCh38, drop GRCh37 uplifts" filter is the wrong primary
lever — it would discard ~90% of this panel (incl. the only reliable score and
every high-AUROC model) without improving coverage. Like F13/RefCall, this is a
ruled-out cause for F15; the real driver remains the variant-matching/harmonization
audit in F15.

## F19 — Ancestry is never surfaced or checked (development ancestry + reference-panel ancestry) — PRIORITY

The single largest interpretability gap. Many PGS Catalog T2D scores are developed
for non-EUR ancestries (e.g. `DPRISM_…trainedforAFR`, PGS005334 = EAS), yet
neither a score's **development ancestry** nor the **percentile reference panel's
ancestry** is exposed by `score_info`, `compute_prs`, or the trait report.
Scoring a EUR-ancestry sample against an EAS-trained model *and* a EUR reference
panel is triply incoherent — this ancestry mismatch is a primary reason
percentiles for the same person on the same trait span the full 0→100 range
(arguably more than coverage; see F15).

Library fix: surface per-score development/evaluation ancestry (it's in the PGS
Catalog `Ancestry Distribution` metadata) and the reference-panel ancestry used
for a percentile; ideally warn when score-development, sample, and panel
ancestries disagree.

Wrapper mitigation (partial, wrapper-actionable): the percentile path already
knows which panel it used — surface that panel ancestry and flag obvious
mismatches; expose ancestry as a `TraitScoreRow` column once the library returns
it (tracked as a report column in dogfooding F21).

## F20 — Coverage reliability gate is absolute and perversely selects trivially small scores — extends F9/F15

**Status: partially addressed in `just-prs` (2026-06-22).** Weight-mass coverage
`C_wt` (`weight_mass_coverage` on `PRSResult`) is now computed and is scale-free: a
3-SNP score that misses one variant craters `C_wt` while a genome-wide score near its
achievable weight-mass ceiling scores well — directly countering the count-based
inversion. `percentile_full` keys its reliability verdict on `C_wt`. Not yet done:
folding `C_wt` into a single composite per-(score×genome) quality `Q` and dropping the
count-based gate entirely (deferred to the posterior-Q work, P4).

The `percentile_reliable` ≥90%-match gate (F9) passes only tiny scores: in the
T2D panel **all 11** scores with ≥70% coverage have 3–30 variants and **no AUROC**
(the lone "reliable" PGS000856 is a **3-SNP** score). Every genome-wide model with
real predictive power (AUROC 0.73–0.84) sits at ~50% coverage and is flagged
unreliable. So a flat coverage gate inverts quality: high coverage ⟺ trivially
small score ⟺ no predictive validity.

Library/wrapper fix: judge coverage **relative to score type/size** (a genome-wide
score near its achievable ceiling is more trustworthy than a 3-SNP score at 100%),
or add a distinct "high coverage but low information" flag so a near-null toy score
isn't reported as the one trustworthy result. Pairs with the F15 coverage-normalized
scoring.
