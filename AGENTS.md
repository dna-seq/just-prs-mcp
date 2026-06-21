# AGENTS.md

Guidance for coding agents (Claude Code, Cursor, Codex, Antigravity, …) working
in this repository. Humans: see `README.md`.

## What this is

A template for a **uv + FastMCP** server. The demo domain is cake/baking — swap
it for your own. Keep the patterns; replace the tools.

## Commands (prefer these)

```bash
uv sync                 # install deps (incl. dev)
uv run pytest           # run tests (fast, in-memory — no network)
uv run ruff check .     # lint
uv run ruff format .    # format
uv run pyright          # type-check
uv run mcp-template stdio          # run over stdio
uv run mcp-template http --port 3011   # run over HTTP
uv run fastmcp dev fastmcp.json    # MCP Inspector (interactive)
```

`just <recipe>` wraps all of the above if `just` is installed.

**Always run `uv run pytest` and `uv run ruff check .` after changing code.**

## Architecture (read before editing)

- `src/mcp_template/server.py` — `build_server(mode, settings)` factory wires
  everything; module-level `mcp` is the instance `fastmcp`/Smithery discover.
  Typer CLI + graceful shutdown live here too.
- `src/mcp_template/settings.py` — `pydantic-settings`; all fields default, so
  the server **never** requires env at boot.
- `src/mcp_template/auth.py` — per-session, per-request API-key resolution.
- `src/mcp_template/tools/` — tools grouped by tier (see below).
- `src/mcp_template/models.py` — Pydantic models = typed tool I/O.
- `tests/` — in-memory `Client(transport=build_server(...))`.

## The two gating axes (the core patterns)

1. **Mode (essentials vs extended)** — controls which tools *exist*.
   - Essentials (`tools/recipes.py`) are registered in **every** mode.
   - Extended (`tools/extended.py`) are registered **only** when
     `mode == "extended"` (set via `CAKE_MODE` or `--mode`).
   - Why: a smaller default tool list = less context pollution for the agent.
2. **Auth (per session)** — controls whether key-gated tools *work*.
   - Gated tools (`tools/bakery_cloud.py`, tag `bakery_cloud`) are always listed
     but enforce a key **per call** via `resolve_api_key`.
   - Key precedence: `X-Cake-Api-Key` header → Smithery session config →
     per-session store (set by the `authenticate` tool) → `CAKE_API_KEY` env.
   - No key? The tool returns a friendly `OpResult(success=False)`, never raises.
   - **Never** store a key in server-global state, and **never** use
     `mcp.enable()`/`disable()` to gate per-user on multi-tenant HTTP — it is
     server-global and would leak tools across clients. (A documented
     single-tenant stdio-only variant lives in `auth.py`.)

## How to add a tool

1. Pick the tier: essentials (always), extended (opt-in), or key-gated.
2. Add a function inside the matching `register_*` function with type hints, a
   docstring (becomes the description), and `ToolAnnotations`
   (`readOnlyHint`/`idempotentHint`/`destructiveHint`).
3. Return a Pydantic model from `models.py` for structured output.
4. Key-gated tools: take `ctx: Context`, call `require_key(ctx, settings, store)`,
   and return `unauthenticated_result(settings)` when it's `None`. Tag them
   `bakery_cloud`. Keep `GATED_TOOLS` in `auth.py` in sync.
5. Add a test in `tests/` using the in-memory client.

## Conventions

- Keep the **essentials** surface small and obvious.
- Tools should **return structured `OpResult`/models on failure**, not raise,
  unless the input is malformed (then `ToolError` is fine).
- Server-side logs use stdlib `logging` and **must** go to stderr
  (`logging_setup.py`); client-facing messages use `ctx.info`/`ctx.report_progress`.
- Line length 100; ruff rules in `pyproject.toml`.

## Background tasks

`bake_cake` is a real MCP background task (`@mcp.tool(task=True)`): the client
gets a task id immediately, polls for status, and gets the result when done.
Powered by the `fastmcp[tasks]` extra (a core dependency here). The default
backend is **in-memory** (`memory://`) — zero config, no Redis, embedded worker.
For distributed/persistent tasks set `FASTMCP_DOCKET_URL=redis://...`. Add
`task=True` to any tool to make it a background task. `await client.call_tool(...)`
transparently submits, polls, and returns the final result.

## Optional extras

- **Smithery deploy**: `uv sync --extra smithery` (see `pyproject.toml`
  `[tool.smithery]` and `smithery.yaml`).

## Renaming the template

Rename the dist (`pyproject.toml` `name`) and the package dir
`src/mcp_template/`; update imports, `[project.scripts]`, `fastmcp.json` source
path, and the `.mcp.json`/`.cursor`/`.vscode` configs. Change the `CAKE_` env
prefix in `settings.py`.
