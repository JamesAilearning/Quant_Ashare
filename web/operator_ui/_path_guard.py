"""Shared output path guards for operator UI artifact readers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED_ROOTS: tuple[Path, ...] | None = None


def allowed_output_roots(*, resolve: bool = True) -> tuple[Path, ...]:
    """读边界的两棵树。

    ``resolve=False`` 返回**未解析**的写法。目录记录的过滤要用它:那一侧
    是纯词法路径,而 ``resolve()`` 会穿过符号链接/联接——若 ``output`` 本身
    是个联接,解析后的根变成挂载目标,词法候选一条都对不上,整份目录记录
    被判为不可检视(codex #444 r8,本机用 ``mklink /J`` 复现)。根的定义只
    留这一处,免得调用方各写一份。
    """
    roots = (
        _ALLOWED_ROOTS
        if _ALLOWED_ROOTS is not None
        else (PROJECT_ROOT / "output", PROJECT_ROOT / "output" / "operator_ui")
    )
    return tuple(root.resolve() for root in roots) if resolve else tuple(roots)


def output_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath("output", *parts)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def guard_output_path(path: Path, roots: Iterable[Path] | None = None) -> None:
    resolved = path.resolve()
    allowed_roots = tuple(roots) if roots is not None else allowed_output_roots()
    for root in allowed_roots:
        if _is_under(resolved, root):
            return
    raise ValueError(f"Path {path} is outside allowed roots {allowed_roots}")
