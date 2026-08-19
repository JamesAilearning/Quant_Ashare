"""运行目录索引的体检与清理 —— **默认只报数,不动文件**。

索引 (`output/runs/_index.jsonl`) 长期被测试污染:写入侧的默认路径按进程 CWD
解析,而 pytest 从仓库根跑,于是每一次触发引擎的测试都往操作人的真实索引追加
一行,产物却在随后被删的临时目录里。写入侧的修复见 openspec change
`2026-08-19-run-catalog-cwd-pollution`;本脚本处理**存量**。

判据与写入侧、与控制台的读边界**同一条**:产物目录必须落在该索引自己那棵
output 树内。落在树外的行,操作人永远打不开。

用法::

    python scripts/prune_run_catalog.py                 # 只报数,什么都不动
    python scripts/prune_run_catalog.py --prune         # 清理,移除行写旁车留证
    python scripts/prune_run_catalog.py --catalog PATH  # 指定索引

**先合写入侧的修复再清理**:反过来做,下一次跑全量测试就重新污染。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CATALOG = _REPO_ROOT / "output" / "runs" / "_index.jsonl"


def _is_inside(child: Path, root: Path) -> bool:
    try:
        Path(os.path.normcase(os.path.normpath(str(child)))).relative_to(
            Path(os.path.normcase(os.path.normpath(str(root))))
        )
    except ValueError:
        return False
    return True


def classify(catalog: Path) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """``(树内行, 树外行, 树外行的解析结果)``。原始文本原样保留,不重排。"""
    tree = catalog.resolve().parent.parent
    keep: list[str] = []
    drop: list[str] = []
    drop_records: list[dict[str, Any]] = []
    for line in catalog.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # 读不懂的行**保留**。判据只针对能证明是残骸的那些;看不懂就不动,
            # 那是别人的数据。
            keep.append(line)
            continue
        output_dir = str(record.get("output_dir") or "").strip()
        target = Path(output_dir) if output_dir else None
        if target is not None and not target.is_absolute():
            target = tree.parent / target
        if target is not None and _is_inside(target, tree):
            keep.append(line)
        else:
            drop.append(line)
            drop_records.append(record)
    return keep, drop, drop_records


def report(keep: list[str], drop: list[str], drop_records: list[dict[str, Any]]) -> None:
    total = len(keep) + len(drop)
    print(f"索引总行数 {total}")
    if not total:
        return
    print(f"  产物在 output 树内   {len(keep):6d}  = {len(keep)/total*100:5.1f}%")
    print(f"  产物在树外(可清理)   {len(drop):6d}  = {len(drop)/total*100:5.1f}%")
    if not drop_records:
        return
    engines = Counter(str(r.get("engine")) for r in drop_records)
    print("  树外行按引擎:", dict(engines))
    dirs = Counter(str(r.get("output_dir") or "(空)") for r in drop_records)
    print(f"  树外行涉及 {len(dirs)} 个独立目录,出现最多的:")
    for path, count in dirs.most_common(4):
        print(f"    {count:5d} 次  {path[:70]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=_DEFAULT_CATALOG)
    parser.add_argument(
        "--prune", action="store_true",
        help="真的动手清理(默认只报数)。移除的行原样写入旁车文件留证。",
    )
    args = parser.parse_args(argv)

    if not args.catalog.is_file():
        print(f"索引不存在:{args.catalog}", file=sys.stderr)
        return 2

    keep, drop, drop_records = classify(args.catalog)
    report(keep, drop, drop_records)

    if not args.prune:
        print("\n(只报数模式。加 --prune 才会动文件。)")
        return 0
    if not drop:
        print("\n没有可清理的行。")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    sidecar = args.catalog.with_name(f"{args.catalog.stem}.pruned-{stamp}.jsonl")
    # 先落证据再改原文件:反过来的话,写旁车失败就等于静默删除。
    sidecar.write_text("\n".join(drop) + "\n", encoding="utf-8")
    args.catalog.write_text(
        ("\n".join(keep) + "\n") if keep else "", encoding="utf-8")
    print(f"\n已移除 {len(drop)} 行,原样留证于:\n  {sidecar}")
    print(f"索引现存 {len(keep)} 行:\n  {args.catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
