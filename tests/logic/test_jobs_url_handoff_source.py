"""Regression coverage for cross-page Jobs filter handoffs.

交接语义的最终形态：**URL 是这次导航的完整筛选状态**。交接键不区分「这条
链接带没带它」——没带的键 ``_qp_read`` 给默认值，于是被一次性**重置**。
队列链接（只带 ``status``）因此能显示「该状态的全部作业」，而不是保留一个
无关的搜索词让操作人看到空列表。

曾经在实现里加过 ``k in st.query_params`` 前提（「没带就别动」）。用页面
自己的 AST 跑三变体 × 四个真实场景实测：带前提 3/4、评审建议的「跳过重播种」
2/4、**去掉前提 4/4**。带前提时同一条队列链接会因 ``jobs_last_url_*`` 这种
内部残值而给出不同行为。前提已删——这份夹具不再模拟 ``st.query_params``。
"""

from __future__ import annotations

import ast
from pathlib import Path

from web.operator_ui._param_guard import sanitize

_JOBS_PAGE = Path("web/operator_ui/pages/jobs.py")


class _FakeStreamlit:
    """被测函数用到的全部 streamlit 表面——只有 ``session_state``。

    这里**刻意不提供** ``query_params``：最终实现不问「URL 里有没有这个
    键」，喂一个假的 ``query_params`` 会让这份夹具继续描述一个已被实测否掉
    的契约，也给后来的人一个把那道前提加回去的着力点（codex #473）。URL 的
    内容全部经由 ``_qp_read`` 进入。
    """

    def __init__(self, *, session_state: dict[str, str]) -> None:
        self.session_state = session_state


def _seed_handoff(
    *,
    session_state: dict[str, str],
    url_values: dict[str, str],
    keys: list[str],
    handoff_token: str = "",
    handoff_keys: frozenset[str] = frozenset(),
    handoff_preserve: frozenset[str] = frozenset(),
) -> None:
    tree = ast.parse(_JOBS_PAGE.read_text(encoding="utf-8"))
    # 被测函数的模块级依赖一起取——漏掉会 NameError（响亮），不会静默少测。
    wanted = {"_seed_session_from_url", "_iso_to_date", "_HANDOFF_WIDGET_MIRRORS"}
    body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            body.append(node)
        elif (isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in wanted):
            body.append(node)
    assert len(body) == len(wanted), f"jobs.py 里少了定义: {wanted}"
    from datetime import date as _date
    namespace = {
        "st": _FakeStreamlit(session_state=session_state),
        "_qp_read": url_values.__getitem__,
        "date": _date,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(_JOBS_PAGE), "exec"), namespace)
    namespace["_seed_session_from_url"](
        keys,
        handoff_token=handoff_token,
        handoff_keys=handoff_keys,
        handoff_preserve=handoff_preserve,
    )


def test_jobs_status_handoff_replaces_stale_page_state_once() -> None:
    session_state = {
        "jobs_status": "running",
        "jobs_last_url_status": "running",
    }
    url_values = {"status": "failed"}

    _seed_handoff(session_state=session_state, url_values=url_values, keys=["status"])

    assert session_state["jobs_status"] == "failed"
    assert session_state["jobs_last_url_status"] == "failed"

    # The Jobs page has not mirrored the user's new choice to URL yet; this
    # rerun must not clobber that widget update back to the old handoff value.
    session_state["jobs_status"] = "partial"
    _seed_handoff(session_state=session_state, url_values=url_values, keys=["status"])
    assert session_state["jobs_status"] == "partial"


def test_new_queue_navigation_reapplies_the_same_status_once() -> None:
    session_state = {
        "jobs_status": "running",
        "jobs_last_url_status": "failed",
        "jobs_last_handoff_status": "a" * 32,
    }
    url_values = {"status": "failed"}

    _seed_handoff(
        session_state=session_state,
        url_values=url_values,
        keys=["status"],
        handoff_token="b" * 32,
        handoff_keys=frozenset({"status"}),
    )

    assert session_state["jobs_status"] == "failed"
    assert session_state["jobs_last_handoff_status"] == "b" * 32

    # The same navigation token is already consumed, so a widget selection on
    # the next Streamlit rerun is never overwritten by the stale URL handoff.
    session_state["jobs_status"] = "partial"
    _seed_handoff(
        session_state=session_state,
        url_values=url_values,
        keys=["status"],
        handoff_token="b" * 32,
        handoff_keys=frozenset({"status"}),
    )
    assert session_state["jobs_status"] == "partial"


def test_jobs_handoff_token_accepts_only_opaque_uuid_hex() -> None:
    assert sanitize("handoff", "a" * 32, default="") == "a" * 32
    assert sanitize("handoff", "not-a-token", default="") == ""


def _detail_link_seed(session_state: dict[str, str]) -> None:
    """按详情页链接真实的形状播一次种。

    链接只带 ``status`` + ``search`` + ``handoff``；其余键在 URL 里没有，
    ``_qp_read`` 给默认值——这正是「到达的 URL 就是完整筛选状态」那句话的
    机制。交接键取 ``jobs.py`` 里那个**推导出来**的集合，不在这里手抄。
    """
    import ast

    tree = ast.parse(_JOBS_PAGE.read_text(encoding="utf-8"))
    namespace: dict[str, object] = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            target = names[0] if names else None
        if target not in {"_DEFAULTS", "_HANDOFF_EXEMPT", "_HANDOFF_KEYS"}:
            continue
        exec(  # noqa: S102 - 本仓页面自己的字面量赋值
            compile(ast.Module(body=[node], type_ignores=[]),
                    str(_JOBS_PAGE), "exec"), namespace)
    defaults = dict(namespace["_DEFAULTS"])          # type: ignore[arg-type]
    handoff_keys = namespace["_HANDOFF_KEYS"]        # type: ignore[assignment]

    url_values = dict(defaults)
    url_values["status"] = "running"
    url_values["search"] = "job-42"

    _seed_handoff(
        session_state=session_state,
        url_values=url_values,
        keys=list(defaults),
        handoff_token="tok-1",
        handoff_keys=handoff_keys,   # type: ignore[arg-type]
        handoff_preserve=namespace["_HANDOFF_EXEMPT"],  # type: ignore[arg-type]
    )


def test_a_stale_non_search_filter_does_not_swallow_the_exact_row() -> None:
    """操作人离开前改过 ``type``，而它的「上次消费值」仍是默认。

    普通分支的条件是「URL 值与上次消费的 URL 值不同」:URL 里没有 ``type``
    ⇒ ``_qp_read`` 给 ``all``，而 ``jobs_last_url_type`` 也是 ``all``
    ⇒ 条件为假 ⇒ **保留 provider**。被请求的那个运行当场被筛掉，说好的
    「精确落到那一行」落到一个空列表上（codex P2 on #473）。

    ``page`` 同理:停在第 3 页时，单行结果在第 1 页——照样什么也看不见。
    """
    session_state = {
        "jobs_type": "provider",
        "jobs_last_url_type": "all",
        "jobs_page": "3",
        "jobs_last_url_page": "1",
        "jobs_status": "all",
        "jobs_last_url_status": "all",
        "jobs_search": "",
        "jobs_last_url_search": "",
    }

    _detail_link_seed(session_state)

    assert session_state["jobs_status"] == "running"
    assert session_state["jobs_search"] == "job-42"
    assert session_state["jobs_type"] == "all", "陈旧的 type 把那一行筛掉了"
    assert session_state["jobs_page"] == "1", "陈旧的分页让那一行落在别的页上"


def test_presentation_preferences_survive_the_handoff() -> None:
    """排序与自动刷新**不**被交接重置——它们不改成员，也不改那一行在哪。

    把它们一起重置，等于替操作人做了一个链接从没请求过的决定。
    """
    # **settled 态**:页面每帧把 session 回镜进 URL,所以操作人选定之后两者
    # 相等。原来这里喂的是 `jobs_last_url_sort_by="created_at"` 的**残值
    # 态**——那正是掩盖缺陷的那一格:残值态下普通分支看到「URL 值没变」就
    # 不动，于是偏好「看起来」被保住了。settled 态下 `_qp_read` 给默认值、
    # 「URL 值变了」成立，普通分支照样重置（codex P2 on #473;这是本 PR 第
    # 三次栽在「用例喂了一个非典型状态」上）。
    session_state = {
        "jobs_sort_by": "duration",
        "jobs_last_url_sort_by": "duration",
        "jobs_autorefresh": "1",
        "jobs_last_url_autorefresh": "1",
        "jobs_status": "all",
        "jobs_last_url_status": "all",
    }

    _detail_link_seed(session_state)

    assert session_state["jobs_sort_by"] == "duration"
    assert session_state["jobs_autorefresh"] == "1"


def test_an_exempt_key_is_only_skipped_on_the_handoff_frame() -> None:
    """跳过只发生在**交接的那一帧**，不是永久豁免。

    条件里少了「这次交接还没消费过」，呈现偏好就会在**每一帧**被跳过——
    URL 里带着 handoff 参数期间，操作人再也改不动排序（变异实测会逃逸，
    所以这条用例存在）。
    """
    import ast

    tree = ast.parse(_JOBS_PAGE.read_text(encoding="utf-8"))
    namespace: dict[str, object] = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            target = names[0] if names else None
        if target not in {"_DEFAULTS", "_HANDOFF_EXEMPT", "_HANDOFF_KEYS"}:
            continue
        exec(  # noqa: S102 - 本仓页面自己的字面量赋值
            compile(ast.Module(body=[node], type_ignores=[]),
                    str(_JOBS_PAGE), "exec"), namespace)
    defaults = dict(namespace["_DEFAULTS"])          # type: ignore[arg-type]

    session_state = {
        "jobs_sort_by": "duration",
        "jobs_last_url_sort_by": "duration",
    }

    def seed(url_sort: str) -> None:
        url_values = dict(defaults)
        url_values["sort_by"] = url_sort
        _seed_handoff(
            session_state=session_state,
            url_values=url_values,
            keys=list(defaults),
            handoff_token="tok-1",
            handoff_keys=namespace["_HANDOFF_KEYS"],        # type: ignore[arg-type]
            handoff_preserve=namespace["_HANDOFF_EXEMPT"],  # type: ignore[arg-type]
        )

    # 第一帧:交接到达，链接没带 sort_by ⇒ 偏好被保住。
    seed("created_at")
    assert session_state["jobs_sort_by"] == "duration"

    # 第二帧:**同一个令牌**，但它已经被上一帧消费掉了（由被测代码自己标记,
    # 不是用例预先塞进去的——预塞会让「标记已消费」那一行的变异逃逸）。
    # 此时操作人在页面上改了排序、页面把它回镜进 URL,普通分支必须跟上。
    seed("duration_desc")
    assert session_state["jobs_sort_by"] == "duration_desc", (
        "交接消费完之后，呈现偏好必须重新跟着 URL 走——否则它被永久豁免了"
    )


def test_the_handoff_also_resets_the_widget_that_shadows_a_filter() -> None:
    """遮挡筛选状态的控件键也要跟着重置。

    大多数筛选控件直接用 ``key="jobs_<k>"``——控件与筛选状态是同一个键。但
    ``st.date_input`` 用的是**另一个** key（``jobs_date_from_widget``），而
    紧随其后那行又把控件的值写回 ``jobs_date_from``。只重置筛选状态毫无用
    处:控件在同一帧把陈旧日期原样写回去，链接照样落到一个空列表上
    （codex P2 on #473）。
    """
    import ast

    tree = ast.parse(_JOBS_PAGE.read_text(encoding="utf-8"))
    namespace: dict[str, object] = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            target = names[0] if names else None
        if target not in {"_DEFAULTS", "_HANDOFF_EXEMPT", "_HANDOFF_KEYS"}:
            continue
        exec(  # noqa: S102 - 本仓页面自己的字面量赋值
            compile(ast.Module(body=[node], type_ignores=[]),
                    str(_JOBS_PAGE), "exec"), namespace)
    defaults = dict(namespace["_DEFAULTS"])          # type: ignore[arg-type]

    from datetime import date

    session_state: dict[str, object] = {
        # 操作人先前选过一个日期区间，控件键里躺着它。
        "jobs_date_from": "2026-01-01",
        "jobs_last_url_date_from": "2026-01-01",
        "jobs_date_from_widget": date(2026, 1, 1),
        "jobs_date_to": "2026-01-31",
        "jobs_last_url_date_to": "2026-01-31",
        "jobs_date_to_widget": date(2026, 1, 31),
    }
    url_values = dict(defaults)
    url_values["status"] = "running"
    url_values["search"] = "job-42"

    _seed_handoff(
        session_state=session_state,   # type: ignore[arg-type]
        url_values=url_values,
        keys=list(defaults),
        handoff_token="tok-1",
        handoff_keys=namespace["_HANDOFF_KEYS"],        # type: ignore[arg-type]
        handoff_preserve=namespace["_HANDOFF_EXEMPT"],  # type: ignore[arg-type]
    )

    assert session_state["jobs_date_from"] == ""
    assert session_state["jobs_date_from_widget"] is None, (
        "遮挡筛选的那个控件键没被重置——它会在同一帧把陈旧日期写回来"
    )
    assert session_state["jobs_date_to_widget"] is None


def test_every_shadowing_widget_is_either_mirrored_or_exempt() -> None:
    """任何**遮挡了筛选状态**的控件键，要么进镜像表，要么在豁免集合里。

    构造性地枚举，而不是手写一份清单:将来有人给某个筛选加一个
    ``key="jobs_<k>_widget"`` 的控件，这条会立刻红，而不是等到「链接落到
    空列表」被人发现。
    """
    import ast
    import re

    source = _JOBS_PAGE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    namespace: dict[str, object] = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            target = names[0] if names else None
        if target not in {"_DEFAULTS", "_HANDOFF_EXEMPT",
                          "_HANDOFF_WIDGET_MIRRORS"}:
            continue
        exec(  # noqa: S102 - 本仓页面自己的字面量赋值
            compile(ast.Module(body=[node], type_ignores=[]),
                    str(_JOBS_PAGE), "exec"), namespace)
    defaults = dict(namespace["_DEFAULTS"])            # type: ignore[arg-type]
    exempt = set(namespace["_HANDOFF_EXEMPT"])         # type: ignore[arg-type]
    mirrors = dict(namespace["_HANDOFF_WIDGET_MIRRORS"])  # type: ignore[arg-type]

    widget_keys = set(re.findall(r'key="(jobs_[a-z_]+)"', source))
    assert widget_keys, "一个控件 key 都没解析到——这条守卫会变成空集上的真命题"

    for k in defaults:
        shadow = f"jobs_{k}_widget"
        if shadow not in widget_keys:
            continue
        assert k in mirrors or k in exempt, (
            f"`{shadow}` 遮挡了筛选键 `{k}`，但它既不在镜像表里也不豁免"
            "——交接重置了筛选状态，控件会在同一帧把陈旧值写回来"
        )
    # 镜像表里也不许有**不存在的**控件键（抄错名字 = 那一行永远不生效）。
    for k, widget in mirrors.items():
        assert widget in widget_keys, f"镜像表指向了不存在的控件键 {widget}"
        assert k in defaults, f"镜像表里的 {k} 不是一个筛选键"
