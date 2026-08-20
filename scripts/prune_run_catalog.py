"""运行目录索引的体检与清理 —— **默认只报数,不动文件**。

索引 (`output/runs/_index.jsonl`) 长期被测试污染:写入侧的默认路径按进程 CWD
解析,而 pytest 从仓库根跑,于是每一次触发引擎的测试都往操作人的真实索引追加
一行,产物却在随后被删的临时目录里。写入侧的修复见 openspec change
`2026-08-19-run-catalog-cwd-pollution`;本脚本处理**存量**。

判据与写入侧**共用同一个函数** (`catalog_boundary_verdict`):产物目录必须落在
这棵 output 树内。两处各写一份判据,正是它们日后分叉、清理误删合法行的方式。

用法::

    python scripts/prune_run_catalog.py                 # 只报数,什么都不动
    python scripts/prune_run_catalog.py --prune         # 清理,移除行写旁车留证
    python scripts/prune_run_catalog.py --catalog PATH --tree DIR \
                                        --relative-base DIR

`--tree`(判据的边界)和 `--relative-base`(相对路径的锚)都是**独立点名**的,
默认分别是 `<repo>/output` 和 `<repo>`,并且**总会打印出来**。两者都不从别的
路径反推:一旦让「文件摆在哪」或「路径怎么拼」去决定判据,换个摆放位置或换成
链接拼写就会悄悄改变结论 —— 这个病在本 change 里犯过三次。

清理走跨进程锁,与写入侧互斥;拿不到锁就不动手。索引先归到**规范身份**再派生
锁/旁车/暂存件/替换目标 —— 否则 `--catalog` 写成别名拼写时,替换动的是别名条目
而锁又从别名派生,互斥直接落空。硬链接认不出来,发现就拒绝动手。

**先合写入侧的修复再清理**:反过来做,下一次跑全量测试就重新污染。
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.run_catalog import (  # noqa: E402
    canonical_catalog_path,
    catalog_boundary_verdict,
    catalog_lock,
    catalog_lock_path,
)

_DEFAULT_CATALOG = _REPO_ROOT / "output" / "runs" / "_index.jsonl"
_DEFAULT_TREE = _REPO_ROOT / "output"

#: 清理脚本等锁的上限。等不到就整个不动手 —— 清理是操作人手动发起的维护动作,
#: 挑个空闲时刻重跑没有代价;写入侧则相反,那是运行的最后一步。
_PRUNE_LOCK_TIMEOUT = 30.0


def _read_lines(catalog: Path) -> tuple[list[str], bool]:
    """按**字节忠实**地读出索引的每一行,外加「文件末尾有没有换行」。

    两处不可逆的归一化都出在读这一步(codex #453),所以一起收在这里:

    - ``read_text(encoding="utf-8")`` 撞上**一个**非法字节就整个抛
      ``UnicodeDecodeError``,连只报数模式都跑不完 —— 而本工具的职责恰恰是容忍
      畸形/外来数据。改用 ``surrogateescape`` 解码:实测往返后字节完全一致,
      于是坏字节能以「看不懂的行」原样留住,而不是让扫描崩掉。
    - ``str.splitlines()`` 除了换行符,还在 ``U+2028`` / ``U+0085`` 这类字符上
      断行,而 ``json.dumps(ensure_ascii=False)`` 会把它们**原样**吐进记录里
      (实测确认)。于是一条合法记录被切成几段畸形碎片;一旦触发 ``--prune``,
      碎片会被用真换行重写回去,**永久毁掉那条记录**。只按换行符切。

    行尾的 ``CR`` 保留不动 —— 操作人的索引实测 3560 行全是 CRLF(写入侧文本
    模式在 Windows 上会把换行译成 CRLF)。保留它,重写时拼回去正好还原原字节;
    换成「读时归一化、写时再译回」的老做法,一份混合行尾的文件会被悄悄改写成
    单一行尾。
    """
    raw = catalog.read_bytes().decode("utf-8", errors="surrogateescape")
    trailing_newline = raw.endswith("\n")
    lines = raw.split("\n")
    if trailing_newline:
        lines.pop()            # 末尾那个换行不是一行
    return lines, trailing_newline


def _encode_lines(lines: list[str], trailing_newline: bool) -> bytes:
    """与 :func:`_read_lines` 成对:拼回去正好是原来的字节。"""
    body = "\n".join(lines)
    if trailing_newline and lines:
        body += "\n"
    return body.encode("utf-8", errors="surrogateescape")


class Classified(NamedTuple):
    """分类结果。``retained`` 按**原顺序**,重写索引时直接写回它。

    保留的行分两种,报数时必须分开算:一种是**产物确实验证过在树内**,另一种是
    「看不懂所以不动」(空行、坏 JSON、合法但非记录的值)。混作一谈的话,一份
    全是 `null` 的索引会报成「100% 在树内」——而这份报告正是操作人按下
    ``--prune`` 的依据(codex #453)。
    """

    retained: list[str]
    dropped: list[str]
    dropped_records: list[dict[str, Any]]
    verified_in_tree: int
    unclassified: int
    #: 原文件末尾有没有换行。重写时照原样还原,不替操作人补一个。
    trailing_newline: bool


def classify(
    catalog: Path, tree: Path, relative_base: Path,
) -> Classified:
    """把索引的每一行分到三个桶里。原始文本原样保留,不重排。

    ``relative_base`` 是相对路径的锚,**由调用方点名传进来**,绝不从 ``tree``
    反推。本脚本是事后另起的进程,拿不到历史行当年的 CWD,只能沿用控制台读侧
    那条约定(相对 = 相对仓库根);判据本来就是「控制台永远打不开的行」,与
    控制台同约定才自洽。

    曾经写成 ``tree.parent``:那样 ``--tree`` 一旦经别名/联接指向 output 树,
    合法的相对行就会被锚到别名旁边而判成残骸(codex #453)。这是同一个病的
    第三次复发 —— **从路径的拼写或位置推导语义**。
    """
    retained: list[str] = []
    dropped: list[str] = []
    dropped_records: list[dict[str, Any]] = []
    verified = 0
    unclassified = 0
    lines, trailing_newline = _read_lines(catalog)
    for line in lines:
        if not line.strip():
            # 空行也**保留**。它既不在 keep 也不在 drop 的话,一次 --prune 就把
            # 它从活索引里抹掉了,而旁车留证里没有它——「移除的行留得住」这条
            # 承诺就破了(codex #453)。
            retained.append(line)
            unclassified += 1
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # 读不懂的行**保留**。判据只针对能证明是残骸的那些;看不懂就不动,
            # 那是别人的数据。
            retained.append(line)
            unclassified += 1
            continue
        if not isinstance(record, dict):
            # `null` / `123` / `[...]` 都是合法 JSON 但不是记录。以前这里直接
            # `.get()`,于是连只报数模式都会抛 AttributeError 中断(codex #453)。
            # 同样按「看不懂就不动」处理。
            retained.append(line)
            unclassified += 1
            continue
        if catalog_boundary_verdict(
                str(record.get("output_dir") or ""),
                tree=tree, relative_base=relative_base) is None:
            retained.append(line)
            verified += 1
        else:
            dropped.append(line)
            dropped_records.append(record)
    return Classified(retained, dropped, dropped_records, verified, unclassified,
                      trailing_newline)


def report(result: Classified) -> None:
    total = len(result.retained) + len(result.dropped)
    print(f"索引总行数 {total}")
    if not total:
        return
    dropped = len(result.dropped)
    print(f"  产物在 output 树内   {result.verified_in_tree:6d}"
          f"  = {result.verified_in_tree/total*100:5.1f}%")
    print(f"  保留但未验证         {result.unclassified:6d}"
          f"  = {result.unclassified/total*100:5.1f}%   (空行/读不懂/非记录)")
    print(f"  产物在树外(可清理)   {dropped:6d}  = {dropped/total*100:5.1f}%")
    if not result.dropped_records:
        return
    engines = Counter(str(r.get("engine")) for r in result.dropped_records)
    print("  树外行按引擎:", dict(engines))
    dirs = Counter(str(r.get("output_dir") or "(空)")
                   for r in result.dropped_records)
    print(f"  树外行涉及 {len(dirs)} 个独立目录,出现最多的:")
    for path, count in dirs.most_common(4):
        print(f"    {count:5d} 次  {path[:70]}")


def _fingerprint(catalog: Path) -> tuple[int, int]:
    info = catalog.stat()
    return (info.st_size, info.st_mtime_ns)


def _create_beside_catalog(
    catalog: Path, name_for: Callable[[int], str], mode: int,
) -> Path | None:
    """在索引旁边独占创建一个**空**文件,建完立刻按 ``mode`` 设权限。

    本工具造带内容的文件只走这一条路。两个建文件点各写一套,正是它们的顺序
    分叉的原因:旁车当初是「建→chmod→写」,暂存件却是「写→稍后 chmod」——
    那段窗口里同机其他用户读得到保留下来的记录,而进程若在补 chmod 前中断,
    那份宽权限的副本还会永久留在盘上(codex #453)。**顺序是判据的一部分**,
    所以把它焊在一个函数里。

    独占创建(``"x"``)则是另一条:同一秒里两次清理会派生同名文件,直接写
    会**静默截断**前一次的留证 —— 而本工具的全部承诺就是「移除的行留得住」。
    撞名换序号,而不是照写。
    """
    for serial in range(1, 100):
        candidate = catalog.with_name(name_for(serial))
        try:
            with open(candidate, "x", encoding="utf-8"):
                pass
        except FileExistsError:
            continue
        os.chmod(candidate, mode)
        return candidate
    return None


def _serial_suffix(serial: int) -> str:
    return "" if serial == 1 else f"-{serial}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=_DEFAULT_CATALOG)
    parser.add_argument(
        "--tree", type=Path, default=_DEFAULT_TREE,
        help="判据的边界:产物目录必须落在这棵树内(默认 <repo>/output)。",
    )
    parser.add_argument(
        "--relative-base", type=Path, default=_DEFAULT_TREE.parent,
        help="相对 output_dir 的锚(默认仓库根,与控制台读侧同约定)。",
    )
    parser.add_argument(
        "--prune", action="store_true",
        help="真的动手清理(默认只报数)。移除的行原样写入旁车文件留证。",
    )
    args = parser.parse_args(argv)

    if not args.catalog.is_file():
        print(f"索引不存在:{args.catalog}", file=sys.stderr)
        return 2

    # 先把索引归到它的**规范身份**,后面的锁、旁车、暂存件、替换目标全部从它
    # 派生。否则 `--catalog` 写成符号链接或经联接的目录拼写时:替换动的是那个
    # 别名条目(真索引纹丝不动却报告清理成功),而锁又从别名派生,写入侧和这里
    # 各拿各的锁 —— 互斥直接落空(codex #453)。
    catalog = canonical_catalog_path(args.catalog)

    print(f"索引            :{catalog}")
    if str(catalog) != str(args.catalog):
        print(f"  (你传的是 {args.catalog},上面是它的规范路径)")
    print(f"边界(树内即保留):{args.tree}")
    print(f"相对路径锚在    :{args.relative_base}")
    result = classify(catalog, args.tree, args.relative_base)
    report(result)

    if not args.prune:
        print("\n(只报数模式。加 --prune 才会动文件。)")
        return 0
    if not result.dropped:
        print("\n没有可清理的行。")
        return 0

    # 读完与写回之间被追加的那一行,既不在保留集也不在旁车里 —— 一改写就永久
    # 丢失。原子替换关不上这个窗口:原子的是「换文件」那一步,不是「读—改—写」
    # 这整段(codex #453)。所以**整段都放进跨进程锁里**,并在锁内重新分类:
    # 上面报数用的那份快照可能已经过期。
    with catalog_lock(catalog, timeout=_PRUNE_LOCK_TIMEOUT) as held:
        if not held:
            print(
                f"\n{_PRUNE_LOCK_TIMEOUT:.0f} 秒内没拿到索引的锁,**没有清理"
                "任何东西**。多半有运行正在写入;等它结束后重跑。",
                file=sys.stderr)
            return 4

        # 硬链接靠路径规范化认不出来:两个名字指同一个 inode,``realpath``
        # 也分不清谁是谁。而本工具是「换文件」式重写——换掉其中一个名字,
        # 另外那些名字仍指着旧内容。认不出来就不动手。
        links = catalog.stat().st_nlink
        if links > 1:
            print(
                f"\n索引有 {links} 个硬链接名字。本工具靠原子替换改写,只会换掉"
                "其中一个名字,别的名字仍指着旧内容 —— **没有清理任何东西**。"
                "先解开硬链接再重跑。", file=sys.stderr)
            return 5

        before = _fingerprint(catalog)
        result = classify(catalog, args.tree, args.relative_base)
        keep, drop = result.retained, result.dropped
        if not drop:
            print("\n没有可清理的行。")
            return 0

        # 索引自己的权限。本工具在索引旁边造的**每一个**文件都按它来:旁车装
        # 的是被移除的记录、暂存件装的是保留的记录,两者与索引同等敏感;锁文件
        # 只有一个字节、不含数据,但一并按同一权限,好让守卫无需维护例外表
        # ——例外表正是漏掉入口的方式(上一轮就漏了旁车)。
        mode = stat.S_IMODE(os.stat(catalog).st_mode)
        os.chmod(catalog_lock_path(catalog), mode)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        sidecar = _create_beside_catalog(
            catalog,
            lambda n: f"{catalog.stem}.pruned-{stamp}{_serial_suffix(n)}.jsonl",
            mode)
        # 经临时文件原子替换:中途失败留下的是完整旧索引,不是半截文件。
        # 暂存件与旁车都落在规范路径旁边,于是 ``os.replace`` 必定同盘。
        staged = _create_beside_catalog(
            catalog,
            lambda n: f"{catalog.name}.tmp-{stamp}{_serial_suffix(n)}",
            mode)
        if sidecar is None or staged is None:
            # 两个都要收拾:只清理其中一个的话,另一个会以空文件的形式留在盘上,
            # 而我们刚刚宣称「什么都没动」(codex #453)。
            for leftover in (sidecar, staged):
                if leftover is not None:
                    leftover.unlink(missing_ok=True)
            print(
                "\n同一秒里已有 99 个同名文件,再造就要覆盖别人的东西了 —— "
                "**没有清理任何东西**。", file=sys.stderr)
            return 6
        # 先落证据再改原文件:反过来的话,写旁车失败就等于静默删除。
        # 按字节写回,与 `_read_lines` 成对 —— 保住行尾与不可解码的字节。
        sidecar.write_bytes(_encode_lines(drop, True))
        staged.write_bytes(_encode_lines(keep, result.trailing_newline))

        # 走到这里索引还会变,只剩一种可能:有个不遵守这把锁的写入者。现有的
        # 写入侧不会——它等不到锁就放弃追加而不是绕过——所以这是给「将来某个
        # 新写入口忘了拿锁」留的兜底,不是给某条设计好的绕行路径打补丁。
        if _fingerprint(catalog) != before:
            staged.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
            print(
                "\n索引在清理期间仍被改动(有写入者没走这把锁)。为免吞掉那一行,"
                "**没有清理任何东西**。等运行结束后重跑本脚本。", file=sys.stderr)
            return 3

        # 暂存件在**创建时**就已按索引权限设好(见 `_create_beside_catalog`),
        # 所以这里不需要再 chmod 一次 —— 权限只在一个地方定。否则
        # ``os.replace`` 会把暂存件按 umask 得到的 0644 换到活索引上。
        os.replace(staged, catalog)

    print(f"\n已移除 {len(drop)} 行,原样留证于:\n  {sidecar}")
    print(f"索引现存 {len(keep)} 行:\n  {catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
