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

## codex #443 r1（1×P1 + 2×P2 全实修）

- [x] **P1 文案与工件契约相反**:spec 明写 rebalance_day=true 时「本列表是
  可执行的 T+1 入场清单」,而我那句笼统的「不是明早买入」会让该执行的清单
  被当成不该执行(codex 原话:can cause an intended list to be ignored)。
  修:只纠正**时点**误读(「明早开盘按市价买入」,与 runbook 措辞对齐),
  可执行性交回 rebalance_day/HOLD 横幅,并显式指向它
- [x] P2 文案里写死「20 bps」而常量与列名都随 profile 走 → 导出
  `CERTIFIED_SLIPPAGE_BPS`,文案由它派生
- [x] P2 守卫测试的宽泛 except 会把 import 期 NameError/误删模块统统
  伪装成「依赖不可用」,六个用例静默跳过而 CI 报绿 → 改为只在**确认
  qlib 缺席**时跳过,其余 import 失败一律炸出来
- [x] 顺带修自身测试污染:move-the-source 用例的 `finally: reload` 原在
  patch 上下文**内**,重载把 33.0 固化进模块常量泄漏给后续用例(批量跑
  失败、单独跑通过)。恢复移到 patch 退出之后,并加恢复断言

## codex #443 r2（两条 P2 全实修）

- [x] 缺 `entry_date` 的工件会渲染出「entry — 是已收盘会话」——把违约数据
  当可信引导背书。修:校验非空字符串在前,缺失则 fail-loud 拒绝给任何入场
  时点结论;钉住「校验必须在 caption 之前」且 caption 只读校验过的局部变量
- [x] 同文件另一处宽泛 except(成本常量对齐钉)——它是那两个刻意复制的常量
  唯一的防漂移机制,被静默摘掉等于防线消失。修:qlib 在场时任何 import 失败
  一律炸成 AssertionError,只在确认 qlib 缺席时跳过

## 验证

- [x] 定向：`test_csi800_guard_triple_ui` + `test_operator_ui_config_run_source`
  + `test_daily_decision_page_source` → 131 passed
- [x] governance 全层 + CI 精确 mypy 全量 + ruff
- [x] `openspec validate --strict`
- [x] codex CLEAN + CI 绿 → STOP 等 merge
