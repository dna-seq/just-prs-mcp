# Dogfooding log — just-prs MCP

**Date:** 2026-06-21
**Driver:** Claude Code (agent), acting as a typical MCP client
**Scenario:** "Compute *all* PRS for type 2 diabetes from a personal WGS VCF and
produce a trait report."
**Input VCF:** `/data/newton/consensus/deepvariant18/newton_winter.vcf`
(DeepVariant WGS, single sample `default`, GRCh38, Ensembl chromosome naming).
**Server mode observed:** essentials (default). Only the essentials tool surface
was exposed to the client.

This file is a punch-list for the follow-up agent. Each finding has: what I did,
what I expected, what happened, severity, and a concrete fix with code pointers.
Findings are roughly ordered by severity.

> **Status: one PRS computed as a smoke test (PGS000014); full panel not run.**
> A single score was computed end-to-end (compute → percentile → assess_quality
> → best_performance) to characterize the per-score flow — see F9–F13, which are
> the most concerning correctness findings. The full 195-score panel was not run
> because the headline gap (F1) makes "all PRS for a trait" impractical in the
> default surface. The trait report is pending these fixes; resume after F1–F3
> and ideally F9–F11.

---

## F1 — No "compute all PRS for a trait" tool, and batch is not in the base surface *(blocker / headline)*

**What I did:** Resolved the trait (`MONDO_0005148`, 195 associated PGS IDs) and
wanted to compute every score against the VCF — the literal user request and a
genuinely typical scenario ("score me for trait X").

**Expected:** A single base-mode tool like
`compute_prs_by_trait(trait_id, vcf_path | genotypes_path, ...)` that fans out
over the trait's `associated_pgs_ids` and returns one result per score (plus a
summary). Failing that, at least a batch-by-list tool in the default surface.

**What happened:**
- There is **no by-trait compute tool at all**. To honor the request you must
  manually: `trait_info` → copy 195 PGS IDs → call `compute_prs` 195 times.
- The only batch primitive, `compute_prs_batch` (takes an explicit `pgs_ids`
  list, still not a trait), lives in **extended** mode
  (`tools/extended.py:43`), so it is **not available by default**. The default
  client cannot batch at all.

**Severity:** High. This is the single biggest friction point — the most natural
request for this server is the one it makes hardest.

**Fix (per maintainer direction — "batch_by_trait should be in base package by default"):**
1. Add `compute_prs_by_trait` to the **essentials** surface (`tools/compute.py`,
   inside `register_compute`). Signature roughly:
   `compute_prs_by_trait(trait_id: str, vcf_path: str, genotypes_path: str | None = None, genome_build: str | None = None, include_children: bool = False, limit: int | None = None) -> TraitPRSReport`.
   It should call the REST client's `get_trait(trait_id)`, collect
   `associated_pgs_ids` (and `child_associated_pgs_ids` when `include_children`),
   then reuse the same scoring path as `compute_prs_batch`.
2. Move `compute_prs_batch` (or a thin wrapper) into the **base** surface as
   well, since by-trait is just batch-over-a-resolved-list. Keep it a background
   task (`task=True`) given the variant counts.
3. Return a structured report model (see F8) rather than a bare `list[PRSResult]`
   so the client gets per-score rows + a trait-level summary in one call.
4. Add a `limit`/pagination guard: 195 scores is a lot of downloads; let the
   caller cap it and report how many were skipped (no silent truncation).

---

## F2 — Trait search is exact-substring, not tokenized/fuzzy *(high)*

**What I did:** `search_traits(term="diabetes mellitus type 2")` — the exact
phrasing in the user's request.

**Expected:** A hit for type 2 diabetes mellitus (`MONDO_0005148`).

**What happened:** `{"result": []}` — **zero results.** Only after trying
`"type 2 diabetes"`, `"type 2 diabetes mellitus"`, and `"diabetes"` did the
trait surface. Root cause: the PGS Catalog REST search does a literal substring
match against label + synonyms. The canonical synonym is
`"diabetes mellitus, type 2"` (with a comma); `"diabetes mellitus type 2"` is not
a substring of any synonym, so it misses. Word order and punctuation are
load-bearing.

**Severity:** High. A plausible, well-formed query for the target trait returns
nothing, with no hint that a reorder would help. Easy to conclude "not in the
catalog" and give up.

**Fix:** In `search_traits` (`tools/catalog.py:111`), before/after the REST call:
tokenize the term and retry on token permutations or an AND-of-tokens match; or
fall back to a local fuzzy match over labels+synonyms when the REST result is
empty. At minimum, document in the docstring that matching is exact-substring and
suggest trying canonical phrasings. Consider surfacing "did you mean" candidates.

---

## F3 — `trait_info(efo_id=...)` is mislabeled: it accepts EFO *and* MONDO (and others) *(medium — maintainer-flagged)*

**What I did:** `trait_info(efo_id="MONDO_0005148")`.

**Expected:** Unclear from the API — the parameter is named `efo_id` and the
docstring says *"Fetch a trait by EFO ID (e.g. 'EFO_0001645')"*, which implies
EFO-only. But the trait I needed is a **MONDO** ID (that is what `search_traits`
returns as `id`).

**What happened:** It worked fine with the MONDO ID. So the param name and
docstring are misleading — the underlying catalog uses **EFO/MONDO ontology ID
conventions interchangeably**, and the tool happily accepts either (and likely
any ontology ID the PGS Catalog knows).

**Severity:** Medium. Purely a discoverability/trust issue — a careful user sees
`efo_id` + a MONDO ID and hesitates or transforms the ID incorrectly.

**Fix:** Rename the parameter to `trait_id` (keep `efo_id` as a deprecated alias
if needed) and update the docstring to: *"Fetch a trait by its ontology ID (EFO
or MONDO, e.g. 'EFO_0001645' or 'MONDO_0005148')."* (`tools/catalog.py:120-121`).
Mirror the same wording wherever trait IDs are documented (and in the new
`compute_prs_by_trait` from F1).

---

## F4 — Genome build is silently assumed; no inference or echo-back *(medium)*

**What I did:** Had to determine the VCF's build myself. The header has **no
`##reference` line**; build was only inferable from `##contig` lengths (chr1 =
248,956,422 ⇒ GRCh38). I then passed `genome_build="GRCh38"` explicitly to
`normalize_vcf`/`compute_prs`.

**Expected:** The tools either infer the build from contig lengths, or at least
echo back which build they assumed, so a wrong default surfaces immediately.

**What happened:** `genome_build` defaults to `None`/a configured default. A
client that omits it (most will) gets silent harmonization against whatever the
default is. A build mismatch does not error — it just produces a near-zero match
rate that looks like a bad sample rather than a config mistake. The
`NormalizeResult`/`PRSResult` payloads don't state the build used.

**Severity:** Medium (correctness foot-gun, easy to misattribute).

**Fix:** (a) In `normalize_vcf`/`compute_prs` echo the effective `genome_build`
into the result model. (b) Optionally add build inference from contig lengths in
`normalize_vcf` (it already parses the header to strip the `chr` prefix) and
log/return the inferred build. (c) If `compute_prs` match_rate is implausibly low,
include a hint in `assess_quality` output that a build mismatch is a likely cause.

---

## F5 — `search_traits` payloads are enormous (full PGS-ID arrays inline) *(low–medium)*

**What I did:** `search_traits("type 2 diabetes")` and `search_traits("diabetes")`.

**What happened:** Each trait row inlines the **entire** `associated_pgs_ids`
array (195 IDs for T2D) and, for parent traits, a second `child_associated_pgs_ids`
array. The "diabetes" query returned ~16 traits, several with 70–250 IDs each —
a multi-kilobyte response that is almost all opaque ID strings. Heavy on client
context for a *search*.

**Severity:** Low–medium (cost/context, not correctness).

**Fix:** For `search_traits` specifically, return ID **counts**
(`n_associated`, `n_child_associated`) instead of the full arrays, and reserve
the full arrays for `trait_info` (single-trait lookup). Or add an
`include_pgs_ids: bool = False` flag to `search_traits`. Also document the
`associated_pgs_ids` vs `child_associated_pgs_ids` distinction (direct vs.
descendant-trait scores) — it is currently unexplained.

---

## F6 — `normalize_vcf` is documented as a polling background task but returned its result inline *(low — doc accuracy)*

**What I did:** Called `normalize_vcf`. AGENTS.md and the tool docstring say it
"runs as a real MCP background task: the client gets a task id immediately and
polls for the result."

**What happened:** I received the **final result directly** (`output_path`,
`n_variants`, `message`) with no visible task-id/poll cycle. Likely the harness
abstracts the polling — which is good UX — but it contradicts the documented
contract, so a client author coding to "expect a task id" would be surprised.

**Severity:** Low (works fine; doc/observed mismatch).

**Fix:** Reconcile the docs with the actual client-visible behavior, or note that
the task/poll handshake may be transparently collapsed by some clients. No code
change required if behavior is intentional.

---

## F7 — Positive confirmations (keep these working) *(no action, just record)*

- **BGZF-as-`.vcf` handled transparently.** The input is BGZF-compressed
  (`file` reports *Blocked GNU Zip Format*) but named `newton_winter.vcf` with no
  `.gz`. `normalize_vcf` read it correctly anyway (6,139,024 → 4,725,262 variants
  after `pass_filters=["PASS"]`). Good real-world robustness; add a regression
  test so it doesn't break.
- **`pass_filters=["PASS"]`** worked and meaningfully dropped DeepVariant
  `RefCall`/`./.` sites (~1.41M removed). Sensible default behavior.
- **Ensembl chromosome naming (no `chr` prefix)** went through cleanly; the
  documented `chr`-stripping is a no-op here and didn't corrupt anything.

---

## F8 — No first-class "trait report" output model *(enhancement, ties to F1)*

**Observed:** Even with batch available, the building blocks
(`compute_prs` → `percentile` → `absolute_risk` → `assess_quality`) are four
separate calls per score, and there is no aggregate model that ties a trait's N
scores into one ranked, interpreted report. The user asked for a *trait report*;
today that requires the client to orchestrate 4×N calls and assemble it by hand.

**Severity:** Enhancement (but it is the actual deliverable of the scenario).

**Fix:** Define a `TraitPRSReport` model in `models.py` (trait id/label, per-score
rows with score + match_rate + percentile + quality label + best-performance
effect size, and a summary). Have the F1 `compute_prs_by_trait` tool optionally
roll up percentile/quality per score so one call yields a presentable report.

---

## F9 — `percentile` returns a nonsensical `0` for a low-coverage score; no match-rate normalization *(high / correctness)*

**Smoke-test result** (PGS000014, a genome-wide T2D score, against the GRCh38
normalized Parquet):

```
compute_prs → score=14.944, variants_matched=2,590,167, variants_total=6,917,436,
              match_rate=0.374, percentile=null, theoretical_mean/std=null,
              has_allele_frequencies=false, performance=null, absolute_risk=null
percentile  → percentile=0, method="reference_panel", ancestry="EUR"
```

**What happened / why it's wrong:** The raw PRS is a *sum of effect-allele
dosages*. With only 37% of scoring variants matched, ~63% of loci contribute 0
(treated as missing/absent), so the raw sum is structurally deflated. The
`percentile` tool then compares this deflated raw score against a full-coverage
reference-panel distribution and (predictably) returns **percentile 0** — the
absolute bottom. This isn't a real biological signal; it's an artifact of
coverage. Any low-match-rate score will be pushed to an extreme percentile.

**Severity:** High. The headline number a user reads ("0th percentile for T2D")
is meaningless here but looks authoritative.

**Fix:** `percentile` (`tools/compute.py:153`) should (a) accept/consider
`match_rate` and refuse or heavily caveat when coverage is low, and/or (b)
normalize the raw score by matched-variant coverage before comparing to the
reference distribution. At minimum, return a `caveat`/`reliable: false` field and
never emit a bare `0`/`100` without flagging it as a coverage artifact.

---

## F10 — `assess_quality` and `percentile` contradict each other *(medium / consistency)*

**What happened:** For the same result, `assess_quality(match_rate=0.374)` said
*"No allele frequencies in scoring file — percentile not available"* and labeled
quality **Low**, while `percentile(...)` returned a concrete value (0) via
`method="reference_panel"`. One tool says a percentile is impossible; the other
produces one. `assess_quality` only reasons about the *theoretical* (allele-freq)
path and is unaware a reference-panel distribution exists, so its messaging is
wrong whenever the reference-panel path is available.

**Severity:** Medium (erodes trust; the two tools should never disagree on
whether a percentile exists).

**Fix:** Either let `assess_quality` know which percentile method is available
(pass it the method/percentile), or have it speak only to quality and defer
availability statements to `percentile`. Align the wording so a client gets one
coherent story.

---

## F11 — `compute_prs` doesn't attach performance/AUROC that the catalog clearly has *(medium / UX)*

**What happened:** `compute_prs` returned `performance=null`, yet a separate
`best_performance(PGS000014)` call immediately returned `found=true, AUROC=0.730,
n_individuals=288,978`. The data exists; the user just has to know to make a
second call and then *manually* feed the AUROC back into `assess_quality` to get
a non-degraded quality label. The pieces don't compose on their own.

Minor sub-issue: `best_performance` returns `effect_size: ""` (empty string) and
`or/hr/beta_estimate: null` for this score — an empty string where `null` (or a
populated value derived from AUROC-only scores) would be cleaner.

**Severity:** Medium (every meaningful interpretation needs 3–4 manual calls).

**Fix:** Have `compute_prs` optionally fetch and embed best-performance
(AUROC/effect size) so `assess_quality` can use it without a round-trip — or fold
all of this into the F1/F8 by-trait report so it happens once per score.

---

## F12 — No raw-score → z-score path, so `absolute_risk` is effectively unreachable *(medium / gap)*

**What happened:** `absolute_risk(pgs_id, z_score, sex)` needs a **z-score**, but
nothing in the pipeline produces one from a real computed PRS. `compute_prs`
returns a raw `score` and `null` theoretical mean/std; `percentile` returns a
percentile + method but **not** the z-score it implicitly used. So to call
`absolute_risk` the client must independently know the reference mean/std and
standardize by hand — which is exactly the data `compute_prs` reported as `null`.
The "estimate absolute disease risk" capability is stranded.

**Severity:** Medium (a whole tool is hard to invoke from a genuine result).

**Fix:** Have `percentile` also return the `z_score` it derived (and the
reference mean/std when known), or add an `absolute_risk_from_score` convenience
that chains raw-score → z → risk. Wire it into the F8 report.

---

## F13 — ~~`pass_filters=["PASS"]` drops hom-ref sites and tanks match rate~~ → TESTED & DISPROVEN *(closed)*

**Resolution (2026-06-21):** Tested directly. Re-normalized the same VCF **without**
`pass_filters` (6,139,024 variants kept vs 4,725,262 with PASS) and re-scored
PGS000014: match_rate **0.3768** (no filter) vs **0.3744** (PASS) — essentially
identical (2,606,490 vs 2,590,167 matched). **The PASS filter is not the cause of
low coverage.** Do not chase this. The real driver of ~50% coverage on
genome-wide scores is elsewhere (allele/strand harmonization, or the score
variant set genuinely not overlapping the callset) and is the key open question
for the polish phase — but it was *not* the RefCall hypothesis below.

<details><summary>Original (disproven) hypothesis, kept for the record</summary>

**Hypothesis from the data:** Normalization with `pass_filters=["PASS"]` reduced

**Hypothesis from the data:** Normalization with `pass_filters=["PASS"]` reduced
6,139,024 → 4,725,262 variants, dropping ~1.41M sites. Many of those are
DeepVariant **`RefCall`** records ("genotyping model thinks this site is
reference") — i.e. **homozygous-reference** calls. For PRS, a hom-ref genotype at
a scoring locus is *informative*: it means a known **0** effect-allele dose, not
missing data. By filtering them out, those loci become "unmatched," which (a)
depresses `match_rate` (observed 0.374 on a genome-wide score, suspiciously low
for WGS) and (b) biases the raw score downward — feeding directly into the bogus
percentile=0 in F9.

**Severity:** High *if confirmed* — it means the obvious "keep only PASS" hygiene
step silently corrupts PRS for any gVCF-style caller that emits ref blocks.

**Fix / next step:** (1) Verify by re-normalizing the same VCF **without**
`pass_filters` (or with `RefCall` allowed) and comparing match_rate for
PGS000014. (2) If confirmed, document loudly that confident hom-ref sites must be
retained for PRS, and consider having `normalize_vcf` treat `RefCall`/hom-ref as
dose-0 rather than dropping it, or warn when a FILTER allow-list would remove a
large fraction of hom-ref calls.

</details>

---

## F14 — Full by-trait report exceeds the MCP output token limit *(medium / scale)*

**What happened:** `compute_prs_by_trait(MONDO_0005148, interpret=true)` with no
`limit` scored all 220 scores successfully, but the response (148,299 chars of
interpreted rows) **exceeded the client's max output tokens** and had to be
spilled to a file and parsed with `jq`. A normal client would just see an error.

**Severity:** Medium (the headline tool can't return its headline result for a
big trait without help).

**Fix:** Add a compact/summary return mode (e.g. `interpret` returns only a
trait-level rollup + top-N rows, or a `fields=` selector), and/or paginate. At
minimum, when output would exceed a size threshold, return a trimmed result with
a pointer rather than failing. Consider making the tool a background task so large
panels stream.

---

## Suggested fix order for the follow-up agent

1. **F13** — verify the `RefCall`/hom-ref drop hypothesis first; it may invalidate
   every match_rate/percentile below until fixed.
2. **F9** — stop `percentile` emitting bare `0`/`100` for low-coverage scores;
   normalize by coverage or flag unreliable.
3. **F1** — add `compute_prs_by_trait` to essentials + promote batch to base.
4. **F8** — `TraitPRSReport` model so F1 returns a real report.
5. **F11** — `compute_prs` embeds best-performance; **F10** — align
   `assess_quality`/`percentile` messaging; **F12** — expose z-score for
   `absolute_risk`.
6. **F2** — make trait search forgiving (tokenize / fuzzy fallback).
7. **F3** — rename `efo_id` → `trait_id`, fix docstring (EFO *or* MONDO).
8. **F4** — echo effective genome build; consider inference.
9. **F5** — slim `search_traits` payloads (counts, not full ID arrays).
10. **F6** — reconcile background-task docs with observed inline return.
11. Add regression tests: BGZF-named-`.vcf` input (F7), MONDO ID into trait lookup
   (F3), substring-miss query (F2), low-match-rate percentile guard (F9).

After these land, resume the scenario: resolve T2D → `compute_prs_by_trait` over
the GRCh38 normalized Parquet → assemble the trait report.
