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
> **2026-06-22 Batch 2 (post-0.4.8, in `just-prs` `main`, NOT yet released/pinned).**
> **F4** (genome-build detection + `build_mismatch`) and **F23** (`genotypes_lf` on
> `PRSCatalog.compute_prs`) are now **RESOLVED in `main`**, and **F19** is now
> **PARTIALLY RESOLVED** (percentile reference-panel ancestry surfaced; sample-ancestry
> inference + coherence veto still deferred). These need the next `just-prs` release +
> wrapper pin before they can be relied on at runtime — verify against the wheel and move
> to `previous_issues.md` then, same as the 0.4.8 sweep.

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

## F4 — Genome build inference and echo-back

**Status: RESOLVED in `just-prs` `main` (Batch 2, 2026-06-22) — post-0.4.8, pending the
next release/pin.** `compute_prs` / `compute_prs_duckdb` now call the existing
`just_prs.vcf.detect_genome_build()` (contig-length + `##reference` voting) and surface
`detected_genome_build` + `build_mismatch` on `PRSResult` (and `EnrichedPRSResult`),
logging an Eliot warning on mismatch. Guarded: detection only runs on a real,
VCF-suffixed, existing file — a pre-normalized Parquet/array/empty-path input yields
`detected_genome_build=None` and `build_mismatch=False` (never a false mismatch it can't
prove from a header). *What to expect:* read `result.build_mismatch` to catch a VCF
scored against the wrong build; `None` detected build means "couldn't tell," not "match."
**Not yet in published 0.4.8** — bump/pin the next release to consume it.

The WGS VCF had no `##reference` header; GRCh38 was only inferable from contig
lengths. `just-prs` currently relies on the caller/config default, and a build
mismatch surfaces indirectly as poor match rate.

Wrapper mitigation: normalization and trait reports now echo the effective
`genome_build` assumed by the MCP tool.

Library fix: infer genome build from contig metadata where possible, include the
effective build in `PRSResult`, and warn when the inferred VCF build disagrees
with the scoring build.

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
the library; the rest stays DEFERRED.** In `just-prs` `main` (Batch 2, 2026-06-22;
post-0.4.8, pending release): `PercentileResult` carries `ancestry` + `panel` (the
superpopulation/panel actually used by the `reference_panel` method) and
`EnrichedPRSResult` echoes `reference_panel_ancestry` / `reference_panel`. Evaluation
ancestry (`ancestry_broad`) already flows via `PerformanceInfo` (F11 / `attach_performance`).
*Still DEFERRED (roadmap P3, research):* per-score **development** ancestry from the PGS
Catalog "Ancestry Distribution" metadata, **sample** genetic-ancestry inference (plink2
projection / peddy / somalier), and the 3-way coherence **veto** (`K_anc`). So the
wrapper can now name *which* reference population a percentile came from, but the
score-vs-sample-vs-panel mismatch check is not yet available from the library.

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

## F23 — `PRSCatalog.compute_prs` (single score) lacks `genotypes_lf`, so `attach_performance` is silently dropped on the normalized-Parquet reuse path — cheap

**Status: RESOLVED in `just-prs` `main` (Batch 2, 2026-06-22) — post-0.4.8, pending the
next release/pin.** `PRSCatalog.compute_prs` now takes `genotypes_lf` (mirroring
`compute_prs_batch`), forwards it to the low-level compute, and runs `_attach_performance`
itself — so a single score reuses a normalized Parquet **and** attaches best performance
in one call. *What to expect / wrapper follow-up:* once the wrapper pins the release with
this change, drop the free-function branch in the `compute_prs` tool's `genotypes_path`
path and the per-score `best_performance` fallback in the by-trait loop — call
`PRSCatalog.compute_prs(..., genotypes_lf=..., attach_performance=True)` directly.
(Surfaced 2026-06-22 while wiring the wrapper onto 0.4.8's `attach_performance` (F11).)

The batch method `PRSCatalog.compute_prs_batch` already accepts **both** `genotypes_lf`
(reuse a normalized Parquet instead of re-reading the VCF) **and** `attach_performance`.
The single-score `PRSCatalog.compute_prs` has `attach_performance` but **no**
`genotypes_lf`; the low-level free function `just_prs.prs.compute_prs` is the mirror image
— it has `genotypes_lf` but **no** `attach_performance` (and no catalog handle to look one
up). So there is **no one-call path that both reuses a normalized genotype frame and
attaches best performance for a single score.**

Consequence in the wrapper (just-prs-mcp): the `compute_prs` tool's `genotypes_path`
branch must call the low-level free function, so `attach_performance=True` is silently a
no-op there (the result comes back with `performance=None`). The by-trait
`genotypes_path` loop uses the same free function per score (to keep per-score failure
isolation), so `compute_prs_by_trait(genotypes_path=..., interpret=True)` falls back to a
separate `best_performance` lookup per score (`_best_performance_summary`,
`tools/compute.py`) instead of the embedded attach. The plain-VCF paths are unaffected —
they already use `compute_prs` / `compute_prs_batch` with `attach_performance`.

Library fix (cheap — mirrors `compute_prs_batch`): add
`genotypes_lf: pl.LazyFrame | None = None` to `PRSCatalog.compute_prs`. It is a method
with catalog access that already owns the `_attach_performance` helper, so it can forward
`genotypes_lf` to the low-level compute and attach performance itself — the exact thing
`compute_prs_batch` does today. Once it lands, the wrapper drops its free-function branch
and the per-score `best_performance` fallback entirely. (Adding `attach_performance` to
the free `just_prs.prs.compute_prs` instead is the wrong layer: that function has no
catalog/perf frame to source the metadata from.)

Wrapper note: until this lands, `compute_prs(..., genotypes_path=..., attach_performance=True)`
ignores the flag, and the by-trait genotypes-reuse path attaches performance via a
per-score lookup rather than the batch attach. Cross-references F11
(`previous_issues.md`).
