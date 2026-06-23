"""FastMCP server: assembly, CLI, and deployment entrypoints.

The registration pattern lives in ``build_server``:

* ``register_catalog``   — always (PGS Catalog lookup; read-only essentials).
* ``register_compute``   — always (normalize + single/batch/by-trait PRS + analysis).
* ``register_extended``  — ONLY when mode == "extended" (downloads, arrays, HF,
  prevalence priors, multi-method absolute risk).
* ``register_reference`` — ONLY when mode == "extended" (reference / pgen scoring).

No authentication tier: just-prs needs no API key for its core work. The
HuggingFace upload tool resolves a token from ``PRS_MCP_HF_TOKEN`` / ``HF_TOKEN``
per call and returns a friendly result if none is set. The server NEVER raises at
startup for missing configuration.
"""

from __future__ import annotations

import signal
import sys

import typer
from fastmcp import FastMCP

from just_prs_mcp import __version__
from just_prs_mcp.logging_setup import get_logger, setup_logging
from just_prs_mcp.settings import Mode, Settings
from just_prs_mcp.tools.catalog import register_catalog
from just_prs_mcp.tools.compute import register_compute
from just_prs_mcp.tools.extended import register_extended
from just_prs_mcp.tools.reference import register_reference

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
        name=f"just-prs MCP v{__version__}",
        instructions=(
            "An MCP server for polygenic risk scores, wrapping the just-prs library "
            "and the PGS Catalog. Essentials (catalog search/lookup, VCF "
            "normalization, PRS computation, percentile/absolute-risk/quality "
            "analysis, batch scoring, by-trait reports, and a sample-genome "
            "download for users without their own VCF) are always available. "
            "Run in 'extended' mode for bulk catalog downloads, HuggingFace upload, "
            "prevalence-prior inspection, multi-method absolute risk, and "
            "reference-panel / pgen scoring (the last needs the optional pgenlib "
            "dependency on Linux/WSL). Computation tools take local file paths "
            "(VCF / normalized Parquet) on the server's filesystem.\n\n"
            "## Methodology\n"
            "Percentiles are computed by scoring the 1000 Genomes Project phase 3 "
            "reference panel (2,504 individuals, 5 superpopulations: AFR, AMR, EAS, "
            "EUR, SAS) on GRCh38 harmonized scoring files from the PGS Catalog. "
            "Each individual's PRS is computed as the sum of effect_weight * dosage "
            "for matched variants, then percentiles are derived per superpopulation. "
            "The user's VCF is scored with the same engine and placed on this "
            "distribution.\n\n"
            "## Quality scoring\n"
            "Each PGS model gets a synthetic quality score (0-100) based on four "
            "tiers: T1a (AUROC/C-index reported, no penalty), T1b (Beta only, 0.95 "
            "penalty), T2 (OR/HR only, 0.90 penalty), T3 (no metric, 0.6 penalty). "
            "The score also factors cohort size (log-scaled), model coverage, and a "
            "harmonized-score penalty if coordinates were lifted over. Quality labels: "
            "High (>=70), Normal (>=50), Moderate (>=30), Low (<30).\n\n"
            "## Interpreting results for users\n"
            "When presenting PRS results, follow these principles:\n"
            "- A PRS is a genetic predisposition score, NOT a measurement of the "
            "trait itself. It reflects one factor among many (lifestyle, environment, "
            "other genetics).\n"
            "- Do NOT assume health — traits may be behavioral, physical, or "
            "cognitive. Only suggest medical action for health traits.\n"
            "- Be honest about limitations: low model coverage means the score used "
            "only part of the PRS model; low quality tier means limited validation "
            "evidence.\n"
            "- Ancestry matters: the reference panel ancestry should match the "
            "individual's ancestry for the percentile to be meaningful. Flag "
            "mismatches.\n"
            "- Model coverage breakdown: 'variants_matched' includes variants "
            "observed in the genome file plus those safely inferred as "
            "homozygous-reference. Some variants are unscorable (unknown reference "
            "allele) or no-called (present but missing genotype). Low coverage "
            "deflates the score.\n"
            "- When multiple models exist for a trait, look at model agreement "
            "(consistency of percentiles), not just the single best model.\n"
            "- Always cite the PGS ID and link to the PGS Catalog page "
            "(https://www.pgscatalog.org/score/{pgs_id}/) so the user can verify.\n"
            "- Target audience is citizen scientists — prioritize clarity and honesty "
            "over length.\n\n"
            "## Absolute risk and trait directionality\n"
            "- After computing a percentile, ALWAYS call ``absolute_risk`` for disease "
            "traits (when a z_score is available from the percentile result). Absolute "
            "risk translates the z-score into a concrete lifetime probability and risk "
            "ratio vs the population average — this is far more actionable than a raw "
            "percentile.\n"
            "- Trait directionality matters when comparing individuals or interpreting "
            "results:\n"
            "  - For disease/risk traits (e.g. DVT, diabetes, cancer): a HIGHER "
            "percentile means MORE genetic risk — this is BAD. When comparing, the "
            "person with LOWER risk 'wins.'\n"
            "  - For positive/ability traits (e.g. intelligence, height, longevity): "
            "a HIGHER percentile means MORE of the trait — this is typically GOOD.\n"
            "  - For neutral traits (e.g. hair color, earwax type): directionality "
            "is not meaningful.\n"
            "- The ``absolute_risk`` tool returns the absolute disease probability and "
            "a risk_ratio (how many times the population average). A risk_ratio of 1.0 "
            "means population-average risk; >1 means elevated; <1 means reduced.\n"
            "- When absolute risk is unavailable (no prevalence data for the trait), "
            "say so explicitly rather than omitting the information silently."
        ),
    )

    register_catalog(mcp, settings)
    register_compute(mcp, settings)
    if resolved_mode == "extended":
        register_extended(mcp, settings)
        register_reference(mcp, settings)

    log.info("Server built (mode=%s)", resolved_mode)
    return mcp


# Module-level instance for `fastmcp run` / `fastmcp dev` / Smithery discovery.
# Safe to import: no key required, no network calls at import time.
mcp = build_server()


# --------------------------------------------------------------------------- #
# Graceful shutdown (clean SIGINT/SIGTERM handling)
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
# Typer CLI — `just-prs-mcp [main|stdio|http|sse] --mode ...`
# --------------------------------------------------------------------------- #
app = typer.Typer(add_completion=False, help="just-prs MCP server.")

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
    """Run the server (transport from --transport or PRS_MCP_TRANSPORT)."""
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
# No boot-time configuration is required; cache dir / mode are optional.
# --------------------------------------------------------------------------- #
def _smithery_unavailable(ctx):  # pragma: no cover - only when extra missing
    raise RuntimeError("Smithery support requires the 'smithery' extra: uv sync --extra smithery")


try:
    from pydantic import BaseModel, Field
    from smithery.decorators import smithery  # type: ignore[import-not-found]

    class SmitheryConfigSchema(BaseModel):
        mode: str | None = Field(default=None, description="essentials | extended")
        cache_dir: str | None = Field(
            default=None, description="Optional cache directory for catalog/scoring data."
        )

    @smithery.server(config_schema=SmitheryConfigSchema)
    def start_mcp_smithery(ctx):  # pragma: no cover - run by Smithery runtime
        """Smithery entrypoint: return a fresh server."""
        return build_server()

except ImportError:  # pragma: no cover - smithery extra not installed
    start_mcp_smithery = _smithery_unavailable


if __name__ == "__main__":
    app()
