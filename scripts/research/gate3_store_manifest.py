"""Gate-3 freeze — content-hash manifest of the financial PIT raw store.

Pins the EXACT data version the pre-registration freezes against
(``quality_profitability.yaml`` -> ``pit_data_contract.source_snapshot_manifest``).
Per-file sha256 over every endpoint parquet + an aggregate manifest hash; the
gate's R5 rehearsal check re-runs this and refuses on any mismatch.

Fail-loud: a missing endpoint dir, an empty endpoint, or an unreadable file
aborts — a partial manifest would silently pin less data than the study uses.

Usage:
    python scripts/research/gate3_store_manifest.py \\
        --store-dir D:/qlib_data/financial_pit_raw \\
        --out docs/prereg/quality_profitability_store_manifest.json
    # verify mode (rehearsal R5 / pre-run gate):
    python scripts/research/gate3_store_manifest.py \\
        --store-dir D:/qlib_data/financial_pit_raw \\
        --verify docs/prereg/quality_profitability_store_manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ENDPOINTS = ("income", "balancesheet", "cashflow")


class ManifestError(RuntimeError):
    """Fail-loud: never emit/accept a partial manifest."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_file_hashes(store_dir: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for endpoint in ENDPOINTS:
        ep_dir = store_dir / endpoint
        if not ep_dir.is_dir():
            raise ManifestError(f"endpoint dir missing: {ep_dir}")
        parquets = sorted(ep_dir.glob("*.parquet"))
        if not parquets:
            raise ManifestError(f"endpoint EMPTY: {ep_dir} — refusing a "
                                "partial manifest.")
        for p in parquets:
            files[f"{endpoint}/{p.name}"] = _sha256_file(p)
    return files


def aggregate_hash(files: dict[str, str]) -> str:
    return hashlib.sha256(
        "\n".join(f"{k}={v}" for k, v in sorted(files.items())).encode()
    ).hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store-dir", required=True, type=Path)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--verify", type=Path, default=None,
                   help="compare against an existing manifest; exit 1 on any "
                        "mismatch (rehearsal R5 / pre-run gate).")
    args = p.parse_args(argv)

    files = collect_file_hashes(args.store_dir)
    agg = aggregate_hash(files)

    if args.verify is not None:
        recorded = json.loads(args.verify.read_text(encoding="utf-8"))
        rec_files: dict[str, str] = dict(recorded["files"])
        mismatches: list[str] = []
        for k in sorted(set(rec_files) | set(files)):
            a, b = rec_files.get(k), files.get(k)
            if a != b:
                mismatches.append(f"{k}: recorded={a} actual={b}")
        if mismatches:
            print("MANIFEST MISMATCH — REFUSE:")
            for m in mismatches[:20]:
                print(" ", m)
            return 1
        print(f"MANIFEST OK: {len(files)} files, aggregate={agg[:16]}...")
        return 0

    if args.out is None:
        raise ManifestError("pass --out to write, or --verify to check.")
    manifest: dict[str, object] = {
        "store_dir": str(args.store_dir),
        "n_files": len(files),
        "per_endpoint_counts": {
            ep: sum(1 for k in files if k.startswith(ep + "/"))
            for ep in ENDPOINTS
        },
        "aggregate_sha256": agg,
        "files": files,
    }
    args.out.write_text(
        json.dumps(manifest, indent=1, sort_keys=False), encoding="utf-8",
    )
    print(f"manifest written: {args.out} ({len(files)} files, "
          f"aggregate={agg[:16]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
