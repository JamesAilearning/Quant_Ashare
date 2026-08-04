# CSI800 N5 首次自举（v1 三元组）门拒如实入档

- **日期**：2026-08-03（点火与门评同日）
- **协议**：`2026-07-20-csi800-n5-production-promotion`（R1，#389
  签署）；工装 #390/#391/#392；runbook 首次上线操作卡
- **判定**：m2 成员级门 gate (d) FAIL → **自举中止**（零生产写入，
  现任 canonical 续任）
- **处置**：操作人裁决"另行提案"——
  `2026-08-04-csi800-n5-bootstrap-reanchor`（按冻结公式在新
  bundle 尾重锚定三元组；门判据与工装零改动）

## v1 三元组与门结果

预注册（#392，T=2026-02-13，旧 bundle 尾 2026-06-17）：

| 成员 | 训窗 | valid 窗 | trainer 门 | valid 窗 IC(1d) | 判定 |
|---|---|---|---|---|---|
| m1 | 2023-08-14..2025-08-13 | 2025-08-18..2025-11-18 | PASS（best_iter 30/1000） | +0.025870 | PASS |
| m2 | 2023-11-13..2025-11-13 | 2025-11-18..2026-02-13 | PASS（best_iter 104/1000） | **−0.016346** | **FAIL** |
| m3 | 2024-02-19..2026-02-13 | 2026-02-26..2026-05-26 | PASS（best_iter 210/1000） | +0.030255 | PASS |

门工件（原样字节，含 FAIL 件——永不删除）：
`evidence/csi800_n5_runs/bootstrap_v1_gates/m{1,2,3}_member_gate.json`。
ensemble 级门未跑（成员级已拒，序在其前）。三名成员 run 的
provenance 三件套（`run_config_sha256`/`source_git_commit`=
`2125f9c`/`source_git_dirty`=False）逐一验证通过——#392 生产者侧
绑定首次实战即生效。

## 点火前的数据侧实事（均在 repo 树外，canonical bundle 有 .bak）

1. **index_weight 原始 dumps 止于 2025-12-31**（2026-06 调样从未
   拉取）——m2 v1 首次点火倒在 attribution sleeve 覆盖守卫
   （as_of 2026-02-26 越界，守卫按设计拒绝）。处置：旧 dumps 备份
   至 `index_weight_bak_20260803/`，全量重拉三指数至 2026-08-03
   （358,898 行），`03_resolve_index_membership` 重解析（csi800
   6981 runs / 2142 tickers，实际末次调样 2026-06-30）。
2. **v1 窗口尾=旧 bundle 尾 2026-06-17 物理不可回测**（qlib 需
   T+1 日历日；m3 v1 点火倒在末步 index 越界；认证 isoweek run 末
   折 test 止于 2025-12-31，从未踩过此边；旧候选 run 的 guard 窗
   止于 2026-06-12 同因）。处置：`daily_update.py` 全链
   （fetch→registry→bins→membership→universe→benchmark→
   validate→原子 swap），bundle 尾延至 **2026-08-03**（validate
   A-F 全 PASS，仅已知 vendor-faithful 警告类）。
3. 另排一雷：repo 根曾有未跟踪探针文件会使
   `source_git_dirty=True`（三成员将被源码 provenance 门全拒），
   已归置 `config/presets/`（gitignore 豁免位，未入库）。

## 重锚定的诚实边界

- 新三元组由**同一冻结公式**在新尾重推（+47 天平移 + 交易日
  吸附），不是对失败窗口的挑选；
- m2' 新 valid 窗（2026-01-06..2026-04-03）与 v1 m2 FAIL 窗
  （2025-11-18..2026-02-13）重叠约 5.4 周——新中间成员照样可能
  FAIL，门原样不动，过不了就再次如实中止；
- v1 已训三成员（含过门的 m1/m3）全体弃置，新三元组全部重训。
