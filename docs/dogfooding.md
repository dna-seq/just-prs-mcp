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
  remainder of F2, F4, F9, F10, F11, and the two items below that have *no*
  wrapper-only fix.

Nothing wrapper-actionable is currently outstanding. The two findings below are
the active work, both blocked on just-prs.

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
