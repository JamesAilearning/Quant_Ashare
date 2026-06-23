"""Build the committed CI-real mini qlib bundle for the REGEN-2 replay regression.

The REGEN-2 deterministic frozen-score replay (``replay_frozen_baseline_regen2``)
must run **CI-real** — in the fast suite, no RUN_E2E gate, no 5762-instrument
production bundle. This script extracts a minimal, byte-IDENTICAL qlib provider
directory that serves exactly what the replay's ``BacktestRunner`` reads:

  * close/high/low/volume ``.day.bin`` for the REGEN-2 prediction universe (the
    UNION of every fold's frozen-score instruments — the microstructure mask +
    round-lot preflight query the full prediction universe, not just held names);
  * the SH000300 (price) and SH000300TR (total-return) benchmark feature dirs;
  * calendars/day.txt and the instruments lists; plus the namechange parquet
    (the ST mask source, read by src/data/st_history.py, NOT from the bundle).

It is then packed into a SINGLE deterministic, checksummed gzip tarball — so the
committed reference data is one ~15 MB file + its .sha256 (not ~1900 scattered
.day.bin files bloating the git tree). The replay test unpacks it to a temp dir,
verifies the checksum, and points qlib's provider_uri at it.

SAFE VARIANT (the one gotcha): the bins are full-calendar, full-length — each
``.day.bin`` header is a float32 index into calendars/day.txt, so trimming the
calendar would silently mis-align dates. We keep the full calendar + full bins
(subset INSTRUMENTS only) so the committed bytes are bit-identical to production
and the replay reproduces to ~1e-14.

The tarball is byte-reproducible (sorted members, zeroed mtime/uid/gid, gzip
mtime=0) so re-running this script does not churn git unless the data changed.

Usage::

    python scripts/regen/build_regen2_minibundle.py
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import pickle
import shutil
import tarfile
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_SRC = os.environ.get("QUANT_PROVIDER_URI", "D:/qlib_data/my_cn_data_pit")
_DEFAULT_NAMECHANGE = os.environ.get(
    "QUANT_NAMECHANGE_PATH", "D:/qlib_data/tushare_raw/all_namechanges.parquet")
_DEFAULT_FROZEN = _PROJECT_ROOT / "tests/regression/fixtures/regen2/frozen_fold_scores.pkl.gz"
_DEFAULT_TARBALL = _PROJECT_ROOT / "tests/regression/fixtures/regen2_minibundle.tar.gz"
_STAGING = _PROJECT_ROOT / "output" / "_regen2_minibundle_staging"
# arcname root inside the tarball (the test extracts this dir + uses it as provider_uri).
_ARCROOT = "regen2_minibundle"

_FIELDS = ("close", "high", "low", "volume")  # the only fields the replay reads
_BENCHMARKS = ("SH000300", "SH000300TR")


def _universe_from_frozen(frozen_path: Path) -> list[str]:
    with gzip.open(frozen_path, "rb") as fh:
        frozen = pickle.load(fh)
    codes: set[str] = set()
    for entry in frozen.values():
        codes.update(entry["scores"].index.get_level_values("instrument").unique())
    return sorted(codes)


def _copy_feature_dir(src: Path, dest: Path, code: str, fields: tuple[str, ...]) -> int:
    src_dir = src / "features" / code.lower()
    if not src_dir.is_dir():
        raise FileNotFoundError(f"feature dir missing in source bundle: {src_dir}")
    dest_dir = dest / "features" / code.lower()
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for field in fields:
        bin_path = src_dir / f"{field}.day.bin"
        if not bin_path.is_file():
            raise FileNotFoundError(f"{code}: missing {field}.day.bin at {bin_path}")
        shutil.copy2(bin_path, dest_dir / f"{field}.day.bin")
        copied += 1
    return copied


def build_dir(src: Path, frozen_path: Path, namechange_src: Path, dest: Path) -> dict[str, int]:
    universe = _universe_from_frozen(frozen_path)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "features").mkdir(parents=True)
    (dest / "calendars").mkdir(parents=True)
    (dest / "instruments").mkdir(parents=True)
    shutil.copy2(src / "calendars" / "day.txt", dest / "calendars" / "day.txt")
    # qlib's backtest loads the benchmark with future=True (FileCalendarStorage wants
    # calendars/day_future.txt). Neither the production bundle nor this mini-bundle
    # ships one, so qlib falls back to "current calendar" — and that fallback path
    # computes the benchmark leg DIFFERENTLY across platforms for the FIRST/earliest
    # fold (Linux returned an EMPTY benchmark, so fold-0 excess == the absolute
    # return). Provide the future calendar (= day.txt; the bundle already extends to
    # 2026-06-17, past every fold window) so the future=True load SUCCEEDS and the
    # benchmark leg is deterministic on every OS. This is the cross-platform fix.
    shutil.copy2(dest / "calendars" / "day.txt", dest / "calendars" / "day_future.txt")
    shutil.copy2(src / "instruments" / "benchmark.txt", dest / "instruments" / "benchmark.txt")
    shipped = set(universe) | set(_BENCHMARKS)
    src_all = (src / "instruments" / "all.txt").read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in src_all if ln.split("\t")[0] in shipped]
    (dest / "instruments" / "all.txt").write_text("\n".join(kept) + "\n", encoding="utf-8")
    n_files = 0
    for code in universe:
        n_files += _copy_feature_dir(src, dest, code, _FIELDS)
    for bench in _BENCHMARKS:
        bsrc = src / "features" / bench.lower()
        bfields = tuple(p.stem.split(".")[0] for p in bsrc.glob("*.day.bin"))
        n_files += _copy_feature_dir(src, dest, bench, bfields)
    shutil.copy2(namechange_src, dest / "all_namechanges.parquet")
    return {"universe": len(universe), "feature_files": n_files, "all_lines": len(kept),
            "namechange_rows": len(pd.read_parquet(dest / "all_namechanges.parquet"))}


def pack_tarball(stage_dir: Path, out_tgz: Path) -> str:
    """Pack stage_dir into a BYTE-REPRODUCIBLE gzip tarball; return its sha256."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path in sorted(stage_dir.rglob("*"), key=lambda p: p.as_posix()):
            if not path.is_file():
                continue
            arcname = f"{_ARCROOT}/{path.relative_to(stage_dir).as_posix()}"
            info = tarfile.TarInfo(arcname)
            data = path.read_bytes()
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    raw_tar = buf.getvalue()
    out_tgz.parent.mkdir(parents=True, exist_ok=True)
    with open(out_tgz, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(raw_tar)
    digest = hashlib.sha256(out_tgz.read_bytes()).hexdigest()
    out_tgz.with_suffix(out_tgz.suffix + ".sha256").write_text(
        f"{digest}  {out_tgz.name}\n", encoding="utf-8")
    return digest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default=_DEFAULT_SRC, help="source qlib bundle (provider_uri)")
    ap.add_argument("--frozen", default=str(_DEFAULT_FROZEN))
    ap.add_argument("--namechange", default=_DEFAULT_NAMECHANGE)
    ap.add_argument("--out", default=str(_DEFAULT_TARBALL), help="committed .tar.gz path")
    ap.add_argument("--keep-stage", action="store_true", help="keep the unpacked staging dir")
    args = ap.parse_args(argv)

    stats = build_dir(Path(args.src), Path(args.frozen), Path(args.namechange), _STAGING)
    digest = pack_tarball(_STAGING, Path(args.out))
    if not args.keep_stage:
        shutil.rmtree(_STAGING)
    size = Path(args.out).stat().st_size
    print(f"universe: {stats['universe']} instruments + {len(_BENCHMARKS)} benchmarks  "
          f"({stats['feature_files']} bins, {stats['all_lines']} instr lines, "
          f"{stats['namechange_rows']} namechange rows)")
    print(f"tarball: {size:,} bytes ({size / 1e6:.1f} MB)  sha256={digest[:16]}...")
    print(f"written -> {args.out}  (+ .sha256)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
