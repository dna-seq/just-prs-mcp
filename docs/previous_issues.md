# Previous issues — resolved dogfooding findings

Findings from `docs/dogfooding.md` that are **closed in the MCP wrapper**. Each
notes how it was resolved and where the fix lives. Items that still need an
upstream **just-prs library** change are tracked separately in
`docs/just-prs-pending-fixes.md` — where such an item also had a wrapper-side
mitigation, that mitigation is recorded below and the entry cross-links the
upstream tracker.

Ordered by finding number. Date of last sweep: 2026-06-21.

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

## F4 — Effective genome build echoed back *(wrapper resolved; upstream tracked)*

`NormalizeResult` and `TraitPRSReport` now carry the effective `genome_build`
assumed for scoring (`models.py`, `tools/compute.py`). Build *inference from VCF
contigs* and embedding the build in just-prs's `PRSResult` are upstream —
see `just-prs-pending-fixes.md` F4.

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

## F9 — Low-coverage percentile no longer emits a bare `0`/`100` *(wrapper resolved; upstream tracked)*

`percentile` accepts `match_rate` and returns `reliable=False` + a `caveat` when
coverage is low (<90%) or the result is a bare extreme (0/100)
(`_percentile_result`, `tools/compute.py`). Verified by
`test_percentile_low_match_rate_is_unreliable`. Normalizing the raw score by
coverage inside the library is upstream — `just-prs-pending-fixes.md` F9.

## F10 — `assess_quality` / `percentile` no longer contradict *(wrapper resolved; upstream tracked)*

The quality path (`_quality_assessment`) no longer treats percentile availability
as authoritative when a percentile was supplied separately; `percentile` is the
source of reliability/caveat messaging. The `interpret_prs_result` library change
is upstream — `just-prs-pending-fixes.md` F10.

## F11 — Best performance attached in trait reports *(wrapper resolved; upstream tracked)*

`compute_prs_by_trait(interpret=True)` fetches best-performance per score and
feeds AUROC into the quality label so no manual round-trip is needed
(`_best_performance_summary`, `_trait_score_row`). Embedding performance directly
in `PRSResult` and preferring `null` over an empty `effect_size` are upstream —
`just-prs-pending-fixes.md` F11.

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
