# AGENTS.md

Guidance for coding agents (Claude Code, Cursor, Codex, Antigravity, …) working
in this repository. Humans: see `README.md`.

## What this is

An MCP server wrapping the **just-prs** library (PGS Catalog + polygenic risk
score computation). Built on **uv + FastMCP**. It started life as a generic
FastMCP template; the cake demo has been replaced with real PRS tools, but the
template's patterns (mode gating, background tasks, structured I/O, in-memory
tests) are kept.

## Pre-configured Test Genomes

For testing, benchmarking, or demonstration purposes, two public whole-genome sequencing (WGS) datasets are pre-configured in the server's `download_sample_genome` tool:

- **Anton Kulaga's Genome** (CC0 / Public Domain): [Zenodo Record 18370498](https://zenodo.org/records/18370498)
  - VCF File: `antonkulaga.vcf` (~482 MB)
  - Parameter: `sample="anton"`
- **Livia Zaharia's Genome** (CC-BY-4.0): [Zenodo Record 19487816](https://zenodo.org/records/19487816)
  - VCF File: `SIMHIFQTILQ.hard-filtered.vcf.gz` (~349 MB)
  - Parameter: `sample="livia"`

### Quick Play for Agents:
If you are asked to demonstrate or test any PRS computation or VCF normalization, you can download and use these genomes automatically:
1. Call `download_sample_genome(sample="anton")` or `download_sample_genome(sample="livia")`.
2. The tool runs as a background task. It **auto-normalizes by default** (`auto_normalize=True`), so on success `data` carries both `path` (raw VCF) and `normalized_path` (compute-ready Parquet) — no separate `normalize_vcf` call needed. Pass `auto_normalize=False` for the raw VCF only.
3. Pass `data["normalized_path"]` to `compute_prs` / `compute_prs_batch` (as `genotypes_path`) to compute Polygenic Risk Scores.

Both the download and the normalization are idempotent (size-matched VCF / existing Parquet are reused; `reused_cache` / `normalized_reused` flag the hit). A user's own **local** VCF still needs an explicit `normalize_vcf` call — only the sample-download path folds it in.

## Commands (prefer these)

```bash
uv sync                              # install deps (incl. dev)
uv sync --extra reference            # + pgenlib (reference/pgen tools; Linux/WSL)
uv run pytest                        # tests (fast, in-memory — no network)
uv run ruff check .                  # lint
uv run ruff format .                 # format
uv run pyright                       # type-check
uv run just-prs-mcp stdio            # run over stdio
uv run just-prs-mcp stdio --mode extended   # full surface
uv run just-prs-mcp http --port 3011 # run over HTTP
uv run fastmcp dev fastmcp.json      # MCP Inspector (interactive)
```

`just <recipe>` wraps all of the above if `just` is installed.

**Always run `uv run pytest` and `uv run ruff check .` after changing code.**

## Architecture (read before editing)

- `src/just_prs_mcp/server.py` — `build_server(mode, settings)` factory wires
  everything; module-level `mcp` is the instance `fastmcp`/Smithery discover.
  Typer CLI + graceful shutdown live here too.
- `src/just_prs_mcp/settings.py` — `pydantic-settings` (`PRS_MCP_*`); all fields
  default, so the server **never** requires env at boot.
- `src/just_prs_mcp/client.py` — shared construction of `PRSCatalog` /
  `PGSCatalogClient` (honoring the configured cache dir + genome build) and small
  polars→dict adapters. **Use these helpers; don't construct just-prs objects ad hoc.**
- `src/just_prs_mcp/tools/` — tools grouped by module/tier (see below).
- `src/just_prs_mcp/models.py` — Pydantic tool I/O. Reuses just-prs's own
  `PRSResult` / `AbsoluteRisk` / `ScoreInfo` / `TraitInfo` directly where they fit;
  defines summary models for things just-prs returns as polars frames.
- `tests/` — in-memory `Client(transport=build_server(...))`.

## The mode axis (the core pattern)

**Mode (essentials vs extended)** controls which tools *exist*.
- Essentials (`tools/catalog.py`, `tools/compute.py`) are registered in **every** mode,
  including single-score, batch, and by-trait PRS computation.
- Extended (`tools/extended.py`, `tools/reference.py`) are registered **only**
  when `mode == "extended"` (`PRS_MCP_MODE` or `--mode`).
- Why: a smaller default tool list = less context pollution for the agent. The
  extended tier is also where slow / heavyweight / optional-dependency tools live.

There is **no auth tier** — just-prs needs no API key. The one credentialed tool
(`push_catalog_to_hf`) resolves a token from `PRS_MCP_HF_TOKEN` / `HF_TOKEN` per
call and returns a friendly `OpResult(success=False)` if none is set.

### Mode → tool map (F23)

- **Essentials (always on):**
  - `tools/catalog.py`: `search_scores`, `score_info`, `best_performance`,
    `search_traits`, `trait_info`.
  - `tools/compute.py`: `normalize_vcf`, `download_sample_genome`, `list_genomes`,
    `compute_prs`, `compute_prs_batch`, `compute_prs_by_trait` (curated by default;
    `profile`/`min_match_rate`/`min_auroc`/`build`/`ancestry` filters),
    `percentile`, `absolute_risk`, `assess_quality`, `compare_genomes`,
    `plot_trait_panel`. Prompts: `compute_prs_for_trait`,
    `interpret_prs_for_trait`, `interpret_prs_result`, `interpret_trait_results`.
- **Extended only (`PRS_MCP_MODE=extended` / `--mode extended`):**
  - `tools/extended.py`: `normalize_array`, `download_scoring_file`,
    `list_pgs_ids`, `download_all_metadata`, `bulk_download_scores`,
    `prevalence_info`, `absolute_risk_bundle`, `push_catalog_to_hf`.
  - `tools/reference.py`: `download_reference_panel`, `reference_score`,
    `reference_score_batch`, `pgen_read_pvar`, `pgen_read_psam`, `pgen_score`.

Essentials tools that bump the gate surface a `needs_extended` breadcrumb (e.g.
`compute_prs_by_trait` sets `needs_extended` + `needs_extended_hint` when raw
`min_match_rate` / `min_auroc` knobs are used). There is deliberately **no**
client→server VCF upload/fetch tool — see dogfooding F24 (privacy/compliance).

## How to add a tool

1. Pick the module/tier: catalog or compute (essentials), extended, or reference.
2. Add a function inside the matching `register_*` function with type hints, a
   docstring (becomes the description), and `ToolAnnotations`
   (`readOnlyHint`/`idempotentHint`/`openWorldHint`).
3. Return a Pydantic model (from `models.py`, or a reused just-prs model) for
   structured output.
4. Long-running work → `task=True` + `ctx: Context`, run blocking just-prs calls
   via `from anyio.to_thread import run_sync`, and report progress with
   `await ctx.info(...)` / `ctx.report_progress(...)`.
5. Construct just-prs objects via the `client` helpers, not directly.
6. Add a test in `tests/` using the in-memory client.

## Conventions

- Keep the **essentials** surface small and obvious.
- **Malformed / missing-file input → `raise ToolError`.** Recoverable states with
  useful payloads (download outcomes, missing credential, optional dep absent)
  → return `OpResult(success=False)` (or a clear `ToolError` for `pgenlib`).
- Server-side logs use stdlib `logging` and **must** go to stderr
  (`logging_setup.py`); client-facing messages use `ctx.info`/`ctx.report_progress`.
- Pin to the **installed** just-prs API (currently 0.4.12) — verify signatures
  against the installed wheel, not an unpublished source tree (they can diverge
  under the same version number). E.g. `PRSCatalog.compute_prs_batch` returns a
  `PRSBatchResult` and accepts `genotypes_lf`.
- Line length 100; ruff rules in `pyproject.toml`.

## Testing notes

just-prs hits the PGS Catalog / EBI FTP / HuggingFace on first use, so CI tests
cover the **MCP wiring** (registration, mode gating, structured output, error
handling) and pure-logic tools (`assess_quality`) — **not** just-prs correctness,
which has its own real-data suite. Keep tests network-free; mark any genuine
round-trip `@pytest.mark.network`.

## Background tasks

Slow tools (`normalize_vcf`, `download_sample_genome`, `compute_prs_batch`,
`compute_prs_by_trait`, the download tools, reference scoring) are real MCP
background tasks
(`@mcp.tool(task=True)`). Some clients expose the task id and polling directly;
others transparently collapse that handshake and return the final result inline.
Powered by the `fastmcp[tasks]` extra; default backend is in-memory (`memory://` —
no Redis). Set `FASTMCP_DOCKET_URL=redis://...` for distributed/persistent tasks.

## Known issues & deferred fixes

Findings carry stable `F#` IDs that cross-reference across the three docs below.
As a finding's state changes, **move** it to the right file (don't duplicate) — a
single `F#` may appear in two files (wrapper-resolved *and* its upstream remainder).

- `docs/dogfooding.md` — running log of *open* quirks/bugs/UX gaps found by
  dogfooding the server end-to-end. Read it before touching the tool surface.
- `docs/previous_issues.md` — dogfooding findings already **resolved in the
  wrapper**, each with its resolution and code pointer. Check here before
  re-investigating a finding that looks fixed.
- `docs/just-prs-pending-fixes.md` — the subset of those findings that need an
  **upstream `just-prs` library** change (or real-data verification) before the
  wrapper can fully resolve them; each notes the defensive wrapper mitigation
  already in place. Add new upstream-blocked items here, not just in code TODOs.
  Current priorities: **F15** (genome-wide scores match only ~50% of a full WGS
  callset — harmonization/variant-matching audit; F13 ruled out RefCall, F18 ruled
  out build, and **F22 is the leading lead: gVCF `END` reference blocks aren't
  expanded to dose-0 genotypes**), **F19** (ancestry coherence — *partially* resolved:
  the percentile reference-panel ancestry is now surfaced, but per-score development
  ancestry, sample-ancestry inference, and the coherence veto are still deferred P3),
  and **F21** (make the trait report filterable — wrapper-actionable; also proposes a
  `prs-trait-interpretation` skill).

## Optional extras

- **Reference / pgen**: `uv sync --extra reference` (pgenlib; Linux/WSL only).
- **Smithery deploy**: `uv sync --extra smithery` (see `pyproject.toml`
  `[tool.smithery]` and `smithery.yaml`).

## Cursor Cloud specific instructions

Deps are pre-installed by the startup update script (`uv sync --extra reference`),
so the `.venv` and the optional `reference`/pgen tools are ready — just use the
`uv run ...` commands already documented above. `uv` is installed to
`~/.local/bin` (on PATH for login shells). The project pins `requires-python >=3.13`
and `uv` provisions its own interpreter (currently CPython 3.14) — system `python3`
is 3.12, so **always go through `uv run`**, never the system Python.

- **Tests/lint/type-check are network-free and fast** (`uv run pytest`, etc.);
  they cover MCP wiring only, not just-prs correctness (see "Testing notes").
- **Running the server actually hits the network.** The first real tool call
  (e.g. `search_scores`) downloads PGS Catalog metadata from HuggingFace / EBI
  into the cache, so the first invocation is slower and emits an unauthenticated
  HF-rate-limit warning (harmless; set `HF_TOKEN`/`PRS_MCP_HF_TOKEN` to silence).
- **Smoke-testing the running server:** start it with
  `PRS_MCP_MODE=extended uv run just-prs-mcp http --host 127.0.0.1 --port 3011`,
  then connect with an in-process `fastmcp.Client("http://127.0.0.1:3011/mcp")`
  and call a tool. A raw `GET /mcp` returns HTTP 406 by design (streamable-HTTP
  needs the MCP handshake headers) — use the MCP client, not plain `curl`, to
  verify. The fastest network-free check is the in-memory harness in `tests/`
  (`Client(transport=build_server(...))`).
