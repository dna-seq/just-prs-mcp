# Minimal uv-based image. Defaults to the streamable-HTTP transport.
FROM python:3.11-slim

# uv (and uvx) from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . .

# Install only runtime deps into a project venv.
RUN uv sync --no-dev

ENV CAKE_TRANSPORT=http \
    CAKE_HOST=0.0.0.0 \
    CAKE_PORT=3011
EXPOSE 3011

CMD ["uv", "run", "mcp-template", "http", "--host", "0.0.0.0", "--port", "3011"]
