# mcp-template

A **uv + [FastMCP](https://gofastmcp.com)** server template — a clean skeleton
with the patterns, scripts, and glue you actually need, optimized for use with
agentic coding tools (Claude Code, Cursor, Codex, Antigravity).

The demo domain is **cake/baking** (recipes, a simulated long-running bake, and a
fake "Bakery Cloud" API for the auth demo). Replace the cake tools with your own;
keep the structure.

> Agents: start with [AGENTS.md](./AGENTS.md).

## Highlights

- **uv** packaging with the `uv_build` backend.
- **Hybrid tool registration with modes** — an `essentials` surface that's always
  on, plus `extended` tools registered on start. Keeps the default tool list
  small to avoid polluting an agent's context.
- **Boot-safe runtime auth** — the server never requires a key to start. Key-gated
  tools resolve credentials **per request / per session** (multi-user safe), via
  an `authenticate` tool, an HTTP header, Smithery config, or env.
- **Real background tasks** out of the box — `@mcp.tool(task=True)` with FastMCP's
  in-memory backend (no Redis); `FASTMCP_DOCKET_URL` switches to Redis for scale.
- **Structured I/O** via Pydantic models + tool annotations.
- **In-memory test harness** (`Client(transport=server)`) — fast, deterministic,
  no network.
- **Pre-wired client configs** for Claude Code, Cursor, and VS Code.
- Optional **Docker** and **Smithery** deployment.

## Quickstart

```bash
uv sync
uv run pytest                      # 14 tests, all in-memory
uv run mcp-template stdio          # run over stdio
uv run mcp-template http           # run over HTTP (default :3011)
uv run mcp-template stdio --mode extended   # expose the full tool surface
uv run fastmcp dev fastmcp.json    # MCP Inspector
```

The server **boots with no environment configured.**

## Tools (cake demo)

| Tool | Tier | Key? | Notes |
|------|------|------|-------|
| `list_recipes` | essentials | no | read-only |
| `get_recipe` | essentials | no | read-only |
| `bake_cake` | essentials | no | real background task (`task=True`), streams progress |
| `authenticate` | always | — | unlocks gated tools for *this session* |
| `scale_recipe` | extended | no | `--mode extended` |
| `suggest_pairings` | extended | no | `--mode extended` |
| `continue_bake` | extended | no | `--mode extended` |
| `order_custom_cake` | gated | yes | needs an API key (demo: `cake_*`) |
| `delivery_status` | gated | yes | needs an API key |

Plus a resource (`resource://cakes/pantry`) and a prompt (`bake_a_cake`).

## Modes

`CAKE_MODE` (env) or `--mode` (CLI), default `essentials`:

- `essentials` — minimal, casual surface (low context cost).
- `extended` — everything.

## Auth model (read this)

The server **never** raises at startup for a missing key. Key-gated tools resolve
a key **per request**, in this order:

1. `X-Cake-Api-Key` HTTP header (multi-user safe)
2. Smithery-injected session config
3. per-session key set via the `authenticate` tool
4. `CAKE_API_KEY` env (single-tenant / local default)

If none resolve, gated tools return a friendly message (no exception). A key set
via `authenticate` is scoped to the caller's own session, so it never leaks
between HTTP clients. See [AGENTS.md](./AGENTS.md) for the multi-tenant caveat
about `mcp.enable()`.

## Using with coding agents

Pre-wired configs are included:

- Claude Code → `.mcp.json`
- Cursor → `.cursor/mcp.json`
- VS Code → `.vscode/mcp.json`

They launch `uv run mcp-template stdio`. For **Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.cake]
command = "uv"
args = ["run", "mcp-template", "stdio"]
```

## Configuration

All `CAKE_*` env vars are optional (see `.env.example` and `settings.py`):
`CAKE_API_KEY`, `CAKE_MODE`, `CAKE_TRANSPORT`, `CAKE_HOST`, `CAKE_PORT`,
`CAKE_LOG_LEVEL`, `CAKE_API_KEY_HEADER`, `CAKE_OVEN_MAX_TEMP_C`.

## Deployment

- **Docker**: `docker build -t cake-mcp . && docker run -p 3011:3011 cake-mcp`
  (defaults to HTTP).
- **Smithery**: `uv sync --extra smithery`; entrypoint in `pyproject.toml`
  `[tool.smithery]` + `smithery.yaml`.
- **Declarative**: `fastmcp.json` drives `fastmcp run` / `fastmcp dev`.

## Project layout

```
src/mcp_template/
  server.py        build_server(), CLI, graceful shutdown, Smithery entrypoint
  settings.py      pydantic-settings (CAKE_*), safe defaults
  auth.py          per-session/per-request key resolution + authenticate tool
  models.py        Pydantic tool I/O models
  logging_setup.py stdlib logging -> stderr
  tools/
    recipes.py       essentials (always)
    extended.py      extended-only (mode=extended)
    bakery_cloud.py  key-gated (tag: bakery_cloud)
    data.py          in-memory recipe fixtures
tests/             in-memory client tests
```

## Make it yours

See the "Renaming the template" section in [AGENTS.md](./AGENTS.md).

## License

MIT — see [LICENSE](./LICENSE).
