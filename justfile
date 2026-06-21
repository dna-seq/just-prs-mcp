# Common tasks. Install `just` (https://github.com/casey/just) or copy the
# underlying `uv ...` commands. Run `just` to list recipes.

default:
    @just --list

# Install all dependencies (incl. dev) into the project venv.
install:
    uv sync

# Run the test suite.
test:
    uv run pytest

# Lint.
lint:
    uv run ruff check .

# Auto-format / autofix.
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Type-check.
types:
    uv run pyright

# Run the server over stdio (default transport for local MCP clients).
run mode="essentials":
    CAKE_MODE={{mode}} uv run mcp-template stdio

# Run over HTTP.
serve mode="essentials" port="3011":
    CAKE_MODE={{mode}} uv run mcp-template http --port {{port}}

# Open the MCP Inspector (interactive dev UI).
dev:
    uv run fastmcp dev fastmcp.json

# Everything CI would run.
ci: lint types test
