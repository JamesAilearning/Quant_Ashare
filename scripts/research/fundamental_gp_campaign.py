"""Fundamental GP campaign orchestration — the injection layer.

The miner and the promotion CLI live in ``src/factor_mining/`` and must
not import ``src.research.*`` (research-isolation gate); the fundamental
panel bridge lives in ``src/research/`` and is the ONLY sanctioned
producer of report-period-provenanced panels. This script is the one
layer that sees both sides, so it owns the seam: it builds the canonical
panel factory and injects it into ``run_mining`` / ``promote_run``.

Subcommands::

    python -m scripts.research.fundamental_gp_campaign mine \
        --config config/factor_mining/<campaign>.yaml

    python -m scripts.research.fundamental_gp_campaign starter-check \
        --run <run_dir>

    python -m scripts.research.fundamental_gp_campaign record-baseline \
        --run <run_dir> --end-date <validation_end> --out <baseline.json>

    python -m scripts.research.fundamental_gp_campaign promote \
        --run <run_dir> --to <version> [--config <yaml>] \
        [--baseline <baseline.json>] [--dry-run]

``record-baseline`` is the "independent trusted process" of the
extension-baseline contract: it rebuilds the EFFECTIVE (extended) window
panel through the canonical bridge, digests the full output (values,
evidence, periods) with the miner's own trusted digest, and writes the
expected digest to disk BEFORE any promotion runs. Promotion then
requires the injected factory to reproduce that digest bit-for-bit on
the extension window — a baseline the promoted callable cannot issue to
itself.

The starter-three-factor link check is a TWO-step sequence: ``mine``
proves the GP path (panelization, merged terminals, provenance-masked
evaluation inside the search), then ``starter-check`` proves the three
frozen factors themselves — the GP's random population cannot construct
C3, so its deterministic evaluation is a separate, run-bound step.
Running ``mine`` alone is NOT a completed link check. Igniting on real
data is an operator action, not a CI one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields as dc_fields
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.data.trading_calendar import StaticTradingCalendar
from src.factor_mining.factor_pool import FactorPool
from src.factor_mining.fitness import FitnessConfig
from src.factor_mining.gp_engine import GPConfig
from src.factor_mining.grammar import V1_OPERATORS, FeatureRegistry
from src.factor_mining.miner import (
    DataConfig,
    MinerConfig,
    build_panel_for_data,
    data_definition_sha256,
    load_config,
    run_mining,
    search_definition_sha256,
)
from src.factor_mining.panel_digest import fundamental_output_sha256
from src.factor_mining.promote import (
    PromotionError,
    _load_config,
    _load_run_data_config,
    _verify_pit_binding,
    promote_run,
)
from src.research.financial_pit_view import FinancialPITDataView
from src.research.fundamental_panel import build_fundamental_panel

_log = logging.getLogger(__name__)


def _load_calendar(path: str) -> StaticTradingCalendar:
    """Trading calendar from a one-ISO-date-per-line file (qlib day.txt)."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    dates = [date.fromisoformat(ln.strip()[:10]) for ln in lines if ln.strip()]
    if not dates:
        raise ValueError(f"calendar file {path} contains no dates.")
    return StaticTradingCalendar(dates)


PanelTriple = tuple[
    dict[str, pd.DataFrame], dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
]
PanelFactory = Callable[
    [DataConfig, pd.DatetimeIndex, Sequence[str]], PanelTriple]


def build_panel_factory() -> PanelFactory:
    """The canonical fundamental panel factory for the injection seam.

    Consumes ONLY the run-persisted ``DataConfig`` plus the geometry the
    seam owner hands it — no ambient state — so promotion can re-invoke
    it on the recorded inputs and compare behavior. Returns the
    documented flat triple ``(values, evidence, periods)`` including the
    ``__prior`` generation.
    """

    def factory(
        data: DataConfig, trade_dates: pd.DatetimeIndex,
        instruments: Sequence[str],
    ) -> PanelTriple:
        view = FinancialPITDataView(
            Path(data.fundamental_store_root),
            _load_calendar(data.fundamental_calendar_path),
            financial_issuers=data.financial_exclusions,
        )
        # The exclusion cross-check REPORTS disagreements, never resolves
        # them (spec: v2-financial-pit-contract): a financial-listed
        # issuer that does report oper_cost, or a non-excluded issuer
        # that never does, each gets a visible line. Runs on every
        # factory invocation (mining and promotion alike) so the signed
        # list is re-examined against the store the run actually reads.
        disagreements = view.cross_check_exclusion(list(instruments))
        if disagreements:
            _log.warning(
                "financial-exclusion cross-check: %d disagreement(s) "
                "between the signed industry list and oper_cost "
                "reporting behavior:", len(disagreements))
            for d in disagreements:
                _log.warning("  %s: %s", d.ts_code, d.kind)
        panel = build_fundamental_panel(
            view,
            list(data.fundamental_fields),
            list(trade_dates),
            list(instruments),
            include_prior_period=True,
        )
        values, evidence, periods = panel.flatten()
        # The bridge's columns are the union of SERVED names — the
        # financial issuers the view excludes have no column at all. The
        # seam contract demands the exact requested geometry, so the
        # excluded names come back as all-NA columns here; their cells
        # are simultaneously removed from the coverage DENOMINATOR by
        # the same persisted exclusion set (miner's universe mask), so
        # an all-NA column never counts against any candidate.
        dates = pd.DatetimeIndex(trade_dates)
        cols = list(instruments)

        def _on_geometry(mapping: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
            return {k: f.reindex(index=dates, columns=cols)
                    for k, f in mapping.items()}

        return _on_geometry(values), _on_geometry(evidence), _on_geometry(
            periods)

    return factory


# The starter three factors as DETERMINISTIC ASTs (frozen source:
# docs/prereg/quality_profitability.yaml + the asset-growth Δ form).
# The link check cannot rely on the GP's random population to construct
# them — C3 alone needs four income terms, five current/prior deltas and
# the coalesce pair, far beyond any small-run depth — so the OpenSpec
# "starter three-factor end-to-end" obligation is discharged by
# evaluating these expressions EXPLICITLY through the canonical
# evaluator (values, forward returns, report-period provenance and the
# terminal-level alignment mask all on the same path the GP uses).
# Δx = sub(x, x__prior); the coalesce pair merges per period BEFORE
# differencing, exactly as the frozen formula requires.
_STARTER_EXPRESSIONS: dict[str, str] = {
    "C1_GPA": (
        "cs_rank(div_safe(sub($revenue, $oper_cost), $total_assets))"
    ),
    "asset_growth": (
        "cs_rank(div_safe(sub($total_assets, $total_assets__prior), "
        "$total_assets__prior))"
    ),
    "C3_cash_based_OP": (
        "cs_rank(div_safe("
        "add(add(sub(sub(sub("
        "sub(sub(sub($revenue, $oper_cost), $sell_exp), $admin_exp), "
        "sub($accounts_receiv, $accounts_receiv__prior)), "
        "sub($inventories, $inventories__prior)), "
        "sub($prepayment, $prepayment__prior)), "
        "sub($accounts_pay, $accounts_pay__prior)), "
        "sub(coalesce($adv_receipts, $contract_liab), "
        "coalesce($adv_receipts__prior, $contract_liab__prior))), "
        "$total_assets))"
    ),
}


# ---------------------------------------------------------------------------
# 冻结协议的运行时消费（docs/prereg/fundamental_gp_v1.yaml）
# ---------------------------------------------------------------------------
# 协议自称"被代码读取的机器件"。在此之前全仓唯一的读者是治理测试 ——
# 即一个零运行时校验的断言（codex #446 自审 P1）。本段把它做成真的：
# 战役的唯一入口（本脚本；裸 CLI 因缺注入工厂本就拒跑）在子命令启动时
# 加载协议并 fail-loud 校验。与 pv_incremental_eval 的 load_frozen_plan /
# check_window_discipline 同款结构，不另起一套语义。

FROZEN_PLAN_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "prereg" / "fundamental_gp_v1.yaml"
)
PROTOCOL_ID = "fundamental_gp_v1"

# GPConfig 上由协议**别处**冻结的字段（terminals / operators 两段），
# 不该要求它们再出现在 search 段里。
_GP_FROZEN_ELSEWHERE = frozenset({"allowed_operators", "allowed_terminals"})


class ProtocolViolation(RuntimeError):
    """配置或调用偏离了冻结协议。"""


def load_frozen_plan(path: Path | None = None) -> dict[str, Any]:
    """加载冻结协议并校验它自身可用。

    校验的是"这份协议还是我们签的那份、且 holdout 仍是盲的"；配置的
    对账在 :func:`assert_mining_config_matches_protocol`。
    """
    plan_path = path or FROZEN_PLAN_PATH
    if not plan_path.exists():
        raise ProtocolViolation(
            f"frozen protocol not found at {plan_path} — the campaign "
            "refuses to run unprotocolled.")
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ProtocolViolation(
            f"frozen protocol at {plan_path} did not parse to a mapping.")
    if plan.get("protocol_id") != PROTOCOL_ID:
        raise ProtocolViolation(
            f"protocol_id {plan.get('protocol_id')!r} != {PROTOCOL_ID!r} — "
            "wrong protocol file.")
    missing = [k for k in ("universe", "terminals", "windows", "fitness",
                           "search", "adjudication", "holdout_unblinded")
               if k not in plan]
    if missing:
        raise ProtocolViolation(
            f"frozen protocol is missing required section(s) {missing}.")
    if plan["holdout_unblinded"] is not False:
        raise ProtocolViolation(
            "holdout_unblinded is not False — no campaign step may run "
            "against a blind protocol that is no longer blind.")
    return plan


def _deviations_from_defaults(obj: Any, blank: Any) -> dict[str, Any]:
    """取值偏离 dataclass 缺省的字段。

    穷尽式冻结的实现：不是"协议点名的键要一致"（单向 —— preset 加一个
    协议没点名的键就能溜过去），而是"任何偏离缺省的字段都必须被协议
    点名"。新增 dataclass 字段只要用缺省就不触发；一旦 preset 动它，
    就必须先进协议。
    """
    return {
        f.name: getattr(obj, f.name)
        for f in dc_fields(obj)
        if getattr(obj, f.name) != getattr(blank, f.name)
    }


def _assert_exhaustively_frozen(
    section: str,
    obj: Any,
    blank: Any,
    frozen: dict[str, Any],
    *,
    also_frozen_elsewhere: frozenset[str],
) -> None:
    """穷尽式冻结，两个方向都查。

    方向一（协议 → 配置）：协议点名的每个字段，比的是**生效值**
    ``getattr(obj, key)``，不是"偏离缺省的字段表"。早先的实现在这里
    写了 ``if key not in deviations: continue`` —— 于是冻结值恰好等于
    dataclass 缺省的字段一次都不被比较，而最利的一格是 seed：缺省 42、
    冻结 20260818，preset 写 ``seed: 42`` 或把该行删掉，seed 就不进
    deviations、比对被跳过，一次完全不同的搜索照跑并自称受协议保护
    （codex #446 自审 r2 P1）。

    方向二（配置 → 协议）：任何偏离 dataclass 缺省却没被协议点名的
    字段一律拒 —— 这一半挡的是 preset 单方面加键（如 ``ic_term``）。
    """
    for key, want in frozen.items():
        if not hasattr(obj, key):
            raise ProtocolViolation(
                f"{section}.{key} is frozen by the protocol but "
                f"{type(obj).__name__} has no such field — the protocol "
                "and the code have diverged.")
        got = getattr(obj, key)
        if got != want:
            raise ProtocolViolation(
                f"{section}.{key} = {got!r} but the frozen protocol says "
                f"{want!r}.")
    deviations = _deviations_from_defaults(obj, blank)
    unfrozen = (set(deviations) - set(frozen)) - also_frozen_elsewhere
    if unfrozen:
        shown = {k: deviations[k] for k in sorted(unfrozen)}
        raise ProtocolViolation(
            f"{section}: field(s) {sorted(unfrozen)} deviate from the "
            f"dataclass defaults ({shown}) but are not named by the frozen "
            "protocol — a signed protocol whose effective values fall "
            "outside its frozen scope is not a freeze; add them to the "
            "protocol or drop them from the preset.")


def assert_mining_config_matches_protocol(
    config: MinerConfig, plan: dict[str, Any],
) -> None:
    """挖矿配置逐字段对冻结协议。

    这是 ``--config`` 指向哪份文件不再自由的地方：治理钉只能保证仓库里
    那份 preset 与协议一致，保证不了这次 run 用的就是它。
    """
    windows = plan["windows"]
    if (config.data.start_date, config.data.end_date) != (
            windows["is_start"], windows["is_end"]):
        raise ProtocolViolation(
            f"mining window {config.data.start_date}..{config.data.end_date}"
            f" != the frozen IS window {windows['is_start']}.."
            f"{windows['is_end']} — GP 的唯一可见窗就是 IS。")
    universe = plan["universe"]
    if config.data.universe_name != universe["instruments"]:
        raise ProtocolViolation(
            f"universe {config.data.universe_name!r} != frozen "
            f"{universe['instruments']!r}.")
    # 协议里排除集以**计数 + 摘要**冻结（名单本身在 preset 与
    # fundamental_link_check.yaml 里，摘要是两者的共同绑定）。
    got = sorted(config.data.financial_exclusions)
    if len(got) != universe["ex_financials_count"]:
        raise ProtocolViolation(
            f"financial_exclusions has {len(got)} names, frozen protocol "
            f"says {universe['ex_financials_count']}.")
    digest = hashlib.sha256(chr(10).join(got).encode("utf-8")).hexdigest()
    if digest != universe["ex_financials_sha256"]:
        raise ProtocolViolation(
            f"financial_exclusions sha256 {digest} != frozen "
            f"{universe['ex_financials_sha256']} — 名单被改过。")
    metric = plan["metric"]
    if config.data.forward_return_price != metric["forward_return_price"]:
        raise ProtocolViolation(
            f"forward_return_price {config.data.forward_return_price!r} != "
            f"frozen {metric['forward_return_price']!r} — 注意 DataConfig "
            "的缺省是 'open'，所以把这一行从 preset 删掉就会静默换标签。")
    if config.data.forward_horizon != metric["forward_horizon"]:
        raise ProtocolViolation(
            f"forward_horizon {config.data.forward_horizon} != frozen "
            f"{metric['forward_horizon']} — 繁殖目标与裁决目标必须是同一"
            "个（pv 战役 PV-DP-3 的教训），换标签等于换实验。")
    if config.data.mode != "pit":
        raise ProtocolViolation(
            f"data.mode is {config.data.mode!r} — 本役冻结在 PIT 数据上；"
            "synthetic 面板不产生任何可裁决的证据。")
    terminals = plan["terminals"]
    if sorted(config.data.fundamental_fields) != sorted(
            terminals["charter_fields"]):
        raise ProtocolViolation(
            "fundamental_fields differs from the frozen charter fields.")
    # 算子集 = 冻结基线（operators_baseline_28 为 true 时即 V1_OPERATORS，
    # **不是**注册表 —— 注册表增长不得拓宽任何已签署的搜索）+ 修订入册项。
    if plan["operators_baseline_28"] is not True:
        raise ProtocolViolation(
            "operators_baseline_28 is not true — the frozen operator "
            "baseline no longer applies; refusing to guess the set.")
    want_ops = sorted(V1_OPERATORS) + sorted(plan["operators_amended"])
    if sorted(config.gp.allowed_operators) != sorted(want_ops):
        raise ProtocolViolation(
            "allowed_operators differs from the frozen operator set "
            f"({len(config.gp.allowed_operators)} vs {len(want_ops)}).")
    # 终端比**内容**不比个数：34 个里换 12 个成量价，个数仍是 34。
    # 而本役论点正是"纯财报终端能否产出低换手因子"，混入量价会让 F_B
    # 的换手判据失去鉴别力（codex #446 自审 r2 P1）。
    want_terminals = set()
    for field in terminals["charter_fields"]:
        want_terminals.add(f"${field}")
        if terminals["prior_generation"]:
            want_terminals.add(f"${field}{FeatureRegistry.PRIOR_SUFFIX}")
    if len(want_terminals) != terminals["n_terminals"]:
        raise ProtocolViolation(
            f"protocol self-inconsistent: {len(terminals['charter_fields'])} "
            f"charter fields generate {len(want_terminals)} terminals but "
            f"n_terminals says {terminals['n_terminals']}.")
    got_terminals = set(config.gp.allowed_terminals)
    if got_terminals != want_terminals:
        extra = sorted(got_terminals - want_terminals)
        missing = sorted(want_terminals - got_terminals)
        raise ProtocolViolation(
            f"allowed_terminals differs from the frozen terminal set — "
            f"extra {extra[:6]}, missing {missing[:6]}.")
    _assert_exhaustively_frozen(
        "fitness", config.fitness, FitnessConfig(),
        {k: v for k, v in plan["fitness"].items()
         if k != "complexity_bias_acknowledged"},
        also_frozen_elsewhere=frozenset())
    search = dict(plan["search"])
    pool_top_k = search.pop("pool_top_k")
    _assert_exhaustively_frozen(
        "search", config.gp, GPConfig(), search,
        also_frozen_elsewhere=_GP_FROZEN_ELSEWHERE)
    if config.pool_top_k != pool_top_k:
        raise ProtocolViolation(
            f"pool_top_k {config.pool_top_k!r} != frozen {pool_top_k!r} — "
            "it truncates the pool BEFORE persistence, so it sets the size "
            "of the FWER family the F_A threshold is built from.")


def assert_window_discipline(end_date: str, plan: dict[str, Any]) -> None:
    """任何把面板窗口拉进 holdout / forbidden 段的调用一律拒。

    ``promote._check_pit_window`` 只有下界没有上界，所以一条合法 CLI 就
    能把盲态 holdout 年、以及与生产重叠的 forbidden 段拉进验证与相关性
    过滤。
    """
    windows = plan["windows"]
    end = date.fromisoformat(str(end_date))
    forbidden_from = date.fromisoformat(str(windows["forbidden_from"]))
    if end >= forbidden_from:
        raise ProtocolViolation(
            f"validation end {end} reaches the forbidden window "
            f"(>= {forbidden_from}) — 挖掘全程禁用。")
    holdout_start = date(int(windows["holdout_year"]), 1, 1)
    if end >= holdout_start:
        raise ProtocolViolation(
            f"validation end {end} reaches the blind holdout year "
            f"{windows['holdout_year']} — 单向揭盲仅限晋升终裁，且须先把 "
            "holdout_unblinded 翻成 true 并记账。")


def _minerconfig_from_snapshot(raw_snapshot: Mapping[str, Any]) -> MinerConfig:
    """从 run 快照重建 MinerConfig。

    快照的 data 段已由 ``data_definition_sha256`` 摘要核验，所以"逐字段
    等于签署的协议"是可验证的绑定 —— 强于任何写进 yaml 的自述字段
    （miner 的 ``config_dump`` 是写死的键表，根本不写 protocol_id）。
    """
    return MinerConfig(
        data=DataConfig(**raw_snapshot["data"]),
        gp=GPConfig(**raw_snapshot.get("gp", {})),
        fitness=FitnessConfig(**raw_snapshot.get("fitness", {})),
        output_dir=Path(raw_snapshot.get("output_dir", ".")),
        run_id=raw_snapshot.get("run_id"),
        pool_top_k=raw_snapshot.get("pool_top_k"),
    )


def _verify_pool_truncation(
    run_dir: Path, raw: Mapping[str, Any], cfg: MinerConfig,
) -> None:
    """用**物理证据**核 ``pool_top_k``。

    它是快照里唯一没有摘要背书的语义字段：``search_definition_sha256``
    只覆盖 gp + fitness，``data_definition_sha256`` 只覆盖 data，而
    ``pool_top_k`` 挂在 MinerConfig 上。于是把一个用别的截断规则挖出的
    run 的 ``pool_top_k`` 改成冻结值，两个摘要都不会响，随后的值比对比
    的又正是那个改过的值 —— 一个只存了 50 个候选的池就能冒充"最多 200
    的冻结族"，而族的大小正是 gate_F_A 自举 max-t 门槛的分母
    （codex #446 r15 P1）。

    把 ``pool_top_k`` 塞进某个既有摘要会改变所有存量 run 的摘要值，是
    比缺口更贵的动作。改用池文件本身作证：实际条数必须等于记录的
    ``saved_pool_size``，而后者必须等于 ``min(截断前条数, pool_top_k)``。
    截断没生效时（全池比 K 小）这条恒成立且无害 —— 那种情形下 K 本就
    不影响族的大小。
    """
    saved = raw.get("saved_pool_size")
    full = raw.get("full_pool_size_pre_truncation")
    if saved is None or full is None:
        raise ProtocolViolation(
            "run snapshot records no saved_pool_size / "
            "full_pool_size_pre_truncation — the truncation that fixes the "
            "FWER family size cannot be proven.")
    actual = len(FactorPool.load(run_dir).all_entries())
    if actual != saved:
        raise ProtocolViolation(
            f"persisted pool holds {actual} entries but the snapshot records "
            f"saved_pool_size={saved} — the snapshot does not describe this "
            "pool.")
    expected = min(int(full), cfg.pool_top_k) if cfg.pool_top_k else int(full)
    if saved != expected:
        raise ProtocolViolation(
            f"saved_pool_size {saved} != min(pre-truncation {full}, "
            f"pool_top_k {cfg.pool_top_k}) = {expected} — the recorded "
            "pool_top_k is not the rule this pool was truncated under; "
            "它既不在 search 摘要也不在 data 摘要里，只能由池文件作证。")


def run_protocol_binding(
    run_dir: Path, plan: dict[str, Any],
) -> dict[str, Any]:
    """这个 run 当初是不是在协议下挖的 —— 机器可读的判定，不抛。

    **所有**基于 ``--run`` 的子命令都经这里，而不是各自写一遍：前三轮
    codex 连着挑的都是同一类"守卫只装了部分子命令"（score_expression
    漏了、promote 的窗口纪律漏了、现在是 run 绑定漏了 record-baseline
    与 promote）。补第 N 个点治不了，收敛成一个入口才行。
    """
    try:
        raw = yaml.safe_load(
            (run_dir / "config.yaml").read_text(encoding="utf-8"))
        cfg = _minerconfig_from_snapshot(raw)
        # **摘要先行**：先证明快照自挖矿以来没被改过，再谈它是否等于
        # 协议。反过来（先比值、不比摘要）等于把绑定建在一段可编辑的
        # 文本上 —— 把一个外来 run 的 config.yaml 改成冻结值，它的池
        # 明明是在别的搜索下育出来的，却能通过绑定走到晋升
        # （codex #446 r14 P1）。这与本包对工厂身份立的规矩是同一条：
        # 身份取自防篡改内容，绝不取自自述字段。
        #
        # data 半边复用 promote._load_run_data_config（仓库既有的
        # "记录值 vs 重算，不符即改过"件，含 schema 演进的逃生口），
        # 不另起一份实现。
        _load_run_data_config(run_dir)
        recorded_search = raw.get("search_definition_sha256")
        if not recorded_search:
            raise ProtocolViolation(
                "run snapshot records no search_definition_sha256 — the "
                "gp/fitness sections cannot be proven unedited.")
        recomputed_search = search_definition_sha256(cfg.gp, cfg.fitness)
        if recorded_search != recomputed_search:
            # 措辞对冲，与 promote 对 data 摘要的既有先例一致：
            # `search_definition_sha256` 用 `asdict(gp)` 全量序列化，所以
            # **GPConfig 新增字段**（本 PR 的 allowed_terminals 即是）会让
            # 跨版本重算必然不同。早于本 PR 的 run 因此都会落到这里 ——
            # 拒绝是对的（它们本就不合本协议），但断言"被篡改"是错的
            # 诊断，会把操作人指向错误方向。
            raise ProtocolViolation(
                f"gp/fitness sections do not match the digest recorded at "
                f"mining time (recorded {recorded_search!r}, recomputed "
                f"{recomputed_search!r}) — either the snapshot was edited "
                "after mining, or the run predates the current GPConfig "
                "schema. Either way it is not a run of this protocol.")
        _verify_pool_truncation(run_dir, raw, cfg)
        assert_mining_config_matches_protocol(cfg, plan)
    except (ProtocolViolation, PromotionError, TypeError, KeyError,
            OSError) as exc:
        return {"protocol_id": PROTOCOL_ID, "matches": False,
                "reason": f"{type(exc).__name__}: {exc}"}
    return {"protocol_id": PROTOCOL_ID, "matches": True, "reason": None}


def assert_run_matches_protocol(run_dir: Path, plan: dict[str, Any]) -> None:
    """fail-closed 版本：不符即拒。

    用在 record-baseline / promote —— 这两条是**授权**动作（前者产出扩窗
    晋升的预授权参照，后者写生产），不符协议的 run 走到这里就该停。
    starter-check 是**审计**动作，用不抛的版本并把判定写进报告，因为它
    同时服务链路验证批次（那批本就不受协议保护，不是错误）。
    """
    binding = run_protocol_binding(run_dir, plan)
    if not binding["matches"]:
        raise ProtocolViolation(
            f"run {run_dir} does not match the frozen protocol "
            f"({binding['reason']}) — 一个用了别的终端 / 算子 / fitness / "
            "搜索预算 / IS 窗的 run 不得借本战役的签署外壳走到晋升。")


def assert_promotion_window(
    promotion_config: Path | None, plan: dict[str, Any],
) -> None:
    """晋升配置的 validation.end_date 必须是冻结的 OOS 终点。

    守卫此前只装在 record-baseline 上，promote 这一路没有 —— 而
    ``promote._check_pit_window`` 没有上界，所以一条合法 CLI 就能把
    盲态 holdout 年、以及与生产重叠的 forbidden 段拉进验证与相关性
    过滤（codex #446 r12 P1；同类残留：我给 mine / record-baseline
    装了守卫，漏了 promote）。
    """
    if promotion_config is None:
        return
    raw = yaml.safe_load(Path(promotion_config).read_text(encoding="utf-8"))
    if raw is not None and not isinstance(raw, Mapping):
        raise ProtocolViolation(
            f"promotion config top level is {type(raw).__name__}, expected "
            "a mapping.")
    validation = (raw or {}).get("validation") or {}
    if not isinstance(validation, Mapping):
        raise ProtocolViolation(
            f"promotion config validation section is "
            f"{type(validation).__name__}, expected a mapping.")
    end = validation.get("end_date")
    if end is None:
        raise ProtocolViolation(
            "promotion config sets no validation.end_date — a PIT-mode "
            "promotion must state the evaluation endpoint so it can be "
            "checked against the frozen OOS window.")
    assert_evaluation_endpoint(str(end), plan)


def assert_evaluation_endpoint(end_date: str, plan: dict[str, Any]) -> None:
    """评估窗终点必须**恰好等于**冻结的 OOS 终点，不只是"没越界"。

    只做上界会留下一条选样自由度：``record-baseline --end-date
    2023-06-30`` 与 ``2024-06-30`` 都合法，于是操作人可以在看过若干
    截短窗之后，挑一个最好看的去授权裁决 —— 而这个基线正是扩窗晋升的
    预授权参照（codex #446 r12 P1）。同一条纪律适用于 promote 的
    validation.end_date：``promote._check_pit_window`` 只保证顺序
    （mined < split < validation），既无上界也不要求等值。
    """
    assert_window_discipline(end_date, plan)
    want = str(plan["windows"]["oos_end"])
    if str(end_date) != want:
        raise ProtocolViolation(
            f"evaluation endpoint {end_date!r} != the frozen OOS end "
            f"{want!r} — 截短（或延长）评估窗等于换一个实验；协议冻结的"
            "裁决窗只有一个。")


def assert_promotion_criteria_frozen(
    promotion_config: Path | None, plan: dict[str, Any],
) -> None:
    """晋升配置的 ``criteria`` 段不得偏离协议冻结值。

    池是在 OOS 验证**之后**才组装的，所以一个可覆盖的相关性阈值等于把
    "看到结果再决定保留哪些因子"这条自由度留着 —— 恰是 gate_F_B
    pool_construction 开篇声称已经关掉的那一条。
    """
    if promotion_config is None:
        return
    pool_cfg = plan["adjudication"]["gate_F_B"]["pool_construction"]
    # max_pool_correlation 只写在 pool_construction 一处（协议正文描述池
    # 构成用的就是它）——冻结表若再抄一份，就成了本轮 (甲) 刚立的"门槛
    # 单点"规则的反例，两处一旦漂移，运行时强制的和正文写的会不一致
    # （codex #446 自审 r2 P2）。
    frozen = dict(pool_cfg["promotion_criteria_frozen"])
    frozen["max_pool_correlation"] = pool_cfg["max_pool_correlation"]
    raw = yaml.safe_load(Path(promotion_config).read_text(encoding="utf-8"))
    if raw is not None and not isinstance(raw, Mapping):
        raise ProtocolViolation(
            f"promotion config top level is {type(raw).__name__}, expected "
            "a mapping.")
    criteria = (raw or {}).get("criteria") or {}
    if not isinstance(criteria, Mapping):
        raise ProtocolViolation(
            f"promotion config criteria section is "
            f"{type(criteria).__name__}, expected a mapping.")
    # is_oos_split_date 不是可调阈值而是**窗口边界**，且 promote 对 PIT
    # run 强制要求它（promote._check_pit_window：不给就拒）。把它一并
    # 拒掉会让 promote 结构性不可执行 —— 给了被本守卫拒、不给被 promote
    # 拒（codex #446 自审 r2 P1）。正确处置是钉死它等于冻结的 OOS 起点。
    want_split = plan["windows"]["oos_start"]
    for key, value in criteria.items():
        if key == "is_oos_split_date":
            if str(value) != str(want_split):
                raise ProtocolViolation(
                    f"promotion config sets criteria.is_oos_split_date = "
                    f"{value!r}, frozen OOS start is {want_split!r} — "
                    "移动 IS/OOS 边界等于换一个实验。")
            continue
        if key not in frozen:
            raise ProtocolViolation(
                f"promotion config sets criteria.{key} — the frozen "
                "protocol does not govern it, so it must not be set for "
                "this campaign.")
        if value != frozen[key]:
            raise ProtocolViolation(
                f"promotion config sets criteria.{key} = {value!r}, frozen "
                f"protocol says {frozen[key]!r} — the survivor pool is "
                "assembled AFTER OOS validation; overriding this is exactly "
                "the freedom the protocol closes.")


def _cmd_starter_check(args: argparse.Namespace) -> int:
    """Evaluate the three frozen starter factors against a MINED RUN.

    Bound to a run directory, not to a mutable config path: the run
    snapshot's data definition is digest-verified on load, the factory
    is re-invoked on the run's RECORDED geometry, and its output digest
    must reproduce the recorded identity — so the starter record
    describes exactly the panel the run mined, auditable after the
    fact. A starter factor with NO evaluable observations is a broken
    leg, not a completed check: refused, nothing written.
    """
    from src.factor_mining.expression import parse_expression
    from src.factor_mining.fitness import FitnessConfig
    from src.factor_mining.gp_engine import GPConfig, GPEngine
    from src.factor_mining.miner import (
        MinerConfig,
        build_universe_mask,
        load_baseline_predictions,
        search_definition_sha256,
    )

    run_dir = Path(args.run)
    try:
        run_data, run_sha = _load_run_data_config(run_dir)
    except PromotionError as exc:
        print(f"starter-check failed: {exc}", file=sys.stderr)
        return 1
    if not run_data.fundamental_store_root:
        print("starter-check: the run records no fundamental leg — "
              "nothing to check.", file=sys.stderr)
        return 1
    binding_path = run_dir / "fundamental_binding.json"
    if not binding_path.is_file():
        print("starter-check: run has no fundamental_binding.json — it "
              "predates factory-identity recording; re-mine.",
              file=sys.stderr)
        return 1
    if run_data.mode == "pit":
        # Geometry alone cannot see an in-place PIT bundle refresh:
        # prices can move under unchanged dates x instruments, and the
        # starter metrics would then describe data the mining run never
        # used. The run records content fingerprints; verify them like
        # promotion does (codex #441 r6 P1).
        try:
            _verify_pit_binding(run_dir, run_data)
        except PromotionError as exc:
            print(f"starter-check failed: {exc}", file=sys.stderr)
            return 1
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    dates = pd.DatetimeIndex(
        [pd.Timestamp(d) for d in binding["trade_dates"]])
    instruments = [str(c) for c in binding["instruments"]]

    factory = build_panel_factory()
    values, evidence, periods = factory(run_data, dates, instruments)
    got = fundamental_output_sha256(values, evidence, periods)
    if got != binding["output_sha256"]:
        print("starter-check: factory output does not reproduce the "
              f"run's recorded identity (recorded "
              f"{binding['output_sha256']!r}, got {got!r}) — the store "
              "changed or the builder drifted since mining; the starter "
              "record would describe a different panel. Refusing.",
              file=sys.stderr)
        return 1

    panel, fwd = build_panel_for_data(run_data)
    if ([str(d.date()) for d in fwd.index] != list(binding["trade_dates"])
            or [str(c) for c in fwd.columns] != instruments):
        print("starter-check: the rebuilt price-volume panel geometry "
              "does not match the run's recorded geometry — the pv "
              "inputs moved since mining. Refusing.", file=sys.stderr)
        return 1
    merged = {**panel, **values}
    # The run's OWN gp/fitness configuration scores the starter factors:
    # "panel -> GP -> marginal contribution" means the number the GP
    # search itself would assign (fitness composition included), not a
    # bare evaluator metric bundle.
    raw_snapshot = yaml.safe_load(
        (run_dir / "config.yaml").read_text(encoding="utf-8"))
    # 协议消费（第四个子命令）。绑定的是 run 快照而非可变的配置路径，
    # 所以校验的是"这个 run 当初是不是在协议下挖的"。
    #
    # 判据不是快照里的自述字段：miner 的 config_dump 是一份写死的键表，
    # 不含 protocol_id，任何靠它做条件的分支都是死代码（codex #446 自审
    # r2 P2 —— 第一版就栽在这里）。判据是**快照记录的配置本身能否通过
    # 协议校验**：快照的 data 段已由 data_definition_sha256 摘要核验，
    # 所以"逐字段等于签署的协议"是比自述强得多的绑定。
    protocol_binding = run_protocol_binding(run_dir, load_frozen_plan())
    if protocol_binding["matches"]:
        logging.getLogger(__name__).info(
            "starter-check: run matches frozen protocol %s", PROTOCOL_ID)
    else:
        # 不拒、但把判定**写进报告**（机器可读），因为 starter-check 同时
        # 服务链路验证批次 —— 那批本就不受协议保护，不是错误。只打一条
        # 日志则报告落盘后与合规 run 形状相同、无从分辨（codex #446 r13
        # P2）。
        logging.getLogger(__name__).warning(
            "starter-check: run %s does not match the frozen protocol (%s) "
            "— its report is NOT campaign evidence.",
            run_dir, protocol_binding["reason"])
    try:
        gp_config = GPConfig(**raw_snapshot.get("gp", {}))
        fitness_config = FitnessConfig(**raw_snapshot.get("fitness", {}))
    except TypeError as exc:
        print(f"starter-check: run snapshot gp/fitness sections do not "
              f"parse ({exc}); re-mine.", file=sys.stderr)
        return 1
    # The data section has been digest-bound since #415; the gp/fitness
    # sections get the same treatment (codex #441 r8): an edited
    # snapshot must not let this check claim "the run's own criteria"
    # while scoring with something else.
    recorded_search = raw_snapshot.get("search_definition_sha256")
    if not recorded_search:
        print("starter-check: run snapshot carries no "
              "search_definition_sha256 — the gp/fitness sections "
              "cannot be verified; re-mine.", file=sys.stderr)
        return 1
    recomputed_search = search_definition_sha256(gp_config, fitness_config)
    if recorded_search != recomputed_search:
        print("starter-check: gp/fitness sections do not match the "
              f"digest recorded at mining time (recorded "
              f"{recorded_search!r}, recomputed {recomputed_search!r}) "
              "— the snapshot was edited after mining; refusing to "
              "score with criteria the run never used.", file=sys.stderr)
        return 1
    miner_config = MinerConfig(
        data=run_data, gp=gp_config, fitness=fitness_config,
        output_dir=run_dir)
    universe_mask = build_universe_mask(miner_config)
    # The run's orthogonality baseline joins the scoring context: with
    # w_orthogonality configured, a None baseline silently zeroes the
    # penalty and the reported fitness is NOT the run's composition
    # (codex #441 r8 P1). The canonical loader re-verifies the sidecar;
    # the digest must also equal the bytes mining actually consumed.
    try:
        baseline = load_baseline_predictions(miner_config)
    except ValueError as exc:
        print(f"starter-check: baseline load failed ({exc}); the run's "
              "fitness composition cannot be reproduced.", file=sys.stderr)
        return 1
    recorded_baseline_sha = raw_snapshot.get("baseline_preds_sha256")
    if baseline is not None:
        got_sha = baseline.attrs.get("baseline_preds_sha256")
        if recorded_baseline_sha != got_sha:
            print("starter-check: baseline bytes differ from what "
                  f"mining consumed (recorded {recorded_baseline_sha!r}, "
                  f"loaded {got_sha!r}); refusing.", file=sys.stderr)
            return 1
    elif recorded_baseline_sha:
        print("starter-check: the run recorded a baseline "
              "(baseline_preds_sha256) but none can be loaded now — the "
              "fitness composition cannot be reproduced; refusing.",
              file=sys.stderr)
        return 1

    def _finite(x: float) -> float | None:
        # Bare NaN is not JSON; a strict consumer downstream would
        # reject the whole record. None is the honest spelling of
        # "metric undefined here" (empty/zero-variance IC series).
        import math
        return float(x) if math.isfinite(x) else None

    from src.factor_mining.grammar import GrammarError

    report: dict[str, dict[str, float | int | str | None]] = {}
    for name, text in _STARTER_EXPRESSIONS.items():
        # ONE FRESH ENGINE PER FACTOR: the fitness composition's
        # novelty/correlation term compares against the engine's
        # accumulated per-generation pool, so a reused engine makes the
        # reported number depend on dict ORDER (C1 vs empty pool, C3 vs
        # both predecessors) — reordering the mapping would change the
        # audit artifact with no run input changing (codex #441 r9 P1).
        # Independent scoring = novelty term against an empty pool
        # (identically zero for every factor), declared in the report.
        engine = GPEngine(gp_config, fitness_config)
        try:
            fitness, result = engine.score_expression(
                parse_expression(text), merged, fwd,
                universe_mask=universe_mask, periods=periods,
                baseline=baseline)
        except GrammarError as exc:
            # The run's own sampling pool refuses the injected AST
            # (e.g. a baseline-pool run cannot breed C3's coalesce) —
            # the link claim would be about a search that cannot
            # construct the factor; controlled refusal, not a traceback.
            print(f"starter-check: {name} rejected by the run's "
                  f"configuration ({exc}); refusing.", file=sys.stderr)
            return 1
        if result is None:
            print(f"starter-check: {name} returned no evaluation "
                  "bundle (cache hit on a fresh engine is impossible; "
                  "scoring failed) — refusing.", file=sys.stderr)
            return 1
        import math
        if not math.isfinite(fitness):
            # -inf is the GP's OWN verdict that the candidate is invalid
            # (coverage floor, cross-sectional variance, non-finite IC…)
            # — a leg the search rejected has NO marginal score, and a
            # link report must not claim completion over it
            # (codex #441 r10 P1). Distinct from the zero-observation
            # guard below: here the factor evaluated, and was refused.
            print(f"starter-check: {name} was rejected by the GP "
                  f"fitness path (fitness={fitness!r}) — a refused leg "
                  "is not a completed check. Refusing; nothing written.",
                  file=sys.stderr)
            return 1
        if result.coverage <= 0.0 or result.n_obs_per_day_min < 1:
            # A starter leg with nothing evaluable means a required
            # field is missing/empty or alignment masked everything —
            # the link is NOT verified for this factor.
            print(f"starter-check: {name} produced no evaluable "
                  f"observations (coverage={result.coverage!r}) — a "
                  "broken leg is not a completed check. Refusing; "
                  "nothing written.", file=sys.stderr)
            return 1
        entry: dict[str, float | int | str | None] = {
            "expression": text,
            "fitness": _finite(fitness),
            "rank_ic_mean": _finite(result.rank_ic_mean),
            "rank_ic_std": _finite(result.rank_ic_std),
            "rank_ir": _finite(result.rank_ir),
            "coverage": float(result.coverage),
            "turnover_daily": _finite(result.turnover_daily),
            "n_obs_per_day_min": int(result.n_obs_per_day_min),
        }
        report[name] = entry
        print(f"{name}: fitness={entry['fitness']} "
              f"rank_ic_mean={entry['rank_ic_mean']} "
              f"rank_ir={entry['rank_ir']} "
              f"coverage={entry['coverage']:.4f}")

    if run_data.mode == "pit":
        # Before/after stability, same as mining and promotion: the
        # entry check cannot see a refresh that starts DURING the panel
        # rebuild or the scoring window — re-verify after all PIT reads,
        # before anything is persisted (codex #441 r7 P1).
        try:
            _verify_pit_binding(run_dir, run_data)
        except PromotionError as exc:
            print(f"starter-check failed after evaluation: {exc} — "
                  "the PIT inputs moved mid-check; nothing written.",
                  file=sys.stderr)
            return 1

    out = (Path(args.out) if args.out is not None
           else run_dir / "starter_factor_report.json")
    try:
        with out.open("x", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "purpose": "starter-three-factor-link-check",
                "run_dir": str(run_dir),
                "data_definition_sha256": run_sha,
                "search_definition_sha256": recorded_search,
                "fundamental_output_sha256": got,
                "adjudication_standing": "none — link verification only",
                # 机器可读的协议绑定判定：报告落盘后仍能分辨这是不是
                # 战役 run 产出的（codex #446 r13 P2）。
                "protocol_binding": protocol_binding,
                "scoring_path": (
                    "GPEngine.score_expression, one fresh engine per "
                    "factor: independent scores, novelty/correlation "
                    "term against an empty comparison pool (=0 for "
                    "every factor, order-independent)"),
                "factors": report,
            }, indent=2, allow_nan=False))
    except FileExistsError:
        print(f"starter-check: {out} already exists — refusing to "
              "overwrite an earlier record; pick a fresh path.",
              file=sys.stderr)
        return 1
    print(f"Starter report -> {out}")
    return 0


def _cmd_mine(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    # `protocol_id` 是 preset 对协议的**绑定**（load_config 忽略未知顶层
    # 键，所以不需要改 loader）。三态，不是两态：
    #   * 等于本协议 → 逐字段校验；
    #   * **缺席** → 链路验证之类的非战役批次，如实说明不受协议保护；
    #   * **在场但不认识** → 拒。
    # 第三态此前被并进第二态，于是一个 typo 或外来的 protocol_id 只会
    # 收到一条"未受协议保护"的警告然后照跑；而 run 快照本就不写
    # protocol_id、`run_protocol_binding` 只比生效值，所以那个 run 事后
    # 仍可能被判 matches: true 而当作战役证据 —— 挖矿说"没受管"、绑定说
    # "合规"，两条路径互相打脸（codex #446 r17 P1）。
    #
    # 与本仓对 typo 的一贯处置一致：typo 的终端名拒、typo 的算子名拒，
    # typo 的协议名没有理由被静默降级成"无协议"。
    declared_protocol = raw.get("protocol_id")
    if declared_protocol is not None and declared_protocol != PROTOCOL_ID:
        raise ProtocolViolation(
            f"{args.config} declares protocol_id {declared_protocol!r}, "
            f"which this campaign does not implement (expected "
            f"{PROTOCOL_ID!r}). 缺席 = 非战役批次，可以照跑；在场但不认识"
            " = 配置写错了，拒。")
    if declared_protocol == PROTOCOL_ID:
        plan = load_frozen_plan()
        assert_mining_config_matches_protocol(config, plan)
        assert_window_discipline(config.data.end_date, plan)
        logging.getLogger(__name__).info(
            "mine: config bound to frozen protocol %s — window %s..%s, "
            "%d terminals, %d operators",
            PROTOCOL_ID, config.data.start_date, config.data.end_date,
            len(config.gp.allowed_terminals),
            len(config.gp.allowed_operators))
    else:
        logging.getLogger(__name__).warning(
            "mine: %s declares no protocol_id — this run is NOT governed "
            "by a frozen protocol and must not be reported as a campaign "
            "result.", args.config)
    result = run_mining(
        config, fundamental_panel_factory=build_panel_factory())
    print(f"Run complete: {result.run_id} | pool size: {len(result.pool)}")
    return 0


def _cmd_record_baseline(args: argparse.Namespace) -> int:
    # 窗口纪律在**最前**：协议说的是"启动时校验"，一个把验证窗拉进盲态
    # holdout 的请求不该等到读完 run、建完面板才被拒（codex #446 自审
    # r2：行为钉发现校验排在 run 加载之后）。
    plan = load_frozen_plan()
    assert_evaluation_endpoint(str(args.end_date), plan)
    assert_run_matches_protocol(Path(args.run), plan)
    run_data, _run_sha = _load_run_data_config(Path(args.run))
    if not run_data.fundamental_store_root:
        print(
            "record-baseline: the run records no fundamental leg — "
            "nothing to baseline.", file=sys.stderr)
        return 1
    effective = replace(run_data, end_date=str(args.end_date))
    _panel, fwd = build_panel_for_data(effective)
    factory = build_panel_factory()
    values, evidence, periods = factory(
        effective, fwd.index, list(fwd.columns))
    payload = {
        "purpose": "fundamental-extension-baseline",
        "run_dir": str(args.run),
        "validation_end_date": str(args.end_date),
        "data_definition_sha256": data_definition_sha256(effective),
        "trade_dates": [str(d.date()) for d in fwd.index],
        "instruments": [str(c) for c in fwd.columns],
        "output_sha256": fundamental_output_sha256(
            values, evidence, periods),
    }
    out = Path(args.out)
    try:
        # Exclusive create ("x"), not exists()+write: a baseline is a
        # pre-authorization — silently replacing one that a pending
        # promotion may already reference would let a second recording
        # rewrite what was authorized, and a racing pair of recorders
        # could both pass a separate pre-check. The filesystem enforces
        # the no-overwrite contract atomically (same fix class as the
        # exclusion exporter, codex #441 r3).
        with out.open("x", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2))
    except FileExistsError:
        print(
            f"record-baseline: {out} already exists — refusing to "
            "overwrite a recorded authorization; pick a fresh path.",
            file=sys.stderr)
        return 1
    print(f"Baseline recorded: {out}")
    print(f"  output_sha256: {payload['output_sha256']}")
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    try:
        plan = load_frozen_plan()
        assert_run_matches_protocol(Path(args.run), plan)
        assert_promotion_criteria_frozen(args.promotion_config, plan)
        assert_promotion_window(args.promotion_config, plan)
        config = _load_config(
            args.promotion_config, args.run, args.production_dir,
            args.version,
            fundamental_baseline_path=args.baseline,
        )
        report = promote_run(
            config, dry_run=args.dry_run,
            fundamental_panel_factory=build_panel_factory(),
        )
    except (PromotionError, ProtocolViolation, FileNotFoundError,
            AttributeError, TypeError) as exc:
        print(f"Promotion failed: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(
            f"Promotion (dry-run): "
            f"{report.n_kept_after_correlation}/{report.n_pool} factors "
            f"would be kept -> production/{report.version}/"
        )
    else:
        print(
            f"Promotion complete: "
            f"{report.n_kept_after_correlation}/{report.n_pool} factors "
            f"kept -> {report.output_dir}"
        )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fundamental GP campaign orchestration")
    sub = parser.add_subparsers(dest="command", required=True)

    mine = sub.add_parser("mine", help="mine with the fundamental leg")
    mine.add_argument("--config", type=Path, required=True)
    mine.set_defaults(func=_cmd_mine)

    rb = sub.add_parser(
        "record-baseline",
        help="record the pre-authorized extension baseline")
    rb.add_argument("--run", type=Path, required=True)
    rb.add_argument("--end-date", required=True,
                    help="validation end date (the governed extension)")
    rb.add_argument("--out", type=Path, required=True)
    rb.set_defaults(func=_cmd_record_baseline)

    sc = sub.add_parser(
        "starter-check",
        help="deterministically evaluate the three frozen starter "
             "factors against a mined run")
    sc.add_argument("--run", type=Path, required=True,
                    help="run directory produced by the mine subcommand")
    sc.add_argument("--out", type=Path, default=None,
                    help="report path (default: "
                         "<run>/starter_factor_report.json)")
    sc.set_defaults(func=_cmd_starter_check)

    pr = sub.add_parser("promote", help="promote with the injected factory")
    pr.add_argument("--run", type=Path, required=True)
    pr.add_argument("--to", dest="version", required=True)
    pr.add_argument(
        "--production-dir", type=Path,
        default=Path("research/mined_factors/production"))
    pr.add_argument("--config", dest="promotion_config", type=Path,
                    default=None)
    pr.add_argument("--baseline", type=Path, default=None,
                    help="pre-authorized extension baseline JSON")
    pr.add_argument("--dry-run", action="store_true")
    pr.set_defaults(func=_cmd_promote)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Cross-check disagreements arrive via logging.warning — without a
    # configured handler Python's lastResort prints them, but INFO-level
    # progress would vanish; mirror the miner CLI's explicit config so
    # "visible output" is a property of the command, not of the caller.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        force=True,
    )
    args = _parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
