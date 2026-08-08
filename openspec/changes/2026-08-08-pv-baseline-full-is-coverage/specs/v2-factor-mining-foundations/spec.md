# v2-factor-mining-foundations — delta for 2026-08-08-pv-baseline-full-is-coverage

## ADDED Requirements

### Requirement: 基线 SHALL 覆盖完整的冻结 IS 窗（决策①-rev1）

pv_incremental_v1 的基线 walk-forward SHALL 配置为其**首个样本外
test 窗恰好始于冻结 IS 窗起点**（2018-01-01），使正交惩罚在全部 IS
交易日上有约束力。该要求 SHALL 由折几何推导（overall_start +
train_months + valid_months == is_start）而非字面日期断言，使 train/
valid 月数的任何变更重新推导要求而非使钉子失效。

train 窗长度 SHALL 保持生产同源（24 个月）——本修订解除的是数据边界，
SHALL NOT 以缩短 train 窗的方式取得更早覆盖（原决策①禁令保留）。

折网格 SHALL 与原 19 折对齐（起点前移量为 step 的整数倍）：原有折的
test 窗原样保留，OOS（2023-2024）折 SHALL 分毫不动。

#### Scenario: 首个 test 窗对齐 IS 起点

- **GIVEN** 基线 preset 与父配置的折几何参数
- **WHEN** 由 overall_start + train + valid 推导首个 test 窗
- **THEN** 它等于冻结 IS 起点 2018-01-01，且 overall_end 仍为
  2024-12-31

#### Scenario: OOS 折不受影响

- **GIVEN** 修订前后的两份折清单
- **WHEN** 比对 test 窗落在 2023-01-01..2024-12-31 的折
- **THEN** 两份完全一致（8 折，窗口逐一相同）

### Requirement: 训练窗 SHALL 被数据日历完整覆盖（fail-loud）

walk-forward 引擎 SHALL 在装配每折前校验：该折声明的 train_start 不
早于所绑 bundle 日历的首日。不满足时 SHALL fail-loud 拒绝该 run（而
非静默用被裁剪的数据训练并在 manifest 中记录声明窗口）。

基线导出器 SHALL 将 overall_start 纳入 run-config 绑定，使一个用错误
起点跑出的 run 无法被认证为本战役的基线。

#### Scenario: 旧 bundle 上声明 2015 起点即拒

- **GIVEN** QUANT_PROVIDER_URI 指向首日为 2018-01-02 的 bundle，preset
  声明 overall_start 2015-10-01
- **WHEN** 启动 walk-forward
- **THEN** run 拒绝启动并指出日历首日晚于所需 train_start —— 而非静默
  训练出 3 个月数据的"24 个月模型"

#### Scenario: 导出器拒绑错误起点

- **GIVEN** 一个 overall_start 与本战役 preset 不符的已完成 run
- **WHEN** 基线导出器消费它
- **THEN** 拒绝导出并指出 overall_start 漂移
