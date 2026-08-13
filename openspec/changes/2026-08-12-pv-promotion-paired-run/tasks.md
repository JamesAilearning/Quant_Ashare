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

## Backlog（操作人裁决推迟，2026-08-13；不阻塞本 change）

codex 在 #422 第 5/6 轮提出两条加固，操作人裁决**两条均入 backlog**。
两条都属"把绑定再深一层"的方向（路径 → 日历哈希 → 全量特征字节 →
报告签名），每加一层都能再问一次"那这层怎么保证"；本 change 的证据链
在**非伪造威胁模型**下已闭合，故到此为止。

### B1 — 裁尺按注册值校验戳记分量（而非仅要求分量非空）

现状：`arm_requirements` 的 `components` 只要求每个分量存在且非空，
故手工编辑的 report 可写 `verdict_sha256=x|ledger=x` 蒙混。

不做的理由：这防的是**手工伪造的 report JSON**，而现有 gate 的每一项
（`git_commit` 祖先链、ST content hash）同样不防伪造 report——能改
stamp 的人也能把 `git_commit` 写成一个真 commit。对伪造 report 的正解
是**报告签名**，属另案。

若将来要做，**干净路径**（不碰共享基础设施、不让通用 gate 耦合战役台账）：
把 `components` 由"名单"升级为"名 → 期望值"映射，期望值写进 **plan**
——plan 本身就是 git 提交的注册件，gate 只需比对注册值。

### B2 — bundle 内容身份只覆盖日历

现状：`src/data/bundle_manifest.py:164` `compute_bundle_content_hash`
**明文只哈希** `calendars/day.txt`，docstring 声明 mid-bin 数值漂移
"out of scope"（理由：bins 可达数十 GB）。故 bundle 就地重灌但日历不变
时，特征值可变而身份不变。

不做的理由：这是**仓库级既定设计**，所有战役的 bundle 身份都依赖它。
要"绑定实际服务的特征数据"须新造不可变快照身份机制并改
`bundle_manifest` 语义——波及全仓，远超本 change（PV-DP-7 步 2-4
执行细则）范围。若要做，应作为独立 change 走设计通道。
