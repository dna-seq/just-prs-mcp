"""Typed configuration for the MCP server.

Everything has a safe default, so the server boots with no environment set
(important for Smithery's immutable-server model and for casual local users).
Values are read from ``CAKE_*`` environment variables and an optional ``.env``.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["essentials", "extended"]


class Settings(BaseSettings):
    """Server settings sourced from ``CAKE_*`` env vars / ``.env`` (all optional)."""

    model_config = SettingsConfigDict(
        env_prefix="CAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Auth — NEVER required at boot. Resolved per-request (see auth.py).
    api_key: str | None = None
    api_key_header: str = "X-Cake-Api-Key"

    # Tool surface. "essentials" keeps the default tool count small to avoid
    # polluting an agent's context; "extended" exposes the full surface.
    mode: Mode = "essentials"

    # Transport / network (used by the CLI; overridable per command).
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 3011

    # Logging (stdlib logging -> stderr; stdout stays a clean JSON-RPC channel).
    log_level: str = "INFO"

    # Domain knob for the cake demo.
    oven_max_temp_c: int = 250
