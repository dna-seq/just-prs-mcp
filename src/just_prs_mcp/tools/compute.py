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
    NormalizeResult,
    OpResult,
    PercentileResult,
    PRSResult,
    QualityAssessment,
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
    },
    "livia": {
        "record": "19487816",
        "who": "Livia Zaharia",
        "license": "CC-BY-4.0",
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
    ) -> OpResult:
        """Download a public sample WGS VCF from Zenodo to try PRS without your own data.

        For users who don't have their own VCF: two genomes open-sourced by the
        just-dna-lite authors are available — ``sample="anton"`` (Anton Kulaga,
        CC0) and ``sample="livia"`` (Livia Zaharia, CC-BY-4.0). Pass ``record_url``
        (e.g. 'https://zenodo.org/records/18370498') to fetch any other Zenodo
        record, and ``filename`` to pick a specific file when a record has several.

        The downloaded ``.vcf`` / ``.vcf.gz`` lands under the cache dir (or
        ``output_dir``) and is a drop-in path for ``normalize_vcf`` / ``compute_prs``.
        Returns an ``OpResult`` whose ``data`` carries the local ``path`` on success.
        Runs as a background task (the file is several GB for a full WGS genome).
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
        return OpResult(
            success=True,
            message=(
                f"Downloaded {chosen['key']} ({downloaded / 1e9:.2f} GB) from {label}. "
                f"Pass it to normalize_vcf or compute_prs as the VCF path."
            ),
            data={
                "path": str(dest),
                "filename": str(chosen["key"]),
                "bytes": downloaded,
                "source": label,
            },
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
        return TraitPRSReport(
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
        )

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
