# Proposal: PV-DP-7 晋升门步 2-4 — 代表桥接 + 配对 canonical run + 净基差裁尺

**签署项**。E007（#421 合并入档）裁决 `survivors`（50/50，实质单
family），触发冻结晋升路径 `promotion.path` 的后续步骤：
`phase6_handler → walkforward_paired_vs_baseline →
net_basis_improvement → operator_signature`。本提案冻结这三步的
全部执行语义；操作人合并本 change 的实现 PR = 签署。
`production_wiring_in_scope: false` 不变——生产接线另案（步 6）。

## 已裁决的前提（不在本提案重议）

- 代表 = **pv001_2789e60e**（`cs_demean(abs($turnover_rate))`，
  orientation −1，注册序 fitness 最高；操作人 2026-08-12 裁决）。
  50 幸存者系等价变换（E007 注记），晋升门按单 family 走单代表。
- 2025 holdout 仍盲。单向揭盲仅限步 5 终裁，且须操作人单独明示。

## W1 — 代表生产 bundle（phase6_handler 前半）

新工具 `scripts/research/pv_incremental_promote_representative.py`：
从 GP run 目录按 `candidate_id + expr_hash` 精确提取 pv001 单条目，
构造单条目 FactorPool 写入
`production/factor_mining/pv_incremental_v1/<version>/`，并落
provenance sidecar：E007 verdict sha256（`195c21bc…`）、注册 manifest
sha（`8889f223…`）、GP run 四重 sha、candidate_id、orientation、
表达式全文。**判据只有一条：E007 幸存名单包含该 candidate_id。**

**明确不使用 `src/factor_mining/promote.py`**（v1/D4 旧判据）：其
`min_oos_ir 0.3` 会拒掉本 family（OOS 日频 IR≈0.23）——它是与已签
FWER 裁决冲突的第二裁判。旧工具保持原样服务 v1 流程，本路径不触碰。

**orientation 的特征侧语义**：treatment 臂把因子原始值直接给 LGB
（树模型分裂方向自适应，符号翻转不改变可学信息）；orientation 仅
入 provenance 照录，不对特征值做翻转。评估侧（E007）已按 IS 符号
规范化，两侧语义各自成立、互不混用。

## W2 — 组合 handler `Alpha158PlusMined`（phase6_handler 后半）

`src/data/mined_factor_handler.py` 扩展：注册名
`Alpha158PlusMined`——Alpha158 DataHandlerLP 特征列 + mined 因子
面板按 `(datetime, instrument)` 对齐拼列。**label 与 Alpha158 缺省
逐字节同**（REGEN-2 锚同款义务）；treatment/baseline 两臂 label
一致性由测试钉死。cache identity = `alpha158_default` × mined
bundle 复合指纹（现有 `_compute_bundle_cache_identity` 复用）。
D5 合规：模块在 `src/data/`，qlib 惰性导入不变。

## W3 — 双臂 preset（canonical 口径）

- `config/presets/pv_promo_paired_baseline.yaml`：继承 config_walk；
  `feature_handler: Alpha158`。
- `config/presets/pv_promo_paired_treatment.yaml`：唯一差异
  `feature_handler: Alpha158PlusMined`（绑 W1 bundle）。

两臂共同钉死：
- **overall_start: "2020-10-01"** —— 24m train + 3m valid → 首 test
  窗恰为 2023-01-01：**判定窗独占 OOS dev（2023-2024，8 个季度折）**。
  GP 在 IS（2018-2022）见过数据，IS 段的 treatment 优势属 in-sample
  泄漏，不得进净基差判定——判定几何直接不生成 IS test 折。
  train 窗保持 24m 生产同源（不缩短 train 窗的禁令延续）。
- **overall_end: "2024-12-31"**（圣规：holdout 盲态防线）。
- **不设** `risk_constraints_mode` / `metrics_purpose` —— 取默认
  `raise` / `official` = **canonical 口径**（净基差判据需要真实
  可信的净收益；E004 的 predictions_only 口径不可充当对照臂）。
- `QUANT_PROVIDER_URI=D:/qlib_data/my_cn_data_pit_2015`（新 bundle，
  与基线/GP 同源）。
- RAISE 折失败风险如实预告：任一臂折 fail → 该折无预测 → ruler 的
  配对重叠门（`--min-paired-days` / overlap floor）自动收窄判定；
  重叠不足则 ruler 拒出 verdict，fail-loud 呈操作人，不得放宽门槛
  续跑。失败折数照录台账。

## W4 — 裁尺预注册 plan（net_basis_improvement 的唯一判据）

`docs/prereg/pv_promotion_paired.yaml`（**先行提交，运行在其
commit 之后**——ruler 祖先链门要求）：

```yaml
hypothesis: "csi800 生产模型族加入 pv001（低换手因子）后,
  2023-2024 判定窗的日净超额改善"
expected_direction: treatment_better
baseline: alpha158-canonical-8fold
treatments: ["alpha158-plus-pv001"]   # 单变体,设计时冻结,不再增补
```

判据 = ruler 三态 verdict **原样**（paired moving-block-bootstrap CI
on daily net excess，`--prereg-plan --variant` 决策级路径），不另造
第二判据、不设额外数字门槛：
- `TREATMENT_BETTER` → 进步 5（操作人签字裁决是否晋升 + 是否揭盲终裁）；
- `INCONCLUSIVE` / `BASELINE_BETTER` → 照录台账，不晋升；后续归操作人。

## W5 — 台账义务（时序即规则，E002 教训）

- **E008（intent）**：随实现 PR 入台账——两臂 preset 指纹、plan 文件
  指纹、W1 bundle sha、判定窗、ruler 参数。**E008 入 main 先于两臂
  点火**（合并即台账先行落位）。
- 点火在操作人；两臂各一次不间断调用（ruler 拒 resume 混提交 run）。
- **E009（result）**：两臂 run + ruler verdict 数字原样照录（含失败
  折与配对剔除计数）。

## 影响

- 新增：W1 工具 + 测试；`Alpha158PlusMined` handler + 测试；两 preset；
  prereg plan；治理钉（W1 判据绑 E007、两臂 label 一致、preset 语义
  与本提案逐项一致）。
- 不改：`promote.py`（边界写明）、冻结件 `pv_incremental.yaml`（晋升
  路径语义未变，本提案是其步 2-4 的执行细则）、生产 serving 链路。
