"""Shared pytest fixtures: in-memory FastMCP clients (no network/process).

Passing the server object straight to ``Client`` uses FastMCP's in-memory
transport — fast, deterministic, and ideal for agent-driven TDD loops.

These tests cover the MCP *wiring* (tool registration, mode gating, structured
output, error handling) — not just-prs correctness, which has its own real-data
suite. Tools that hit the PGS Catalog / EBI FTP are exercised only in tests
marked ``@pytest.mark.network`` (deselected by default).
"""

from __future__ import annotations

import pytest
from fastmcp.client import Client

from just_prs_mcp.server import build_server
from just_prs_mcp.settings import Mode, Settings


@pytest.fixture
async def essentials_client():
    server = build_server(mode="essentials", settings=Settings())
    async with Client(transport=server) as client:
        yield client


@pytest.fixture
async def extended_client():
    server = build_server(mode="extended", settings=Settings())
    async with Client(transport=server) as client:
        yield client


@pytest.fixture
def make_client():
    """Factory returning a fresh in-memory client (its own session)."""

    def _make(mode: Mode = "essentials", settings: Settings | None = None):
        server = build_server(mode=mode, settings=settings or Settings())
        return Client(transport=server)

    return _make
