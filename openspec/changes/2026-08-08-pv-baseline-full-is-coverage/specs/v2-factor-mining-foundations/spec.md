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

walk-forward 引擎 SHALL 校验所绑 bundle 日历覆盖自 overall_start 起
的完整训练历史，不满足时 SHALL fail-loud 拒绝该 run（而非静默用被裁
剪的数据训练并在 manifest 中记录声明窗口）。

"预期首个交易日"的权威 SHALL 是 bundle 完整性戳的
`data_coverage_start`（codex #412 r2）——构建器自 fetch manifest 的必需
端点覆盖复制（取其 coverage_start_date 之最大者；仅零 hole 的完整
fetch 可盖此戳）。零 hole 完整 fetch 自 X 日起的语义即"X 起每个交易日
的数据都在"，故日历首日就是 ≥X 的第一个真实交易日：`coverage_start >
overall_start` 即 SHALL 拒，**与空缺大小无关**——任何按空缺尺寸的启发
式都会放行藏在假期窗口内的截断 bundle（例：起于 2015-10-12 者仅缺 7
个工作日，却缺失 10-08/10-09 两个真实交易日）。

戳校验 SHALL 是**叠加**而非替代（codex #412 r4）：manifest 覆盖是
fetch 的**请求窗口**，在文件校验之前记录，且 fetcher 的新鲜度规则只验
各文件的尾端——头部原始文件缺失时戳仍会声称请求起点，而实际构建出的日
历起于多年之后。故日历空缺守卫 SHALL 无条件执行（工作日容差：A 股史
上最长连续闭市恰 6 个工作日，超 6 即拒——上界取史实精确值，任何+1 缓冲正是截断可藏身之处，codex #412 r5），戳存在时**另加**"覆盖起点晚
于 overall_start 即拒"一道。构建器 SHALL 在盖戳前将声称的覆盖起点与
实际构建出的日历对账，空缺超过闭市容差即拒绝构建（fail-loud——这是损
坏输入，不是部分构建的选择）。

戳无该字段时（早于本字段的 bundle，含生产 bundle；与 identity 字段同
一 schema-v1 可选姿态）仅日历空缺守卫生效（例：起于 2015-10-20 者缺
13 个工作日，拒）。

基线导出器 SHALL 将 overall_start 纳入 run-config 绑定，使一个用错误
起点跑出的 run 无法被认证为本战役的基线。

#### Scenario: 戳内覆盖起点晚于 overall_start 即拒（与空缺大小无关）

- **GIVEN** 完整性戳 data_coverage_start=2015-10-12 的 bundle（仅缺 7
  个工作日，任何闭市容差都会放行）
- **WHEN** 启动 walk-forward（overall_start=2015-10-01）
- **THEN** run 拒绝启动并指出 fetch 从未建立该日前的历史

#### Scenario: 戳声称的覆盖未被日历兑现即拒（叠加不替代）

- **GIVEN** 完整性戳 data_coverage_start=2015-10-01（等于 overall_start，
  权威检查通过），但头部原始文件缺失使构建出的日历起于 2018-01-02
- **WHEN** 启动 walk-forward
- **THEN** 日历空缺守卫仍然拒绝 —— 戳的存在不得关闭它

#### Scenario: 构建器拒绝盖未兑现的戳

- **GIVEN** fetch manifest 声称覆盖自 2015-10-01，而实际数据构建出的
  日历起于 2018-01-02（588 个工作日的声称历史缺失）
- **WHEN** 构建 bundle
- **THEN** 构建 fail-loud 拒绝，指出头部原始文件缺失须重新 fetch

#### Scenario: 无戳（legacy）bundle 回退工作日容差

- **GIVEN** 一个无 data_coverage_start 戳、起于 2015-10-20 的部分构建
  bundle（距 overall_start 仅 19 个日历天，但缺 13 个工作日）
- **WHEN** 启动 walk-forward
- **THEN** run 拒绝启动 —— 固定日历天容差不得放行它

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
