"""Server-side logging via the standard library ``logging`` module.

IMPORTANT: under the stdio transport, ``stdout`` carries the JSON-RPC stream.
All logs MUST go to ``stderr`` or they will corrupt the protocol. We therefore
attach a single ``StreamHandler(sys.stderr)``.

Client-facing logs/progress (things the MCP *client* should see) go through the
FastMCP ``Context`` instead: ``await ctx.info(...)`` / ``ctx.report_progress(...)``.
"""

from __future__ import annotations

import logging
import sys

from just_prs_mcp.settings import Settings

LOGGER_NAME = "just_prs_mcp"
_CONFIGURED = False


def setup_logging(settings: Settings | None = None) -> logging.Logger:
    """Configure and return the package logger (idempotent)."""
    global _CONFIGURED
    settings = settings or Settings()
    logger = logging.getLogger(LOGGER_NAME)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)  # stderr — never stdout
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
        _CONFIGURED = True
    logger.setLevel(settings.log_level.upper())
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
