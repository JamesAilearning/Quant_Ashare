# Proposal: serving 模型↔宇宙一致性守卫（单模型路径 fail-closed）

## Why

架构侧 review 实证（工单 `docs/serving_universe_guard_workorder.md`，
origin/main @ 07414d2）：`daily_recommend` 的 `--instruments` 是自由
参数（默认 csi300），**单模型路径没有任何"模型↔宇宙一致性"检查**
——`--model alpha158_lgb_pit.pkl --instruments csi800` 会静默出清单：
一个零认证证据的组合（认证的超额属于滚动重训 N5 ensemble），且横
截面分布外（模型在 300 只上学的排序加 500 只中盘）。ensemble 路径
已有守护（成员 index 恰等拒绝 + serving config 两级绑定，#396）；
洞只在单模型 legacy 路径。

工单原定"cutover 前合"；cutover 已完成（#395/#396），本 change 的
价值定位相应修正为 **legacy 逃生路径的 fail-loud 加固**——单模型
路径作为后备仍在、仍可按旧习惯误用，fail-loud 优于 silent-wrong。

## What Changes

- **元数据侧（来源真相）**：`ModelTrainConfig` 增 `instruments`
  字段（projection 从 runtime config 自动带入）；trainer sidecar
  产出时自记 `universe`（未知时不写字段，绝不造默认值）。现役
  legacy 模型（`alpha158_lgb_pit.pkl`）的晋升 meta 由操作人**签字
  回填** `universe: csi300`（Gate-④ 晋升记录在案的事实）——回填
  是盘上运维操作，不在本 PR 代码内。
- **serving 侧（守卫）**：`recommend()` 纯前置区（provider 守卫后、
  qlib init 前）加模型↔宇宙一致性守卫，仅单模型路径：requested
  `--instruments` ≠ sidecar `universe` → fail-closed，domain 错误
  给三样（两个值/为什么拒/修法）；sidecar 缺 `universe` 字段 →
  同样拒 + 回填指引，绝不静默默认。sidecar 解析序与 CLI fit 窗
  解析器共用同一 `_model_meta_paths`（晋升 meta 优先），迁入库侧
  单一定义。
- **产物 meta**：单模型工件增记 `model_universe`（sidecar 解析值）；
  ensemble 工件形状不变（身份走 manifest）。
- **ensemble 路径不改**：现有拒绝行为（成员 index 恰等，
  `test_ensemble_serving.py` 已 pin）保持。
- **Runbook**：`daily-recommend-runbook.md` 拒绝表加两行（宇宙不
  匹配/缺 universe 字段）；`csi800-n5-production-runbook.md` legacy
  段引用。
- **首版不做逃生口**（工单自荐方案）：直接硬拒，研究需求走研究
  脚本。

## Impact

- Affected specs: `v2-daily-stock-recommendation`（ADDED 一条
  Requirement）。
- Affected code: `src/inference/daily_recommend.py`（守卫 + meta）、
  `src/core/model_trainer.py`（config 字段 + sidecar 字段）、
  `scripts/daily_recommend.py`（`_model_meta_paths` 迁址导入）。
- **行为变更**：单模型路径对宇宙不匹配/宇宙未记录的组合从静默出单
  变为 exit 1。现役晨跑（ensemble manifest 模式）不受影响；回填后
  的 legacy csi300 组合正常通过。
- 不碰：cutover 脚本/自举门/认证判据/canonical 训练路径（除
  sidecar 增字段）。
