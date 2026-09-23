"""Read-only disclosure of the exact serving exception recorded by inference."""

from __future__ import annotations

from typing import Any

from src.contracts.suspension_quarantine import INSTRUMENT, POLICY_ID, TS_CODE, SuspensionQuarantine
from src.data.pit._common import qlib_to_ts_code


def quarantine_notice(payload: dict[str, Any]) -> str | None:
    """Return a warning even for zero scored exclusions; reject corrupt evidence."""
    meta = payload.get("meta")
    if not isinstance(meta, dict) or "suspension_quarantine" not in meta:
        if "n_quarantined" in payload:
            raise ValueError("隔离计数缺少对应的隔离证据，需核查工件。")
        return None
    context = meta["suspension_quarantine"]
    if not isinstance(context, dict) or set(context) != {
        "policy_id", "instrument", "built_from_holey_fetch", "evidence",
    }:
        raise ValueError("隔离元数据形状不合法，需核查工件。")
    if (context["policy_id"] != POLICY_ID or context["instrument"] != INSTRUMENT
            or context["built_from_holey_fetch"] is not True):
        raise ValueError("隔离元数据未标注指定事件及数据不完整状态，需核查工件。")
    try:
        SuspensionQuarantine.from_dict(context["evidence"])
    except ValueError as exc:
        raise ValueError(f"隔离证据不合法：{exc}") from exc
    count = payload.get("n_quarantined")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("隔离计数必须是非负整数，需核查工件。")
    picks = payload.get("picks")
    if not isinstance(picks, list) or any(not isinstance(pick, dict) for pick in picks):
        raise ValueError("隔离工件候选列表不合法，需核查工件。")
    for pick in picks:
        code = pick.get("stock_code")
        if not isinstance(code, str) or not code or code != code.strip():
            raise ValueError("隔离工件候选代码不合法，需核查工件。")
        if qlib_to_ts_code(code) == TS_CODE:
            raise ValueError("被隔离的 688766.SH 出现在候选列表中，禁止据此操作。")
    return (
        "数据不完整：688766.SH 的指定历史停复牌记录仍缺失，已启用 1 只股票的"
        f"明确隔离，本次评分中排除 {count} 条。其余候选仍须按常规核验；"
        "本状态不代表历史验证通过，也不会自动清除或卖出已有持仓。"
    )
