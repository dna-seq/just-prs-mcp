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

## Where things stand (last sweep 2026-06-23)

- **Resolved in the wrapper → `docs/previous_issues.md`:** F1, F3, F5, F6, F7,
  F8, F14, plus the wrapper-side mitigations for F2, F4, F9, F10, F11. F13 is
  closed (tested & disproven). **F21** (filterable trait report + curated
  profile + `interpret_prs_for_trait` prompt + `prs-trait-interpretation`
  skill), **F23** (mode-gate visibility + `needs_extended` breadcrumb), and
  **F27** (Plotly-JSON `plot_trait_panel`) were resolved in the wrapper on
  2026-06-23 — see `docs/previous_issues.md`.
- **Open, upstream-blocked → `docs/just-prs-pending-fixes.md`:** the library
  remainder of F2, F4, F9, F10, F11; the F15 coverage root cause (F18 ruled out
  build, **F22 is the leading lead — unexpanded gVCF ref blocks**); F19
  (ancestry), F12; the F19/F21 column slice (development ancestry, dev sample
  size, `is_harmonized` — not in the cleaned scores sheet); and the F27
  empirical-distribution exposure (per-individual reference scores).
- **Declined (not planned):** F24 (remote VCF ingest) — see below.

The 2026-06-23 wrapper pass closed the *expressible-filtering* gap: the by-trait
report now carries the metadata columns, the curated profile trims by live
criteria, and the methodology ships as a prompt + skill. The remaining gates that
need library data — development ancestry, the coverage root cause, the empirical
reference distribution — stay upstream.

---

## F24 — Remote server can't ingest a client-local VCF *(DECLINED — not planned, 2026-06-23)*

**Decision:** we will **not** build a client→server VCF upload or fetch-URL path.
Persisting a user's personal genomic/medical data on a remote/hosted server is a
privacy and compliance liability we decline to take on. A user with their own
genome should run the server **locally** (stdio) against their own filesystem; on
a hosted server the only ingest path remains `download_sample_genome` (public
Zenodo sample WGS). The server `instructions` now state this plainly so a remote
user's first compute call doesn't fail confusingly with "VCF not found"
(`server.py`). Left here (not moved to `previous_issues.md`) because it is a
deliberate non-fix, not a resolution.

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

---

## Batch 3 — remote-deployment dogfooding (2026-06-23)

New pass driven against the **hosted** server (`just-prs-web`, cache dir
`/home/web/.cache/just-prs`) instead of a local stdio server. Scenario: "compute
a 5–35-model trait panel from a personal WGS VCF and plot it." The local-vs-remote
filesystem split surfaces a cluster of gaps that don't appear when client and
server share a disk. Trait used: **gout** (`MONDO_0005393`, 32 directly-associated
scores); had to substitute Anton Kulaga's public WGS (`download_sample_genome
sample="anton"`) because the user's own VCF could not reach the server (F24).

## F24 — Remote server can't ingest a client-local VCF; no upload/fetch mechanism *(open / wrapper-actionable)*

All computation tools take **server-side** filesystem paths (`vcf_path`,
`genotypes_path`). Against a hosted server that's a different host/user than the
client, so a path the client can see (`/data/newton/.../newton_winter.vcf`) just
returns `VCF not found`. Today the **only** ways to get genotypes onto the server
are `download_sample_genome` (Zenodo records only) or a file an admin pre-placed in
the cache. A normal remote user with their own WGS has no path in.

- Add a client→server ingest path: an upload tool / resumable-chunk upload, or a
  "fetch this URL/presigned-S3/object-store key" tool that pulls to
  `<cache_dir>/samples/`. `download_sample_genome`'s `record_url` is Zenodo-shaped;
  generalize it (arbitrary HTTPS URL, with size/type guards) as a stopgap.
- Until then, the server `instructions` should state plainly that compute tools
  require server-side paths and that remote users must use `download_sample_genome`
  / a fetch URL — otherwise the first call always fails confusingly.

**F25** (`download_sample_genome` not idempotent — re-downloaded + re-normalized a
cached sample) and **F26** (cached genomes not exposed as an MCP resource) are now
**resolved in the wrapper** → `docs/previous_issues.md`. F25: cache-hit short-circuit
(size match) + `force` param + `reused_cache`/`downloaded_bytes` in the result + atomic
`.part` write; F26: `resource://prs/genomes` (JSON) backed by a shared
`_scan_genome_catalog` helper.

## F27 — No plotting / visualization tool exposed by MCP *(wrapper resolved 2026-06-23; richer chart upstream)*

**Resolved in the wrapper → `docs/previous_issues.md`.** Decision: emit a **Plotly
figure spec (JSON)** the client renders, not a server-rendered PNG — more flexible
(client can restyle / rasterize / drop into HTML+JS) and no heavy render
dependency on the server. `plot_trait_panel(result_path, include_html=False)`
returns a `TraitPanelPlot` (theoretical normal curve + one marker per scored model
at its percentile; shape = quality tier, color = reliability/outlier — mirroring
prs-ui's `bell_curve`), plus an optional self-contained HTML page.

**Upstream remainder → `docs/just-prs-pending-fixes.md` F27:** the richer chart — a
histogram of the 2,504 1000G reference individuals with the user's score marked —
needs `just-prs` to expose per-individual reference scores
(`reference_individual_scores(...)`); today only aggregated summary stats are
public. That doc also carries the prs-ui consumption recommendation so both
surfaces stay fed from one accessor.
