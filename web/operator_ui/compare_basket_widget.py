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

import streamlit as st

from web.operator_ui.compare_basket import (
    BASKET_STATE_KEY,
    MAX_BASKET_SIZE,
    add_to_basket,
    admit_to_basket,
    basket_query_value,
    basket_readiness,
    remove_from_basket,
)

#: 对比页在 ``st.page_link`` 里的相对路径（app.py 用同一个文件名注册）。
COMPARISON_PAGE = "pages/research_run_comparison.py"


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
    already = admission.resolved_run_id in basket if admission.admissible else False
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


def render_basket_panel(*, key_prefix: str) -> None:
    """篮子本体:成员、移除、清空、前往对比。

    篮子为空时**什么都不画**——一个常驻的空面板会占掉每页的注意力预算,
    而它此刻没有信息。
    """

    note = st.session_state.pop(f"{BASKET_STATE_KEY}_note", "")
    if note:
        st.caption(note)

    basket = current_basket()
    if not basket:
        return

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

        gap = basket_readiness(basket)
        if gap:
            st.caption(f"⚠ {gap}")
        else:
            st.page_link(
                COMPARISON_PAGE,
                label=f"→ 对比这 {len(basket)} 个运行",
                query_params={"run_ids": basket_query_value(basket)},
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
