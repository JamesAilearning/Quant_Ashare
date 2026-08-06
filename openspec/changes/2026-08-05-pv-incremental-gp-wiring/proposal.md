# Proposal: pv_incremental_v1 GP 适应度接线 + 基线预测导出器

## Why

#398 签署 PV-DP-1..8、#399 落地冻结件与 OOS 决策三件套后，战役二还
缺两件才能点火：GP 侧没有正交惩罚（PV-DP-3 的**增量判据**在繁殖阶段
形同虚设），且基线预测本身没有生成/导出通路（评估器已强制要求
provenance sidecar，但没有任何东西产出它）。

## What Changes

- **GP 适应度接线**：`FitnessConfig` 增两个**惰性默认**字段
  （`w_orthogonality=0.0` / `orthogonality_band=0.0`）与 banded hinge
  罚项 `w × max(0, mean|ρ| − band)`；v1 公式与全部既有 pin 逐字不变。
  基线沿 `universe_mask` 先例穿线进 `GPEngine.run(baseline=)`，日截面
  Spearman（与冻结件/评估器同语义，非池内 Pearson novelty）。
- **缓存纪律**：新增 `baseline_key`（`no_baseline` / `baseline:<fp>`）
  入 checkpoint 并在 `run` 比对——正交罚对 coverage_key 不可见，跨基线
  resume 会把带罚与不带罚的分数混进同一池；legacy checkpoint 默认
  `no_baseline`（写入时必然无基线），campaign resume 即失效重算。
- **miner 基线装载**：`DataConfig.baseline_preds_path/baseline_model`；
  装载强制 provenance sidecar 绑定（model 恰等/file_sha256 绑盘/
  run_config_sha256+source_git 非空），与 OOS 评估器同强度。纯
  pandas+json+hashlib，D5 边界不动（新增两条 D5 门）。
- **基线导出器** `scripts/research/pv_incremental_baseline_export.py`：
  消费完成的 walk-forward run 目录 → 逐折 sha256 校验、窗口纪律
  （触 holdout/2026 即拒）、ensemble 语义恰等、折间重复行拒、
  provenance 三件（commit 40-hex + dirty=False + run_config sha）→
  宽表 parquet + sidecar（含 IS 覆盖披露），已存在即拒不覆盖。
- **preset** `config/presets/pv_incremental_baseline.yaml`：与
  csi800_campaign_base 同形，`overall_end: 2024-12-31` 钉死（父配置
  缺省 2025-12-31 会训进 holdout——圣规防线）。
- **冻结件补三口径**（操作人签，PV-DP-3 原留给实现 PR）：
  `is_coverage_policy: penalize_covered_days_only`（决策①）、
  `baseline.ensemble_window: 3`（决策②）、`ic_term: abs_rank_ic`
  （决策③）、`baseline.overall_end`；governance pin 逐条钉死。
- **ledger E001**：基线 run 点火前 intent 登记（语义 pin + 点火条件）。

## Impact

- Affected specs: `v2-factor-mining-foundations`（ADDED 一条 Requirement）。
- Affected code: `src/factor_mining/{fitness,gp_engine,miner}.py`、
  `scripts/research/pv_incremental_baseline_export.py`、preset、冻结件、
  ledger、测试（24 逻辑 + 2 治理）。
- **行为变更**：仅在 campaign 配置显式开启 `w_orthogonality` 时生效；
  默认路径逐字不变（v1 pin 全绿佐证）。
- 点火不在本 PR：基线 run 与 GP 批次均由操作人点火。
