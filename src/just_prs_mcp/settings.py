"""Typed configuration for the just-prs MCP server.

Everything has a safe default, so the server boots with no environment set
(important for Smithery's immutable-server model and for casual local users).
Values are read from ``PRS_MCP_*`` environment variables and an optional ``.env``.

Note: the underlying ``just-prs`` library reads its own ``PRS_CACHE_DIR``,
``PRS_DUCKDB_MEMORY_LIMIT`` and ``HF_TOKEN`` env vars natively. The settings here
use the ``PRS_MCP_`` prefix so server config never collides with those; where a
value maps onto a just-prs concept (cache dir, DuckDB memory limit, HF token) it
is passed through explicitly by the tools.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["essentials", "extended"]


class Settings(BaseSettings):
    """Server settings sourced from ``PRS_MCP_*`` env vars / ``.env`` (all optional)."""

    model_config = SettingsConfigDict(
        env_prefix="PRS_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Tool surface. "essentials" keeps the default tool count small to avoid
    # polluting an agent's context; "extended" exposes the full surface
    # (batch scoring, bulk downloads, reference-panel / pgen tools, HF upload).
    mode: Mode = "essentials"

    # just-prs domain defaults.
    # cache_dir: root for cached catalog metadata, scoring files, reference panels.
    # None -> just-prs resolves its own (PRS_CACHE_DIR / platformdirs).
    cache_dir: str | None = None
    default_genome_build: str = "GRCh38"
    default_panel: str = "1000g"

    # DuckDB memory limit for batch PRS (e.g. "8GB"). None -> just-prs default.
    duckdb_memory_limit: str | None = None

    # HuggingFace token for the catalog-upload tool. None -> just-prs falls back
    # to its own HF_TOKEN env / .env resolution.
    hf_token: str | None = None

    # Transport / network (used by the CLI; overridable per command).
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 3011

    # Agno agent (dev-only; the MCP server itself needs none of these).
    agent_api_key: str | None = None
    agent_model_id: str = "gemini-flash-latest"
    agent_base_url: str | None = None
    agent_timeout: float = 120.0

    # Logging (stdlib logging -> stderr; stdout stays a clean JSON-RPC channel).
    log_level: str = "INFO"
