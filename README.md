# just-prs-mcp: Polygenic Risk Scores for AI Agents

[![PyPI version](https://badge.fury.io/py/just-prs-mcp.svg)](https://pypi.org/project/just-prs-mcp/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Research use only](https://img.shields.io/badge/use-research%20only-orange.svg)](#research-use-only)
[![Not medical advice](https://img.shields.io/badge/medical-not%20advice-red.svg)](#research-use-only)
[![MCP server](https://img.shields.io/badge/MCP-Claude%20%7C%20Cursor%20%7C%20Codex-blueviolet.svg)](#using-with-claude-cursor-codex-antigravity-or-other-agents)
[![FastMCP](https://img.shields.io/badge/FastMCP-server-2ea44f.svg)](https://gofastmcp.com)

An [MCP](https://modelcontextprotocol.io/) server that gives Claude Code, Cursor,
Codex, Antigravity, and other AI agents access to the
**[just-prs](https://github.com/antonkulaga/just-prs)** toolbox — 5,000+
published polygenic scoring models from the
[PGS Catalog](https://www.pgscatalog.org/), VCF normalization, PRS computation,
population percentiles, absolute-risk estimation, cross-genome comparison, and
quality assessment. Ask an agent in plain language; it calls the right tools and
explains the results.

Source: [github.com/dna-seq/just-prs-mcp](https://github.com/dna-seq/just-prs-mcp).
Built on **uv + [FastMCP](https://gofastmcp.com)**.

> Coding agents: start with [AGENTS.md](./AGENTS.md).

## What Can You Ask an Agent To Do?

```
"Download Anton's public genome, normalize it, and compute a type 2 diabetes PRS."

"Search the PGS Catalog for breast cancer scores and show the best-performing ones."

"Score both Anton's and Livia's genomes for DVT and intelligence, then compare them."

"Compute PRS for this local VCF and explain the percentile and absolute risk."

"List all genomes I've already normalized and score the latest one for longevity."
```

### What is a PRS?

Many traits and common diseases — type 2 diabetes, coronary artery disease,
height, longevity — are **polygenic**: influenced by thousands of small genetic
effects rather than one single gene. A Polygenic Risk Score (PRS) adds those
effects together and places the result relative to a reference population. It is
not a diagnosis, but it can help visualize inherited predisposition and, where
enough evidence exists, translate a percentile into an absolute-risk estimate.

### What is MCP?

The [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) is an open
standard that lets AI assistants call external tools. This server exposes PRS
computation as MCP tools so any compatible agent — Claude Code, Cursor, Codex,
Antigravity, or others — can search the PGS Catalog, normalize genomes, compute
scores, and interpret results, all from a natural-language conversation. No
genomics expertise required to get started; the agent handles the workflow.

## Contents

- [What Can You Ask an Agent To Do?](#what-can-you-ask-an-agent-to-do)
- [Quickstart](#quickstart)
- [Using with Claude, Cursor, Codex, Antigravity, or other agents](#using-with-claude-cursor-codex-antigravity-or-other-agents)
- [Test Genomes (Quick Play)](#test-genomes-quick-play)
- [Tools](#tools)
- [Prompts and Resources](#prompts-and-resources)
- [Typical Agent Workflow](#typical-agent-workflow)
- [Modes](#modes)
- [Configuration](#configuration)
- [Methodology](#methodology)
- [Research Use Only](#research-use-only)
- [Deployment](#deployment)
- [Project Layout](#project-layout)
- [License](#license)

## Quickstart

Requires Python >= 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
# Install
uv sync                                    # deps (incl. dev)
uv sync --extra reference                  # + pgenlib for reference/pgen tools (Linux/WSL)

# Run
uv run just-prs-mcp stdio                 # stdio transport (for MCP clients)
uv run just-prs-mcp stdio --mode extended  # expose the full tool surface
uv run just-prs-mcp http                   # HTTP transport (default :3011)
uv run fastmcp dev fastmcp.json            # MCP Inspector (interactive UI)

# Test
uv run pytest                              # all in-memory, no network needed
uv run ruff check .                        # lint
uv run pyright                             # type-check
```

The server **boots with no environment configured** — no API keys, no cache
directory, no database. Every setting is optional.

## Using with Claude, Cursor, Codex, Antigravity, or other agents

### Hosted server — nothing to install

A public instance is hosted at **`https://just-prs-mcp.just-dna.life/mcp`** (HTTP
transport). Point any MCP client at it — no Python, no `uvx`, no local cache.

**Claude Code:**

```bash
claude mcp add --transport http just-prs https://just-prs-mcp.just-dna.life/mcp
```

**Cursor / Claude Desktop / other `mcpServers` JSON:**

```json
{
  "mcpServers": {
    "just-prs": {
      "type": "http",
      "url": "https://just-prs-mcp.just-dna.life/mcp"
    }
  }
}
```

> **Hosted instance only serves the two built-in sample genomes.** Computation
> tools take **server-side** file paths, and the hosted server provides **no
> upload or fetch path for your own VCF** — by design, to avoid persisting
> personal genomic data on a remote host. You can therefore only score the
> pre-loaded public samples — **Anton Kulaga** (`sample="anton"`) and **Livia
> Zaharia** (`sample="livia"`) — via `download_sample_genome`. To analyze **your
> own** genome, run the server **locally** (below) against your own filesystem.

### Published package — no clone needed

The server is on [PyPI](https://pypi.org/project/just-prs-mcp/), so any MCP
client can launch it with [`uvx`](https://docs.astral.sh/uv/) — no clone or
install step needed.

**Claude Code:**

```bash
claude mcp add just-prs -- uvx just-prs-mcp@latest stdio   # always newest
claude mcp add just-prs -- uvx just-prs-mcp@0.1.2 stdio    # pinned (reproducible)
claude mcp list                                             # → just-prs ... ✔ Connected
```

**Cursor** (`.cursor/mcp.json` in your project or user MCP config):

```json
{
  "mcpServers": {
    "just-prs": {
      "command": "uvx",
      "args": ["just-prs-mcp@latest", "stdio"],
      "env": { "PRS_MCP_MODE": "essentials" }
    }
  }
}
```

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.just-prs]
command = "uvx"
args = ["just-prs-mcp@latest", "stdio"]
```

**Antigravity** or another MCP-capable assistant: add the same server command
in its MCP settings — `uvx just-prs-mcp@latest stdio`.

> **Version pinning.** `uvx` caches the first version it resolves for a bare
> name, so `uvx just-prs-mcp` keeps running that cached build. Use `@latest`
> to always fetch the newest, or `@<version>` to pin. A bare name is the worst
> of both — avoid it.

Use `--mode extended` (or `PRS_MCP_MODE=extended`) to expose the full tool
surface including batch downloads, HuggingFace upload, prevalence priors,
multi-method absolute risk, and reference-panel scoring.

### From a clone (development)

The repo's `.mcp.json` launches the working tree via `uv run just-prs-mcp stdio`.
Codex equivalent:

```toml
[mcp_servers.just-prs]
command = "uv"
args = ["run", "just-prs-mcp", "stdio"]
```

## Test Genomes (Quick Play)

Two public whole-genome sequencing (WGS) datasets from the
[just-dna-lite](https://github.com/dna-seq/just-dna-lite) project are built in,
so you (and your AI agent) can try PRS computation without needing your own
genomic data:

| Sample | Zenodo | VCF | Size | License | Tool parameter |
|--------|--------|-----|------|---------|----------------|
| Anton Kulaga | [18370498](https://zenodo.org/records/18370498) | `antonkulaga.vcf` | ~482 MB | CC0 (public domain) | `sample="anton"` |
| Livia Zaharia | [19487816](https://zenodo.org/records/19487816) | `SIMHIFQTILQ.hard-filtered.vcf.gz` | ~349 MB | CC-BY-4.0 | `sample="livia"` |

Just ask your agent:

```
"Download Anton's sample genome, normalize it, and compute the PRS for type 2 diabetes."
```

Under the hood, the agent calls `download_sample_genome` → `normalize_vcf` →
`compute_prs_by_trait` → `percentile` → `absolute_risk`.

## Tools

### Essentials (always available)

| Tool | Description |
|------|-------------|
| `search_scores` | Search the PGS Catalog by free text |
| `score_info` | Cleaned metadata for one PGS ID |
| `best_performance` | Best evaluation metrics (OR / HR / AUROC / C-index) |
| `search_traits` | REST trait search with synonym retry |
| `trait_info` | Trait by EFO / MONDO ID + associated PGS IDs |
| `list_genomes` | Inventory of downloaded and normalized genomes in the cache |
| `download_sample_genome` | Fetch a public sample WGS VCF from Zenodo (background task) |
| `normalize_vcf` | VCF → genotype Parquet (background task) |
| `compute_prs` | Score one VCF against one PGS model |
| `compute_prs_batch` | Score one VCF against many PGS models (background task) |
| `compute_prs_by_trait` | Score all PGS models for a trait, auto-save result to disk (background task) |
| `percentile` | Population percentile (reference panel / theoretical / AUROC fallback) |
| `absolute_risk` | Absolute disease risk from a PRS z-score + population prevalence |
| `assess_quality` | Quality label + interpretation (pure logic, no I/O) |
| `compare_genomes` | Cross-genome comparison from saved `compute_prs_by_trait` results |

### Extended (opt-in via `--mode extended`)

| Tool | Description |
|------|-------------|
| `normalize_array` | 23andMe / AncestryDNA → Parquet (background task) |
| `download_scoring_file` | One harmonized scoring file from EBI FTP |
| `list_pgs_ids` | All PGS IDs on EBI FTP |
| `download_all_metadata` | All metadata sheets as Parquet (background task) |
| `bulk_download_scores` | Many/all scoring files (background task) |
| `prevalence_info` | Population prevalence priors for a score or trait |
| `absolute_risk_bundle` | Multi-method absolute-risk estimation |
| `push_catalog_to_hf` | Upload cleaned catalog to HuggingFace (needs token) |
| `download_reference_panel` | Fetch 1000G / HGDP+1kGP panel (background task) |
| `reference_score` / `reference_score_batch` | Score against a reference panel (needs `pgenlib`) |
| `pgen_read_pvar` / `pgen_read_psam` / `pgen_score` | PLINK2 binary ops (needs `pgenlib`) |

> **File paths:** computation tools take local paths (VCF / normalized Parquet /
> `.pgen` dir) on the **server's** filesystem. Over stdio that's your machine.
> **Reference / pgen tools** need the optional native `pgenlib` (Linux/WSL —
> `uv sync --extra reference`); without it they return a clear install hint.

## Prompts and Resources

MCP prompts are reusable prompt templates that agents can invoke to structure
their interpretation of results:

| Prompt | Description |
|--------|-------------|
| `compute_prs_for_trait` | Step-by-step workflow: search → normalize → score → interpret |
| `interpret_prs_result` | Interpret a single PRS result (verdict, key numbers, context, actions) |
| `interpret_trait_results` | Interpret combined results across multiple models for one trait |

Resource: `resource://prs/panels` — lists available reference panels, supported
genome builds, and the active cache directory.

## Typical Agent Workflow

A full PRS analysis through the MCP server follows this chain:

```
1. search_traits("venous thromboembolism")     → find trait ID (EFO_0001645)
2. download_sample_genome(sample="anton")       → download VCF
3. normalize_vcf(vcf_path)                      → VCF → Parquet
4. compute_prs_by_trait(trait_id, vcf_path)      → score all PGS models, auto-save JSON
5. percentile(prs_score, pgs_id)                → population percentile + z-score
6. absolute_risk(pgs_id, z_score)               → lifetime probability + risk ratio
7. assess_quality(match_rate, auroc, percentile) → quality label
```

For **cross-genome comparison**, repeat steps 2–6 for each genome, then:

```
8. compare_genomes(result_paths=[...])          → ranked comparison across genomes
```

`compute_prs_by_trait` auto-saves each result as JSON in the cache directory and
returns the file path in `result_path`. Pass those paths to `compare_genomes` to
get per-trait rankings sorted by percentile (high → low, no directionality
judgment — the agent interprets whether high is good or bad for each trait),
percentile spread, model consistency, and the most divergent traits highlighted.

## Modes

`PRS_MCP_MODE` (env) or `--mode` (CLI), default `essentials`:

| Mode | What's registered |
|------|-------------------|
| `essentials` | Catalog lookup + core compute/analyze workflow + genome comparison. Small default tool list = less context pollution for the agent. |
| `extended` | Everything: batch downloads, HuggingFace upload, prevalence priors, multi-method absolute risk, reference-panel / pgen scoring. |

## Configuration

All settings are optional — the server boots with sensible defaults.
See [`.env.example`](./.env.example) and
[`settings.py`](src/just_prs_mcp/settings.py) for the full list.

| Variable | Description |
|----------|-------------|
| `PRS_MCP_MODE` | `essentials` (default) or `extended` |
| `PRS_MCP_CACHE_DIR` | Root for cached catalog data, scoring files, reference panels, and saved results. Defaults to just-prs's own (`PRS_CACHE_DIR` / platformdirs). |
| `PRS_MCP_DEFAULT_GENOME_BUILD` | Default genome build (`GRCh38`) |
| `PRS_MCP_DEFAULT_PANEL` | Default reference panel (`1000g`) |
| `PRS_MCP_DUCKDB_MEMORY_LIMIT` | DuckDB memory limit for batch scoring (e.g. `8GB`) |
| `PRS_MCP_HF_TOKEN` | HuggingFace token for `push_catalog_to_hf` (also honors `HF_TOKEN`) |
| `PRS_MCP_TRANSPORT` | `stdio` / `http` / `sse` |
| `PRS_MCP_HOST` / `PRS_MCP_PORT` | Bind address for HTTP/SSE (default `0.0.0.0:3011`) |
| `PRS_MCP_LOG_LEVEL` | Logging level (`info` by default) |

## Methodology

### Percentile estimation

Percentiles are computed by scoring the **1000 Genomes Project phase 3**
reference panel (2,504 individuals, 5 superpopulations: AFR, AMR, EAS, EUR, SAS)
on GRCh38 harmonized scoring files from the PGS Catalog. Each individual's PRS
is computed as `Σ(effect_weight × dosage)` for matched variants, then percentiles
are derived per superpopulation. The user's VCF is scored with the same engine
and placed on this distribution.

### Quality scoring

Each PGS model gets a synthetic quality score (0–100) based on four tiers:
- **T1a**: AUROC / C-index reported (strongest evidence)
- **T1b**: Beta only (0.95× penalty)
- **T2**: OR / HR only (0.90× penalty; converted via probit transform)
- **T3**: No performance metric (0.6× floor)

The score also factors cohort size (log-scaled), model coverage, and a
harmonized-score penalty if coordinates were lifted over. Quality labels:
High (≥70), Normal (≥50), Moderate (≥30), Low (<30).

### Absolute risk

For disease traits, `absolute_risk` converts a PRS z-score into a concrete
lifetime probability and risk ratio vs the population average, using trait
prevalence and published effect-size data. A `risk_ratio` of 1.0 means
population-average risk; >1 means elevated; <1 means reduced. When prevalence
data is unavailable, the tool raises an error — the agent should disclose this
explicitly.

### Interpreting results

The server's built-in instructions guide connected agents to:
- Present PRS as genetic predisposition, not a measurement of the trait itself.
- Always call `absolute_risk` after `percentile` for disease traits.
- Respect trait directionality: higher percentile = more risk for disease traits
  (bad), more of the trait for positive traits (good), meaningless for neutral traits.
- Flag ancestry mismatches, low coverage, and model disagreement.
- Cite PGS IDs with links to the [PGS Catalog](https://www.pgscatalog.org/).

For a thorough discussion of PRS interpretation, quality methodology, ancestry
considerations, and common questions, see the
[just-prs documentation](https://github.com/antonkulaga/just-prs#research-use-only-interpreting-prs-results).

## Research Use Only

PRS results from this server are for **research and educational purposes only**
and do not constitute medical advice. Key caveats:

- PRS models are statistical proxies, not causal readouts. Most GWAS variants are
  tag SNPs in linkage disequilibrium with causal loci, not the causal variants
  themselves.
- Many published scores have limited validation, narrow ancestry representation,
  or modest predictive power. Being listed in the PGS Catalog does not mean a
  score is clinically ready.
- A high PRS shifts estimated risk relative to a reference population, but
  environment, lifestyle, age, sex, and clinical biomarkers often matter as much
  as or more than the common-variant signal.
- Low match rates (common with microarray-based consumer tests) mean the score
  used only a fragment of the model — noisier and less informative.
- Ancestry matters: scores trained in one population often lose accuracy in
  another due to differing LD patterns and allele frequencies.

A high PRS is not a diagnosis; a low PRS is not a guarantee.

## Deployment

- **Docker**: `docker build -t just-prs-mcp . && docker run -p 3011:3011 just-prs-mcp`
  (defaults to HTTP).
- **Smithery**: `uv sync --extra smithery`; entrypoint in `pyproject.toml`
  `[tool.smithery]` + `smithery.yaml`.
- **Declarative**: `fastmcp.json` drives `fastmcp run` / `fastmcp dev`.

## Project Layout

```
src/just_prs_mcp/
  server.py          build_server(), CLI, graceful shutdown, Smithery entrypoint
  settings.py        pydantic-settings (PRS_MCP_*), safe defaults
  client.py          shared PRSCatalog / REST-client construction + adapters
  models.py          Pydantic tool I/O models (+ reused just-prs models)
  logging_setup.py   stdlib logging → stderr
  tools/
    catalog.py         essentials — PGS Catalog search and lookup
    compute.py         essentials — normalize, compute, analyze, compare
    extended.py        extended — batch downloads, HF upload, prevalence, multi-risk
    reference.py       extended — reference-panel / pgen scoring (pgenlib)
tests/               in-memory client tests (wiring + logic, no network)
```

## License

MIT — see [LICENSE](./LICENSE).
