# Delta: v2-daily-stock-recommendation — CSI800 N5 生产晋升

## ADDED Requirements

### Requirement: 生产服务 SHALL 以 iso-week 节奏披露再平衡日语义

生产服务（daily_recommend）SHALL 按 **每 ISO 周第一个交易日 =
再平衡日** 的锚判定当日角色，并在输出工件中携带
`rebalance_day: true|false` 字段：再平衡日输出照常为可执行买单；
非再平衡日输出 SHALL 附醒目 HOLD 提示（监控用途，不构成调仓
指令）。周中 ST/退市/停牌事件 SHALL NOT 触发中途调仓——卖出在
下一再平衡日处理，与认证回测的 N5 语义（持有日仅市场漂移、约束
仅在再平衡生效日校验）保持一致。再平衡日判定 SHALL 由交易日历
驱动（节假日顺延至该 ISO 周内第一个实际交易日；整周无交易日则
该周无再平衡日），判定逻辑 SHALL 有确定性测试覆盖（跨年 ISO 周
边界、春节长假周、单日交易周）。

#### Scenario: 非再平衡日输出携带 HOLD 语义

- **WHEN** 操作人在非再平衡日运行 daily_recommend
- **THEN** 输出工件携带 `rebalance_day: false` 与 HOLD 提示，
  列表内容仍完整可查但被明确标注为监控视图

#### Scenario: 节假日周锚顺延

- **WHEN** ISO 周第一个日历工作日为节假日
- **THEN** 该周再平衡日 = 该 ISO 周内第一个实际交易日

### Requirement: 生产模型晋升 SHALL 以 certify 侧车与 guard eval 双门把守

任何替换 canonical 生产模型（pkl + meta）的晋升执行 SHALL 满足
全部前置，缺一即拒绝执行且不产生任何写入：

1. **战役资格门**：已提交 verdict 侧车经
   `csi800_campaign_certify.py --verify` 复验通过且
   `promotion_eligible: true`（晋升资格唯一权威，沿
   `v2-csi800-expansion-guards`）；
2. **候选 guard eval 硬 veto**（frozen 模型、csi800/SH000906TR/
   N5/20 bps 口径、guard 窗跑前钉死）：0 degenerate days、
   0 cutoff-straddle days、净超额年化 > 0，且
   `v2-csi800-expansion-guards` 五 veto 数字原样适用；任一不过
   SHALL 如实入档并中止晋升；
3. **回滚件义务**：替换前 SHALL 写 pre-promote 备份（pkl + meta，
   带时间戳）并在 `docs/promotion/` 落新基线记录，现任基线保留；
   回滚 SHALL 为恢复备份件的单步操作。

guard eval 的全部数字 SHALL 于晋升执行 PR 的数字 STOP 首次呈报，
跑后 SHALL NOT 修改判据或数字。

#### Scenario: 侧车缺失或复验失败时拒绝晋升

- **WHEN** 晋升工具在无已提交侧车、或 `--verify` 失败、或
  `promotion_eligible != true` 的状态下被调用
- **THEN** 拒绝执行且不写任何文件，报错指向缺失的前置

#### Scenario: guard eval 任一硬 veto 触发

- **WHEN** 候选在 guard 窗出现 degenerate day 或净超额 ≤ 0
- **THEN** 晋升中止、结果如实入档、现任 canonical 不动

### Requirement: 生产服务参数 SHALL 与 campaign 胜者 preset 治理绑定

生产服务侧参数 SHALL 与 `csi800_cadence5_conservative.yaml`（认证
胜者）的语义字段保持治理钉死的恰差关系：允许差异 SHALL 仅限
服务侧必要字段（白名单跑前写死入治理测试，如输出目录、serving
专属路径），universe / benchmark / cadence 数值语义 / 约束校准 /
作用域 / 成本口径 SHALL 同值。20 bps 保守成本口径与 73 bps 盈亏
平衡参考 SHALL 记入运维 runbook 作为预期管理基准；观察期纪律
（首季度只记录不回调）SHALL 同步入档。

#### Scenario: 服务参数漂移被治理测试拦截

- **WHEN** 有人修改生产服务参数使其与胜者 preset 的语义字段
  产生白名单之外的差异
- **THEN** 治理测试失败，指出漂移字段与所需的 OpenSpec 变更路径
