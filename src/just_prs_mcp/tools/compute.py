"""ESSENTIALS — PRS computation and analysis tools.

The core workflow: normalize a VCF to Parquet (a long-running background task),
compute a polygenic score against it, then interpret the result (percentile,
absolute risk, quality). Computation tools take **local file paths** on the
server's filesystem (VCF / normalized Parquet); the analysis tools
(``percentile``, ``absolute_risk``, ``assess_quality``) need no files.

Bad/missing input paths raise ``ToolError`` (the template's sanctioned channel
for malformed input); everything else returns a typed model.
"""

from __future__ import annotations

from pathlib import Path

from anyio.to_thread import run_sync
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from just_prs_mcp import client
from just_prs_mcp.logging_setup import get_logger
from just_prs_mcp.models import (
    AbsoluteRisk,
    GenomeCatalog,
    GenomeComparison,
    GenomeEntry,
    GenomeRanking,
    NormalizeResult,
    OpResult,
    PercentileResult,
    PRSResult,
    QualityAssessment,
    TraitComparison,
    TraitPRSReport,
    TraitScoreRow,
)
from just_prs_mcp.settings import Settings

log = get_logger()

# Public sample genomes open-sourced by the just-dna-lite authors (see that
# project's README). For users who want to try PRS but have no VCF of their own.
_SAMPLE_GENOMES: dict[str, dict[str, str]] = {
    "anton": {
        "record": "18370498",
        "who": "Anton Kulaga",
        "license": "CC0 (public domain)",
        "vcf_filename": "antonkulaga.vcf",
        "size_approx": "~482 MB",
        "description": "Whole-genome sequencing (WGS), open-sourced by the just-dna-lite project.",
    },
    "livia": {
        "record": "19487816",
        "who": "Livia Zaharia",
        "license": "CC-BY-4.0",
        "vcf_filename": "SIMHIFQTILQ.hard-filtered.vcf.gz",
        "size_approx": "~349 MB",
        "description": "WGS, hard-filtered, from the just-dna-lite project.",
    },
}

_VCF_SUFFIXES = (".vcf", ".vcf.gz", ".vcf.bgz")


def _require_file(path: str, kind: str) -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise ToolError(f"{kind} not found: {path}")
    return p


def _count_rows(parquet_path: Path) -> int:
    import polars as pl

    return int(pl.scan_parquet(parquet_path).select(pl.len()).collect().item())


def _percentile_result(
    settings: Settings,
    prs_score: float,
    pgs_id: str,
    superpopulation: str,
    panel: str | None,
    weight_mass_coverage: float | None = None,
) -> PercentileResult:
    """Wrap just-prs ``percentile_full`` — the library owns the reliability verdict.

    Passing ``weight_mass_coverage`` (C_wt) lets the library flag a deflated
    low-coverage percentile as ``reliable=False`` with a caveat (gate is C_wt, not
    the old match-rate heuristic). The true z-score / reference mean+std it used are
    surfaced so callers can feed absolute risk without inverting the percentile.
    """
    res = client.make_catalog(settings).percentile_full(
        prs_score=prs_score,
        pgs_id=pgs_id,
        ancestry=superpopulation,
        panel=client.panel(settings, panel),
        weight_mass_coverage=weight_mass_coverage,
    )
    reliable = res.reliable
    caveat = res.caveat or None
    # When no coverage signal is supplied the library cannot judge reliability, so keep a
    # lightweight guard so a bare extreme percentile is not read as authoritative.
    if reliable and weight_mass_coverage is None and res.percentile in (0, 100):
        reliable = False
        caveat = (
            "Extreme percentile (0/100) returned with no coverage signal. Pass "
            "weight_mass_coverage (C_wt from compute_prs) to confirm this is not a "
            "low-coverage artifact."
        )
    return PercentileResult(
        pgs_id=pgs_id,
        prs_score=prs_score,
        percentile=res.percentile,
        method=res.method,
        ancestry=superpopulation,
        reliable=reliable,
        caveat=caveat,
        z_score=res.z_score,
        reference_mean=res.reference_mean,
        reference_std=res.reference_std,
        reference_panel_ancestry=res.ancestry,
        reference_panel=res.panel,
    )


def _auroc_from_performance(performance) -> float | None:
    """Pull the AUROC estimate out of a library ``PerformanceInfo`` (else None)."""
    if performance is None:
        return None
    for metric in performance.class_acc:
        if metric.name_short == "AUROC":
            return metric.estimate
    return None


def _format_effect_size(performance) -> str | None:
    """Format the primary effect size of a ``PerformanceInfo``, e.g. 'OR=1.55 [1.52-1.58]'."""
    if performance is None or not performance.effect_sizes:
        return None
    e = performance.effect_sizes[0]
    text = f"{e.name_short}={e.estimate:.2f}"
    if e.ci_lower is not None and e.ci_upper is not None:
        text += f" [{e.ci_lower:.2f}-{e.ci_upper:.2f}]"
    return text


def _trait_score_row(
    settings: Settings,
    result: PRSResult,
    interpret: bool,
    superpopulation: str,
    panel: str | None,
) -> TraitScoreRow:
    percentile_result = None
    quality = None
    auroc = None
    effect_size = None
    if interpret:
        try:
            percentile_result = _percentile_result(
                settings=settings,
                prs_score=result.score,
                pgs_id=result.pgs_id,
                superpopulation=superpopulation,
                panel=panel,
                weight_mass_coverage=result.weight_mass_coverage,
            )
        except Exception:  # noqa: BLE001 - interpretation is best-effort in aggregate reports
            percentile_result = None
        try:
            # Performance is embedded by the batch (attach_performance=True) on both the
            # VCF and genotypes-reuse paths now that compute_prs_batch takes genotypes_lf
            # (F23) — no per-score best_performance round-trip.
            if result.performance is not None:
                auroc = _auroc_from_performance(result.performance)
                effect_size = _format_effect_size(result.performance)
        except Exception:  # noqa: BLE001 - interpretation is best-effort in aggregate reports
            auroc, effect_size = None, None
        quality = _quality_assessment(
            match_rate=result.match_rate,
            auroc=auroc,
            percentile=percentile_result.percentile if percentile_result else result.percentile,
            percentile_method=(
                percentile_result.method if percentile_result else result.percentile_method
            ),
            reliable=percentile_result.reliable if percentile_result else True,
            caveat=(percentile_result.caveat or "") if percentile_result else "",
        )

    return TraitScoreRow(
        pgs_id=result.pgs_id,
        status="scored",
        score=result.score,
        variants_matched=result.variants_matched,
        variants_total=result.variants_total,
        match_rate=result.match_rate,
        weight_mass_coverage=result.weight_mass_coverage,
        percentile=percentile_result.percentile if percentile_result else result.percentile,
        percentile_method=(
            percentile_result.method if percentile_result else result.percentile_method
        ),
        percentile_reliable=percentile_result.reliable if percentile_result else None,
        percentile_caveat=percentile_result.caveat if percentile_result else None,
        reference_panel_ancestry=(
            percentile_result.reference_panel_ancestry if percentile_result else None
        ),
        quality_label=quality.quality_label if quality else None,
        quality_summary=quality.summary if quality else None,
        effect_size=effect_size,
        auroc_estimate=auroc,
    )


def _zenodo_api_url(sample: str | None, record_url: str | None) -> tuple[str, str]:
    """Resolve a sample alias or Zenodo record URL/ID to its API URL + a label.

    Raises ``ToolError`` for an unknown alias so the no-network failure path is
    deterministic and testable.
    """
    if record_url:
        token = record_url.rstrip("/").rsplit("/", 1)[-1]
        if not token.isdigit():
            raise ToolError(
                f"Could not parse a Zenodo record id from '{record_url}'. "
                "Pass a records URL like 'https://zenodo.org/records/18370498'."
            )
        return f"https://zenodo.org/api/records/{token}", f"Zenodo record {token}"

    key = (sample or "").strip().lower()
    entry = _SAMPLE_GENOMES.get(key)
    if entry is None:
        known = ", ".join(sorted(_SAMPLE_GENOMES))
        raise ToolError(f"Unknown sample '{sample}'. Known samples: {known}; or pass record_url.")
    label = f"{entry['who']} ({entry['license']})"
    return f"https://zenodo.org/api/records/{entry['record']}", label


def _pick_vcf_file(files: list[dict], filename: str | None) -> dict | None:
    """Choose the VCF entry from a Zenodo record's ``files`` list.

    With ``filename`` set, match it exactly; otherwise pick the largest file whose
    key looks like a VCF. Returns ``None`` when nothing matches.
    """
    if filename:
        return next((f for f in files if f.get("key") == filename), None)
    vcfs = [f for f in files if str(f.get("key", "")).lower().endswith(_VCF_SUFFIXES)]
    if not vcfs:
        return None
    return max(vcfs, key=lambda f: f.get("size") or 0)


def _zenodo_download_url(file_entry: dict) -> str | None:
    """Extract a content download URL from a Zenodo file entry (API shape varies)."""
    links = file_entry.get("links") or {}
    return links.get("content") or links.get("download") or links.get("self")


def _row_rank_key(row: TraitScoreRow) -> tuple[bool, bool, float, float]:
    """Rank rows best-coverage first for ``top_n`` trimming (sorted reverse=True).

    Scored rows outrank failed ones, reliable percentiles outrank unreliable, then
    higher weight-mass coverage (C_wt, the scale-free signal) wins, with match rate
    as the final tiebreak — so a trimmed report keeps the most trustworthy scores
    rather than an arbitrary prefix.
    """
    return (
        row.status == "scored",
        bool(row.percentile_reliable),
        row.weight_mass_coverage if row.weight_mass_coverage is not None else -1.0,
        row.match_rate if row.match_rate is not None else -1.0,
    )


def _quality_assessment(
    match_rate: float,
    auroc: float | None = None,
    percentile: float | None = None,
    percentile_method: str | None = None,
    reliable: bool = True,
    caveat: str = "",
) -> QualityAssessment:
    from just_prs.quality import interpret_prs_result

    # The library now derives the summary from the actual percentile method and
    # appends the caveat when unreliable, so the wrapper no longer patches its text.
    interp = interpret_prs_result(
        percentile=percentile,
        match_rate=match_rate,
        auroc=auroc,
        percentile_method=percentile_method,
        reliable=reliable,
        caveat=caveat,
    )
    return QualityAssessment(
        quality_label=interp.get("quality_label", ""),
        quality_color=interp.get("quality_color", ""),
        summary=interp.get("summary", ""),
    )


def register_compute(mcp: FastMCP, settings: Settings) -> None:
    """Register the always-on compute + analysis tools, a resource, and a prompt."""

    @mcp.tool(
        task=True,
        annotations=ToolAnnotations(title="Normalize VCF", readOnlyHint=False, idempotentHint=True),
    )
    async def normalize_vcf(
        vcf_path: str,
        ctx: Context,
        output_path: str | None = None,
        pass_filters: list[str] | None = None,
        min_depth: int | None = None,
        min_qual: float | None = None,
        sex: str | None = None,
        genome_build: str | None = None,
    ) -> NormalizeResult:
        """Normalize a VCF to a quality-filtered genotype Parquet (background task).

        Strips the chr prefix, renames id→rsid, computes genotype from GT, applies
        optional quality filters (FILTER allow-list, min DP, min QUAL), and writes
        zstd-compressed Parquet. The output is a drop-in genotype source for
        ``compute_prs`` / ``compute_prs_batch`` (pass it as ``genotypes_path``),
        so a VCF is normalized once and reused across many scores.

        Runs as a real MCP background task, though some clients transparently
        collapse the task/poll handshake and return the final result inline.
        Normalization is the slow step (seconds to minutes depending on VCF size).
        The result echoes the effective genome build assumed for downstream PRS
        scoring; build inference from VCF contigs is deferred to just-prs.
        """
        from just_prs.normalize import VcfFilterConfig
        from just_prs.normalize import normalize_vcf as _normalize_vcf

        src = _require_file(vcf_path, "VCF")
        b = client.build(settings, genome_build)
        if output_path:
            out = Path(output_path).expanduser()
        else:
            out_dir = client.resolved_cache_dir(settings) / "normalized"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / (src.name.split(".")[0] + ".parquet")

        config = None
        if any(v is not None for v in (pass_filters, min_depth, min_qual, sex)):
            config = VcfFilterConfig(
                pass_filters=pass_filters, min_depth=min_depth, min_qual=min_qual, sex=sex
            )

        await ctx.info(f"Normalizing {src.name} -> {out.name}")
        await ctx.report_progress(progress=0, total=1)
        result_path = await run_sync(lambda: _normalize_vcf(src, out, config=config))
        n = await run_sync(lambda: _count_rows(result_path))
        await ctx.report_progress(progress=1, total=1)
        log.info("Normalized %s (%d variants) -> %s", src.name, n, result_path)
        return NormalizeResult(
            output_path=str(result_path),
            n_variants=n,
            genome_build=b,
            message=f"Normalized {n} variants to {result_path}.",
        )

    @mcp.tool(
        task=True,
        annotations=ToolAnnotations(
            title="Download sample genome", readOnlyHint=False, openWorldHint=True
        ),
    )
    async def download_sample_genome(
        ctx: Context,
        sample: str = "anton",
        output_dir: str | None = None,
        record_url: str | None = None,
        filename: str | None = None,
        auto_normalize: bool = False,
    ) -> OpResult:
        """Download a public sample WGS VCF from Zenodo to try PRS without your own data.

        Two whole-genome sequencing (WGS) datasets open-sourced by the
        just-dna-lite project are pre-configured:

        - ``sample="anton"`` — Anton Kulaga's genome (~482 MB, CC0 public domain,
          Zenodo record 18370498, file: antonkulaga.vcf).
        - ``sample="livia"`` — Livia Zaharia's genome (~349 MB, CC-BY-4.0,
          Zenodo record 19487816, file: SIMHIFQTILQ.hard-filtered.vcf.gz).

        Pass ``record_url`` (e.g. 'https://zenodo.org/records/18370498') to fetch
        any other Zenodo record, and ``filename`` to pick a specific file when a
        record has several.

        The downloaded VCF lands under ``<cache_dir>/samples/`` (or ``output_dir``)
        and is a drop-in path for ``normalize_vcf`` / ``compute_prs``.

        Set ``auto_normalize=True`` to automatically run ``normalize_vcf`` on
        the downloaded file, producing a reusable Parquet for faster batch
        scoring. When enabled, ``data`` includes both ``path`` (the raw VCF)
        and ``normalized_path`` (the Parquet). When disabled (default), call
        ``normalize_vcf`` separately after download.

        Use ``list_genomes`` to see which genomes have already been downloaded
        and/or normalized.

        Returns an ``OpResult`` whose ``data`` carries the local ``path`` on
        success. Runs as a background task (files are hundreds of MB).
        """
        import httpx

        try:
            api_url, label = _zenodo_api_url(sample, record_url)
        except ToolError as exc:
            return OpResult(success=False, message=str(exc))

        out_dir = (
            Path(output_dir).expanduser()
            if output_dir
            else client.resolved_cache_dir(settings) / "samples"
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        await ctx.info(f"Resolving {label} on Zenodo ...")
        try:
            timeout = httpx.Timeout(60.0, read=300.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
                meta_resp = await http.get(api_url)
                meta_resp.raise_for_status()
                files = meta_resp.json().get("files", [])
                chosen = _pick_vcf_file(files, filename)
                if chosen is None:
                    available = ", ".join(str(f.get("key")) for f in files) or "(none)"
                    return OpResult(
                        success=False,
                        message=(
                            f"No VCF found in {label}. Available files: {available}. "
                            "Pass filename= to pick one explicitly."
                        ),
                    )
                download_url = _zenodo_download_url(chosen)
                if not download_url:
                    return OpResult(
                        success=False,
                        message=f"Could not resolve a download URL for '{chosen.get('key')}'.",
                    )

                dest = out_dir / str(chosen["key"])
                total = int(chosen.get("size") or 0)
                await ctx.info(
                    f"Downloading {chosen['key']} ({total / 1e9:.2f} GB) from {label} -> {dest}"
                )
                await ctx.report_progress(progress=0, total=max(1, total))
                downloaded = 0
                with dest.open("wb") as fh:
                    async with http.stream("GET", download_url) as resp:
                        resp.raise_for_status()
                        total = int(resp.headers.get("content-length") or total) or total
                        async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
                            fh.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                await ctx.report_progress(progress=downloaded, total=total)
        except Exception as exc:  # noqa: BLE001 — download outcome is data, not a protocol error
            return OpResult(success=False, message=f"Sample download failed: {exc}")

        log.info("Downloaded sample genome %s (%d bytes) -> %s", chosen["key"], downloaded, dest)

        data: dict = {
            "path": str(dest),
            "filename": str(chosen["key"]),
            "bytes": downloaded,
            "source": label,
        }
        msg = f"Downloaded {chosen['key']} ({downloaded / 1e9:.2f} GB) from {label}."

        if auto_normalize:
            try:
                from just_prs.normalize import normalize_vcf as _normalize_vcf

                norm_dir = client.resolved_cache_dir(settings) / "normalized"
                norm_dir.mkdir(parents=True, exist_ok=True)
                norm_out = norm_dir / (dest.name.split(".")[0] + ".parquet")
                await ctx.info(f"Auto-normalizing {dest.name} -> {norm_out.name}")
                norm_path = await run_sync(lambda: _normalize_vcf(dest, norm_out))
                n = await run_sync(lambda: _count_rows(norm_path))
                data["normalized_path"] = str(norm_path)
                data["n_variants"] = n
                msg += f" Normalized to {norm_path} ({n} variants)."
                log.info("Auto-normalized %s (%d variants) -> %s", dest.name, n, norm_path)
            except Exception as exc:  # noqa: BLE001
                msg += f" Auto-normalization failed: {exc}. You can retry with normalize_vcf."
                log.warning("Auto-normalization of %s failed: %s", dest.name, exc)
        else:
            msg += " Pass it to normalize_vcf or compute_prs as the VCF path."

        return OpResult(success=True, message=msg, data=data)

    @mcp.tool(
        annotations=ToolAnnotations(title="List genomes", readOnlyHint=True),
    )
    def list_genomes() -> GenomeCatalog:
        """List genomes available in the server's cache directory.

        Scans ``<cache_dir>/samples/`` for downloaded raw VCF files and
        ``<cache_dir>/normalized/`` for normalized Parquet files. Also lists
        the pre-configured sample genomes that can be downloaded via
        ``download_sample_genome`` (even if not yet downloaded).

        Use this to discover:
        - Which genomes have already been downloaded (ready for normalize_vcf).
        - Which genomes have already been normalized (ready for compute_prs
          as ``genotypes_path``).
        - Which pre-configured samples are available for download.

        No network access required — reads the local filesystem only.
        """
        root = client.resolved_cache_dir(settings)
        samples_dir = root / "samples"
        normalized_dir = root / "normalized"

        known_vcf_filenames = {
            v["vcf_filename"]: k for k, v in _SAMPLE_GENOMES.items() if "vcf_filename" in v
        }

        downloaded: list[GenomeEntry] = []
        if samples_dir.is_dir():
            for f in sorted(samples_dir.iterdir()):
                if f.is_file() and f.name.lower().endswith(_VCF_SUFFIXES):
                    downloaded.append(
                        GenomeEntry(
                            filename=f.name,
                            path=str(f),
                            size_bytes=f.stat().st_size,
                            stage="downloaded",
                            sample_alias=known_vcf_filenames.get(f.name),
                        )
                    )

        normalized: list[GenomeEntry] = []
        if normalized_dir.is_dir():
            for f in sorted(normalized_dir.iterdir()):
                if f.is_file() and f.suffix == ".parquet":
                    stem = f.stem
                    alias = next(
                        (
                            k
                            for k, v in _SAMPLE_GENOMES.items()
                            if "vcf_filename" in v and v["vcf_filename"].split(".")[0] == stem
                        ),
                        None,
                    )
                    normalized.append(
                        GenomeEntry(
                            filename=f.name,
                            path=str(f),
                            size_bytes=f.stat().st_size,
                            stage="normalized",
                            sample_alias=alias,
                        )
                    )

        available_samples = [
            {
                "name": k,
                "who": v["who"],
                "license": v["license"],
                "size_approx": v.get("size_approx", "unknown"),
                "description": v.get("description", ""),
                "zenodo_record": v["record"],
                "already_downloaded": any(e.sample_alias == k for e in downloaded),
                "already_normalized": any(e.sample_alias == k for e in normalized),
            }
            for k, v in _SAMPLE_GENOMES.items()
        ]

        parts = []
        n_dl, n_nr = len(downloaded), len(normalized)
        parts.append(f"{n_dl} downloaded VCF(s), {n_nr} normalized Parquet(s)")
        not_downloaded = [s["name"] for s in available_samples if not s["already_downloaded"]]
        if not_downloaded:
            parts.append(
                f"Available for download: {', '.join(not_downloaded)} (use download_sample_genome)"
            )
        not_normalized = [
            e.filename
            for e in downloaded
            if not any(
                n.sample_alias == e.sample_alias or n.filename.startswith(e.filename.split(".")[0])
                for n in normalized
            )
        ]
        if not_normalized:
            parts.append(f"Not yet normalized: {', '.join(not_normalized)} (use normalize_vcf)")

        return GenomeCatalog(
            cache_dir=str(root),
            downloaded=downloaded,
            normalized=normalized,
            available_samples=available_samples,
            message=". ".join(parts) + ".",
        )

    @mcp.tool(
        annotations=ToolAnnotations(title="Compute PRS", readOnlyHint=True, openWorldHint=True)
    )
    def compute_prs(
        vcf_path: str,
        pgs_id: str,
        genome_build: str | None = None,
        genotypes_path: str | None = None,
        attach_performance: bool = False,
    ) -> PRSResult:
        """Compute a polygenic risk score for one VCF against one PGS score.

        Downloads the harmonized scoring file (cached) and scores the genotypes.
        Pass ``genotypes_path`` to reuse a normalized Parquet from ``normalize_vcf``
        (avoids re-reading the VCF); otherwise the VCF is read directly. Returns
        the score, match rate, variant counts, weight-mass coverage (C_wt), trait,
        and (when data permits) a theoretical percentile.

        Set ``attach_performance=True`` to embed the score's best published
        performance (effect sizes, AUROC/C-index, evaluation ancestry) on the
        result in the same call — no separate ``best_performance`` round-trip; it
        is honored on the ``genotypes_path`` reuse branch too (F23). The result also
        carries ``detected_genome_build`` / ``build_mismatch`` from the VCF (F4).

        **Recommended follow-up:** after computing the score, call ``percentile``
        to place it on the population distribution, then call ``absolute_risk``
        with the z_score from the percentile result to get the concrete disease
        probability and risk ratio (for disease traits with prevalence data).
        """
        b = client.build(settings, genome_build)
        cat = client.make_catalog(settings)
        try:
            genotypes_lf = None
            if genotypes_path:
                import polars as pl

                gpath = _require_file(genotypes_path, "Genotypes Parquet")
                genotypes_lf = pl.scan_parquet(gpath)
            else:
                _require_file(vcf_path, "VCF")
            return cat.compute_prs(
                vcf_path=vcf_path,
                pgs_id=pgs_id,
                genome_build=b,
                attach_performance=attach_performance,
                genotypes_lf=genotypes_lf,
            )
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"PRS computation failed for {pgs_id}: {exc}") from exc

    @mcp.tool(
        task=True,
        annotations=ToolAnnotations(title="Compute PRS (batch)", readOnlyHint=True),
    )
    async def compute_prs_batch(
        vcf_path: str,
        pgs_ids: list[str],
        ctx: Context,
        genome_build: str | None = None,
        attach_performance: bool = False,
    ) -> list[PRSResult]:
        """Compute PRS for one VCF against many PGS scores (background task).

        Memory-safe DuckDB engine with spill-to-disk; reuses the parsed VCF and
        scoring caches across scores. Returns one result per PGS ID. Set
        ``attach_performance=True`` to embed each score's best published
        performance on its result in the same pass.
        """
        b = client.build(settings, genome_build)
        cat = client.make_catalog(settings)
        _require_file(vcf_path, "VCF")

        await ctx.info(f"Batch-scoring {len(pgs_ids)} PGS IDs against {Path(vcf_path).name}")
        batch = await run_sync(
            lambda: cat.compute_prs_batch(
                vcf_path=vcf_path,
                pgs_ids=pgs_ids,
                genome_build=b,
                attach_performance=attach_performance,
            )
        )
        # compute_prs_batch returns a PRSBatchResult (results + per-score outcomes);
        # the tool's contract is the list of successful PRSResults.
        await ctx.info(f"Computed {batch.n_ok}/{batch.n_total} score(s); {batch.n_failed} failed")
        return batch.results

    @mcp.tool(
        task=True,
        annotations=ToolAnnotations(title="Compute PRS by trait", readOnlyHint=True),
    )
    async def compute_prs_by_trait(
        trait_id: str,
        vcf_path: str,
        ctx: Context,
        genotypes_path: str | None = None,
        genome_build: str | None = None,
        include_children: bool = False,
        limit: int | None = None,
        interpret: bool = False,
        superpopulation: str = "EUR",
        panel: str | None = None,
        top_n: int | None = None,
    ) -> TraitPRSReport:
        """Compute all directly associated PRS scores for a trait ontology ID.

        ``trait_id`` may be an EFO or MONDO identifier. Set ``include_children``
        to also score PGS IDs associated through descendant traits. ``limit`` caps
        how many scores are *computed* and reports skipped IDs explicitly.

        ``top_n`` caps how many per-score rows are *returned*: rows are ranked
        best-coverage first (scored before failed, reliable percentile before not,
        higher match rate first) and the rest are trimmed, with ``n_omitted``
        reporting the count. A big trait (100+ scores) with ``interpret=True`` can
        otherwise exceed the client's output-token limit; trait-level counts and
        ``mean_match_rate`` always reflect every score, so trimming is explicit,
        never silent.
        """
        b = client.build(settings, genome_build)
        try:
            with client.make_rest_client() as rest:
                trait = rest.get_trait(trait_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Trait lookup failed for {trait_id}: {exc}") from exc

        pgs_ids = list(dict.fromkeys(trait.associated_pgs_ids))
        if include_children:
            pgs_ids = list(dict.fromkeys([*pgs_ids, *trait.child_associated_pgs_ids]))
        n_total = len(pgs_ids)
        selected_ids = pgs_ids[:limit] if limit is not None and limit >= 0 else pgs_ids
        n_skipped = max(0, n_total - len(selected_ids))

        await ctx.info(
            f"Trait {trait.id} has {n_total} candidate PGS ID(s); scoring {len(selected_ids)}"
        )
        await ctx.report_progress(progress=0, total=max(1, len(selected_ids)))

        # Single batch path for both VCF and normalized-Parquet reuse: compute_prs_batch
        # accepts genotypes_lf and attach_performance (F23), so the per-score genotypes
        # loop + best_performance round-trip are gone. The batch continues past per-score
        # errors and reports them in `outcomes`.
        genotypes_lf = None
        if genotypes_path:
            import polars as pl

            gpath = _require_file(genotypes_path, "Genotypes Parquet")
            genotypes_lf = pl.scan_parquet(gpath)
        else:
            _require_file(vcf_path, "VCF")

        rows: list[TraitScoreRow] = []
        detected_genome_build: str | None = None
        build_mismatch = False
        try:
            batch = await run_sync(
                lambda: client.make_catalog(settings).compute_prs_batch(
                    vcf_path=vcf_path,
                    pgs_ids=selected_ids,
                    genome_build=b,
                    genotypes_lf=genotypes_lf,
                    attach_performance=interpret,
                )
            )
            by_id = {result.pgs_id: result for result in batch.results}
            errors = {o.pgs_id: o.error for o in batch.outcomes if o.status != "ok"}
            for pgs_id in selected_ids:
                result = by_id.get(pgs_id)
                if result is None:
                    rows.append(
                        TraitScoreRow(
                            pgs_id=pgs_id,
                            status="failed",
                            error=errors.get(pgs_id) or "No result returned by batch scoring.",
                        )
                    )
                    continue
                # Build detection (F4) is per-VCF, so the same verdict applies to every
                # score — capture the first non-null reading for the report header.
                if detected_genome_build is None and result.detected_genome_build:
                    detected_genome_build = result.detected_genome_build
                build_mismatch = build_mismatch or bool(result.build_mismatch)
                rows.append(
                    _trait_score_row(
                        settings=settings,
                        result=result,
                        interpret=interpret,
                        superpopulation=superpopulation,
                        panel=panel,
                    )
                )
            await ctx.report_progress(progress=len(selected_ids), total=max(1, len(selected_ids)))
        except Exception as exc:  # noqa: BLE001
            rows = [
                TraitScoreRow(pgs_id=pgs_id, status="failed", error=str(exc))
                for pgs_id in selected_ids
            ]

        n_scored = sum(1 for row in rows if row.status == "scored")
        n_failed = sum(1 for row in rows if row.status == "failed")
        n_reliable = sum(1 for row in rows if row.percentile_reliable)
        match_rates = [row.match_rate for row in rows if row.match_rate is not None]
        mean_match_rate = sum(match_rates) / len(match_rates) if match_rates else None

        ranked = sorted(rows, key=_row_rank_key, reverse=True)
        returned = ranked if top_n is None else ranked[: max(0, top_n)]
        n_omitted = len(rows) - len(returned)

        summary = (
            f"Scored {n_scored}/{len(selected_ids)} PGS ID(s) for {trait.label or trait.id} "
            f"on {b}; {n_failed} failed, {n_skipped} skipped by limit"
        )
        if mean_match_rate is not None:
            summary += f"; mean coverage {mean_match_rate:.0%}, {n_reliable} reliable percentile(s)"
        if n_omitted:
            summary += f"; {n_omitted} row(s) trimmed from response by top_n={top_n}"
        if build_mismatch:
            summary += (
                f"; WARNING: VCF build detected as {detected_genome_build} but scored on {b} "
                "— coverage/percentiles are unreliable until resolved"
            )
        genome_label = Path(vcf_path).stem.split(".")[0]

        report = TraitPRSReport(
            trait_id=trait.id,
            label=trait.label or trait.id,
            genome_build=b,
            detected_genome_build=detected_genome_build,
            build_mismatch=build_mismatch,
            n_requested=len(selected_ids),
            n_scored=n_scored,
            n_failed=n_failed,
            n_skipped=n_skipped,
            n_reliable=n_reliable,
            mean_match_rate=mean_match_rate,
            n_returned=len(returned),
            n_omitted=n_omitted,
            rows=returned,
            summary=summary + ".",
            genome_label=genome_label,
        )

        # Auto-save to disk so compare_genomes can load by path.
        results_dir = Path(client.resolved_cache_dir(settings)) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        safe_trait = trait.id.replace(":", "_").replace("/", "_")
        save_path = results_dir / f"{genome_label}_{safe_trait}.json"
        save_path.write_text(report.model_dump_json(indent=2))
        report.result_path = str(save_path)

        return report

    @mcp.tool(
        annotations=ToolAnnotations(title="PRS percentile", readOnlyHint=True, openWorldHint=True)
    )
    def percentile(
        prs_score: float,
        pgs_id: str,
        superpopulation: str = "EUR",
        panel: str | None = None,
        weight_mass_coverage: float | None = None,
    ) -> PercentileResult:
        """Estimate the population percentile (0-100) for a computed PRS value.

        Uses the 3-tier fallback: precomputed reference-panel distributions
        (best), then a theoretical distribution, then an AUROC approximation.
        ``superpopulation`` is a 1000G code (AFR/AMR/EAS/EUR/SAS). Pass
        ``weight_mass_coverage`` (C_wt) from ``compute_prs`` so a deflated
        low-coverage percentile is flagged ``reliable=False`` with a caveat
        instead of presented as authoritative. Also returns the true z-score and
        reference mean/std used, so absolute risk can be computed without
        inverting the percentile.

        **Important next step:** for disease traits, feed the returned
        ``z_score`` directly into ``absolute_risk`` to get the concrete
        lifetime probability and risk ratio vs the population average. This is
        more informative than the percentile alone.
        """
        try:
            return _percentile_result(
                settings=settings,
                prs_score=prs_score,
                pgs_id=pgs_id,
                superpopulation=superpopulation,
                panel=panel,
                weight_mass_coverage=weight_mass_coverage,
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Percentile estimation failed for {pgs_id}: {exc}") from exc

    @mcp.tool(
        annotations=ToolAnnotations(title="Absolute risk", readOnlyHint=True, openWorldHint=True)
    )
    def absolute_risk(pgs_id: str, z_score: float, sex: str | None = None) -> AbsoluteRisk:
        """Estimate absolute disease risk from a PRS z-score and population prevalence.

        Joins the score's trait to prevalence + effect-size data. ``z_score`` is
        the PRS in standard deviations from the population mean. Raises if the
        required prevalence / effect-size data is unavailable for this score.
        """
        try:
            risk = client.make_catalog(settings).absolute_risk(pgs_id, z_score, sex=sex)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Absolute-risk estimation failed for {pgs_id}: {exc}") from exc
        if risk is None:
            raise ToolError(
                f"No absolute-risk estimate available for {pgs_id} "
                "(missing prevalence or effect-size data)."
            )
        return risk

    @mcp.tool(annotations=ToolAnnotations(title="Assess quality", readOnlyHint=True))
    def assess_quality(
        match_rate: float,
        auroc: float | None = None,
        percentile: float | None = None,
        percentile_method: str | None = None,
        reliable: bool = True,
        caveat: str = "",
    ) -> QualityAssessment:
        """Classify and interpret a PRS result's quality (pure logic — no I/O).

        ``match_rate`` is the fraction of scoring variants matched (0-1). Returns
        a quality label/color and a human-readable interpretation combining match
        rate, AUROC, and (optionally) the result percentile. Pass
        ``percentile_method`` / ``reliable`` / ``caveat`` from the ``percentile``
        tool so the summary describes how the percentile was actually derived and
        echoes any low-coverage caveat.
        """
        return _quality_assessment(
            match_rate=match_rate,
            auroc=auroc,
            percentile=percentile,
            percentile_method=percentile_method,
            reliable=reliable,
            caveat=caveat,
        )

    @mcp.tool(
        annotations=ToolAnnotations(title="Compare genomes", readOnlyHint=True),
    )
    def compare_genomes(
        result_paths: list[str],
        genome_labels: list[str] | None = None,
    ) -> GenomeComparison:
        """Compare PRS results across multiple genomes for the same trait(s).

        ``result_paths`` — list of JSON file paths produced by ``compute_prs_by_trait``
        (the ``result_path`` field in each ``TraitPRSReport``). At least two paths are
        required. Each file may cover a different genome but they are grouped by trait.

        ``genome_labels`` — optional override labels (same length as ``result_paths``);
        if omitted, labels are taken from each saved report's ``genome_label`` field or
        inferred from the filename.

        Returns a ``GenomeComparison`` with per-trait rankings (sorted high→low
        percentile — **no directionality judgment**), percentile spread, model
        consistency, and a ``most_divergent_traits`` list for the LLM to interpret.
        """
        if len(result_paths) < 2:
            raise ToolError("compare_genomes requires at least 2 result paths.")

        reports: list[TraitPRSReport] = []
        for i, p in enumerate(result_paths):
            fp = Path(p)
            if not fp.is_file():
                raise ToolError(f"Result file not found: {p}")
            report = TraitPRSReport.model_validate_json(fp.read_text())
            if genome_labels and i < len(genome_labels):
                report.genome_label = genome_labels[i]
            elif not report.genome_label:
                report.genome_label = fp.stem.split("_")[0]
            reports.append(report)

        # Group reports by trait_id.
        from collections import defaultdict

        by_trait: dict[str, list[TraitPRSReport]] = defaultdict(list)
        for r in reports:
            by_trait[r.trait_id].append(r)

        trait_comparisons: list[TraitComparison] = []
        for trait_id, trait_reports in by_trait.items():
            if len(trait_reports) < 2:
                continue
            label = trait_reports[0].label

            # Pick best-model percentile per genome: highest-coverage reliable model.
            rankings: list[GenomeRanking] = []
            for tr in trait_reports:
                reliable_rows = [
                    row for row in tr.rows
                    if row.status == "scored" and row.percentile is not None
                    and row.percentile_reliable
                ]
                best_row = (
                    max(reliable_rows, key=lambda r: r.match_rate or 0)
                    if reliable_rows else None
                )
                rankings.append(GenomeRanking(
                    genome_label=tr.genome_label or "unknown",
                    best_pgs_id=best_row.pgs_id if best_row else None,
                    percentile=best_row.percentile if best_row else None,
                    n_models_scored=tr.n_scored,
                    n_reliable=tr.n_reliable,
                    rank=0,
                ))

            # Sort high→low percentile, assign ranks.
            rankings.sort(
                key=lambda g: g.percentile if g.percentile is not None else -1,
                reverse=True,
            )
            for idx, g in enumerate(rankings):
                g.rank = idx + 1

            percentiles = [g.percentile for g in rankings if g.percentile is not None]
            spread = max(percentiles) - min(percentiles) if len(percentiles) >= 2 else None

            # Model consistency: check if the rank order is the same across all
            # reliable models (not just the best one).
            consistency = "consistent"
            if len(rankings) >= 2 and all(g.percentile is not None for g in rankings):
                top_label = rankings[0].genome_label
                for tr in trait_reports:
                    if tr.genome_label != top_label:
                        continue
                    alt_reliable = [
                        row for row in tr.rows
                        if row.status == "scored" and row.percentile is not None
                        and row.percentile_reliable and row.pgs_id != rankings[0].best_pgs_id
                    ]
                    second_genome = [g for g in rankings if g.genome_label != top_label]
                    if alt_reliable and second_genome:
                        for alt_row in alt_reliable:
                            for sg_report in trait_reports:
                                if sg_report.genome_label != second_genome[0].genome_label:
                                    continue
                                sg_row = next(
                                    (r for r in sg_report.rows if r.pgs_id == alt_row.pgs_id
                                     and r.status == "scored" and r.percentile is not None),
                                    None,
                                )
                                if (
                                    sg_row
                                    and sg_row.percentile is not None
                                    and alt_row.percentile is not None
                                    and sg_row.percentile > alt_row.percentile
                                ):
                                    consistency = "mixed"
                                    break

            trait_comparisons.append(TraitComparison(
                trait_id=trait_id,
                label=label,
                rankings=rankings,
                percentile_spread=spread,
                model_consistency=consistency,
            ))

        # Sort most divergent traits.
        divergent = sorted(
            [tc for tc in trait_comparisons if tc.percentile_spread is not None],
            key=lambda tc: tc.percentile_spread or 0,
            reverse=True,
        )

        all_labels = list(dict.fromkeys(
            r.genome_label or "unknown" for r in reports
        ))

        summary_parts = [
            f"Compared {len(all_labels)} genomes across "
            f"{len(trait_comparisons)} trait(s)."
        ]
        if divergent:
            summary_parts.append(
                f"Most divergent: {divergent[0].label} "
                f"(spread {divergent[0].percentile_spread:.1f} percentile points)."
            )

        return GenomeComparison(
            genome_labels=all_labels,
            n_traits=len(trait_comparisons),
            traits=trait_comparisons,
            most_divergent_traits=[tc.label for tc in divergent],
            summary=" ".join(summary_parts),
        )

    @mcp.resource("resource://prs/panels")
    def panels() -> str:
        """Reference panels, supported genome builds, and the active cache directory."""
        from just_prs.reference import REFERENCE_PANELS

        lines = ["# just-prs server", ""]
        lines.append(f"- default genome build: **{settings.default_genome_build}**")
        lines.append(f"- default reference panel: **{settings.default_panel}**")
        lines.append(f"- cache dir: `{client.resolved_cache_dir(settings)}`")
        lines.append("")
        lines.append("## Reference panels")
        for name in REFERENCE_PANELS:
            lines.append(f"- `{name}`")
        return "\n".join(lines)

    @mcp.prompt
    def compute_prs_for_trait(trait: str, vcf_path: str = "<path/to/sample.vcf.gz>") -> str:
        """Prompt template: find and compute PRS for a trait against a VCF."""
        return (
            f"I want to compute polygenic risk scores for '{trait}' from the VCF at "
            f"{vcf_path}. Search the PGS Catalog for relevant scores, normalize the "
            "VCF, compute the PRS for the most relevant score(s), and interpret the "
            "results (percentile and quality)."
        )

    @mcp.prompt
    def interpret_prs_result(pgs_id: str, trait: str, percentile: str = "", score: str = "") -> str:
        """Prompt template: interpret a single PRS result for a citizen scientist."""
        header = (
            f"Interpret this Polygenic Risk Score (PRS) result for a citizen scientist.\n\n"
            f"Trait: {trait}\n"
            f"PGS ID: {pgs_id}  https://www.pgscatalog.org/score/{pgs_id}/\n"
        )
        if percentile:
            header += f"Percentile: {percentile}\n"
        if score:
            header += f"Raw PRS value: {score}\n"
        return (
            header + "\n"
            "Please try to use the PGS Catalog page link above for extra context. "
            "If you cannot access it, say that clearly instead of inventing details.\n\n"
            "Structure your response as follows (under 250 words):\n"
            "1. **Verdict** — one bold sentence (e.g. 'Your genetic score for [trait] "
            "is moderately elevated (74th percentile) with moderate confidence.')\n"
            "2. **Key numbers** — 2-4 bullet points: percentile meaning, match "
            "quality, model confidence in plain language.\n"
            "3. **Context** — 1-2 sentences: what this trait IS (health, behavioral, "
            "physical, cognitive — do NOT assume health), how much genetics vs "
            "environment matters, why PRS is one factor among many.\n"
            "4. **What to do** — 1-2 sentences: only if actionable (screening for "
            "health traits). For non-health traits, say no action is needed and why.\n"
            "Citizen scientist audience — clarity and honesty over length.\n\n"
            "After the main section, you MAY add a clearly separated section "
            "(use a horizontal rule ---) with additional commentary: caveats, "
            "ancestry considerations, trait-specific biology, or links to further "
            "reading. This optional section has no word limit but should earn its "
            "length — only include it if you have genuinely useful additional context."
        )

    @mcp.prompt
    def interpret_trait_results(trait: str, n_models: str = "", best_pgs_id: str = "") -> str:
        """Prompt template: interpret combined PRS results across multiple models for one trait."""
        header = (
            f"Interpret these combined Polygenic Risk Score (PRS) results for "
            f"\"{trait}\".\n\n"
        )
        if n_models:
            header += f"Models computed: {n_models}\n"
        if best_pgs_id:
            header += (
                f"Best model: {best_pgs_id}  "
                f"https://www.pgscatalog.org/score/{best_pgs_id}/\n"
            )
        return (
            header + "\n"
            "Please try to use the PGS Catalog page link above for extra context. "
            "If you cannot access it, say that clearly instead of inventing details.\n\n"
            "Structure your response as follows (under 300 words):\n"
            "1. **Verdict** — one bold sentence (e.g. 'Five models consistently "
            "place your [trait] score in the top 10% with moderate confidence.')\n"
            "2. **Model agreement** — 2-3 bullet points: do models agree, percentile "
            "spread, which model is best and why.\n"
            "3. **What the percentile means** — 1-2 sentences in plain language. "
            "This is a genetic predisposition score, not a measurement of the trait "
            "itself.\n"
            "4. **Confidence** — 1-2 sentences: combine model coverage, quality "
            "tier, and number of high-quality models into an honest statement.\n"
            "5. **Context & actions** — 1-2 sentences: what this trait IS (health, "
            "behavioral, physical, cognitive — do NOT assume health), and whether "
            "any action makes sense. For non-health traits, say no action is needed.\n"
            "Citizen scientist audience — clarity and honesty over length.\n\n"
            "After the main section, you MAY add a clearly separated section "
            "(use a horizontal rule ---) with additional commentary: caveats, "
            "ancestry considerations, trait-specific biology, or links to further "
            "reading. This optional section has no word limit but should earn its "
            "length — only include it if you have genuinely useful additional context."
        )
