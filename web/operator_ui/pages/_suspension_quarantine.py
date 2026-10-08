"""Read-only disclosure of the exact serving exception recorded by inference."""

from __future__ import annotations

from typing import Any

from src.contracts.suspension_quarantine import incident_for_policy, validate_quarantine_context
from src.data.pit._common import qlib_to_ts_code


def quarantine_notice(payload: dict[str, Any]) -> str | None:
    """Return a warning even for zero scored exclusions; reject corrupt evidence."""
    meta = payload.get("meta")
    if not isinstance(meta, dict) or "suspension_quarantine" not in meta:
        if "n_quarantined" in payload:
            raise ValueError("隔离计数缺少对应的隔离证据，需核查工件。")
        return None
    context = meta["suspension_quarantine"]
    try:
        evidence = validate_quarantine_context(context)
        incident = incident_for_policy(evidence.policy_id)
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
        if qlib_to_ts_code(code) in incident.ts_codes:
            raise ValueError(f"被隔离的 {code} 出现在候选列表中，禁止据此操作。")
    problem = ("存在已记录的缺失与冲突" if len(incident.instruments) > 1 else
               "存在已记录的冲突" if incident.conflict_keys else "仍缺失")
    return (
        f"数据不完整：{'、'.join(incident.ts_codes)} 的指定历史停复牌记录{problem}，"
        f"已启用 {len(incident.instruments)} 只股票的"
        f"明确隔离，本次评分中排除 {count} 条。其余候选仍须按常规核验；"
        "本状态不代表历史验证通过，也不会自动清除或卖出已有持仓。"
    )
