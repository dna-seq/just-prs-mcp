"""Shared helpers for the just-prs tool modules.

Centralizes construction of the ``PRSCatalog`` / REST client (honoring the
server's configured cache dir and genome build) plus small polars→dict adapters
so each tool module stays thin.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from just_prs_mcp.settings import Settings

if TYPE_CHECKING:  # avoid importing heavy just-prs internals at module import time
    import polars as pl
    from just_prs.catalog import PGSCatalogClient
    from just_prs.prs_catalog import PRSCatalog


def cache_dir(settings: Settings) -> Path | None:
    """Resolve the configured cache dir as a Path, or None to let just-prs decide."""
    return Path(settings.cache_dir).expanduser() if settings.cache_dir else None


def resolved_cache_dir(settings: Settings) -> Path:
    """Resolve the cache dir to a concrete Path (falling back to just-prs default)."""
    from just_prs.scoring import resolve_cache_dir

    return cache_dir(settings) or resolve_cache_dir()


def build(settings: Settings, override: str | None) -> str:
    """Pick the genome build: explicit override > server default."""
    return override or settings.default_genome_build


def panel(settings: Settings, override: str | None) -> str:
    """Pick the reference panel: explicit override > server default."""
    return override or settings.default_panel


def make_catalog(settings: Settings) -> PRSCatalog:
    """Construct a ``PRSCatalog`` bound to the configured cache dir."""
    from just_prs.prs_catalog import PRSCatalog

    return PRSCatalog(cache_dir=cache_dir(settings))


def make_rest_client() -> PGSCatalogClient:
    """Construct a PGS Catalog REST client (a context manager — close it)."""
    from just_prs.catalog import PGSCatalogClient

    return PGSCatalogClient()


def records(lf: pl.LazyFrame, limit: int) -> list[dict]:
    """Collect a LazyFrame to a capped list of row dicts."""
    df = lf.head(limit).collect() if limit and limit > 0 else lf.collect()
    return df.to_dicts()
