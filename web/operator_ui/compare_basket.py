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

from web.operator_ui._param_guard import sanitize
from web.operator_ui.pages._research_run_comparison_helpers import (
    _catalog_dir_key,
    parse_selected_run_ids,
)

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
#: 目录说它可选，但它的 id 过不了对比页 URL 的白名单。
ADMIT_UNROUTABLE_ID = "unroutable_id"


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


def _url_safe_or_refused(
    run_id: str, resolved: str, verdict: str, reason: str,
) -> BasketAdmission:
    """目录说它可选，但 URL 守卫收不收它？

    篮子唯一的交接通道是 ``?run_ids=``，而 ``_param_guard._run_ids`` 只收
    ``[A-Za-z0-9_.-]``。CLI 目录行的 run_id 来自 ``_index.jsonl``，只被**结构**
    校验过——含 ``:`` 或 ``/`` 的 id 结构合法、``selectable_catalog`` 也照收，
    于是按钮显示可用；但一旦拼进 URL，``sanitize`` 会把**整条选择**静默换成空
    默认值。操作人看到的是对比页什么都没选中，而按钮刚刚说这次运行可以加入。

    判据是**完整回环**，不是「过得了 sanitize」：把它单独拼成一次请求、再按
    对比页的方式解析回来，必须原样得到它自己。只问 sanitize 的话，一个含
    **逗号**的 id 会通过——逗号是那个参数的分隔符，`run,a` 被守卫读成两个
    合法 id，对比页于是去找两个并不存在的运行，然后 ``st.stop()``（实测：
    ``sanitize("run_ids", "run,a")`` 原样返回）。

    这里不自己抄那套字符集——抄的那份会与守卫分叉，而分叉的症状正是这条
    意见描述的静默丢弃。
    """

    if parse_selected_run_ids(
            sanitize("run_ids", resolved, default="")) != (resolved,):
        return BasketAdmission(
            run_id, "", ADMIT_UNROUTABLE_ID,
            f"`{resolved}` 的运行 ID 无法原样通过对比页的 URL 参数，"
            "带过去会让整组选择被丢弃或被读成别的运行。",
        )
    return BasketAdmission(run_id, resolved, verdict, reason)


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
        return _url_safe_or_refused(run_id, run_id, ADMIT_OK, "")

    aliased = run_id_alias.get(run_id, "")
    if aliased:
        return _url_safe_or_refused(
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
    # 同一份产物目录，目录里换了个 id 当所有者——这与「被更新的运行接管」
    # 不是一回事，说成后者是**假话**。
    #
    # 真实成因：UI 作业与它的 CLI 目录记录指向同一个 output_dir，而
    # `selectable_catalog` 只在 CLI 行**生产者记录**了这个 UI 作业 id、且
    # 时间线互相包含时才认这层镜像；证明不成立时它让 CLI 行当所有者，于是
    # 来源页交出的 UI id 既不可选、也不是别名键。把那个 id 说出来，操作人
    # 才知道下一步该找谁。
    owner = _same_directory_owner(run_id, selectable_ids, all_rows)
    if owner:
        return BasketAdmission(
            run_id, "", ADMIT_SUPERSEDED,
            f"该产物目录在对比页的目录里由 `{owner}` 代表（同一份产物的另一条"
            f"记录），`{run_id}` 本身不可直接对比。",
        )
    # 走到这里说明：这条记录在目录输入里、类型可比、有产物目录，却既不可选、
    # 也不是别名键，而**同目录上也找不到任何可选的替代记录**。
    #
    # 按 `selectable_catalog` 的构造这一格不该出现：它给每个 (类型, 目录)
    # 恰好选一个所有者，所以「我不是所有者」蕴含「同目录上另有一个所有者」，
    # 而那个所有者就在同一份 `all_rows` 里。真到了这里，说明输入本身不自洽
    # （例如调用方传了一份被过滤过的 `all_rows`，或两侧的目录键推导不一致）。
    #
    # 所以这里**不猜原因**。说成「被更新的运行接管」是编一个我们并不知道的
    # 因果——而上面那条分支正是为了不编它才加的。
    return BasketAdmission(
        run_id, "", ADMIT_UNKNOWN,
        f"`{run_id}` 不在对比页的可选目录中，且同目录上找不到代表它的记录——"
        "目录数据不自洽，无法判断原因。",
    )


def _same_directory_owner(
    run_id: str, selectable_ids: Iterable[str], all_rows: Iterable[object],
) -> str:
    """同一份产物目录上，对比页目录**实际**认的那个 id（没有就空串）。

    目录键的推导直接复用对比页那一份（``_catalog_dir_key``）——在这里另写
    一套 normcase/锚定规则，就是第二份会漂的推导，而漂的那一份会把两条本
    该配对的记录说成互不相干。
    """

    rows = list(all_rows)
    mine = next((r for r in rows if getattr(r, "run_id", "") == run_id), None)
    if mine is None or not str(getattr(mine, "run_dir", "")):
        return ""
    mine_key = _catalog_dir_key(str(getattr(mine, "run_dir", "")))
    selectable = set(selectable_ids)
    for row in rows:
        other_id = str(getattr(row, "run_id", ""))
        other_dir = str(getattr(row, "run_dir", ""))
        if other_id == run_id or other_id not in selectable or not other_dir:
            continue
        if _catalog_dir_key(other_dir) == mine_key:
            return other_id
    return ""


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

    只做拼接:去重与上界由 ``add_to_basket`` 与 ``revalidate_basket`` 保证,
    在这里再筛一次等于把同一条规则写两份,两份会分叉。
    """
    return ",".join(basket)


@dataclass(frozen=True)
class BasketRevalidation:
    """篮子相对**当前**目录的复核结果。"""

    #: 可以送出去的成员,按当前目录解析后、保序去重。
    live: tuple[str, ...]
    #: 送不出去的成员及其原因。留在篮子里等操作人移除——静默丢弃等于替他
    #: 决定「这个不要了」,而他可能正想知道它去哪了。
    stale: tuple[BasketAdmission, ...]
    #: 加入时是两个不同的运行,如今解析到同一个所有者。对比页会因重复而
    #: 整页停下,所以这里就要说出来。
    collapsed: tuple[str, ...]
    #: 加入之后**改了名**的成员:``(篮子里存的 id, 现在会送出去的 id)``。
    #: 加入时当场披露别名是本模块的既有纪律,而复核路径此前没有——于是篮子
    #: 显示 A、链接静默带 B 过去,两个名字都合法,操作人无从发现。同一条
    #: 纪律要覆盖**两条**路径。
    rerouted: tuple[tuple[str, str], ...] = ()


def revalidate_basket(
    basket: Sequence[str],
    *,
    selectable_ids: Iterable[str],
    run_id_alias: Mapping[str, str],
    all_rows: Iterable[object] = (),
) -> BasketRevalidation:
    """把篮子重新对**当前**目录核一遍。

    加入时校验过不等于送出时还成立:篮子是会话级的,而在此期间同一产物目录
    可能被一次更新的运行接管——那个成员就不再是目录的当前所有者。照原样拼
    进 URL,对比页会把它判成未知并 ``st.stop()``:一模一样的拒绝页,只是晚了
    一步发生。本 change 声称要防的正是这件事,所以链接**渲染之前**必须再核
    一次,而不是只在加入时核。

    两个成员后来解析到同一个所有者也一样致命(对比页有重复检查)。这在加入
    时看不出来:那时它们确实是两个不同的可选运行。
    """

    # 先物化一次再进循环。签名收的是 ``Iterable``，而下面每个成员都要把它
    # **再交给** ``admit_to_basket`` 消费一遍——传进来一个一次性迭代器时，
    # 第一个成员就把它抽干，之后每个成员都看到空目录、被判成「已被接管」。
    # 类型标注反而给这个不成立的契约背了书：mypy 不会报，而 list 字面量的
    # 测试永远走不到那条路径。
    selectable = tuple(selectable_ids)
    rows = tuple(all_rows)
    seen: set[str] = set()
    live: list[str] = []
    stale: list[BasketAdmission] = []
    collapsed: list[str] = []
    rerouted: list[tuple[str, str]] = []
    for run_id in basket:
        admission = admit_to_basket(
            run_id,
            selectable_ids=selectable,
            run_id_alias=run_id_alias,
            all_rows=rows,
        )
        if not admission.admissible:
            stale.append(admission)
            continue
        resolved = admission.resolved_run_id
        if resolved != run_id:
            # 加入时当场披露别名是本模块的纪律。复核路径不披露的话,篮子
            # 显示 A、链接静默带 B 过去——两个名字都合法,操作人无从发现。
            rerouted.append((run_id, resolved))
        if resolved in seen:
            collapsed.append(run_id)
            continue
        seen.add(resolved)
        live.append(resolved)
    return BasketRevalidation(
        live=tuple(live), stale=tuple(stale), collapsed=tuple(collapsed),
        rerouted=tuple(rerouted))


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
