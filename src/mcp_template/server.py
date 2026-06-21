"""FastMCP server: assembly, CLI, and deployment entrypoints.

The hybrid registration pattern lives in ``build_server``:

* ``register_essentials`` — always (the tools present in every mode).
* ``register_auth``       — always (the per-session ``authenticate`` tool).
* ``register_bakery_cloud`` — always listed, auth enforced per call.
* ``register_extended``   — ONLY when mode == "extended" (registered on start).

The server NEVER raises at startup for a missing key (see auth.py).
"""

from __future__ import annotations

import signal
import sys

import typer
from fastmcp import FastMCP

from mcp_template import __version__
from mcp_template.auth import SessionKeyStore, register_auth
from mcp_template.logging_setup import get_logger, setup_logging
from mcp_template.settings import Mode, Settings
from mcp_template.tools.bakery_cloud import register_bakery_cloud
from mcp_template.tools.extended import register_extended
from mcp_template.tools.recipes import register_essentials

log = get_logger()


def build_server(mode: Mode | None = None, settings: Settings | None = None) -> FastMCP:
    """Construct a fresh, fully-wired FastMCP server.

    A factory (not a singleton) so each Smithery session / test gets an isolated
    instance. Pass ``mode`` to override ``settings.mode``.
    """
    settings = settings or Settings()
    resolved_mode: Mode = mode or settings.mode
    setup_logging(settings)

    mcp = FastMCP(
        name=f"Cake MCP Template v{__version__}",
        instructions=(
            "A cake-themed FastMCP template. Essentials (recipes + baking) are "
            "always available; run in 'extended' mode for more tools. Bakery "
            "Cloud tools require an API key via the `authenticate` tool."
        ),
    )

    store = SessionKeyStore()
    register_essentials(mcp, settings)
    register_auth(mcp, settings, store)
    register_bakery_cloud(mcp, settings, store)
    if resolved_mode == "extended":
        register_extended(mcp, settings)

    log.info("Server built (mode=%s)", resolved_mode)
    return mcp


# Module-level instance for `fastmcp run` / `fastmcp dev` / Smithery discovery.
# Safe to import: no key required, no network calls.
mcp = build_server()


# --------------------------------------------------------------------------- #
# Graceful shutdown (ported pattern: clean SIGINT/SIGTERM handling)
# --------------------------------------------------------------------------- #
class GracefulShutdownHandler:
    """Handle SIGINT/SIGTERM so the server stops cleanly; double-signal forces."""

    def __init__(self) -> None:
        self.shutdown_requested = False
        self._orig_sigint = None
        self._orig_sigterm = None

    def register_handlers(self) -> None:
        self._orig_sigint = signal.signal(signal.SIGINT, self._handle)
        self._orig_sigterm = signal.signal(signal.SIGTERM, self._handle)

    def restore_handlers(self) -> None:
        if self._orig_sigint is not None:
            signal.signal(signal.SIGINT, self._orig_sigint)
        if self._orig_sigterm is not None:
            signal.signal(signal.SIGTERM, self._orig_sigterm)

    def _handle(self, signum: int, frame) -> None:
        if self.shutdown_requested:
            log.warning("Force shutdown requested")
            sys.exit(1)
        self.shutdown_requested = True
        name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        log.info("Received %s, shutting down gracefully...", name)
        raise KeyboardInterrupt()


def run_with_graceful_shutdown(server: FastMCP, **run_kwargs) -> None:
    """Run ``server.run(**run_kwargs)`` with graceful shutdown handling."""
    handler = GracefulShutdownHandler()
    try:
        handler.register_handlers()
        log.info("Starting server: %s", run_kwargs or "stdio")
        server.run(**run_kwargs)
    except KeyboardInterrupt:
        log.info("Shutdown signal received, cleaning up...")
    except Exception:
        log.exception("Server error")
        raise
    finally:
        handler.restore_handlers()
        log.info("Server stopped")


# --------------------------------------------------------------------------- #
# Typer CLI — `mcp-template [main|stdio|http|sse] --mode ...`
# --------------------------------------------------------------------------- #
app = typer.Typer(add_completion=False, help="Cake MCP Template server.")

_MODE_OPT = typer.Option(None, "--mode", help="essentials | extended")


def _run(transport: str, mode: str | None, host: str | None, port: int | None) -> None:
    settings = Settings()
    server = build_server(mode=mode, settings=settings)  # type: ignore[arg-type]
    kwargs: dict = {"transport": transport}
    if transport != "stdio":
        kwargs["host"] = host or settings.host
        kwargs["port"] = port or settings.port
    run_with_graceful_shutdown(server, **kwargs)


@app.command()
def main(
    mode: str = _MODE_OPT,
    transport: str = typer.Option(None, help="stdio | http | sse"),
    host: str = typer.Option(None, help="Host to bind (network transports)."),
    port: int = typer.Option(None, help="Port to bind (network transports)."),
) -> None:
    """Run the server (transport from --transport or CAKE_TRANSPORT)."""
    settings = Settings()
    _run(transport or settings.transport, mode, host, port)


@app.command()
def stdio(mode: str = _MODE_OPT) -> None:
    """Run with the stdio transport (for local MCP clients)."""
    _run("stdio", mode, None, None)


@app.command()
def http(
    mode: str = _MODE_OPT,
    host: str = typer.Option(None),
    port: int = typer.Option(None),
) -> None:
    """Run with the streamable-HTTP transport."""
    _run("http", mode, host, port)


@app.command()
def sse(
    mode: str = _MODE_OPT,
    host: str = typer.Option(None),
    port: int = typer.Option(None),
) -> None:
    """Run with the (legacy) SSE transport."""
    _run("sse", mode, host, port)


def cli_app() -> None:
    """Console-script entrypoint (see [project.scripts])."""
    app()


# --------------------------------------------------------------------------- #
# Optional Smithery cloud deployment (guarded; needs the `smithery` extra).
# Note: NO boot-time key requirement. The Smithery-injected per-request config
# key is consumed by resolve_api_key at call time, not stored globally here.
# --------------------------------------------------------------------------- #
def _smithery_unavailable(ctx):  # pragma: no cover - only when extra missing
    raise RuntimeError(
        "Smithery support requires the 'smithery' extra: uv sync --extra smithery"
    )


try:
    from pydantic import BaseModel, Field
    from smithery.decorators import smithery  # type: ignore[import-not-found]

    class SmitheryConfigSchema(BaseModel):
        api_key: str | None = Field(
            default=None, description="Optional Bakery Cloud API key."
        )

    @smithery.server(config_schema=SmitheryConfigSchema)
    def start_mcp_smithery(ctx):  # pragma: no cover - run by Smithery runtime
        """Smithery entrypoint: return a fresh server (key resolved per request)."""
        return build_server()

except ImportError:  # pragma: no cover - smithery extra not installed
    start_mcp_smithery = _smithery_unavailable


if __name__ == "__main__":
    app()
