"""AppTest 宿主：真跑 `_select_trading_day`，但**不执行整个配置页**。

`config_run.py` 是一个 streamlit 页面脚本——`import` 它会把整页跑一遍，于是
`AppTest` 看到的第一个 selectbox 是那一页自己的「模式」下拉，而不是被测的
日期控件（首版就这么写，实测当场看出来了）。所以这里按本仓既有做法用 AST
把需要的几个函数取出来单独 exec。
"""
from __future__ import annotations

import ast
import types
from datetime import date
from pathlib import Path

import streamlit as st

_PAGE = (
    Path(__file__).resolve().parents[3]
    / "web" / "operator_ui" / "pages" / "config_run.py"
)
#: 这两个纯函数住在 `_config_run_helpers`（可以直接 import，不带 streamlit
#: 副作用）；被测的两个住在页面脚本里，只能按 AST 取。
_WANTED = ("_bind_trading_day_state", "_select_trading_day")

_tree = ast.parse(_PAGE.read_text(encoding="utf-8"))
_defs = [
    node for node in _tree.body
    if isinstance(node, ast.FunctionDef) and node.name in _WANTED
]
assert len(_defs) == len(_WANTED), (
    f"config_run.py 里少了这些函数: {set(_WANTED) - {d.name for d in _defs}}"
)
from web.operator_ui.pages._config_run_helpers import (  # noqa: E402
    _option_index,
    _trading_day_options,
)

_ns: dict[str, object] = {
    "st": st,
    "ProviderMetadata": types.SimpleNamespace,
    "_trading_day_options": _trading_day_options,
    "_option_index": _option_index,
}
exec(  # noqa: S102 - 执行的是本仓页面自己的函数定义
    compile(ast.Module(body=_defs, type_ignores=[]), str(_PAGE), "exec"), _ns)
_select_trading_day = _ns["_select_trading_day"]

OPTIONS = ["2020-01-02", "2021-01-04", "2022-01-04", "2023-01-03"]
_METADATA = types.SimpleNamespace(
    calendar_dates=tuple(date.fromisoformat(v) for v in OPTIONS))

# live default 由用例注入，模拟「换 provider 之后按新日历重算」。
# 用例可以把 live default 设成一个**日历外**的日期,复现「回退 + 绑定」那条路。
_live_default = st.session_state.get("_probe_live_default", "2021-01-04")
_prefilled = st.session_state.get("cr_overall_start")
_wanted = (
    _prefilled if isinstance(_prefilled, str) and _prefilled else _live_default)

# 这次载荷**带没带**这个字段。生产里由 `_PREFILL_SUPPLIED` 算出来;宿主让
# 用例直接注入，用来覆盖「动作是新的、但这个字段压根没被预填」那条路。
_supplied = bool(st.session_state.get("_probe_supplied", _prefilled))

_picked = _select_trading_day(
    "overall_start",
    default=_wanted,
    metadata=_METADATA,
    state_key="cr_dt_overall_start",
    prefill_supplied=_supplied,
)
st.text(f"wanted={_wanted}")
st.text(f"picked={_picked}")
