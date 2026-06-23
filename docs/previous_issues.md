# Previous issues — resolved dogfooding findings

Findings from `docs/dogfooding.md` that are **closed in the MCP wrapper**. Each
notes how it was resolved and where the fix lives. Items that still need an
upstream **just-prs library** change are tracked separately in
`docs/just-prs-pending-fixes.md` — where such an item also had a wrapper-side
mitigation, that mitigation is recorded below and the entry cross-links the
upstream tracker.

Ordered by finding number. Date of last sweep: 2026-06-22.

**2026-06-22 — `just-prs` 0.4.8 upstream landing.** The scoring-foundations library
fixes for **F9** (percentile reliability metadata), **F10**, **F11**, and **F12** were
published in `just-prs` **0.4.8**, pinned in `pyproject.toml`, and verified against the
installed wheel. They moved out of `just-prs-pending-fixes.md` into the entries below;
each now records both the wrapper resolution and the confirmed upstream API. F9's
coverage-*normalization* remainder stays open as **F15**; F20 (composite quality gate)
remains partially resolved upstream.

**2026-06-22 — `just-prs` 0.4.9 (Batch 2) landing.** Genome-build detection (**F4**) and
single-score `genotypes_lf` (**F23**) were published in **0.4.9**, pinned, verified, and
adopted in the wrapper — both moved here. **F19** is only *partially* resolved (percentile
reference-panel ancestry now surfaced; dev-ancestry / sample inference / coherence veto
still deferred P3), so it stays in `just-prs-pending-fixes.md` with the resolved slice
recorded there. This sweep also fixed a latent wrapper bug: `compute_prs_batch` returns a
`PRSBatchResult`, not a bare `list[PRSResult]` (since the 0.4.8 batch change), so the
batch tool and by-trait report now read `.results` / `.outcomes` instead of iterating the
model directly.

---

## F1 — "Compute all PRS for a trait" tool + batch in the base surface *(resolved)*

`compute_prs_by_trait(trait_id, vcf_path, ...)` and `compute_prs_batch` are both
registered in **essentials** (`tools/compute.py`, `register_compute`). By-trait
resolves the trait's `associated_pgs_ids` (plus `child_associated_pgs_ids` when
`include_children=True`), scores them via the batch path, and returns a structured
`TraitPRSReport`. A `limit` caps how many are *computed* and reports skipped IDs
explicitly (no silent truncation). Mode-gating covered by `tests/test_modes.py`.

## F2 — Forgiving trait search *(wrapper resolved; upstream tracked)*

`search_traits` now retries punctuation/word-order variants and falls back to a
token-AND filter over labels+synonyms when the first REST query is empty
(`_search_traits_forgiving`, `tools/catalog.py`). Verified by
`test_search_traits_defaults_to_counts_and_retries_empty_query`. The deeper
"make the REST search itself token-aware / did-you-mean" fix is upstream —
see `just-prs-pending-fixes.md` F2.

## F3 — `trait_info` / `compute_prs_by_trait` accept EFO *or* MONDO *(resolved)*

`trait_info(trait_id=...)` is the documented parameter; `efo_id` is kept as a
deprecated alias. Docstrings now read "by ontology ID (EFO or MONDO)".
`compute_prs_by_trait` takes `trait_id` with the same semantics. Verified by
`test_trait_info_accepts_trait_id_and_efo_id`.

## F4 — Genome build echoed back *and* detected from the VCF *(wrapper resolved; upstream landed in 0.4.9)*

`NormalizeResult` and `TraitPRSReport` carry the effective `genome_build` assumed for
scoring (`models.py`, `tools/compute.py`).

**Upstream (landed in `just-prs` 0.4.9, verified 2026-06-22).** `compute_prs` /
`compute_prs_duckdb` now call `just_prs.vcf.detect_genome_build()` (contig-length +
`##reference` voting) and surface `detected_genome_build` + `build_mismatch` on
`PRSResult`, logging a warning on mismatch. Detection is guarded: it runs only on a real,
VCF-suffixed, existing file, so a pre-normalized Parquet / array / empty path yields
`detected_genome_build=None` + `build_mismatch=False` (never a false mismatch).

**Wrapper (0.4.9 adoption).** `compute_prs` / `compute_prs_batch` return the just-prs
`PRSResult` directly, so `detected_genome_build` / `build_mismatch` are surfaced
automatically. `compute_prs_by_trait` now folds them up to the report:
`TraitPRSReport.detected_genome_build` + `build_mismatch` (per-VCF, captured from the
first scored result) and a `WARNING: VCF build detected as … but scored on …` line in the
summary when they disagree (`tools/compute.py`). `None` detected build means "couldn't
tell," not "match." Verified by `test_compute_prs_by_trait_attaches_performance`.

## F5 — Slim `search_traits` payloads *(resolved)*

`search_traits` returns compact `TraitSummary` rows with `n_associated` /
`n_child_associated` counts by default; pass `include_pgs_ids=True` for the full
arrays (reserved for single-trait `trait_info`). Verified by
`test_search_traits_can_return_full_pgs_id_arrays` and the counts test.

## F6 — Background-task docs reconciled with inline return *(resolved)*

`normalize_vcf`'s docstring and `AGENTS.md` now state that it runs as a real MCP
background task but that some clients transparently collapse the task/poll
handshake and return the result inline. Doc-only; no behavior change.

## F7 — Positive confirmations + regression tests *(resolved / recorded)*

Confirmed working and to keep: BGZF-as-`.vcf` transparent read, `pass_filters`
behavior, Ensembl (no-`chr`) chromosome naming. Network-free regression tests
were added where feasible (mode gating, by-trait `top_n`/aggregates, Zenodo
helpers). BGZF-named-`.vcf` and real MONDO round-trip remain `@pytest.mark.network`
TODOs since they require live data/network per the testing policy.

## F8 — First-class `TraitPRSReport` model *(resolved)*

`models.py` defines `TraitPRSReport` (trait id/label, genome build, per-score
`TraitScoreRow`s with score + match_rate + percentile + reliability + quality +
effect size, plus trait-level counts and a summary). `compute_prs_by_trait`
optionally rolls up percentile/quality/performance per score with `interpret=True`.

## F9 — Low-coverage percentile no longer emits a bare `0`/`100` *(wrapper now uses the library C_wt verdict; 0.4.8)*

**Upstream (landed in `just-prs` 0.4.8, verified 2026-06-22).** `PRSResult` now carries
weight-mass coverage `C_wt` as `weight_mass_coverage` (plus `weight_mass_matched` /
`weight_mass_total`), and `PRSCatalog.percentile_full(score, pgs_id,
weight_mass_coverage=...)` returns a `PercentileResult` with `reliable: bool` + `caveat:
str`, flipping to `reliable=False` below `MIN_RELIABLE_WEIGHT_MASS_COVERAGE` (0.20,
`prs_catalog.py`) while keeping the percentile value. The legacy count-based
`MIN_PERCENTILE_MATCH_RATE` gate is left in place; the C_wt verdict is purely additive.

**Wrapper (0.4.8 adoption).** `_percentile_result` (`tools/compute.py`) now calls
`percentile_full(weight_mass_coverage=...)` and surfaces the library's `reliable`/`caveat`
verdict — the old `match_rate < 0.9` heuristic is **removed**. The `percentile` tool's
`match_rate` parameter was replaced by `weight_mass_coverage` (C_wt), trait-report rows
carry `weight_mass_coverage` and `_row_rank_key` ranks on it ahead of match_rate, and a
thin guard still flags a bare 0/100 only when no coverage signal was supplied. Verified by
`test_percentile_low_coverage_is_unreliable`. The remaining gap — coverage-*normalizing*
the raw score before comparison — is still upstream as **F15** in
`just-prs-pending-fixes.md`.

## F10 — `assess_quality` / `percentile` no longer contradict *(wrapper now forwards method/reliability; 0.4.8)*

**Upstream (landed in `just-prs` 0.4.8, verified 2026-06-22).**
`interpret_prs_result(percentile, match_rate, auroc, percentile_method=None,
reliable=True, caveat='')` gained the three backward-compatible parameters: the summary
now describes the *actual* percentile derivation via a method→phrase map, appends the
`caveat` when `reliable=False`, and uses a generic "no population percentile available"
sentence instead of blaming missing allele frequencies.

**Wrapper (0.4.8 adoption).** `_quality_assessment` (`tools/compute.py`) now forwards
`percentile_method` / `reliable` / `caveat` straight into `interpret_prs_result`, and the
**summary string-patching hack is removed** — the library's text is consistent with
whichever method ran. The `assess_quality` tool gained the same optional parameters, and
the trait-report path threads the `percentile_full` method/verdict through.

## F11 — Best performance attached in trait reports *(wrapper now uses attach_performance; 0.4.8)*

**Upstream (landed in `just-prs` 0.4.8, verified 2026-06-22).** `compute_prs` and
`compute_prs_batch` gained an opt-in `attach_performance: bool = False`; when `True` the
score's `best_performance()` row is composed onto `PRSResult.performance` as a
`PerformanceInfo` (effect sizes, AUROC/C-index, evaluation ancestry, sample number) — no
separate round-trip. `format_effect_size` / `format_classification` now return
`str | None` (None == "no estimate", distinct from an empty value).

**Wrapper (0.4.8 adoption).** `compute_prs_by_trait(interpret=True)` now passes
`attach_performance=True` into `compute_prs_batch`, and `_trait_score_row` reads AUROC +
effect size off `result.performance` via `_auroc_from_performance` /
`_format_effect_size` — the **per-score `best_performance` round-trip is eliminated** in
the common VCF path (`_best_performance_summary` is kept only as the fallback for the
low-level `genotypes_path` branch, which can't attach — that residual gap is tracked
upstream as **F23** in `just-prs-pending-fixes.md`). The `compute_prs` /
`compute_prs_batch` tools also expose an opt-in `attach_performance` parameter, and the
widened `str | None` formatters are coalesced to `""` at the display layer
(`tools/catalog.py`, `tools/compute.py`). Verified by
`test_compute_prs_by_trait_attaches_performance`.

## F12 — Raw-score → z-score → absolute-risk chain closed *(upstream landed in 0.4.8)*

This was a purely upstream gap: `absolute_risk` required a z-score, but `compute_prs`
returned only a raw score and `percentile` threw away the `z = (score − mean)/std` it
computed internally. The wrapper could only mitigate the *prior* side — `prevalence_info`
surfaces the prevalence table and `absolute_risk_bundle` returns every method's estimate
with the prior it used (extended mode); it could not produce a reliable z-score alone.

**Upstream (landed in `just-prs` 0.4.8, verified 2026-06-22).** `PercentileResult`
(`PRSCatalog.percentile_full(...)`) and `PRSResult` now expose the true `z_score`,
`reference_mean`, and `reference_std` actually used (no more lossy percentile inversion
that clamped to `z=0` at the 0/100 extremes), and
`PRSCatalog.absolute_risk_from_score(pgs_id, score, ancestry=, sex=,
weight_mass_coverage=)` chains raw score → true-z → `absolute_risk_bundle` in one call.
Risk numbers at the distribution extremes are now correct.

**Wrapper (0.4.8 adoption).** The `percentile` tool's `PercentileResult` now carries the
true `z_score` / `reference_mean` / `reference_std` from `percentile_full`, so a caller
can feed absolute risk without inverting the percentile. The extended `absolute_risk_bundle`
tool gained a raw-`score` path (plus `ancestry` / `weight_mass_coverage`): supply a raw
score instead of a z-score and it routes through `absolute_risk_from_score` (true-z chain).
Verified by `test_absolute_risk_bundle_from_raw_score` /
`test_absolute_risk_bundle_requires_score_or_z`.

## F13 — `pass_filters=["PASS"]` / RefCall hom-ref drop *(closed — tested & disproven)*

Re-normalizing the dogfooding VCF without `pass_filters` (6,139,024 vs 4,725,262
variants) and re-scoring PGS000014 gave match_rate **0.3768** vs **0.3744** —
effectively identical. Dropping ~1.41M RefCall/hom-ref records is **not** the
cause of low coverage. No library change needed; the F9 reliability flag is the
safeguard. The real low-coverage driver is the open upstream item F15.

## F14 — Big by-trait reports no longer blow the output-token limit *(resolved)*

`compute_prs_by_trait` gained a `top_n` parameter: rows are ranked best-coverage
first (scored before failed, reliable percentile before not, higher match rate
first; `_row_rank_key`) and trimmed to `top_n`, with `n_omitted` reporting the
count. `TraitPRSReport` now also carries `n_reliable`, `mean_match_rate`,
`n_returned`, and `n_omitted`, so the trait-level rollup always reflects **every**
score even when rows are trimmed — trimming is explicit, never silent. Verified by
`test_compute_prs_by_trait_top_n_trims_and_ranks`.

## F17 — Zenodo sample-genome download works *(test passed — recorded)*

`download_sample_genome(sample="anton")` succeeded end-to-end: returned a clean
`OpResult(success=True)` with `data.path`, downloading `antonkulaga.vcf`
(482,783,972 bytes / 0.48 GB, Anton Kulaga, CC0) into the requested `output_dir`.
File verified as valid VCFv4.2, GRCh38 (chr1 len 248,956,422), Ensembl chromosome
naming, DeepVariant-style (`RefCall` FILTER) — a drop-in path for
`normalize_vcf`/`compute_prs`. Good onboarding UX (recoverable `OpResult` rather
than an exception; lands where asked).

Minor, deferred (neither blocks anything): the file arrives as plain uncompressed
`.vcf` (not `.vcf.gz`/BGZF), and the docstring's "several GB for a full WGS
genome" overstates Anton's 0.48 GB — loosen the size hint.

## F23 — Single-score `genotypes_lf` so normalized-Parquet reuse attaches performance in one call *(wrapper now uses it; 0.4.9)*

**Upstream (landed in `just-prs` 0.4.9, verified 2026-06-22).** `PRSCatalog.compute_prs`
gained `genotypes_lf` (mirroring `compute_prs_batch`): it forwards the pre-normalized
frame to the low-level compute and runs `_attach_performance` itself, so a single score
reuses a normalized Parquet **and** attaches best performance in one call. This closes the
gap from the 0.4.8 round, where the batch method had both params but the single-score
method had only `attach_performance`.

**Wrapper (0.4.9 adoption).** The `compute_prs` tool's `genotypes_path` branch now calls
`cat.compute_prs(..., genotypes_lf=pl.scan_parquet(...), attach_performance=...)` — the
low-level free-function branch and its manual `score_info_row` trait lookup are **gone**.
`compute_prs_by_trait` is unified onto a **single** `compute_prs_batch(genotypes_lf=...,
attach_performance=interpret)` call for both the VCF and Parquet-reuse paths, so the
per-score loop and the `_best_performance_summary` fallback are **removed entirely**
(`_best_performance_summary` deleted). Per-score errors come from the batch's `outcomes`.
Verified by `test_compute_prs_genotypes_path_attaches_performance` and
`test_compute_prs_by_trait_attaches_performance`.

## F25 — `download_sample_genome` is now idempotent *(resolved)*

A second call for an already-cached sample no longer re-streams the ~hundreds-of-MB
VCF (or re-normalizes). After the (cheap) Zenodo metadata fetch resolves the target
filename and size, the tool checks `<cache_dir>/samples/<file>`: if it exists and the
on-disk size matches Zenodo's reported size, the content download is skipped and the
cached file reused; if `auto_normalize=True` and the Parquet already exists, normalization
is skipped too. A new `force=False` param overrides both. `data` now echoes
`reused_cache` (download skipped) and `downloaded_bytes` (0 on a cache hit) so a caller
can tell a cache hit from a fresh fetch — the result message also flips to "Reused cached
…". Downloads write to a `.part` temp file and atomically `Path.replace` into place so two
clients fetching the same sample can't read a half-written VCF. `tools/compute.py`
(`download_sample_genome`). Verified by `test_download_sample_genome_idempotent`.

## F26 — Cached genomes exposed as an MCP resource *(resolved)*

The server now serves `resource://prs/genomes` (JSON) alongside `resource://prs/panels`,
so a client can enumerate the server-side paths it may pass as `vcf_path` /
`genotypes_path` from the resource list rather than only via the `list_genomes` tool — the
discovery surface the remote split (F24) makes essential. The scan that `list_genomes`
performed was extracted into a shared `_scan_genome_catalog(settings)` helper; both the
tool and the resource call it, so the inventory (downloaded VCFs, normalized Parquets, and
the pre-configured downloadable samples, each with server paths/sizes/aliases) is computed
exactly one way. `tools/compute.py` (`_scan_genome_catalog`, `genomes` resource,
`list_genomes`). Verified by `test_genomes_resource_listed` and
`test_genomes_resource_mirrors_list_genomes`. The per-genome templated URI
(`resource://prs/genomes/{alias}`) noted as a "bonus" in the finding was not added — the
single-resource inventory covers discovery.
