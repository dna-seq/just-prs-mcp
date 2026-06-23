"""Agno-powered natural-language agent for PRS queries.

Wraps the just-prs library directly (not the MCP tool layer) so the agent can
call tools synchronously.  Agno and a model backend (``openai`` or
``google-genai``) are dev dependencies — the MCP server runs fine without them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from just_prs_mcp import client
from just_prs_mcp.settings import Settings


def _load_agent() -> type[Any]:
    try:
        from agno.agent import Agent
    except ImportError as error:
        raise RuntimeError(
            "The natural-language agent requires dev dependencies. "
            "Install them with `uv sync --dev`."
        ) from error
    return Agent


def _is_gemini_model(model_id: str) -> bool:
    return model_id.lower().startswith("gemini")


def get_agent_api_key(settings: Settings) -> str | None:
    """Return the configured model API key, or *None* if none is set."""
    explicit = (settings.agent_api_key or "").strip()
    if explicit:
        return explicit
    if _is_gemini_model(settings.agent_model_id):
        return (
            os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip()
            or None
        )
    return None


def _create_model(settings: Settings, api_key: str) -> Any:
    try:
        if _is_gemini_model(settings.agent_model_id):
            from agno.models.google import Gemini

            return Gemini(
                id=settings.agent_model_id,
                api_key=api_key,
                timeout=settings.agent_timeout,
            )
        from agno.models.openai import OpenAIChat

        return OpenAIChat(
            id=settings.agent_model_id,
            api_key=api_key,
            base_url=settings.agent_base_url or None,
            timeout=settings.agent_timeout,
        )
    except ImportError as error:
        raise RuntimeError(
            "The configured agent model requires missing dev dependencies. "
            "Install them with `uv sync --dev`."
        ) from error


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_agent(settings: Settings | None = None) -> Any:
    """Create an Agno agent that answers PRS questions using just-prs tools."""
    settings = settings or Settings()
    api_key = get_agent_api_key(settings)
    if not api_key:
        raise ValueError(
            "Set PRS_MCP_AGENT_API_KEY, GEMINI_API_KEY, or GOOGLE_API_KEY "
            "in .env or the environment to use the agent."
        )

    agent_cls = _load_agent()

    # ---- tool wrappers (all synchronous) ----

    def search_scores(
        query: str, genome_build: str | None = None, limit: int = 25
    ) -> list[dict[str, Any]]:
        """Search the PGS Catalog for polygenic scores by free text.

        Case-insensitive substring match across PGS ID, score name, reported
        trait, and EFO trait. Returns up to ``limit`` matches with key metadata.
        """
        lf = client.make_catalog(settings).search(query, genome_build=genome_build)
        return client.records(lf, limit)

    def score_info(pgs_id: str) -> dict[str, Any] | None:
        """Get metadata for a single PGS score by its ID (e.g. 'PGS000001').

        Returns trait, variant count, genome build, and quality label.
        """
        return client.make_catalog(settings).score_info_row(pgs_id)

    def best_performance(pgs_id: str) -> dict[str, Any]:
        """Look up the best published evaluation for a PGS score.

        Returns AUROC/C-index, effect sizes (OR/HR/Beta), cohort size and
        ancestry. Use this to judge which PGS model is most reliable.
        """
        from just_prs.quality import format_classification, format_effect_size

        df = client.make_catalog(settings).best_performance(pgs_id=pgs_id).collect()
        if df.height == 0:
            return {"pgs_id": pgs_id, "found": False}
        row = df.row(0, named=True)
        return {
            "pgs_id": pgs_id,
            "found": True,
            "n_individuals": row.get("n_individuals"),
            "ancestry_broad": row.get("ancestry_broad"),
            "auroc_estimate": row.get("auroc_estimate"),
            "effect_size": format_effect_size(row) or "",
            "classification": format_classification(row) or "",
        }

    def search_traits(term: str, limit: int = 25) -> list[dict[str, Any]]:
        """Search the PGS Catalog for traits by keyword.

        Use this to find trait ontology IDs (EFO/MONDO) and their associated
        PGS score counts. For example, search 'intelligence' or 'thrombosis'.
        """
        from just_prs_mcp.tools.catalog import _search_traits_forgiving

        with client.make_rest_client() as rest:
            results = _search_traits_forgiving(rest, term, limit)
        return [
            {
                "id": t.id,
                "label": t.label,
                "description": t.description,
                "n_associated": len(t.associated_pgs_ids),
                "associated_pgs_ids": t.associated_pgs_ids[:10],
            }
            for t in results
        ]

    def trait_info(trait_id: str) -> dict[str, Any]:
        """Get full trait info by ontology ID (EFO/MONDO) with associated PGS IDs.

        Use after search_traits to get the full list of PGS IDs for scoring.
        """
        with client.make_rest_client() as rest:
            t = rest.get_trait(trait_id)
        return {
            "id": t.id,
            "label": t.label,
            "description": t.description,
            "associated_pgs_ids": t.associated_pgs_ids,
            "child_associated_pgs_ids": t.child_associated_pgs_ids,
        }

    def list_genomes() -> dict[str, Any]:
        """List genomes in the server's cache directory.

        Shows downloaded VCF files and normalized Parquet files, plus
        pre-configured samples ('anton', 'livia') available for download.
        """
        root = client.resolved_cache_dir(settings)
        samples_dir = root / "samples"
        normalized_dir = root / "normalized"

        downloaded: list[dict[str, Any]] = []
        if samples_dir.is_dir():
            for f in sorted(samples_dir.iterdir()):
                if f.is_file() and f.name.lower().endswith((".vcf", ".vcf.gz", ".vcf.bgz")):
                    downloaded.append(
                        {"filename": f.name, "path": str(f), "size_bytes": f.stat().st_size}
                    )

        normalized: list[dict[str, Any]] = []
        if normalized_dir.is_dir():
            for f in sorted(normalized_dir.iterdir()):
                if f.is_file() and f.suffix == ".parquet":
                    normalized.append(
                        {"filename": f.name, "path": str(f), "size_bytes": f.stat().st_size}
                    )

        return {
            "cache_dir": str(root),
            "downloaded": downloaded,
            "normalized": normalized,
            "available_samples": ["anton", "livia"],
        }

    def download_sample_genome(sample: str = "anton") -> dict[str, Any]:
        """Download a public sample genome from Zenodo.

        Pre-configured samples: 'anton' (Anton Kulaga, ~482 MB) and 'livia'
        (Livia Zaharia, ~349 MB). Returns the local file path on success.
        Skips download if the file already exists. Can take several minutes.
        """
        import httpx

        from just_prs_mcp.tools.compute import (
            _pick_vcf_file,
            _zenodo_api_url,
            _zenodo_download_url,
        )

        api_url, label = _zenodo_api_url(sample, None)
        out_dir = client.resolved_cache_dir(settings) / "samples"
        out_dir.mkdir(parents=True, exist_ok=True)

        with httpx.Client(
            timeout=httpx.Timeout(60.0, read=600.0), follow_redirects=True
        ) as http:
            meta = http.get(api_url).json()
            files = meta.get("files", [])
            chosen = _pick_vcf_file(files, None)
            if chosen is None:
                return {"success": False, "message": f"No VCF found in {label}"}
            url = _zenodo_download_url(chosen)
            if not url:
                return {"success": False, "message": f"No download URL for {chosen.get('key')}"}

            dest = out_dir / str(chosen["key"])
            if dest.exists():
                return {
                    "success": True,
                    "path": str(dest),
                    "message": f"Already downloaded: {dest}",
                }

            downloaded = 0
            with dest.open("wb") as fh, http.stream("GET", url) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
                    downloaded += len(chunk)

        return {
            "success": True,
            "path": str(dest),
            "bytes": downloaded,
            "message": f"Downloaded {chosen['key']} ({downloaded / 1e9:.2f} GB) to {dest}",
        }

    def normalize_vcf(vcf_path: str, output_path: str | None = None) -> dict[str, Any]:
        """Normalize a VCF to a quality-filtered genotype Parquet file.

        The output Parquet is a drop-in for compute_prs (pass as genotypes_path).
        Skips if the output already exists. Can take seconds to minutes.
        """
        import polars as pl
        from just_prs.normalize import normalize_vcf as _normalize_vcf

        src = Path(vcf_path).expanduser()
        if not src.exists():
            return {"success": False, "message": f"VCF not found: {vcf_path}"}

        if output_path:
            out = Path(output_path).expanduser()
        else:
            out_dir = client.resolved_cache_dir(settings) / "normalized"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / (src.name.split(".")[0] + ".parquet")

        if out.exists():
            n = int(pl.scan_parquet(out).select(pl.len()).collect().item())
            return {
                "success": True,
                "output_path": str(out),
                "n_variants": n,
                "message": f"Already normalized: {out} ({n} variants)",
            }

        result_path = _normalize_vcf(src, out)
        n = int(pl.scan_parquet(result_path).select(pl.len()).collect().item())
        return {
            "success": True,
            "output_path": str(result_path),
            "n_variants": n,
            "message": f"Normalized {n} variants to {result_path}",
        }

    def compute_prs(
        vcf_path: str,
        pgs_id: str,
        genome_build: str | None = None,
        genotypes_path: str | None = None,
    ) -> dict[str, Any]:
        """Compute a polygenic risk score for one genome against one PGS model.

        Pass ``genotypes_path`` (normalized Parquet from normalize_vcf) to skip
        re-reading the VCF — much faster for repeated scoring. Returns the raw
        score, match rate, variant counts, and weight-mass coverage (C_wt).

        After computing, call ``percentile`` to place the score on the population
        distribution, then ``absolute_risk`` for disease traits.
        """
        b = client.build(settings, genome_build)
        cat = client.make_catalog(settings)
        genotypes_lf = None
        if genotypes_path:
            import polars as pl

            genotypes_lf = pl.scan_parquet(Path(genotypes_path).expanduser())

        result = cat.compute_prs(
            vcf_path=vcf_path,
            pgs_id=pgs_id,
            genome_build=b,
            genotypes_lf=genotypes_lf,
        )
        return {
            "pgs_id": result.pgs_id,
            "score": result.score,
            "match_rate": result.match_rate,
            "variants_matched": result.variants_matched,
            "variants_total": result.variants_total,
            "weight_mass_coverage": result.weight_mass_coverage,
            "trait_reported": result.trait_reported,
        }

    def percentile(
        prs_score: float,
        pgs_id: str,
        superpopulation: str = "EUR",
        weight_mass_coverage: float | None = None,
    ) -> dict[str, Any]:
        """Estimate the population percentile (0-100) for a computed PRS value.

        Returns the percentile, z_score, and reliability assessment. Pass
        ``weight_mass_coverage`` (C_wt from compute_prs) so low-coverage
        percentiles are flagged as unreliable.

        For disease traits, feed the returned z_score into absolute_risk.
        """
        res = client.make_catalog(settings).percentile_full(
            prs_score=prs_score,
            pgs_id=pgs_id,
            ancestry=superpopulation,
            panel=client.panel(settings, None),
            weight_mass_coverage=weight_mass_coverage,
        )
        return {
            "pgs_id": pgs_id,
            "percentile": res.percentile,
            "z_score": res.z_score,
            "reliable": res.reliable,
            "caveat": res.caveat,
            "method": res.method,
        }

    def absolute_risk(pgs_id: str, z_score: float, sex: str | None = None) -> dict[str, Any]:
        """Estimate absolute disease risk from a PRS z-score.

        Returns the absolute probability and risk ratio vs the population
        average. Only available for disease traits with prevalence data.
        """
        risk = client.make_catalog(settings).absolute_risk(pgs_id, z_score, sex=sex)
        if risk is None:
            return {"available": False, "message": f"No prevalence data for {pgs_id}"}
        return {
            "available": True,
            "pgs_id": pgs_id,
            "absolute_risk": risk.absolute_risk,
            "risk_ratio": risk.risk_ratio,
            "baseline_risk": risk.baseline_risk,
            "trait": risk.trait,
        }

    model = _create_model(settings, api_key)
    return agent_cls(
        name="PRS MCP Agent",
        model=model,
        tools=[
            search_scores,
            score_info,
            best_performance,
            search_traits,
            trait_info,
            list_genomes,
            download_sample_genome,
            normalize_vcf,
            compute_prs,
            percentile,
            absolute_risk,
        ],
        instructions=[
            "You are a polygenic risk score (PRS) analysis agent. Answer questions "
            "about genetic predisposition using the PGS Catalog and PRS tools.",
            (
                "Use search_traits to find trait ontology IDs (EFO/MONDO), then "
                "pick the best PGS model (highest AUROC or largest cohort via "
                "best_performance) and compute_prs to score a genome."
            ),
            (
                "Two sample genomes are pre-configured: 'anton' (Anton Kulaga) and "
                "'livia' (Livia Zaharia). Use list_genomes to check availability, "
                "download_sample_genome to fetch, and normalize_vcf to prepare them."
            ),
            (
                "Workflow: search_traits -> pick PGS ID -> list_genomes -> "
                "(download if needed) -> (normalize if needed) -> compute_prs -> "
                "percentile -> absolute_risk (for disease traits)."
            ),
            (
                "Trait directionality: for disease/risk traits (DVT, diabetes) a HIGHER "
                "percentile means MORE risk (bad). For positive traits (intelligence, "
                "height) a HIGHER percentile is typically good."
            ),
            (
                "When comparing individuals, state clearly who has the higher/lower "
                "score and what that means for the specific trait."
            ),
            "Always cite PGS IDs. Be honest about limitations.",
            "Keep answers concise — citizen scientist audience.",
        ],
        markdown=True,
        tool_call_limit=30,
    )


def run_agent_query(query: str, settings: Settings | None = None) -> str:
    """Run one natural-language query through the Agno PRS agent."""
    response = create_agent(settings).run(query)
    if hasattr(response, "get_content_as_string"):
        return response.get_content_as_string()
    return str(getattr(response, "content", response))
