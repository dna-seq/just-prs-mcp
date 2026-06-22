# Competitor research — PRS approaches

How competing AI-agent genomics projects handle the hurdles we track in
`just-prs-pending-fixes.md` (F#), and what is worth borrowing for `just-prs`.

Competitors covered:

1. **[ClawBio](https://github.com/ClawBio/ClawBio)** — `🦖` bioinformatics
   AI-agent *skill* library on OpenClaw. Small, prompt-and-CLI style skills.
2. **[Genomi](https://github.com/exon-research/genomi)** (exon-research) — an
   agent harness with a real Python engine (`src/genomi/`) and an indexed local
   genome store ("Active Genome Index"). Substantially more engineered.

## Combined scorecard

| Our finding | ClawBio | Genomi | Best idea to borrow |
|---|---|---|---|
| **F22 / F15** gVCF hom-ref blocks / ~50% genome-wide coverage | Dodged — caps scores at 50k variants, never genome-wide | **Solved** — build-time `spans` index of reference-block `[pos,end]`; score positions inside a hom-ref block resolve to dose-0/2 *matched* | **Genomi's span join** (see below) |
| **F15** matching diagnosability | Single "overlap %" only | **Solved** — per-variant `matched/missing/excluded` + reason-code counters | Genomi's reason codes |
| **F4** build/liftover honesty | Warns, no liftover | **Solved** — pyliftover; strand-flipped/unmapped variants dropped *and counted* as excluded | Genomi's lift accounting |
| **F12** raw-score → z-score | **Deterministic** Tier-2 HWE analytic null from scoring-file AF | Same formula but **agent-synthesised** from gnomAD AF, not in code | ClawBio's code path + Genomi's framing |
| **F19** ancestry surfaced | Prose EUR caveat only | **Best** — ancestry in metadata, analytic z tied to a *named* population, "may not transfer" baked into prompts | Genomi's population-named z |
| **F9 / F20** coverage gate inverting quality | Hard drop <50% + warn <70% | Avoids inversion by design — always returns *raw* score; declines only standardisation/categories | Genomi's "raw always, calibration gated" split |
| Palindromic/strand harmonization | None | **Solved** — skips ambiguous A/T,C/G unless harmonized | Genomi's `skip_ambiguous_palindromic` |

Net: **ClawBio** is the place to borrow the deterministic raw→z math; **Genomi**
is the place to borrow everything in the variant-matching layer where our hardest
findings (F22/F15/F4) actually live.

---

# 1. ClawBio

Scope: PRS-relevant skills — **gwas-prs** (core PRS engine), **wgs-prs**
(FASTQ→VCF→PRS bridge), **equity-scorer**, **claw-ancestry-pca**, **profile-report**.
The actual risk logic lives in `skills/gwas-prs/gwas_prs.py` + `curated_scores.json`.

## TL;DR

| Our gap | What ClawBio does | Verdict |
|---|---|---|
| **F12** raw-score → z-score path | Tiered estimator; Tier 2 builds an **analytic HWE null** from the scoring file's allele-frequency column — no reference panel needed | **Borrow this** |
| **F15** genome-wide ~50% coverage | Caps at `--max-variants 50000`; never scores genome-wide models | Dodged, not solved |
| **F20** coverage gate inverts quality | Whole catalog is 8–147-variant scores, so coverage is naturally high | Sidestepped by scope choice |
| **F9** low-coverage percentile artifact | Hard drop `<50%` overlap + soft warning `<70%` | Same artifact; only gated by warnings |
| **F19** ancestry never surfaced/gated | Prose EUR-mismatch caveat only; ancestry PCA never wired into the math | No further than us |

## The one valuable idea: tiered percentile estimator with an analytic HWE null

`estimate_percentile()` (`skills/gwas-prs/gwas_prs.py:612`) is exactly the
**raw-score → z-score → percentile** path we lack (our **F12**). It is tiered:

- **Tier 1 — curated reference distribution.** A hand-curated `{mean, sd, population}`
  per score is bundled in `curated_scores.json` (e.g. PGS000013 T2D → mean 1.12,
  sd 0.30, EUR). `z = (raw − mean)/sd`; percentile via `0.5·(1 + erf(z/√2))`
  (`_percentile_from_z`, line 593).
- **Tier 2 — analytic null from allele frequencies (the interesting one).** When no
  curated stats exist, the PRS null distribution is derived *analytically* from the
  `allelefrequency_effect` column already present in PGS Catalog harmonized files,
  under Hardy-Weinberg equilibrium + variant independence:

  ```
  E[PRS]   = Σ 2·fᵢ·wᵢ
  Var[PRS] = Σ 2·fᵢ·(1−fᵢ)·wᵢ²
  z        = (raw − E[PRS]) / √Var[PRS]
  ```

  **No reference panel of genotypes is required** — the z-score and percentile come
  from the scoring file alone. Requires ≥3 variants carrying a frequency
  (`skills/gwas-prs/gwas_prs.py:652-677`).
- **Tier 3 — unavailable** (too few frequencies; returns `method="unavailable"`).

Risk bands come off the percentile via `RISK_CATEGORIES`
(`gwas_prs.py:53`): ≤25 Low / ≤75 Average / ≤95 Elevated / >95 High. (Note: this
drifts from their own `SKILL.md`, which documents 20/80/95 — code/doc mismatch.)

### Why this matters for us

This is the cleanest answer to **F12** seen so far: it produces a z-score (and thus a
percentile, and a feed into `absolute_risk`) from data we already download in the
harmonized scoring file, with no reference panel and no library change to expose
reference mean/std. Candidate to port into `just-prs` as an
`absolute_risk_from_score` path or an additional `percentile` method
(`method="hwe_analytic"`).

## How they handle coverage and ancestry — mostly by avoiding the problem

- **Coverage (our F15 / F20): dodged, not solved.** Two hard gates:
  - `--max-variants 50000` *skips any score above 50k variants entirely*
    (`gwas_prs.py:1093` and `:1200`).
  - `--min-overlap 0.5` drops a computed score below 50% overlap (`:1299`), plus a
    soft `<70%` warning in the report (`:751`).

  Their entire curated catalog is **8–147-variant scores**. They never attempt
  genome-wide (>500k) models, so they never hit our ~50% WGS-coverage artifact. This
  is a deliberate scope choice: small, clumped, interpretable scores where coverage is
  naturally high — which is *also* what keeps their HWE null valid (the independence
  assumption only holds for LD-pruned/clumped scores).

- **Ancestry (our F19): not solved — prose only.** A whole-repo search found nothing
  that adjusts, recalibrates, or gates a percentile by ancestry. `reference_population`
  is carried as a label and the report prints an EUR-mismatch caveat. `claw-ancestry-pca`
  is *suggested* in the SKILL docs to "validate" the reference population but is never
  wired into the PRS math. `equity-scorer` computes a cohort-level **HEIM diversity**
  score (heterozygosity / FST / PCA / representation), not individual PRS portability.
  So they are no further than us on F19.

- **WGS sample QC (`wgs-prs`):** a fail-fast gate on Ti/Tv 1.8–2.5, Het/Hom 1.0–3.0,
  QUAL ≥ 30, DP ≥ 10 before any scoring. Orthogonal to PRS interpretation, but a clean
  upstream sample-quality gate pattern.

## A bug to inherit-with-care if we port the HWE null

Their `calculate_prs` sums `raw_score` over **all matched** variants, but Tier 2 sums
`E`/`Var` over the freq-carrying subset of **all scoring** variants — including ones the
patient is *missing* (`gwas_prs.py:652-666`). At <100% coverage the numerator is
deflated against the null mean → the same **F9** coverage artifact, now baked into the
analytic null. It only behaves because their 50k cap + 50% floor keep coverage high.

**If we port the HWE null to genome-wide scores, `E`/`Var` must be summed over exactly
the matched variants** (or the raw score coverage-normalized first), or it reproduces
F9/F15. This is the same coverage-normalization our F15/F20 already call for.

## Takeaways for `just-prs`

1. **Borrow:** the **HWE analytic-null z-score** (Tier 2). It closes F12 using data
   already in the harmonized scoring file — no reference panel, no upstream-library
   dependency. Pair it with the tiered fallback (curated → analytic → unavailable).
2. **Validation of our direction (F20):** their honest move on coverage is to
   *restrict score size*. This argues our F20 fix should judge coverage *relative to
   score type/size*, and possibly offer a small-score interpretable tier — not merely
   flag genome-wide scores unreliable.
3. **No help on:** F19 ancestry (they only warn) and F15 genome-wide coverage (they
   sidestep it by capping at 50k variants).

## Source pointers

Cloned from `https://github.com/ClawBio/ClawBio` (MIT). Key files:

- `skills/gwas-prs/gwas_prs.py` — `estimate_percentile` (612), `_percentile_from_z`
  (593), `calculate_prs` (524), `parse_scoring_file` (397, reads
  `allelefrequency_effect`), `RISK_CATEGORIES` (53), `--max-variants` / `--min-overlap`
  CLI gates.
- `skills/gwas-prs/curated_scores.json` — per-score `{mean, sd, population}` reference
  distributions (6 scores, all EUR).
- `skills/wgs-prs/wgs_prs.py` — fail-fast VCF QC thresholds.
- `skills/equity-scorer/equity_scorer.py` — HEIM cohort-diversity (not individual PRS).
- `skills/claw-ancestry-pca/ancestry_pca.py` — ancestry PCA (not wired into PRS).

---

# 2. Genomi (exon-research)

A genuinely engineered system, not a prompt-and-CLI skill. Genotypes are
ingested once into a local SQLite **Active Genome Index** (AGI); PRS runs as a
capability over that index. Source: `src/genomi/capabilities/prs/` (scorer,
harmonize, scoring_files, pgs_catalog) + `src/genomi/active_genome_index/`
(dosage, record_kinds, genotype_qc). The `prs` skill is `skills/prs/SKILL.md`.

Unlike ClawBio it does **not** try to invent a risk category. Its entire design
philosophy: return a **raw weighted score + rigorous variant accounting**, and
refuse to standardise/categorise unless valid calibration parameters are supplied.
This is the right altitude for the problems we actually have — and it solves our
hardest open findings in the variant-matching layer.

## F22 / F15 — gVCF reference blocks (THE one to borrow)

This is our top open finding (genome-wide scores match only ~50% of a WGS
callset because confident hom-ref positions are never emitted as dose-0). Genomi
solves it cleanly:

- **Build time:** records are typed (`record_kinds.py`): a normal variant record
  vs a `reference_block` (gVCF `END`-spanning hom-ref). Reference blocks are
  indexed in a separate **`spans`** table keyed by `(chrom, pos, end)`.
- **Score time:** `dosage.py:_bulk_fetch_records` runs **two** joins per score
  position (`dosage.py:118-155`):
  1. *point* join — `records.pos = site.pos` (a called variant at the locus);
  2. *span* join — `spans.pos <= site.pos AND spans.end >= site.pos` (the locus
     falls *inside* a hom-ref reference block).
- `_dosage_from_record` (`dosage.py:234-250`) then treats a hom-ref block as a
  real genotype: effect-allele dosage = ploidy if `effect == ref`, else `0`,
  with `match_type="reference_homozygous_inferred"`. So a score variant in a
  confident hom-ref region becomes a **matched dose-0/2**, not a miss.

That `spans` join is exactly the F22 fix we scoped. The principled guard lives in
`genotype_qc.py`: `absence_allowed = has_reference_blocks and has_depth`
(`genotype_qc.py:79`) — you may infer hom-ref for an absent position **only** when
the callset actually carries reference blocks with depth (gVCF). For array/sparse
VCF, absence stays *missing*, never silently hom-ref. just-prs must keep that
distinction if we port this.

## F15 — matching diagnosability

Every score variant resolves to `matched` / `missing` / `excluded`, and the
non-matches carry **reason codes** aggregated into counters
(`scorer.py:_variant_accounting`, `dosage.py`): `no_record_at_locus`,
`other_allele_not_in_record`, `genotype_allele_outside_score_alleles`,
`effect_allele_not_in_record`, `filter_fail`, `ambiguous_palindromic_unharmonized`,
`unparseable_genotype`, plus liftover reasons below. This is precisely the
per-score "*why* were variants unmatched" breakdown F15 asks us to surface, vs our
current opaque match-rate.

## F4 — build / liftover honesty

`harmonize.py:lift_score_variants` lifts score variants between builds via
pyliftover and **drops + counts** failures with explicit reasons
(`missing_coordinates`, `invalid_position`, `unmapped`, `strand_flipped`). Strand
flips on liftover are excluded rather than silently scored. Dropped counts surface
in `sample_qc.liftover`. Palindromic A/T and C/G SNPs are skipped unless
harmonized (`dosage.py:30`, default `skip_ambiguous_palindromic=True`).

## F9 / F20 — coverage gating without inverting quality

Constants (`scorer.py:13-16`): `MIN_SCORE_VARIANTS=10`, `MIN_OVERLAP_FRACTION=0.10`,
`MODERATE=0.50`, `HIGH=0.90`. Note the floor is **10%**, far more permissive than
ClawBio's 50%. The key design move that dodges our F20 inversion:

- A **raw** score is computed whenever overlap clears the (low) floor —
  genome-wide scores included. `overlap_quality` (high/standard/low/insufficient)
  is *descriptive metadata*, never a go/no-go that flips a high-AUROC genome-wide
  score to "unreliable".
- Standardisation/categories are the only thing gated: `_score_result`
  (`scorer.py:336-363`) computes a z **only** if the caller supplies valid
  `score_mean`/`score_sd`; otherwise it returns raw with
  `"meaning": "Raw score only; no absolute risk, percentile, or clinical category is inferred."`

So coverage governs *interpretation confidence*, not *whether the score exists* —
the opposite of our flat ≥90% reliability gate that perversely blesses only
trivially small scores (F20).

## F12 — raw → z

`prs.calculate_score` standardises only from caller-supplied `score_mean`/`score_sd`
(`scorer.py:353`). The HWE closed-form z (same maths as ClawBio's Tier 2) is **not
in code** — it is an *agent-synthesis* instruction in `skills/prs/SKILL.md`: combine
gnomAD population allele frequencies into a closed-form z, disclose the assumptions
(HWE, variant independence, ancestry of the AF source), and phrase it as
"analytic z relative to <population>, ~Yth percentile … a closed-form estimate, not
an empirical reference-cohort percentile." For just-prs (a library, not a chat
agent) we'd implement it deterministically like ClawBio — but adopt Genomi's
framing: name the population, mark it analytic-not-empirical.

## F19 — ancestry

Best of the three. Ancestry is surfaced, not buried: `prs.fetch_score_metadata`
exposes development/evaluation cohort ancestry; `source_context.py:36` states
"Reported ancestry labels are cohort/source descriptors, not personal identity
labels"; the analytic z is tied to a **named** gnomAD population; and "performance
may not transfer across ancestry/evaluation cohorts" is baked into the skill
prompt. There's a dedicated `ancestry` capability with its own overlap policy.
They still don't *quantitatively recalibrate* for ancestry mismatch (nobody does
cheaply), but they surface it at every layer — exactly what F19 asks of us.

## What Genomi does NOT solve for us

- No ancestry-aware recalibration of the score itself (only labelling/caveats).
- The analytic-z is delegated to the LLM, so there's no reusable library function
  — and no coverage-normalisation of the raw score before standardising (our F9
  numerator-deflation concern still applies if coverage is partial).
- Absolute risk (incidence × calibrated z) is explicitly out of scope; they stop
  at standardised z, same as where our F12 would land.

## Takeaways for `just-prs`

1. **Port the `spans`/reference-block dosage (F22/F15).** A build-time span index
   of gVCF `END` blocks + a span join at score time, returning hom-ref positions
   as dose-0/2 matched. Keep the `absence_allowed = has_reference_blocks and
   has_depth` guard so array/sparse VCFs don't fake hom-ref.
2. **Adopt three-way variant accounting with reason codes (F15).** Replace the
   opaque match-rate with `matched/missing/excluded` + reason counters.
3. **Split "raw score" from "calibration" in the gate (F20).** Always return the
   raw score with descriptive coverage quality; gate only standardisation and any
   category labels. This dissolves the perverse small-score inversion.
4. **Surface cohort ancestry everywhere and name the population in any z (F19).**
5. **Liftover accounting (F4):** drop strand-flipped/unmapped and count them.

## Source pointers

Cloned from `https://github.com/exon-research/genomi` (see LICENSE). Key files:

- `src/genomi/active_genome_index/dosage.py` — `dosage_for_variants` (18),
  `_bulk_fetch_records` point+span joins (105), `_dosage_from_record` (181),
  `_is_homozygous_reference_block` (312).
- `src/genomi/active_genome_index/record_kinds.py` — `reference_block` typing,
  `reference_block_sql`.
- `src/genomi/active_genome_index/genotype_qc.py` — `absence_allowed` /
  `has_reference_blocks` gate (76-110).
- `src/genomi/capabilities/prs/scorer.py` — overlap constants (13-16),
  `collect_score_context` (106), `_sample_qc` (237), `_score_result` (336),
  `_variant_accounting` (382), `_overlap_status`/`_overlap_quality` (440-456).
- `src/genomi/capabilities/prs/harmonize.py` — `lift_score_variants` (liftover
  accounting).
- `skills/prs/SKILL.md` — calibration boundaries + agent-synthesised analytic-z
  instructions.

---

# Alt Ideas (unattributed)

Candidate PRS gating techniques gathered from mature WGS practice, recorded here
as design options for `just-prs` — no provenance, evaluate on merit.

## A1 — R²-scaled trait calibration (F12, F11)

To turn a standardized PRS into an expected trait/liability value, scale the
z-score by the **square root of the score's variance explained**, not by SD alone:

```
E[trait] = population_mean + SD · sqrt(R²) · Z
```

- Using `SD · Z` *without* `sqrt(R²)` inflates the predicted effect roughly
  **3–10×** — a common and serious error.
- **Never mix** an `R²` and a `Z` from different PGS publications; both must come
  from the same score/evaluation.

This gives a principled raw→z→trait path (our F12) that also *consumes* the
performance metadata we already fetch (our F11). For binary traits the same `Z`
feeds a liability-threshold → absolute-risk step; `R²` (or AUC-derived
discrimination) sets how much the score is allowed to move the prior.

## A2 — HWE analytic null with a frequency-source fallback chain (F12, F19)

Same closed-form null as ClawBio's Tier 2, but with an explicit, documented
priority for where the per-variant allele frequency `pᵢ` comes from:

```
E[PRS]   = Σ 2·pᵢ·βᵢ
Var[PRS] = Σ 2·pᵢ·(1−pᵢ)·βᵢ²
Z_HWE    = (PRS_raw − E[PRS]) / √Var[PRS]
```

Frequency source priority:
1. `allelefrequency_effect` from the PGS scoring file (if present);
2. **gnomAD (population-matched, e.g. v4.1 NFE/EUR)** if the column is absent;
3. 1000G Phase 3 (population-matched) as final fallback.

The fallback chain is the upgrade over ClawBio (which only reads the scoring-file
column and gives up otherwise) and ties the null to a **named population** (F19).
Assumes HWE + linkage equilibrium, so it is only valid for LD-pruned/clumped
scores — disclose that.

## A3 — Hom-ref imputation by effect-allele orientation (F22, F15)

A cheap alternative (or complement) to gVCF span expansion: when a score variant
is **absent** from the callset, impute its dosage from the *orientation of the
effect allele* rather than treating it as zero:

- effect_allele == REF and missing → **dosage = 2** (assume homozygous reference);
- effect_allele == ALT and missing → **dosage = 0**.

This removes the systematic downward bias that deflates every genome-wide score
(our F15). **Guard:** absence only equals hom-ref on a high-depth WGS callset —
the same precondition as A's reference-block approach (depth present). On
array/sparse VCF, absence must stay missing. The gVCF-span method (resolve absence
against actual reference blocks) is the *rigorous* form; this orientation rule is
the *fast approximation* when spans aren't indexed.

## A4 — Cross-caller Z stability as the real reliability metric (F20, F9)

The empirical refutation of our flat coverage gate. Run the **same** score through
≥2 independent callers, standardize each, and judge reliability by **how much Z
moves across callers**, not by coverage fraction:

| Cross-caller spread | Verdict |
|---|---|
| RMSE < 0.1 **and** N_snp > 100K | EXCELLENT — confident |
| RMSE < 0.2 | STABLE |
| RMSE < 0.5 | MODERATE — report with caveat |
| RMSE ≥ 0.5 **or** max_delta > 1.0 **or** N_snp < 50 | UNSTABLE — exclude |

Key empirical finding: **large genome-wide scores (N > 100K) are the *most*
reproducible** (median |ΔZ| ≈ 0.03–0.04); instability concentrates in tiny scores
(N < 50) and in one less-robust caller. This is the direct counter to F20 — high
variant count correlates with *stability*, the opposite of what our ≥90%-coverage
gate rewards. Reliability ≈ stability, not overlap.

## A5 — Match-rate < 50% is an *artifact*, not just a caveat (F9)

Below ~50% match rate, the deflated raw score produces wildly inflated extreme
z-scores (real example seen: a ~147-SNP score at 56/147 matched → Z ≈ +6 to +9).
Treat such scores as **artifacts and exclude them from conclusions**, not merely
flag them. This is stronger than our current "`reliable=false` + caveat" and worth
considering for obviously-extreme low-coverage results.

## A6 — R² informativeness floor (F20, F11)

Independent of percentile: if a score's `R²` for the trait is **< ~0.02**,
individual prediction is uninformative — say so regardless of how extreme the
percentile looks. Pairs with A1/A4 so a score isn't reported as meaningful just
because it lands at the 99th percentile on thin evidence.

## A7 — Multi-score concordance (F20)

When several published scores exist for one trait, report **concordance vs
discordance** across them rather than cherry-picking one. Agreement across
independently-derived scores is itself a reliability signal (and disagreement is a
flag) — a natural extension of our `compute_prs_by_trait` report and the
multi-score-consensus idea Genomi gestures at.

## A8 — Harmonization caveats worth surfacing (F15, F19)

- **Inferred other-allele bias:** when the score's other allele is *inferred*
  (not given in the file), it can introduce a systematic directional shift
  (a documented ~+0.9σ on one cognitive score) that is **not** a caller artifact.
  Flag scores relying on inferred other-alleles.
- **ref = minor sanity check:** REF/ALT are technical labels; some SNPs have the
  minor allele as REF. Check population AF before treating a hom-ALT genotype as
  rare/anomalous, and orient dosage by allele identity, not by REF/ALT position.

## A9 — Risk-composition rule (interpretation)

A monogenic pathogenic finding **dominates** and should not be multiplied by a PRS
for the same condition — PRS modifies polygenic background risk, it does not stack
on a Mendelian hit. Relevant if `just-prs` outputs ever feed a combined risk
narrative alongside single-variant findings.

## Mapping to our findings

| Finding | Alt Ideas that bear on it |
|---|---|
| **F22 / F15** coverage / hom-ref | A3 (orientation imputation), A8 (harmonization) |
| **F12** raw→z→risk | A1 (R²-scaled trait), A2 (HWE null + fallback) |
| **F11** attach performance | A1, A6 (R² consumed as a gate) |
| **F9** low-coverage artifact | A4, A5 |
| **F20** coverage gate inverts quality | A4 (stability), A6 (R² floor), A7 (concordance) |
| **F19** ancestry | A2 (population-named frequencies), A8 |

## Where this lands

The synthesis of all three sources — and how `just-prs` intends to differ from the
landscape (posterior per-genome gating, weight-mass coverage, a phenotypic-vs-
populational reliability reframe, Q-weighted consensus) — is written up as a design
doc in the library repo: `just-prs/docs/posterior-quality-gating.md`. It drives from
the existing `just-prs/docs/prs-quality-score.md` and `demo-trait-ranking.md`.
