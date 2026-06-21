# just-prs-mcp

An MCP server that wraps **[just-prs](https://pypi.org/project/just-prs/)** — a
[Polars](https://pola.rs/)-based tool for **Polygenic Risk Scores (PRS)** from the
[PGS Catalog](https://www.pgscatalog.org/). It exposes catalog search, PRS
computation, VCF/array normalization, percentile & absolute-risk estimation,
quality assessment, and (in extended mode) bulk downloads and reference-panel /
pgen scoring as MCP tools.

Built on **uv + [FastMCP](https://gofastmcp.com)**.

> Agents: start with [AGENTS.md](./AGENTS.md).

## Highlights

- **Hybrid tool registration with modes** — a small always-on `essentials`
  surface (catalog lookup + the core compute/analyze workflow) plus an
  `extended` surface (batch, bulk downloads, HuggingFace upload, reference/pgen
  scoring) registered on opt-in. Keeps the default tool list small.
- **Real background tasks** — slow operations (`normalize_vcf`,
  `compute_prs_batch`, downloads, reference scoring) run as
  `@mcp.tool(task=True)` with FastMCP's in-memory backend (no Redis);
  `FASTMCP_DOCKET_URL` switches to Redis for scale.
- **Structured I/O** via Pydantic models + tool annotations — just-prs's own
  `PRSResult` / `AbsoluteRisk` / `TraitInfo` are returned directly where they fit.
- **Boot-safe** — the server starts with no environment configured; no API key
  is required for any core feature.
- **In-memory test harness** (`Client(transport=server)`) — fast, deterministic,
  network-free.

## Quickstart

```bash
uv sync                                  # install deps (incl. dev)
uv sync --extra reference                # + pgenlib for reference/pgen tools (Linux/WSL)
uv run pytest                            # tests, all in-memory (no network)
uv run just-prs-mcp stdio                # run over stdio
uv run just-prs-mcp stdio --mode extended  # expose the full tool surface
uv run just-prs-mcp http                 # run over HTTP (default :3011)
uv run fastmcp dev fastmcp.json          # MCP Inspector
```

The server **boots with no environment configured.**

## Tools

| Tool | Tier | Notes |
|------|------|-------|
| `search_scores` | essentials | Search the PGS Catalog by free text |
| `score_info` | essentials | Cleaned metadata for one PGS ID |
| `best_performance` | essentials | Best evaluation metrics (OR/HR/AUROC/C-index) |
| `search_traits` | essentials | REST trait search |
| `trait_info` | essentials | Trait by EFO ID + associated PGS IDs |
| `normalize_vcf` | essentials | VCF → genotype Parquet (background task) |
| `compute_prs` | essentials | Score one VCF against one PGS |
| `percentile` | essentials | Percentile of a PRS value (reference/theoretical/AUROC) |
| `absolute_risk` | essentials | Absolute disease risk from a PRS z-score |
| `assess_quality` | essentials | Quality label + interpretation (pure logic) |
| `compute_prs_batch` | extended | Score one VCF against many PGS (background task) |
| `normalize_array` | extended | 23andMe / AncestryDNA → Parquet (background task) |
| `download_scoring_file` | extended | One harmonized scoring file from EBI FTP |
| `list_pgs_ids` | extended | All PGS IDs on EBI FTP |
| `download_all_metadata` | extended | All metadata sheets as Parquet (background task) |
| `bulk_download_scores` | extended | Many/all scoring files (background task) |
| `push_catalog_to_hf` | extended | Upload cleaned catalog to HuggingFace (needs token) |
| `download_reference_panel` | extended | Fetch 1000G / HGDP+1kGP panel (background task) |
| `reference_score` / `reference_score_batch` | extended | Score against a panel (needs `pgenlib`) |
| `pgen_read_pvar` / `pgen_read_psam` / `pgen_score` | extended | PLINK2 binary ops (needs `pgenlib`) |

Plus a resource (`resource://prs/panels`) and a prompt (`compute_prs_for_trait`).

> **File paths:** computation tools take local paths (VCF / normalized Parquet /
> `.pgen` dir) on the **server's** filesystem. Over stdio that's your machine.
> **Reference / pgen tools** need the optional native `pgenlib` (Linux/WSL —
> `uv sync --extra reference`); without it they return a clear install hint.

## Modes

`PRS_MCP_MODE` (env) or `--mode` (CLI), default `essentials`:

- `essentials` — catalog lookup + the core compute/analyze workflow.
- `extended` — everything (batch, bulk downloads, HF upload, reference/pgen).

## Configuration

All `PRS_MCP_*` env vars are optional (see `.env.example` and `settings.py`):
`PRS_MCP_MODE`, `PRS_MCP_CACHE_DIR`, `PRS_MCP_DEFAULT_GENOME_BUILD`,
`PRS_MCP_DEFAULT_PANEL`, `PRS_MCP_DUCKDB_MEMORY_LIMIT`, `PRS_MCP_HF_TOKEN`,
`PRS_MCP_TRANSPORT`, `PRS_MCP_HOST`, `PRS_MCP_PORT`, `PRS_MCP_LOG_LEVEL`.

`PRS_MCP_CACHE_DIR` sets the root for cached catalog metadata, scoring files, and
reference panels; if unset, just-prs uses its own default (`PRS_CACHE_DIR` /
platformdirs). The HuggingFace upload tool reads `PRS_MCP_HF_TOKEN` or the native
`HF_TOKEN`.

## Using with coding agents

`.mcp.json` (Claude Code) launches `uv run just-prs-mcp stdio`. For **Codex**
(`~/.codex/config.toml`):

```toml
[mcp_servers.just-prs]
command = "uv"
args = ["run", "just-prs-mcp", "stdio"]
```

## Deployment

- **Docker**: `docker build -t just-prs-mcp . && docker run -p 3011:3011 just-prs-mcp`
  (defaults to HTTP).
- **Smithery**: `uv sync --extra smithery`; entrypoint in `pyproject.toml`
  `[tool.smithery]` + `smithery.yaml`.
- **Declarative**: `fastmcp.json` drives `fastmcp run` / `fastmcp dev`.

## Project layout

```
src/just_prs_mcp/
  server.py        build_server(), CLI, graceful shutdown, Smithery entrypoint
  settings.py      pydantic-settings (PRS_MCP_*), safe defaults
  client.py        shared PRSCatalog / REST-client construction + adapters
  models.py        Pydantic tool I/O models (+ reused just-prs models)
  logging_setup.py stdlib logging -> stderr
  tools/
    catalog.py       essentials — PGS Catalog lookup
    compute.py       essentials — normalize / compute / analyze
    extended.py      extended — batch, downloads, HF upload
    reference.py     extended — reference-panel / pgen scoring (pgenlib)
tests/             in-memory client tests (wiring, not just-prs correctness)
```

## License

MIT — see [LICENSE](./LICENSE).
