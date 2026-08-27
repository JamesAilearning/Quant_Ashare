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


def _page_applicable_keys() -> frozenset[str]:
    """页面**真正**用的可用键集合,按它自己的表达式算出来。

    在测试里抄一份「PIPELINE_KEYS | WALK_FORWARD_KEYS - run_scoped」等于把
    同一条规则写两份:页面漏掉那个减法时,抄的这份照样绿。取页面里那行赋值
    的 AST 真跑它。
    """
    from web.operator_ui.config_forms import PIPELINE_KEYS, WALK_FORWARD_KEYS
    from web.operator_ui.pages._config_run_helpers import (
        _RUN_SCOPED_PREFILL_KEYS,
    )

    # 整条派生链一起取:`_PREFILL_APPLICABLE_KEYS` 由 `_PAGE_EMITTED_KEYS`
    # 派生,后者又由三份 `*_EMITTED` 派生。只取最后一行、把中间量当外部注入,
    # 等于把链条中段替换成测试自己的版本——页面在中段漏一个键就测不出来。
    wanted = {
        "_SHARED_EMITTED", "_PIPELINE_ONLY_EMITTED",
        "_WALK_FORWARD_ONLY_EMITTED", "_PAGE_EMITTED_KEYS",
        "_PREFILL_APPLICABLE_KEYS",
    }
    tree = ast.parse(_CONFIG_RUN_PAGE.read_text(encoding="utf-8"))
    chain = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id in wanted
            for target in node.targets
        )
    ]
    assert {
        target.id
        for node in chain
        for target in node.targets
        if isinstance(target, ast.Name)
    } == wanted, "派生链上的常量少了——守卫会静默失去覆盖面"
    namespace: dict[str, Any] = {
        "PIPELINE_KEYS": PIPELINE_KEYS,
        "WALK_FORWARD_KEYS": WALK_FORWARD_KEYS,
        "_RUN_SCOPED_PREFILL_KEYS": _RUN_SCOPED_PREFILL_KEYS,
    }
    exec(  # noqa: S102 - 取的是本仓自己的页面源码
        compile(
            ast.Module(body=chain, type_ignores=[]),
            str(_CONFIG_RUN_PAGE),
            "exec",
        ),
        namespace,
    )
    return frozenset(namespace["_PREFILL_APPLICABLE_KEYS"])


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


def test_run_scoped_key_never_produces_a_false_overwrite_warning() -> None:
    # codex P2 on #471:两个后端 KEYS 常量都含 `output_dir`,但本页从不提交
    # 它(JobManager.start 每次自己注入)。不扣掉的话,同一会话里连着重跑两
    # 次作业,第二次会把第一次的目录报成「被覆盖」——一个本页同时声明「随
    # 运行而生、不会携带」的字段。这里用**页面真正的**可用键集合跑,而不是
    # 自己编一个:编的那个漏掉了才是这条测试要抓的东西。
    applicable = _page_applicable_keys()
    assert "output_dir" not in applicable
    session: dict[str, Any] = {"cr_output_dir": "output/runs/first"}
    apply_prefill = _load("_apply_prefill_to_session", session)

    overwritten = apply_prefill(
        {"topk": 50, "output_dir": "output/runs/second"}, applicable)

    assert overwritten == []
    assert session["cr_output_dir"] == "output/runs/first"


def test_mode_survives_the_run_scoped_subtraction() -> None:
    # 扣 run-scoped 时手滑扣掉 `mode`,源运行的模式就再也落不进本页状态。
    assert "mode" in _page_applicable_keys()


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
