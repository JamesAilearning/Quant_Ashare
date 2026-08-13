# Tasks: 2026-08-06-pv-incremental-candidate-registration

## 1. 实现（本 PR）
- [x] 注册器：run 绑定 / top-K 选择 / 安全稳定 id / 可解析表达式 /
      orientation 位对齐 / 评估器 preflight 自证 / 不覆盖既有注册
- [x] provenance sidecar + ledger 注册条目产出
- [x] 测试：20 逻辑（冻结件/run 绑定七类漂移/字段集/无基线/选择/
      清单/自证/端到端/裁决器可消费）+ 1 治理

## 2. 点火（并后，操作人）
- [x] 基线 walk-forward + 导出（PR③ 已就绪，命令已呈）  ← 台账 E004
- [x] GP 搜索批次 → 注册清单 → ledger 条目追加  ← 台账 E006（manifest 8889f223…）
- [x] OOS 一次性评估 → FWER 裁决 → 数字 STOP（阴性照报）  ← 台账 E007（数字 STOP 后由操作人裁决）
