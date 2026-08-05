# Tasks: 2026-08-05-add-serving-universe-consistency-guard

## 1. 实现（本 PR）
- [x] `ModelTrainConfig.instruments` + trainer sidecar 记 `universe`
      （未知不写，绝不造默认）
- [x] `_model_meta_paths` 迁入 `src/inference/daily_recommend`（CLI
      导入同一定义，fit 窗解析与宇宙守卫不可能对不齐）
- [x] `_resolve_model_universe` / `_assert_model_universe_match` +
      `recommend()` 纯前置区接线（仅单模型路径）
- [x] 产物 meta 增记 `model_universe`（单模型；ensemble 形状不变）
- [x] 治理测试：不匹配拒（含两个值）/匹配过/缺字段拒（回填指引）/
      无 sidecar 拒/晋升 meta 优先/非法值拒/坏 JSON 拒/trainer
      sidecar 记录/projection 拉取/meta 增记
- [x] Runbook：拒绝表两行 + 生产 runbook legacy 段引用

## 2. 运维（并后，操作人签字点）
- [ ] 现役 `alpha158_lgb_pit.pkl` 晋升 meta 回填
      `"universe": "csi300"`（操作人过目签字后落盘；回填前该模型
      的单模型运行会被守卫拒——按设计 fail-closed）
- [ ] 回填后验证：csi300 组合正常通过、csi800 组合拒
