# CSI800 N5 生产运维手册（季度重训 + ensemble 3 协议）

来源：OpenSpec `2026-07-20-csi800-n5-production-promotion`（R1 修订，
PR #389 签署；PR-A' #390 服务机制；PR-B' 门工装 + 轮换执行器）。
生产协议本体 = **季度重训 + 最近三名成员 ensemble + N5 iso-week
服务节奏**。单一冻结模型近似协议已被实证否决（
`docs/research/csi800_n5_promotion_guard_brief.md`）。

## 预期管理基准（跑前写死，勿以单季波动回调）

- 成本口径：**20 bps 保守单边滑点**（认证战役口径）；盈亏平衡
  参考 ≈ **73 bps**/单边。
- 认证证据（八年 walk-forward 均值）：cons 净超额 +6.52%/yr、毛
  保持率 78.8%。**协议级单季波动 ±30-70% 属正常**——edge 仅在
  均值意义上存在；任何单季净数字都不是回调依据。
- **净业绩唯一权威 = 已认证战役证据 + 年度再认证**。per-retrain
  轻门**不含净收益门**（R1-DP-B）。

## 周节奏服务卡（每交易日早晨）

1. `python scripts/daily_recommend.py --ensemble-manifest <生产 manifest>`
   （PR-C' 切换后；切换前仍为单模型 `--model` 路径）。
2. 输出工件携 `rebalance_day: true|false` 与 `next_rebalance_date`：
   - `rebalance_day: true`（ISO 周第一交易日）→ 可执行 T+1 入场清单；
   - `rebalance_day: false` → **HOLD 监控视图**，不构成入场指令；
     决策页会阻断入场表单。
3. 周中 ST/退市/停牌**不触发中途调仓**——卖出在下一再平衡日处理
   （与认证回测 N5 语义一致）。
4. 任何 serving fail-loud 拒绝（manifest 缺员/断链/框架版本漂移/
   重复成员）= 当日不出单，排查工件链，**绝不手工降级为单模型**。
5. 晨跑命令的宇宙/节奏参数见"首次上线操作卡"第 6 步——它们是显式
   参数而非默认值，任何脚本化封装都必须原样携带。

## 首次上线操作卡（自举，晋升路径，仅执行一次）

R1-DP-C：以三名**错峰**成员自举（训窗终点 T-6m/T-3m/T，各 24 个月
滚动训窗 + 3 个月 valid）。窗口已**跑前钉死**在
`config/presets/csi800_n5_bootstrap_m{1,2,3}.yaml`（治理测试钉守）：

| 成员 | 训窗 | valid 窗 |
|---|---|---|
| m1（T-6m） | 2023-08-14..2025-08-13 | 2025-08-18..2025-11-18 |
| m2（T-3m） | 2023-11-13..2025-11-13 | 2025-11-18..2026-02-13 |
| m3（T）    | 2024-02-19..2026-02-13 | 2026-02-26..2026-05-26 |

fit_end 间隔 92/92 天、训窗跨度 730/731/725 天——满足 serving
manifest 的错峰与 24 月窗 pins。

1. **三发 GPU 点火（操作人执行，严格串行，绝不并发）**：
   ```bash
   python main.py config/presets/csi800_n5_bootstrap_m1.yaml
   ```
   ```bash
   python main.py config/presets/csi800_n5_bootstrap_m2.yaml
   ```
   ```bash
   python main.py config/presets/csi800_n5_bootstrap_m3.yaml
   ```
2. **成员级门 ×3**（trainer 完整性 + valid 窗 IC>0）：对每名成员跑
   `scripts/retrain_gate.py --scope member`，窗口照抄其 preset。
3. **候选 manifest**：`scripts/rotate_ensemble_member.py plan` 不适用
   （自举时无现行 manifest）——按 `csi800_n5_ensemble_manifest_v1`
   直接写三成员（oldest→newest），随后由切换执行器以严格加载器验证。
4. **ensemble 级门 ×1**（退化/约束干跑/veto 面）：
   `--window-start 2026-03-17 --window-end 2026-06-17`（trailing
   quarter，对三名成员全样本外）。
5. **切换执行**（晋升路径全门→零写入拒绝；先 `--dry-run` 看门）：
   ```bash
   python scripts/bootstrap_ensemble_cutover.py --dry-run      --manifest <候选 manifest>      --member-gate <m1 gate> --member-gate <m2 gate>      --member-gate <m3 gate> --ensemble-gate <ensemble gate>      --incumbent D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl      --manifest-out <生产 manifest 路径>
   ```
   门全过后去掉 `--dry-run` 实跑：写现任备份 → 三成员 inference
   meta → 生产 manifest → baseline 记录 → **初始 WIN 状态工件**
   （`docs/promotion/csi800_recert_status.json` 的**首写**，此后由
   年检流程维护；缺它则季度轮换会因读不到有效状态而冻结）。
6. **切换后：改晨跑命令**——`--ensemble-manifest` 只接管"用哪个
   模型"，**宇宙与节奏必须一并显式传入**（CLI 默认仍是 csi300/日频，
   漏传会产出 csi300 日频工件、且无 `rebalance_day`/
   `next_rebalance_date` 字段，与 N5 iso-week 协议相悖）：
   ```bash
   python scripts/daily_recommend.py      --ensemble-manifest <生产 manifest>      --instruments csi800      --rebalance-cadence-days 5      --topk 50
   ```
   三个显式值取自 `config/serving/csi800_n5_production.yaml`（两级
   绑定链第二级，治理测试钉死其与认证胜者的同值性）；fit 窗由
   manifest 最新成员自动解析，无须再传 `--fit-*`。
   随后提交 baseline 与状态工件，进入观察期（首季只记录不回调）。

**任一门不过 = 自举中止**：不切换、现任 canonical 续任、失败如实
入档，处置升级为操作人决策点——自举没有"沿用旧 ensemble"分支
（那是季度轮换维护路径专属动作）。

## 季度重训操作卡（维护路径）

前提：现行认证有效（状态工件 `docs/promotion/csi800_recert_status.json`
在 `origin/main` 上 verdict=WIN 且未过 15 个月有效期——执行器会
机器校验，操作人无须也**不得**以口头断言替代）。

1. **训练新成员**（GPU，操作人点火）：同族配置
   （Alpha158/LGB/csi800/campaign 三守卫），24 个月滚动训窗 +
   3 个月 valid，embargo 同 walk-forward 折算术；训窗终点 = 本季度末。
2. **成员级门**（gate a/d）：
   ```sh
   python scripts/retrain_gate.py --scope member \
     --member-pkl <新成员.pkl> --member-meta <新成员.pkl.meta.json> \
     --fit-start <训窗起> --fit-end <训窗终> \
     --valid-start <valid 起> --valid-end <valid 终> \
     --out output/retrain_gates/<季度>_member_gate.json
   ```
   四个窗口参数**照抄该成员训练所用 preset**（训窗 + valid 窗）：
   门以生产推理形状建集（归一化 fit = 训窗，评分段 = valid 窗）。
   trainer 完整性（sidecar 必须携 `num_boost_round`；
   `best_iteration == num_boost_round` = 早停从未触发，拒）+
   valid 窗 IC(1d) > 0。
3. **候选 manifest**：
   ```sh
   python scripts/rotate_ensemble_member.py plan \
     --manifest <生产 manifest> \
     --new-pkl <新成员.pkl> --new-meta <新成员.pkl.meta.json> \
     --fit-start <训窗起> --fit-end <训窗终> \
     --out output/retrain_gates/<季度>_candidate_manifest.json
   ```
4. **ensemble 级门**（gate b/c/e，trailing quarter 干跑）：
   ```sh
   python scripts/retrain_gate.py --scope ensemble \
     --manifest output/retrain_gates/<季度>_candidate_manifest.json \
     --window-start <上季度首交易日> --window-end <上季度末> \
     --out output/retrain_gates/<季度>_ensemble_gate.json
   ```
   退化 0-0 + campaign_v1 约束干跑零触发 + serving veto 面
   ②(<80%)/⑤(<75%/<10%)/③（干跑换手 ≤ 锚上 iso_week 复核均值
   ×1.5，锚经 `git show origin/main` 读取）。
5. **轮换执行**（两门工件均 PASS 才可能成功；任一缺失/FAIL =
   执行器拒绝，manifest 零写入）：
   ```sh
   python scripts/rotate_ensemble_member.py execute \
     --manifest <生产 manifest> \
     --candidate output/retrain_gates/<季度>_candidate_manifest.json \
     --member-gate output/retrain_gates/<季度>_member_gate.json \
     --ensemble-gate output/retrain_gates/<季度>_ensemble_gate.json
   ```
   执行器自动写 `<manifest>.pre_rotation_<UTC时间戳>` 备份。
6. **回滚（单步）**：把备份文件复制回 manifest 路径即回到上一
   ensemble。不需要其他任何操作。

**轻门失败动作（维护路径专属）**：该成员**不入 ensemble**、现行
ensemble 沿用、门工件如实入档（勿删除 FAIL 工件）；**连续两季
不过 = 操作人决策点**（升级裁决，勿静默第三次重试）。

## 观察期纪律（首季度）

- 只记录、不回调：每周把实际出单与 HOLD 披露归档；季度末做
  复盘报告（毛/净/换手 vs 认证基准的偏离幅度记录在案）。
- +6.52%/yr 是八年均值证据，**不是实盘承诺**；成本侵蚀过半
  （实盘等效成本 > ~36 bps/单边）→ 另行提案，不在观察期内改参数。

## 年度再认证义务（业绩权威）

- 每年以最新数据重跑战役协议全链（walk-forward → pair → attach →
  certify），产物 = **状态工件的新状态**：
  - WIN → certify 产新 verdict 侧车，状态工件更新携其内容哈希；
  - LOSE → certify 按设计不写侧车，状态工件单独承载 LOSE 判定
    （= 生产降级决策点，操作人裁决；裁决前生产 ensemble 不自动
    变更，季度轮换冻结）。
- 状态工件 `docs/promotion/csi800_recert_status.json` **仅由年检
  流程与首次自举修改**，走 PR 入库。有效期 15 个月（12 个月周期
  + 3 个月执行宽限），锚 = 状态工件路径在 `origin/main` 的 tip
  commit 日期——过期后季度轮换自动冻结，直到新状态合并。
- 首写属 PR-C' 切换（本手册入库时该文件**不存在**，属预期状态）。
