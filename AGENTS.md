# AGENTS.md

Guidance for coding agents (Claude Code, Cursor, Codex, Antigravity, …) working
in this repository. Humans: see `README.md`.

## What this is

An MCP server wrapping the **just-prs** library (PGS Catalog + polygenic risk
score computation). Built on **uv + FastMCP**. It started life as a generic
FastMCP template; the cake demo has been replaced with real PRS tools, but the
template's patterns (mode gating, background tasks, structured I/O, in-memory
tests) are kept.

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
- Essentials (`tools/catalog.py`, `tools/compute.py`) are registered in **every** mode.
- Extended (`tools/extended.py`, `tools/reference.py`) are registered **only**
  when `mode == "extended"` (`PRS_MCP_MODE` or `--mode`).
- Why: a smaller default tool list = less context pollution for the agent. The
  extended tier is also where slow / heavyweight / optional-dependency tools live.

There is **no auth tier** — just-prs needs no API key. The one credentialed tool
(`push_catalog_to_hf`) resolves a token from `PRS_MCP_HF_TOKEN` / `HF_TOKEN` per
call and returns a friendly `OpResult(success=False)` if none is set.

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
- Pin to the **installed** just-prs API (currently 0.4.7) — verify signatures
  against the installed wheel, not an unpublished source tree (they can diverge
  under the same version number). E.g. `PRSCatalog.compute_prs_batch` returns a
  `list[PRSResult]` and takes no `genotypes`/`memory_limit`.
- Line length 100; ruff rules in `pyproject.toml`.

## Testing notes

just-prs hits the PGS Catalog / EBI FTP / HuggingFace on first use, so CI tests
cover the **MCP wiring** (registration, mode gating, structured output, error
handling) and pure-logic tools (`assess_quality`) — **not** just-prs correctness,
which has its own real-data suite. Keep tests network-free; mark any genuine
round-trip `@pytest.mark.network`.

## Background tasks

Slow tools (`normalize_vcf`, `compute_prs_batch`, the download tools, reference
scoring) are real MCP background tasks (`@mcp.tool(task=True)`): the client gets a
task id immediately, polls, and receives the result when done. Powered by the
`fastmcp[tasks]` extra; default backend is in-memory (`memory://` — no Redis).
Set `FASTMCP_DOCKET_URL=redis://...` for distributed/persistent tasks.

## Optional extras

- **Reference / pgen**: `uv sync --extra reference` (pgenlib; Linux/WSL only).
- **Smithery deploy**: `uv sync --extra smithery` (see `pyproject.toml`
  `[tool.smithery]` and `smithery.yaml`).
