# Dogfooding log — just-prs MCP

**Original run:** 2026-06-21
**Driver:** Claude Code (agent), acting as a typical MCP client
**Scenario:** "Compute *all* PRS for type 2 diabetes from a personal WGS VCF and
produce a trait report."
**Input VCF:** `/data/newton/consensus/deepvariant18/newton_winter.vcf`
(DeepVariant WGS, single sample `default`, GRCh38, Ensembl chromosome naming).

This is the running punch-list of quirks/bugs/UX gaps found by dogfooding the
server end-to-end. As findings are resolved they move to
`docs/previous_issues.md` (with resolution + code pointer); findings that need an
upstream **just-prs library** change live in `docs/just-prs-pending-fixes.md`.

## Where things stand (last sweep 2026-06-21)

- **Resolved in the wrapper → `docs/previous_issues.md`:** F1, F3, F5, F6, F7,
  F8, F14, plus the wrapper-side mitigations for F2, F4, F9, F10, F11. F13 is
  closed (tested & disproven).
- **Open, upstream-blocked → `docs/just-prs-pending-fixes.md`:** the library
  remainder of F2, F4, F9, F10, F11; the F15 coverage root cause (F18 ruled out
  build, **F22 is the leading lead — unexpanded gVCF ref blocks**); F19
  (ancestry), F12.
- **Open, wrapper-actionable (below):** F21 (filterable trait report + the
  essentials/extended curation-profile design), F23 (mode-gate visibility +
  switch-to-extended signal), and the proposed `interpret_prs_for_trait` MCP
  prompt / `prs-trait-interpretation` skill.

These came out of a second dogfooding pass: *"how do we trim a 220-score trait
panel down to an interpretable read?"* The headline lesson — coverage/build
filters are necessary but not sufficient; the gates that actually matter
(ancestry, model quality, coverage-relative-to-score-size) aren't expressible in
the tool today.

---

## F21 — Trait report isn't filterable; the axes that matter aren't columns *(open / wrapper-actionable)*

To trim `compute_prs_by_trait`'s 220 rows to an interpretable shortlist you must
filter on per-score attributes the report doesn't carry — so today an agent has
to call `score_info`/`best_performance` ~220× and join by hand.

- **Add columns to `TraitScoreRow`:** `genome_build`, development ancestry,
  `variants_number`, `weight_type`, and development sample size. (`auroc_estimate`
  is already joined when `interpret=True` — extend that join.)
- **Add filter/rank params to `compute_prs_by_trait`:** `min_match_rate`,
  `min_auroc`, `ancestry`, `build` — today only `top_n` exists, which trims but
  cannot *select*.
- **`score_info` returns `variants_number: null` and `is_harmonized: null`** — the
  exact fields you'd filter on are empty. Parse them from catalog metadata.

Why it matters: the empirically-correct trimming recipe (see F18/F19/F20) is
ancestry → model-quality → coverage-relative-to-score-type → family-dedup →
report-the-consensus. **None of those gates is expressible against the current
report.** A `top_n` cap is not a filter.

### Curation design — essentials vs extended (persona: essentials = non-bioinformatician)

Decision from the design pass: **do not hard-code curated per-trait PGS lists** in
just-prs (the right score is ancestry-dependent, the catalog grows monthly, and
anointing winners is a liability the PGS Catalog itself avoids). Instead **curate
by criteria, computed live**, and split by mode:

- **Essentials** returns a curated, interpreted *shortlist* by default — not 220
  raw rows. `compute_prs_by_trait(trait, vcf, ancestry, profile="curated"|"all")`.
  The default `curated` profile applies, with good defaults and a report of what
  it dropped: ancestry-match (dev ancestry + reference panel keyed to the user's
  ancestry), has-performance-evidence, coverage-adequate-for-score-type (F20),
  score-family dedup, and a no-toy-score guard. Layperson-meaningful knobs only
  (ancestry; profile).
- **Extended** exposes the granular curation surface: explicit PGS-ID lists /
  arbitrary batch (`compute_prs_batch`), `min_match_rate`, `min_auroc`,
  include-preprints, build/weight_type/method overrides, reference-panel & pgen
  scoring, bulk download / HF, and an optional *frozen, versioned* curated set for
  reproducibility/benchmarking.

The curated thing is the **criteria**, not the IDs. This is the same column/filter
work as F21's bullets above (ancestry is F19) — just packaged as a default profile
in essentials and as raw knobs in extended.

### Proposed `interpret_prs_for_trait` MCP prompt + `prs-trait-interpretation` skill

The 5-stage recipe lives only in agent context per-run. Ship the methodology as a
**server-side MCP prompt** (alongside the existing `compute_prs_for_trait`) so
*every* client gets it, not just Claude: resolve trait → confirm ancestry →
curated by-trait → read shortlist concordance → state caveats. The Claude skill
(`prs-trait-interpretation`) becomes a thin wrapper over the prompt. This is the
docs/methodology gap the dogfooding flagged.

---

## F23 — Agent can't see the mode gate; no signal for when to switch to extended *(open / wrapper-actionable)*

The agent only discovers a capability is extended-gated by the tool's **absence**
— there's no up-front map of what's behind the gate or trigger to ask the user to
switch. Fix is threefold:

- **Server `instructions`** should crisply enumerate extended-only capabilities
  (the partial list exists; make it a clear "switch to extended for: …").
- **Essentials tools emit a `needs_extended` breadcrumb** in structured output
  when a request bumps the gate (e.g. user asks for specific PGS IDs, raw
  match-rate tuning, reference-panel scoring, or to override curation) — naming
  the env/CLI switch (`PRS_MCP_MODE=extended` / `--mode extended`).
- **AGENTS.md** keeps the mode→tool map for coding agents.

Switch-to-extended triggers: specific PGS-ID list / arbitrary batch;
reference-panel or pgen scoring; overriding curation (preprints, non-ancestry-
matched, toy scores); bulk catalog download / HF upload; raw match-rate/method
tuning.

---

## F15 — Genome-wide scores overlap only ~50% of a full WGS callset *(open / PRIORITY)*

The dominant unresolved issue and the reason the dogfooding trait report is not
yet interpretable. Across the full 220-score T2D panel, mean coverage was **48%**,
only **1 of 220** scores reached `percentile_reliable=true`, and percentiles for
the *same sample on the same trait* span the full **0→100** range across
genome-wide scores — a classic coverage artifact. PASS/RefCall filtering (F13) and
genome build (GRCh38, confirmed) are ruled out. Likely an allele/strand
harmonization or variant-matching gap in PRS computation.

Full detail, ruled-out hypotheses, and the suggested upstream audit:
`docs/just-prs-pending-fixes.md` F15.

## F12 — No raw-score → z-score path for `absolute_risk` *(open)*

`absolute_risk` needs a z-score, but `compute_prs` returns a raw score and
`percentile` does not expose the z-score / reference mean+std it used, so the
capability is effectively unreachable from a genuine result. **No reliable
wrapper-only fix exists** — the library must expose the reference distribution
parameters (or add `absolute_risk_from_score`). Tracked in
`docs/just-prs-pending-fixes.md` F12.
