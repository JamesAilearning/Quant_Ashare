"""CLI: Phase A.1 — fetch raw Tushare data for A-share survivorship correction.

Reads ``TUSHARE_TOKEN`` from the environment via
:class:`TushareClient.from_environment`. The token MUST NOT appear in
any CLI argument, config file, or log line.

Usage::

    # Smoke test: pull stock_basic only, dry-run
    python scripts/data_pipeline/01_fetch_tushare.py \\
        --endpoints stock_basic --dry-run \\
        --output-dir /tmp/tushare_dry

    # Real fetch of stock_basic + namechange (fast — 3 calls total)
    python scripts/data_pipeline/01_fetch_tushare.py \\
        --endpoints stock_basic,namechange \\
        --output-dir D:/qlib_data/tushare_raw

    # Full backfill (long pole — 12-24h at 5000-point tier)
    python scripts/data_pipeline/01_fetch_tushare.py \\
        --output-dir D:/qlib_data/tushare_raw

Resume
------
The script uses per-file existence as its checkpoint. Re-running with
the same ``--output-dir`` skips any file already on disk. No separate
``.checkpoint`` file is maintained — if you need to force a re-pull of
a specific endpoint, delete the corresponding output file(s) before
re-running.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logger import get_logger, setup_logging  # noqa: E402
from src.data.tushare.client import TushareClient, TushareClientError  # noqa: E402
from src.data.tushare.fetch_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    FetchManifestError,
    build_manifest,
    clear_manifest,
    merge_manifest,
    read_manifest,
    write_manifest,
)
from src.data.tushare.fetcher import (  # noqa: E402
    DEFAULT_INDICES,
    DEFAULT_RATE_LIMIT_SLEEP_MS,
    ENDPOINTS,
    FetchHole,
    TushareFetcher,
    TushareFetcherConfig,
    TushareFetcherError,
)

# `setup_logging` only attaches a handler to the ``src.*`` logger
# namespace; using ``__name__`` here (which resolves to ``__main__`` when
# the script is run via ``python scripts/...``) would silently drop log
# output. Pin under ``src.scripts.*`` so handlers attach correctly.
_logger = get_logger("src.scripts.data_pipeline.fetch_tushare")


def _log_hole_report(holes: tuple[FetchHole, ...]) -> None:
    """Print a per-endpoint hole report (no-op when there are no holes).

    Called on BOTH the completed-with-holes path AND the hard-abort path so a
    recorded hole is never silently lost — even when a later prerequisite
    failure aborts the run, the holes accumulated before it are surfaced.
    """
    if not holes:
        return
    _logger.error("")
    _logger.error("=== HOLES (%d) — fetch is INCOMPLETE ===", len(holes))
    by_endpoint: dict[str, int] = {}
    for h in holes:
        by_endpoint[h.endpoint] = by_endpoint.get(h.endpoint, 0) + 1
    for endpoint, count in sorted(by_endpoint.items()):
        _logger.error("  %-14s  holes=%5d", endpoint, count)
    for h in holes[:20]:
        _logger.error(
            "    - %s [%s] (%s): %s",
            h.endpoint, h.unit, h.reason_class, h.last_error,
        )
    if len(holes) > 20:
        _logger.error("    ... and %d more", len(holes) - 20)
    _logger.error(
        "Re-run with the same --output-dir to fill the holes "
        "(existing files are skipped; only the missing units are re-fetched)."
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch raw Tushare data for the A-share survivorship "
                    "correction pipeline (Phase A.1).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--output-dir", required=True, type=Path,
        help="Directory to write parquet dumps into. Existing files are "
             "skipped (resume semantics).",
    )
    p.add_argument(
        "--start-date", default="20000101",
        help="YYYYMMDD inclusive (default: 20000101).",
    )
    p.add_argument(
        "--end-date", default="20251231",
        help="YYYYMMDD inclusive (default: 20251231).",
    )
    p.add_argument(
        "--endpoints", default=",".join(ENDPOINTS),
        help=f"Comma-separated endpoint names. Default: all 7. Valid: {','.join(ENDPOINTS)}",
    )
    p.add_argument(
        "--indices", default=",".join(DEFAULT_INDICES),
        help=f"Comma-separated index codes for index_weight. Default: {','.join(DEFAULT_INDICES)}",
    )
    p.add_argument(
        "--rate-limit-sleep-ms", type=int, default=DEFAULT_RATE_LIMIT_SLEEP_MS,
        help=f"Sleep (ms) between Tushare calls. Default {DEFAULT_RATE_LIMIT_SLEEP_MS}ms "
             "= 300 calls/min; tune for your account tier.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Do not write any files; log what would happen.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_arg_parser().parse_args(argv)

    endpoints = tuple(e.strip() for e in args.endpoints.split(",") if e.strip())
    indices = tuple(i.strip() for i in args.indices.split(",") if i.strip())

    try:
        config = TushareFetcherConfig(
            output_dir=args.output_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            endpoints=endpoints,
            indices=indices,
            rate_limit_sleep_ms=args.rate_limit_sleep_ms,
            dry_run=args.dry_run,
        )
    except TushareFetcherError as exc:
        _logger.error("Config invalid: %s", exc)
        return 2

    try:
        client = TushareClient.from_environment()
    except TushareClientError as exc:
        _logger.error("Cannot construct Tushare client: %s", exc)
        return 1

    fetcher = TushareFetcher(client, config)
    try:
        results = fetcher.fetch()
    except (TushareFetcherError, TushareClientError) as exc:
        _logger.error("Fetch failed: %s", exc)
        # A hard abort still surfaces any holes recorded before it, so a
        # recorded hole is never silently lost (the abort dominates the exit
        # code — this stays the hard-abort path, return 1).
        _log_hole_report(fetcher.holes)
        # codex P1: the completed-run manifest update below never runs on a hard
        # abort, but a mid-run abort can leave PARTIAL output — files written
        # before the abort, with or without a recorded hole (e.g. stock_basic
        # writes active_stocks then aborts on the delisted call). So INVALIDATE
        # the manifest on ANY hard abort: a stale "complete" manifest must not be
        # left covering a dir the aborted run may have made partial. A re-run
        # rebuilds it (resume fills the gaps).
        if not config.dry_run:
            manifest_path = config.output_dir / MANIFEST_FILENAME
            # codex P2: clear_manifest itself can raise OSError (read-only dir,
            # permission, locked file). Catch it so the hard-abort path still
            # returns 1 cleanly instead of escaping as a traceback; warn loudly
            # that a stale manifest may remain.
            try:
                clear_manifest(manifest_path)
                _logger.error(
                    "Invalidated %s (hard abort; the run may have left partial "
                    "output). Re-run to rebuild it.", manifest_path,
                )
            except OSError as clear_exc:
                _logger.error(
                    "Hard abort AND could not invalidate %s (%s) — a stale "
                    "manifest may remain; remove it before trusting the dir.",
                    manifest_path, clear_exc,
                )
        return 1

    _logger.info("")
    _logger.info("=== Summary ===")
    total_written = 0
    total_rows = 0
    total_skipped = 0
    for r in results:
        _logger.info(
            "  %-14s  files_written=%5d  rows=%10d  skipped=%5d",
            r.endpoint, r.files_written, r.rows_total, r.skipped,
        )
        total_written += r.files_written
        total_rows += r.rows_total
        total_skipped += r.skipped
    _logger.info("  %-14s  files_written=%5d  rows=%10d  skipped=%5d",
                 "TOTAL", total_written, total_rows, total_skipped)

    # P3-4b: persist this run's coverage + holes to fetch_manifest.json, merged
    # with the prior run so a unit re-fetched this run self-heals its hole (and a
    # still-failing unit's hole stays). Skipped under --dry-run (no side effects).
    # Downstream gating on a holey manifest is P3-4c; this only records.
    if not config.dry_run:
        manifest_path = config.output_dir / MANIFEST_FILENAME
        # The whole read → build → merge → write is fail-loud: read_manifest
        # rejects an unusable prior manifest, merge_manifest refuses a
        # narrower-scope merge (both FetchManifestError), and write_manifest can
        # raise OSError (disk full / permissions / rename failure). All of these
        # MUST surface as a clean non-zero exit, not an escaping traceback after
        # the fetch already ran (codex P2).
        try:
            prev_manifest = read_manifest(manifest_path)
            current_manifest = build_manifest(
                results, fetcher.holes, config.start_date, config.end_date,
            )
            write_manifest(manifest_path, merge_manifest(prev_manifest, current_manifest))
        except (FetchManifestError, OSError) as exc:
            _logger.error("Fetch manifest update failed: %s", exc)
            return 1
        _logger.info("Wrote fetch manifest: %s", manifest_path)

    # Continue-on-error (P3-4a): the fetch finished, but any unit whose call
    # exhausted its retryable retries (or a per-ticker endpoint skipped because
    # stock_basic holed) was recorded as a hole instead of aborting the whole
    # run. A holey dump MUST NOT be mistaken for a complete one — report the
    # holes loudly and exit non-zero so an orchestrator (and the operator) treat
    # this as "completed with holes", never "success". Re-run with the same
    # --output-dir to fill them (file-existence resume re-fetches only the
    # missing units).
    holes = fetcher.holes
    if holes:
        _log_hole_report(holes)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
