# Tasks: 2026-08-17-ui-drift-p0

## W1 config_run：csi800 守卫三件套

- [x] `_GUARD_FIELD_DEFAULTS`（= dataclass 默认，非契约值）+
  `_RESET_FIELD_DEFAULTS` 并集；`_apply_preset` / `_detect_preset` 共用
- [x] 「🛡️ csi800 扩池守卫」区块三控件（checkbox×2 + selectbox）
- [x] 三键进**共享** `config_dict`（mode 切分前，两侧 schema 都收）
- [x] `training_guards.validate_csi800_guard_triple` 委托 canonical 校验器；
  渲染守卫段 + 提交复检段各调一次
- [x] `slippage_bps` 默认不动（治理钉 = base 带 in-code 默认），改加 help
  文案说明 base 5.0 vs 认证 conservative 20.0

## W2 今日推荐：成本口径与 entry 红线

- [x] `ROUND_TRIP_COST` 由认证 profile 滑点 + 佣金 + 印花税组装（55 bps），
  不复述字面量；佣金/印花税跨层刻意复制 + 测试钉
- [x] `COST_REFERENCE_COLUMN` 由常量派生，表头与被减数同源
- [x] 页面两条 caption：entry 已收盘会话（红线）、成本列是保守下界
- [x] `cost_reference` docstring 讲清 1 日评分 vs 周频持有的错配

## W3 测试

- [x] `tests/logic/test_csi800_guard_triple_ui.py`：复现开箱即坏 / 守卫拒绝 /
  补齐可构造 / 全部真子集拒绝 / 非 csi800 不受影响 / padded csi800 仍守
- [x] config_run 源码钉重锚（两族并集 + 私有重置路径不复存在）
- [x] 成本三钉：值 55bps / move-the-source 联动 / 复制常量对齐 canonical
- [x] 列名派生钉（旧字面量表头不得残留）

## 验证

- [x] 定向：`test_csi800_guard_triple_ui` + `test_operator_ui_config_run_source`
  + `test_daily_decision_page_source` → 131 passed
- [ ] governance 全层 + CI 精确 mypy 全量 + ruff
- [ ] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge
