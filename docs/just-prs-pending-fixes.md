# Pending just-prs fixes from MCP dogfooding

These findings came from `docs/dogfooding.md`. They need changes in the
underlying `just-prs` library or real-data verification before the MCP wrapper can
fully resolve them. The wrapper now includes defensive mitigations where possible.

> **2026-06-22 upstream sweep.** `just-prs` **0.4.8** was published and pinned. Its
> RESOLVED foundations items — **F9** (reliability metadata), **F10**, **F11**, **F12**
> — were verified against the installed wheel and **moved to `docs/previous_issues.md`**.
> What remains below is genuinely still pending/deferred upstream. F9's coverage
> *normalization* remainder is tracked under **F15**; F20 keys on the now-shipped C_wt
> metric but its composite-quality gate is still future work.
>
> **2026-06-22 Batch 2 — now released in `just-prs` 0.4.9 and pinned.** **F4**
> (genome-build detection + `build_mismatch`) and **F23** (`genotypes_lf` on
> `PRSCatalog.compute_prs`) were verified against the 0.4.9 wheel, adopted in the wrapper,
> and **moved to `docs/previous_issues.md`**. **F19** is **PARTIALLY RESOLVED**: the
> percentile reference-panel ancestry now ships and the wrapper surfaces it (see below) —
> the entry stays here because per-score development ancestry, sample-ancestry inference,
> and the coherence veto are still deferred (P3).

## F2 — Trait search should be tokenized/fuzzy

**Status: PENDING (not addressed in `just-prs` `main`).** No library change yet; the
wrapper mitigation below is the only thing in play. Lives in the REST client, outside
the scoring-foundations scope.

`PGSCatalogClient.search_traits("diabetes mellitus type 2")` misses
`MONDO_0005148` because the REST search is exact-substring and the synonym is
punctuated as `diabetes mellitus, type 2`.

Wrapper mitigation: `search_traits` retries normalized/order-adjusted terms and
filters broad fallback matches by tokens.

Library fix: make the REST/client trait search token-aware or fuzzy enough that
punctuation and word order are not load-bearing. Ideally return did-you-mean
candidates when no exact substring matches.

## F13 — RefCall/hom-ref handling — TESTED & RULED OUT

Status: **resolved by testing (2026-06-21).** Re-normalized the dogfooding VCF
**without** `pass_filters` (6,139,024 variants retained vs 4,725,262 with PASS)
and re-scored `PGS000014`: match_rate **0.3768** (unfiltered) vs **0.3744**
(PASS) — effectively identical (2,606,490 vs 2,590,167 matched). Dropping the
~1.41M `RefCall`/hom-ref records is **not** the cause of low coverage. No
library change needed for this hypothesis; the percentile reliability flag (F9)
is the relevant safeguard. The real driver of low coverage is F15.

## F15 — Genome-wide scores only overlap ~50% of a full WGS callset (root cause of low coverage) — PRIORITY

**Status: DEFERRED — planned on the `just-prs` `refcall-resolution` branch, not yet
implemented.** The foundations round made the artifact *measurable and visible* (the new
`C_wt` and `variants_unscorable_absent` counters quantify exactly how much of each
score's weight mass is lost to absent loci) but did **not** recover the coverage. The
recovery plan is written up in `just-prs/docs/refcall-resolution-plan.md`: resolve the
reference allele at absent score positions from the in-repo reference-panel `.pvar`
(cheap, no download) and a GRCh38 reference FASTA (universal fallback, ~3 GB) so an
absent-but-hom-ref position scores as dose-0/2 instead of falling into
`variants_unscorable_absent`. Until that lands, expect genome-wide WGS coverage to stay
~50% — now honestly labelled via `C_wt` rather than silently deflating percentiles.

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

**Status: DEFERRED — planned on the `just-prs` `refcall-resolution` branch (item C of
`docs/refcall-resolution-plan.md`), not yet implemented.** The diagnosis below stands;
the fix (a build-time span index of gVCF `END` reference blocks + a span-join so a
scoring position inside a confident `0/0` block resolves to dose-0/2 *matched*, gated on
`MIN_DP`/`GQ`) is scoped but unbuilt. Until then the wrapper note holds: **gVCF input is
not yet advantageous** over a plain VCF.

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

**Status: DEFERRED (not addressed in `just-prs` `main`).** Low-priority reconciliation;
untouched by the scoring-foundations round.

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

**Status: PARTIALLY RESOLVED — the percentile reference-panel ancestry now ships from
the library (0.4.9) and the wrapper surfaces it; the rest stays DEFERRED.** In `just-prs`
0.4.9: `PercentileResult` carries `ancestry` + `panel` (the superpopulation/panel actually
used by the `reference_panel` method) and `EnrichedPRSResult` echoes
`reference_panel_ancestry` / `reference_panel`. Evaluation ancestry (`ancestry_broad`)
already flows via `PerformanceInfo` (F11 / `attach_performance`). **Wrapper (0.4.9):** the
`percentile` tool's `PercentileResult` now exposes `reference_panel_ancestry` /
`reference_panel`, and `TraitScoreRow` carries `reference_panel_ancestry`
(`_percentile_result` / `_trait_score_row`, `tools/compute.py`). *Still DEFERRED (roadmap
P3, research):* per-score **development** ancestry from the PGS Catalog "Ancestry
Distribution" metadata, **sample** genetic-ancestry inference (plink2 projection / peddy /
somalier), and the 3-way coherence **veto** (`K_anc`). So the wrapper can now name *which*
reference population a percentile came from, but the score-vs-sample-vs-panel mismatch
check is not yet available from the library.

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

**Status: PARTIALLY RESOLVED — the inverting metric shipped in `just-prs` **0.4.8**
(verified 2026-06-22); the composite gate that consumes it is still future work.**

*What was addressed.* A flat count-based coverage gate ranked a fully-matched 3-SNP toy
above a genome-wide AUROC-0.84 model at 50% count coverage — high count-coverage
correlated with *no* predictive validity.

*How exactly.* The new weight-mass coverage `C_wt` (`weight_mass_coverage` on
`PRSResult`) is **scale-free and self-penalizing in the right direction**: each variant
contributes in proportion to `|β|`, so a 3-SNP score that misses one variant craters its
`C_wt`, whereas a genome-wide score whose matched variants carry most of `Σ|β|` scores
well even at modest count coverage. `percentile_full`'s reliability verdict keys on
`C_wt` (not the count match-rate), so the "trivially small score is the only reliable
one" inversion no longer holds. (The `C_wt` metric and `percentile_full` verdict are now
confirmed shipped in 0.4.8 — see the F9 entry in `previous_issues.md`.)

*What to expect from new just-prs.* Rank/gate on `weight_mass_coverage`, not
`match_rate`, when judging whether a score is informative for an individual.
**Still pending:** folding `C_wt` together with within-genome stability, HWE coherence,
ancestry coherence, and the validity prior into a single composite per-(score×genome)
quality `Q`, and retiring the legacy count-based `percentile_reliable` gate entirely —
that is the posterior-`Q` work (roadmap P4), not yet implemented.

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
