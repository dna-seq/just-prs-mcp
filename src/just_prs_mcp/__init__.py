"""just-prs-mcp — a FastMCP server wrapping the just-prs polygenic-risk-score library."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("just-prs-mcp")
except PackageNotFoundError:  # running from a source checkout without install
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
