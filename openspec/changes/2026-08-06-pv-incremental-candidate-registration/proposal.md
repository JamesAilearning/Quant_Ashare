# Proposal: pv_incremental_v1 候选注册器（GP 池 → OOS 清单）

## Why

#401 让 OOS 评估器**强制**要求候选清单逐项携 `orientation`（符号盲
的 |rank-IC| 判据 + 单侧正门槛 = 未标方向的候选会被反向检验）。方向
存在 `PoolEntry` 与池 parquet 里，但仓库中**没有任何工具**把
`FactorPool` 转成评估器读的
`[{candidate_id, expression, orientation}]`。因此 GP 批次跑完后无法
进入 OOS 评估——这是战役序上的硬缺口。

## What Changes

- 新增 `scripts/research/pv_incremental_register_candidates.py`：
  - **run 绑定**：GP run 的 `config.yaml`（miner 落盘的解析后配置）
    须与冻结件恰等——宇宙/IS 窗/七字段/close 目标/ic_term/薄日门/
    简约系数/正交带权；且必须携 `baseline_preds_path`（无基线 =
    没有增量判据，其候选不构成本役 trial）。
  - **选择**：按 fitness 降序取 top-K，`-inf`（无效因子）永不注册
    ——它会白占一个 FWER family 槽并抬高全批门槛。
  - **清单**：id = 排名前缀 + 表达式内容摘要（**不用** `expr_hash`
    ——池自身文档指出那是进程随机的 `hash()`）；表达式取
    `to_qlib_string()`（可解析形态，`str`/`repr` 是 AST 构造式，
    冻结文法读不了）；orientation 逐行从池带出。
  - **自证**：写盘前用评估器**自己的** `preflight_candidates` 跑一
    遍（七字段/文法/CSF-PURE 根/id slug+唯一/方向域），避免产出一
    个消费方会拒的清单——在 OOS 一次性窗口才发现等于烧掉窗口。
  - **写纪律**：清单一经写出即冻结，已存在即拒（重注册复用 id 会
    让新批继承旧批工件）。
  - 同时产出 provenance sidecar 与 ledger 注册条目（成对纪律的
    intent 侧）。
- 治理测试：注册器绑定冻结件、且自证走消费方 preflight。

## Impact

- Affected specs: `v2-factor-mining-foundations`（ADDED 一条 Requirement）。
- Affected code: 仅新增脚本 + 测试 + 一条治理 pin。**未改**评估器 /
  裁决器 / GP 引擎（本 PR 只加工具）。
- 点火不在本 PR：GP 批次与注册均由操作人执行。
