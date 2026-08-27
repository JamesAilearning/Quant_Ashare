"""把「我想比这几次运行」从三个来源页带到研究运行对比页。

## 为什么需要一层准入判定

对比页不接受任意 run_id。它的可选目录来自
``selectable_catalog``：每个 ``(type, 产物目录)`` 只留**一个当前所有者**，
其余同目录的行要么经 ``run_id_alias`` 折进所有者，要么根本不可寻址。一个
URL 里的未知 id 会让对比页 ``st.error`` + ``st.stop()`` ——整页停在拒绝
信息上。

所以来源页在把 run_id 交出去之前必须自己判一次，并且在不可交时**说清是
哪一种不可交**（类型不支持 / 没有产物目录 / 被同目录的更新运行取代）。
把人送进拒绝页，或者只说一句「不可用」，都不算如实。

## 边界

本模块只回答「这个 id 送过去会不会被拒」与「篮子现在是什么状态」。它
**不**判可比性——实验合同是否一致、指标是否完整，是对比页
``assess_comparability`` 的事，在这里重推一遍就是第二份会漂移的推导。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

#: 篮子在 session_state 里的键。
BASKET_STATE_KEY = "research_compare_basket"

#: 篮子上界。与 ``_param_guard._run_ids`` 的 1-5 白名单、以及对比页
#: ``max_selections=5`` 对齐——三处任何一处放宽都要一起改,否则操作人能
#: 攒到第 6 个,然后在跳转时撞上 URL 守卫的静默拒绝。
MAX_BASKET_SIZE = 5

#: 对比页要求至少 2 个（``if not 2 <= len(selected_ids) <= 5``）。
MIN_COMPARE_SIZE = 2

#: 对比页可收的运行类型（``selectable_catalog`` 的 ``allowed_types``）。
COMPARABLE_TYPES = frozenset({"pipeline", "walk_forward"})

ADMIT_OK = "ok"
ADMIT_ALIASED = "aliased"
ADMIT_WRONG_TYPE = "wrong_type"
ADMIT_NO_ARTIFACTS = "no_artifacts"
ADMIT_SUPERSEDED = "superseded"
ADMIT_UNKNOWN = "unknown"


@dataclass(frozen=True)
class BasketAdmission:
    """一个 run_id 能否进对比篮子,以及**具体**为什么不能。"""

    run_id: str
    #: 对比页最终会用的 id。别名行会解析成它的所有者——提示要说出这一点,
    #: 否则操作人加进去的是 A、对比页显示的是 B,而无处解释。
    resolved_run_id: str
    verdict: str
    #: 面向操作人的一句话。可入篮时为空。
    reason: str

    @property
    def admissible(self) -> bool:
        return self.verdict in (ADMIT_OK, ADMIT_ALIASED)


def admit_to_basket(
    run_id: str,
    *,
    selectable_ids: Iterable[str],
    run_id_alias: Mapping[str, str],
    all_rows: Iterable[object] = (),
) -> BasketAdmission:
    """判断 ``run_id`` 交给对比页会不会被拒,并给出**分因**。

    ``selectable_ids`` / ``run_id_alias`` 来自
    ``selectable_catalog(load_all_jobs_read_only())``——传的是它的产出,不是
    自己重算一遍目录归属:那套所有权规则（生产者记录的 UI/CLI 关系、时间
    只作生命周期佐证）在这里重推必然漂移。

    ``all_rows`` 是原始目录行（``JobSummary``,只读 ``run_id`` / ``type`` /
    ``run_dir``）。它只用来把「不可寻址」拆成操作人能行动的几类:类型不
    支持、没有产物目录、被同目录的更新运行取代、目录里根本没有。
    """

    run_id = str(run_id or "")
    if not run_id:
        return BasketAdmission(
            "", "", ADMIT_UNKNOWN, "该运行没有可用的运行 ID，无法加入对比。")

    if run_id in set(selectable_ids):
        return BasketAdmission(run_id, run_id, ADMIT_OK, "")

    aliased = run_id_alias.get(run_id, "")
    if aliased:
        return BasketAdmission(
            run_id, aliased, ADMIT_ALIASED,
            f"该运行的当前工件由 `{aliased}` 持有，将以后者加入对比。",
        )

    row = next(
        (r for r in all_rows if getattr(r, "run_id", "") == run_id), None)
    if row is None:
        return BasketAdmission(
            run_id, "", ADMIT_UNKNOWN,
            f"`{run_id}` 不在统一作业目录中，对比页无法定位它的工件。",
        )
    row_type = str(getattr(row, "type", ""))
    if row_type not in COMPARABLE_TYPES:
        return BasketAdmission(
            run_id, "", ADMIT_WRONG_TYPE,
            f"对比页只收 pipeline 与 walk_forward 运行，这次是 `{row_type}`。",
        )
    if not str(getattr(row, "run_dir", "")):
        return BasketAdmission(
            run_id, "", ADMIT_NO_ARTIFACTS,
            f"`{run_id}` 没有记录产物目录，没有可对比的工件。",
        )
    return BasketAdmission(
        run_id, "", ADMIT_SUPERSEDED,
        f"`{run_id}` 的产物目录已被同目录的更新运行接管，它不再是该目录的"
        "当前所有者，因此不可直接对比。",
    )


def add_to_basket(
    basket: Sequence[str], admission: BasketAdmission,
) -> tuple[tuple[str, ...], str]:
    """把一次准入结果并进篮子。返回 ``(新篮子, 面向操作人的一句话)``。

    篮子存**解析后**的 id:对比页收到的就是它,篮子里显示的也该是它。存
    原 id 会让「篮子里 3 个」和「对比页选中 2 个」对不上,而无处解释。
    """

    current = tuple(basket)
    if not admission.admissible:
        return current, admission.reason
    resolved = admission.resolved_run_id
    if resolved in current:
        return current, f"`{resolved}` 已经在对比篮子里了。"
    if len(current) >= MAX_BASKET_SIZE:
        return current, (
            f"对比篮子最多 {MAX_BASKET_SIZE} 个运行（对比页的上界），"
            "先移除一个再加。"
        )
    added = (*current, resolved)
    note = admission.reason or f"已加入对比篮子：`{resolved}`。"
    return added, note


def remove_from_basket(
    basket: Sequence[str], run_id: str,
) -> tuple[str, ...]:
    return tuple(item for item in basket if item != run_id)


def basket_query_value(basket: Sequence[str]) -> str:
    """篮子的 URL 值。空篮子给空串（对比页把空串当作「没有请求」）。

    只做拼接:去重与上界由 ``add_to_basket`` 保证,在这里再筛一次等于把
    同一条规则写两份,两份会分叉。
    """
    return ",".join(basket)


def basket_readiness(basket: Sequence[str]) -> str:
    """篮子还差什么才能对比。可以对比时返回空串。"""
    count = len(basket)
    if count == 0:
        return "对比篮子是空的。"
    if count < MIN_COMPARE_SIZE:
        return (
            f"对比篮子里有 {count} 个运行，对比页至少需要 "
            f"{MIN_COMPARE_SIZE} 个。"
        )
    return ""
