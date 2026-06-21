"""mcp-template — a uv + FastMCP server template (cake-themed demo)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-template")
except PackageNotFoundError:  # running from a source checkout without install
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
