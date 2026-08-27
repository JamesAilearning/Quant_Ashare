"""对比篮子的共享渲染。

三个来源页（作业 / 结果 / 滚动验证）各自写一遍渲染就会分叉——尤其是
「送不进去时说什么」那一段，分叉的后果是某一页悄悄退回一句「不可用」。
渲染集中在这里，页面只提供 run_id 与它自己的键前缀。

与 :mod:`web.operator_ui.page_header` 同构：模块级 ``import streamlit``，
函数直接画，不返回可测结构。可测的判定全在
:mod:`web.operator_ui.compare_basket` 的纯函数里。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import streamlit as st

from web.operator_ui.compare_basket import (
    BASKET_STATE_KEY,
    MAX_BASKET_SIZE,
    add_to_basket,
    admit_to_basket,
    basket_query_value,
    basket_readiness,
    remove_from_basket,
    revalidate_basket,
)
from web.operator_ui.job_io import load_all_jobs_read_only
from web.operator_ui.pages._research_run_comparison_helpers import (
    selectable_catalog,
)

#: 对比页在 ``st.page_link`` 里的相对路径（app.py 用同一个文件名注册）。
COMPARISON_PAGE = "pages/research_run_comparison.py"


@dataclass(frozen=True)
class CatalogView:
    """本帧读到的目录，按加入按钮与篮子面板都需要的形状。

    加入按钮在来源页的动作列里画，篮子面板要在**页面宽度**下画（成员行、
    每条失效说明、嵌套的移除列、跳转链接挤进三分之一列宽会没法读）。两者
    因此分两次调用——但目录只读一次，判定逻辑也只有一份：让每页各读一次，
    等于把「传全量目录行还是只传当前所有者」这个坑挖三遍。
    """

    all_rows: tuple[object, ...]
    selectable_ids: tuple[str, ...]
    run_id_alias: Mapping[str, str]


def render_add_to_basket(run_id: str, *, key_prefix: str) -> CatalogView:
    """画「加入对比」按钮，并把本帧读到的目录交回给调用方。

    调用方拿它去画篮子面板（``render_basket``）——**在动作列之外**。传全量
    目录行是必需的：只传当前所有者的话，「被同目录的更新运行接管」会退化成
    「目录里根本没有这条」，分因就没了，操作人只剩一句「不可用」。
    """

    all_rows = tuple(load_all_jobs_read_only())
    catalog = selectable_catalog(all_rows)
    view = CatalogView(
        all_rows=all_rows,
        selectable_ids=tuple(row.run_id for row in catalog.rows),
        run_id_alias=catalog.run_id_alias,
    )
    render_add_to_basket_button(
        run_id,
        selectable_ids=view.selectable_ids,
        run_id_alias=view.run_id_alias,
        all_rows=view.all_rows,
        key_prefix=key_prefix,
    )
    return view


def render_basket(view: CatalogView, *, key_prefix: str) -> None:
    """在**页面宽度**下画篮子面板，复用上一步读到的目录。"""

    render_basket_panel(
        key_prefix=key_prefix,
        selectable_ids=view.selectable_ids,
        run_id_alias=view.run_id_alias,
        all_rows=view.all_rows,
    )


def render_standalone_basket(*, key_prefix: str) -> None:
    """没有「当前运行」可加入时，仍然把篮子画出来。

    作业页的表格默认没有选中行，而加入按钮需要一个选中的运行。篮子面板本身
    **不**需要——它是会话级的，操作人从别的页攒好切过来就该看得见。挂在
    「有选中行」里面的话，他看到的是「篮子不见了」，随便点中任意一行（哪怕
    与篮子毫无关系）它才回来。

    自己读一次目录：这条路径上没有加入按钮先读过。空篮子时面板本身什么都不
    画，所以这次读取只发生在真的有东西要复核的时候。
    """

    if not current_basket():
        return
    all_rows = tuple(load_all_jobs_read_only())
    catalog = selectable_catalog(all_rows)
    render_basket_panel(
        key_prefix=key_prefix,
        selectable_ids=tuple(row.run_id for row in catalog.rows),
        run_id_alias=catalog.run_id_alias,
        all_rows=all_rows,
    )


def current_basket() -> tuple[str, ...]:
    """本会话的对比篮子。非法残值（不是字符串序列）当作空篮子。"""
    raw = st.session_state.get(BASKET_STATE_KEY)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw if str(item))


def _store_basket(basket: tuple[str, ...]) -> None:
    st.session_state[BASKET_STATE_KEY] = list(basket)


def render_add_to_basket_button(
    run_id: str,
    *,
    selectable_ids: Iterable[str],
    run_id_alias: Mapping[str, str],
    all_rows: Iterable[object] = (),
    key_prefix: str,
    use_container_width: bool = True,
) -> None:
    """「加入对比」按钮 + 它自己的结果说明。

    准入判定在**按下之前**就算好，所以按钮在不可加入时是禁用的，且旁边
    直接写明是哪一种不可加入。等按下去再报错，等于把操作人送进一次注定
    失败的交互——而这正是对比页 ``st.stop()`` 那条路径的翻版。
    """

    admission = admit_to_basket(
        run_id,
        selectable_ids=selectable_ids,
        run_id_alias=run_id_alias,
        all_rows=all_rows,
    )
    basket = current_basket()
    # 「已在篮子里」要在**同一个解析状态**上比。
    #
    # 篮子存的是**加入当时**解析出来的 id；目录归属后来变了，同一次运行的
    # 新解析 id 与旧存的 id 就不是同一个串。直接比 `resolved in basket` 会
    # 判成「不在」⇒ 按钮可用 ⇒ 同一次运行被加进去两次（一个旧 id、一个新
    # id）⇒ 复核时两者坍塌成同一所有者 ⇒ 链接被重复检查挡住，而操作人看到
    # 的是两行不同的 id，无从知道它们是同一次运行。
    #
    # 所以把篮子也按当前目录解析一遍再比。
    resolved_basket = revalidate_basket(
        basket,
        selectable_ids=selectable_ids,
        run_id_alias=run_id_alias,
        all_rows=all_rows,
    ).live
    already = (
        admission.resolved_run_id in resolved_basket
        if admission.admissible else False
    )
    full = len(basket) >= MAX_BASKET_SIZE and not already
    if st.button(
        "＋ 加入对比",
        key=f"{key_prefix}_add_basket_{run_id}",
        use_container_width=use_container_width,
        disabled=not admission.admissible or already or full,
        help="把这次运行攒进对比篮子，攒够 2 个后前往研究运行对比页。",
    ):
        updated, note = add_to_basket(basket, admission)
        _store_basket(updated)
        st.session_state[f"{BASKET_STATE_KEY}_note"] = note
        st.rerun()

    if not admission.admissible:
        st.caption(f"⚠ {admission.reason}")
    elif already:
        st.caption(f"✓ 已在对比篮子里：`{admission.resolved_run_id}`")
    elif full:
        st.caption(
            f"⚠ 对比篮子已满（{MAX_BASKET_SIZE} 个），先在下方移除一个。")
    elif admission.reason:
        # 别名行:加进去的 id 与按钮旁显示的不是同一个,必须先说。
        st.caption(f"· {admission.reason}")


def render_basket_panel(
    *,
    key_prefix: str,
    selectable_ids: Iterable[str],
    run_id_alias: Mapping[str, str],
    all_rows: Iterable[object] = (),
) -> None:
    """篮子本体:成员、移除、清空、前往对比。

    链接**渲染之前**把每个成员对当前目录再核一遍。加入时校验过不等于送出时
    还成立:篮子是会话级的,而在此期间同一产物目录可能被一次更新的运行接管。
    照原样拼进 URL,对比页会把它判成未知并 ``st.stop()`` ——一模一样的拒绝
    页,只是晚了一步发生,而本模块声称要防的正是这件事。

    篮子为空时**什么都不画**——一个常驻的空面板会占掉每页的注意力预算,
    而它此刻没有信息。
    """

    note = st.session_state.pop(f"{BASKET_STATE_KEY}_note", "")
    if note:
        st.caption(note)

    basket = current_basket()
    if not basket:
        return

    checked = revalidate_basket(
        basket,
        selectable_ids=selectable_ids,
        run_id_alias=run_id_alias,
        all_rows=all_rows,
    )
    with st.expander(
        f"对比篮子（{len(basket)}/{MAX_BASKET_SIZE}）", expanded=False,
    ):
        for run_id in basket:
            row_col, drop_col = st.columns([5, 1])
            row_col.markdown(f"`{run_id}`")
            if drop_col.button(
                "移除",
                key=f"{key_prefix}_drop_basket_{run_id}",
                use_container_width=True,
            ):
                _store_basket(remove_from_basket(basket, run_id))
                st.rerun()

        if checked.stale:
            # 不自动踢出:静默丢弃等于替操作人决定「这个不要了」,而他可能
            # 正想知道它去哪了。说清原因,移除交给他。
            #
            # 总标题**不替逐条原因下结论**:失效可以是被接管、被删除、类型
            # 不收、没有产物目录、或 id 带不进 URL——把其中一种（「目录归属
            # 变了」）写成总标题，对另外四种就是假话，而紧跟的逐条说明会与
            # 它直接打架。
            st.warning(
                f"⚠ 篮子里有 {len(checked.stale)} 个运行现在送不到对比页。"
                "各自的原因如下；移除它们才能继续："
            )
            for _stale in checked.stale:
                st.caption(f"· `{_stale.run_id}`：{_stale.reason}")
        if checked.collapsed:
            st.warning(
                "⚠ 以下运行现在与篮子里的另一个指向**同一份当前工件**，"
                "对比页会因重复而拒绝整组："
                + "、".join(f"`{_r}`" for _r in checked.collapsed)
            )
        if checked.rerouted:
            # 加入时当场披露别名是本模块的纪律,复核路径同样要披露:否则篮子
            # 显示 A、链接静默带 B 过去,两个名字都合法,操作人无从发现。
            st.caption(
                "· 以下成员的当前工件已由另一个 ID 持有，对比将以后者进行："
                + "、".join(
                    f"`{_from}` → `{_to}`" for _from, _to in checked.rerouted)
            )

        gap = basket_readiness(checked.live)
        if checked.stale or checked.collapsed:
            st.caption("· 上述问题解决前不提供对比链接——送过去只会被拒。")
        elif gap:
            st.caption(f"⚠ {gap}")
        else:
            st.page_link(
                COMPARISON_PAGE,
                label=f"→ 对比这 {len(checked.live)} 个运行",
                query_params={"run_ids": basket_query_value(checked.live)},
            )
            st.caption(
                "对比页会自己核验实验合同与指标完整性——篮子只负责把这几"
                "个运行带过去，不预判它们可不可比。"
            )
        if st.button(
            "清空篮子",
            key=f"{key_prefix}_clear_basket",
            use_container_width=False,
        ):
            _store_basket(())
            st.rerun()
