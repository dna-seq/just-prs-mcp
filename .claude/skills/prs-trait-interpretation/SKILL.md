---
name: prs-trait-interpretation
description: >-
  Produce an interpretable polygenic-risk read for one trait from a personal
  genome VCF using the just-prs MCP server. Use when a user asks to "compute
  PRS for <trait>", "what's my genetic risk for <trait>", or to turn a big
  by-trait score panel into a trustworthy shortlist. Thin wrapper over the
  server's `interpret_prs_for_trait` MCP prompt — the methodology lives there
  so every client gets it; this skill is the Claude Code entry point.
---

# PRS trait interpretation

A raw by-trait PRS panel can be 100+ scores whose percentiles for the *same
person on the same trait* span the full 0–100 range — mostly a
coverage/ancestry artifact, not real signal. Do **not** dump the panel. Trim it
to a trustworthy shortlist and report the consensus.

The canonical methodology is the server-side MCP prompt
**`interpret_prs_for_trait`** (just-prs-web). Invoke it (or follow its 5 stages
below) rather than improvising.

## The 5-stage recipe

1. **Resolve the trait.** `search_traits` / `trait_info` → the ontology ID (EFO
   or MONDO). If several match, confirm which one with the user.
2. **Confirm ancestry.** Percentiles are only meaningful when the reference
   panel matches the person's genetic ancestry. Ask (or note the default EUR
   assumption) and pass it as `superpopulation`. Flag mismatches; never hide them.
3. **Curated by-trait computation.** Call
   `compute_prs_by_trait(trait_id, vcf_path, interpret=True, profile="curated",
   superpopulation=<ancestry>)`. The curated profile already drops toy scores
   (<10 variants), no-performance-evidence scores, and below-C_wt-floor coverage,
   and de-dups score families. Read `filter_summary` / `n_filtered` and tell the
   user what was excluded and why. Use `profile="all"` only if explicitly asked.
4. **Read shortlist concordance.** Judge whether the surviving high-quality
   models *agree* on the percentile — a tight cluster of `percentile_reliable`
   models with healthy `weight_mass_coverage` (C_wt) is a strong read; a wide
   spread is weak. Don't over-index on the single best model.
5. **State caveats.** Be explicit about coverage, ancestry match, quality tier,
   and that a PRS is one predisposition factor among many (lifestyle,
   environment, other genetics) — not a diagnosis. For disease traits with a
   z-score, call `absolute_risk` to translate the percentile into a concrete
   lifetime probability.

## Visualizing the panel (optional)

`compute_prs_by_trait` auto-saves the report to disk and returns its
`result_path`. Pass that to **`plot_trait_panel(result_path, include_html=True)`**
to get a Plotly figure spec (markers = models on a reference normal curve; shape
= quality tier, color = reliability/outlier) and a self-contained HTML page you
can save and open in a browser.

## Write-up

Summarize for a citizen scientist. The `interpret_trait_results` MCP prompt
gives a good structure (verdict → model agreement → what the percentile means →
confidence → context & actions). Clarity and honesty over length.

## Caveats baked into the data (know these)

- **Coverage (F15, upstream):** genome-wide scores currently match only ~50% of
  a full WGS callset, so many percentiles are coverage-deflated. `C_wt` /
  `percentile_reliable` are your honesty signals — lean on them.
- **Development ancestry (F19, upstream):** a score's *training* ancestry isn't
  exposed yet; only the reference-panel ancestry (`reference_panel_ancestry`) is.
  Mention this limit when ancestry coherence matters.
