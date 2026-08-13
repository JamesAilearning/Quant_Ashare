# Tasks: 2026-08-12-pv-promotion-paired-run

## W1 代表生产 bundle
- [ ] `scripts/research/pv_incremental_promote_representative.py`：
      `--gp-run-dir --candidates --verdict --candidate-id --out-dir`；
      判据仅"E007 幸存名单含该 id"；expr_hash 与池逐字节核对；单条目
      FactorPool 落盘 + provenance sidecar（verdict sha/manifest sha/
      GP run 四重 sha/orientation/表达式全文）
- [ ] 拒绝路径测试：id 不在幸存名单拒；verdict 文件 sha 与台账 E007
      不符拒；池中 expr_hash 不匹配拒
- [ ] 治理钉：工具源码不 import `src.factor_mining.promote`（旧判据
      隔离）

## W2 Alpha158PlusMined handler
- [ ] `register_alpha158_plus_mined_handler(bundle)`：Alpha158 特征 +
      mined 面板 (datetime, instrument) 对齐拼列；label 与 Alpha158
      缺省逐字节同
- [ ] cache identity 复合（alpha158_default × bundle 指纹）
- [ ] 测试：拼列后列集 = Alpha158 列 ∪ 因子列；label 列逐字节同；
      因子列 NaN 对齐语义；D5（qlib 惰性导入）现有钉延续
- [ ] 两臂 label 一致性钉（treatment/baseline 同 config 下 label
      表达式字符串相等）

## W3 双臂 preset
- [ ] `pv_promo_paired_baseline.yaml` / `pv_promo_paired_treatment.yaml`
      （overall 2020-10-01..2024-12-31；canonical 默认口径；唯一差异
      feature_handler）
- [ ] 治理钉：两 preset 逐键 diff 仅 feature_handler（+bundle 绑定键）；
      overall_end 圣规；不含 risk_constraints_mode/metrics_purpose 键；
      折几何推导 = 首 test 窗 2023-01-01、8 折
- [ ] 引擎侧确认 treatment preset 的 handler 绑定链在真实 run 路径
      成立（#412 r3 教训：钉真实装配路径非手搓对象）

## W4 裁尺 plan
- [ ] `docs/prereg/pv_promotion_paired.yaml`（hypothesis/direction/
      baseline/treatments 单变体）
- [ ] 治理钉：plan 的 treatments 恰为单变体；文件被 ruler
      `--prereg-plan` 可加载（load 层冒烟）

## W5 台账
- [ ] E008 intent 条目（preset 指纹/plan 指纹/W1 bundle sha/判定窗/
      ruler 参数）随实现 PR 入 `docs/prereg/pv_incremental_ledger.yaml`
- [ ] 台账解析钉（已有）覆盖新条目

## 验证
- [ ] 全量快速套件绿；ruff/mypy CI 对齐命令绿
- [ ] 关键守卫双向突变验证（W1 判据、W3 preset diff 钉、W2 label 钉）
- [ ] codex 循环至 CLEAN + CI 七绿 → STOP 等操作人 merge（= 签署）
