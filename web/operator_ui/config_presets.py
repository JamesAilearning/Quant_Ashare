"""Preset helpers for the operator Config & Run page.

``_detect_preset`` in ``pages/config_run.py`` re-walks every preset on
every Streamlit rerun, and rerun frequency on the config page is
extremely high (any widget edit fires one). The two functions exposed
here do all the disk IO — ``list_preset_names`` scans the preset
directory, ``load_preset`` reads + parses a single YAML file — so a
no-cache implementation stat'd and read every preset file on every
keystroke. UI review P1-4 traced visible UI lag to this loop.

Both functions wrap an ``lru_cache``-backed implementation whose key
includes the source file/dir mtime. That means:

* same key, same mtime → O(1) cache hit, zero disk IO.
* same key, mtime changed (operator saved a preset, ran ``cleanup
  output``, hand-edited YAML) → cache miss, fresh read. No TTL guess.
* different key → cache miss as usual.

The cached function returns an **immutable** ``tuple[tuple[str, Any], ...]``
of items; the public wrapper rebuilds a fresh ``dict`` per call so a
caller mutating the returned mapping cannot pollute the cache. Preset
values are flat (no nested dicts in any current preset), so a shallow
copy is sufficient.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

BUILT_IN_PRESET_NAMES = ("Smoke", "Default", "Production")
CUSTOM_PRESET_NAME = "Custom"

# Cache sizes are deliberately small — operators in practice have one
# active preset directory and < 10 saved custom presets. Keeping the
# caches bounded prevents long-lived UI sessions from accumulating
# stale entries.
_LIST_CACHE_SIZE = 8
_LOAD_CACHE_SIZE = 32


def sanitise_preset_name(raw: str) -> str:
    """Return a filesystem-safe preset stem."""
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(raw or "")).strip("_-")


def _safe_mtime(path: Path) -> float:
    """Return ``path``'s mtime, or 0.0 if it cannot be stat'd.

    Used as part of the cache key so the cache invalidates whenever
    the on-disk source changes. Falling back to 0.0 for missing /
    inaccessible paths is safe because the underlying cached function
    also handles the missing case — they'll consistently return the
    empty result.
    """

    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def list_preset_names(presets_dir: Path) -> tuple[str, ...]:
    """Return built-in presets, saved custom presets, and the Custom sentinel.

    Result is cached against ``(str(presets_dir), dir_mtime)``; saving a
    new preset bumps the directory mtime and invalidates the cache,
    so the operator sees the new entry on the next rerun (no TTL wait).
    """

    return _list_preset_names_cached(
        str(presets_dir),
        _safe_mtime(presets_dir),
    )


@functools.lru_cache(maxsize=_LIST_CACHE_SIZE)
def _list_preset_names_cached(
    presets_dir_str: str,
    _dir_mtime: float,  # noqa: ARG001 — part of the cache key only
) -> tuple[str, ...]:
    presets_dir = Path(presets_dir_str)
    builtin_stems = {name.lower() for name in BUILT_IN_PRESET_NAMES}
    saved: list[str] = []
    seen: set[str] = set()
    if presets_dir.is_dir():
        for path in sorted(presets_dir.glob("*.yaml")):
            name = sanitise_preset_name(path.stem).lower()
            if not name or name in builtin_stems or name in seen:
                continue
            saved.append(name)
            seen.add(name)
    return (*BUILT_IN_PRESET_NAMES, *saved, CUSTOM_PRESET_NAME)


#: UI 形状的判据。``mode`` 是 UI 专用键——两条 runtime 入口
#: (``scripts/run_walk_forward.py`` / ``main.py``)都把它当未知键硬拒,而
#: UI 的保存路径必然写入它。所以「带 mode ⟺ 本页能跑」这条判据**自维护**:
#: 新存的预设自动进白名单,新落地的战役冻结件自动进黑名单,无需任何人
#: 手工登记。
UI_SHAPE_MARKER_KEY = "mode"


#: gate3 冻结件带这个键前缀,``run_walk_forward.py`` 见到就硬拒——它们是
#: 预注册裁决包,不是可跑配置。
GATE3_KEY_PREFIX = "gate3_"


def frozen_preset_runner(preset: dict[str, Any]) -> str:
    """冻结件实际该用哪个 runner 复跑:``walk_forward`` / ``pipeline`` /
    ``none``(不可跑) / ``unknown``。

    一份冻结件说明里若统一写「用 run_walk_forward.py」,对 pipeline 形状
    的那几份(bootstrap 三成员、candidate)就是错的——它们 extends
    ``config.yaml``、带 pipeline 窗口键,walk-forward 加载器会拒绝;gate3
    那批则根本不可跑(codex #445 r1)。判据取自各自的窗口键与 gate3 前缀,
    而不是文件名约定。
    """
    if any(str(k).startswith(GATE3_KEY_PREFIX) for k in preset):
        return "none"
    if any(k in preset for k in ("overall_start", "train_months", "step_months")):
        return "walk_forward"
    if "config_walk" in str(preset.get("extends") or ""):
        return "walk_forward"
    if any(k in preset for k in ("train_start", "valid_start", "test_start")):
        return "pipeline"
    return "unknown"


def classify_preset_names(
    presets_dir: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """把预设分成(本页可跑的, 战役冻结件)两组。

    冻结件不是"坏文件"——它们是预注册/认证证据,只是**不能从本页启动**:
    本页产出的是 standalone 配置,不解析 ``extends``(父配置的窗口/成本/ST
    口径会丢),而 ``rebalance_*`` / ``risk_constraint_scope`` / ``output_dir``
    等键本页没有控件、提交时被静默丢弃。把它们和可跑预设混在同一个下拉框里
    就会出现「标签显示 csi800_cadence5_conservative_isoweek、发出去的却是
    日频 pipeline 配置」——操作人读到的节奏不是将要跑的节奏。

    不改 :func:`list_preset_names` 的返回值语义(既有调用方与测试不受影响);
    分类不自建缓存,而是复用它的目录级缓存 + ``load_preset`` 的 per-file
    缓存,避免"目录 mtime 在原地编辑文件时不变 → 分类陈旧"这个坑。
    """
    runnable: list[str] = []
    frozen: list[str] = []
    for name in list_preset_names(presets_dir):
        if name == CUSTOM_PRESET_NAME:
            continue
        raw = load_preset(presets_dir, name)
        if raw and UI_SHAPE_MARKER_KEY in raw:
            runnable.append(name)
        elif raw:
            frozen.append(name)
        else:
            # 读不出来(缺失/畸形)的既不能跑也不该当冻结证据展示;
            # 内置名即使文件暂时读不到也保留在可选项里,否则页面会
            # 因为一个损坏文件而失去默认预设。
            if name in BUILT_IN_PRESET_NAMES:
                runnable.append(name)
    return tuple(runnable), tuple(frozen)


def load_preset(presets_dir: Path, name: str) -> dict[str, Any]:
    """Load a preset by display name or saved preset stem.

    Result is cached against ``(str(path), file_mtime)``. Each call
    constructs a fresh ``dict`` from the cached items tuple so a
    downstream caller cannot accidentally mutate the cache.
    """

    safe_name = sanitise_preset_name(name).lower()
    if not safe_name:
        return {}
    path = presets_dir / f"{safe_name}.yaml"
    items = _load_preset_cached(str(path), _safe_mtime(path))
    return dict(items)


@functools.lru_cache(maxsize=_LOAD_CACHE_SIZE)
def _load_preset_cached(
    path_str: str,
    _file_mtime: float,  # noqa: ARG001 — part of the cache key only
) -> tuple[tuple[str, Any], ...]:
    """Read + parse the preset YAML; return as an immutable tuple of items.

    Using an items tuple (rather than a dict) for the cached value
    means the wrapper above gets a fresh ``dict`` on every call,
    sidestepping the "callers mutate the cached value" footgun that
    bites ``lru_cache`` of mutable returns. Preset YAML in this
    project is flat (no nested dicts), so a shallow ``dict(items)``
    copy is sufficient — a deepcopy would be safer if presets ever
    grow nested values.
    """

    path = Path(path_str)
    if not path.is_file():
        return ()
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return ()
    if not isinstance(loaded, dict):
        return ()
    return tuple(loaded.items())


def clear_preset_caches() -> None:
    """Drop both LRU caches. Useful in tests and rare runtime cases
    (e.g., the operator manually edits a YAML and Streamlit's session
    doesn't naturally rerender)."""

    _list_preset_names_cached.cache_clear()
    _load_preset_cached.cache_clear()
