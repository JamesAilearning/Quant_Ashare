# Tasks: 2026-08-14-ui-incumbent-ensemble-identity

## W1 现任身份事实源
- [x] `docs/operations-env-vars.md` 登记 `QUANT_ENSEMBLE_MANIFEST`  ← 已登记(含读侧专用说明)
      （含「只服务读侧、CLI 不吃默认值」的说明）
- [x] runbook 晨跑命令的 `<生产 manifest>` 占位符换成该变量  ← 已替换 + 加说明块
- [x] 治理钉：`scripts/daily_recommend.py` 的 `--ensemble-manifest`  ← test_cli_ensemble_manifest_has_no_implicit_default
      仍无默认值（防后来者顺手加）

## W2 横幅认 ensemble
- [x] helpers 新增 `resolve_incumbent()`：ensemble / 单模型 / 不可解析 三态  ← IncumbentIdentity 三态 + 4 个运行时钉
- [x] ensemble 态横幅：manifest 名 + sha256 + 成员数 + 各成员 fit 窗  ← test_ensemble_banner_shows_manifest_identity
- [x] 不可解析 → WARN，不回退单模型、不填占位值  ← test_banner_refuses_to_fall_back_when_unresolvable
- [x] 单模型态：既有晋升 meta 横幅逐字不变（回归钉守）  ← 既有 26 钉全绿 + test_single_model_banner_suppressed_under_ensemble

## W3 现任交叉核对
- [x] ensemble 工件 sha 与现任 manifest sha 比对：相同 / 不同 / 无法核对  ← 三分支已实现
- [x] 删除「当前生产为单模型形态…随 PR-C' 落地」的过期陈述  ← test_incumbent_cross_check_replaces_the_expired_claim
- [x] 保留单模型侧既有告警文案（「其他模型」「旧版工件」钉守）  ← 既有 test_stale_artifact_cross_check_present 仍绿

## 验证
- [x] 既有 26 个 daily_decision 钉全绿（改动不得破坏既有契约）  ← 26 passed
- [x] 新增钉：三态横幅 / 三态交叉核对 / CLI 无默认值  ← +10 钉,合计 36 passed
- [x] 关键守卫突变验证（每次先自检突变真落地）  ← 四处咬住(M3 首轮分隔符撞车致假绿,已重做)
- [x] 全量快速套件 + ruff + mypy(CI 对齐)  ← 4113 passed;ruff/mypy 零问题;openspec 44/44
- [ ] codex 循环至 CLEAN + CI 七绿 → STOP 等操作人 merge
