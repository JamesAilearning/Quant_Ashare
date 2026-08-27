"""「用此配置重跑」的**运行时**覆盖——源码串断言看不见 session 状态。

源码串断言能证明某一行存在，不能证明预填**真的落进了字段状态**，更不能
证明控件**真的读了**它。#471 的 codex P1 正是这一条：预填把
``cr_overall_start`` 写好了，而滚动验证的日期控件从不读它，于是重跑跑的
是本机 live default 的区间，两侧源码却都「看起来对」。

沿用本仓既有做法（``test_jobs_url_handoff_source.py``）：用 AST 从页面源码
里取出被测函数，注入一个假 ``st``，**真跑**它。这样不需要 Streamlit 运行
时，也不必把页面拆成两个可导入模块。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from web.operator_ui.pages._config_run_helpers import _values_agree

_CONFIG_RUN_PAGE = Path("web/operator_ui/pages/config_run.py")


class _FakeStreamlit:
    def __init__(self, session_state: dict[str, Any]) -> None:
        self.session_state = session_state


def _load(name: str, session_state: dict[str, Any]) -> Any:
    """取出页面里的一个顶层函数并绑到假 ``st`` 上真跑。"""
    tree = ast.parse(_CONFIG_RUN_PAGE.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace: dict[str, Any] = {
        "st": _FakeStreamlit(session_state),
        "_values_agree": _values_agree,
        "Any": Any,
    }
    exec(  # noqa: S102 - 取的是本仓自己的页面源码
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(_CONFIG_RUN_PAGE),
            "exec",
        ),
        namespace,
    )
    return namespace[name]


_APPLICABLE = frozenset({
    "mode", "topk", "n_drop", "overall_start", "overall_end",
    "risk_constraints_enabled",
})


def test_prefill_overwrites_a_field_the_page_already_seeded() -> None:
    # 缺陷本体:`_cr()` 只要被调用过就把 `cr_*` 种满,所以「打开过一次配置
    # 页」之后每个键都已存在。条件写入在这条路径上一个字段也进不来。
    session: dict[str, Any] = {"cr_topk": 30, "cr_n_drop": 5}
    apply_prefill = _load("_apply_prefill_to_session", session)

    overwritten = apply_prefill({"topk": 50, "n_drop": 5}, _APPLICABLE)

    assert session["cr_topk"] == 50
    assert overwritten == [("topk", 30, 50)]


def test_prefill_writes_fields_the_session_never_had() -> None:
    session: dict[str, Any] = {}
    apply_prefill = _load("_apply_prefill_to_session", session)

    overwritten = apply_prefill({"topk": 50}, _APPLICABLE)

    assert session["cr_topk"] == 50
    # 没有旧值就不是「覆盖」,不该出现在覆盖列表里制造噪音。
    assert overwritten == []


def test_prefill_never_writes_keys_outside_the_submit_schema() -> None:
    # 源 YAML 的任意键都写 `cr_<key>` 会撞控件键(cr_preset_selector 等)。
    session: dict[str, Any] = {}
    apply_prefill = _load("_apply_prefill_to_session", session)

    apply_prefill({"topk": 50, "preset_selector": "Smoke"}, _APPLICABLE)

    assert session == {"cr_topk": 50}


def test_numerically_equal_value_is_not_reported_as_overwritten() -> None:
    # 预填走 yaml.safe_load、生效值走控件,同一个数可以是 50 与 50.0。
    session: dict[str, Any] = {"cr_topk": 50.0}
    apply_prefill = _load("_apply_prefill_to_session", session)

    assert apply_prefill({"topk": 50}, _APPLICABLE) == []


def test_walk_forward_window_reads_the_prefilled_value() -> None:
    # codex P1 on #471:overall_start/overall_end 是滚动验证窗口的两个
    # **定义性**字段。预填把它们写进 session,控件此前从不读 ⇒ 重跑跑的
    # 区间与源运行不同,而复核区看不出来(两侧都是控件产出的 live default)。
    session: dict[str, Any] = {
        "cr_overall_start": "2018-01-02", "cr_overall_end": "2024-12-31",
    }
    prefilled_day = _load("_prefilled_trading_day", session)

    assert prefilled_day("overall_start", "2023-06-12") == "2018-01-02"
    assert prefilled_day("overall_end", "2026-08-27") == "2024-12-31"


def test_walk_forward_window_falls_back_to_the_live_calendar_default() -> None:
    # #300 的病根:`_cr` 会把 provider 相关的 live default **种进** session
    # 并从此粘住,冻结第一帧的 no-calendar 回退。这里没有预填时一个字节也
    # 不写,live default 每帧照常重算。
    session: dict[str, Any] = {}
    prefilled_day = _load("_prefilled_trading_day", session)

    assert prefilled_day("overall_start", "2023-06-12") == "2023-06-12"
    assert session == {}


def test_walk_forward_window_ignores_unusable_prefill_values() -> None:
    # 空串与非字符串残值不能压过 live default——那会把窗口打成空/非法,而
    # 控件的日历索引查找会静默 snap 到日历首日。
    session: dict[str, Any] = {"cr_overall_start": "", "cr_overall_end": None}
    prefilled_day = _load("_prefilled_trading_day", session)

    assert prefilled_day("overall_start", "2023-06-12") == "2023-06-12"
    assert prefilled_day("overall_end", "2026-08-27") == "2026-08-27"
