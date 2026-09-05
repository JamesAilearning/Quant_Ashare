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

> `$QUANT_ENSEMBLE_MANIFEST` = 现任生产 manifest 路径（`docs/operations-env-vars.md` 登记）。运维 UI 用它显示现任身份并核对工件出处；**CLI 这一侧仍须显式传参，不吃该默认值**——出单侧不该隐式选模型。

1. `python scripts/daily_recommend.py --ensemble-manifest $QUANT_ENSEMBLE_MANIFEST`
   （PR-C' 切换后；切换前仍为单模型 `--model` 路径）。ensemble 模式
   下宇宙/节奏/topk **自动从钉死的
   `config/serving/csi800_n5_production.yaml` 绑定**
   （2026-08-05-ensemble-serving-bound-params）；显式传参仍允许但
   必须与绑定值相等，不等即拒——CLI 不是绕过绑定链的通道。
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
`config/presets/csi800_n5_bootstrap_m{1,2,3}.yaml`（治理测试钉守；
v1 三元组因 m2 成员门 IC 为负而中止，按冻结公式在新 bundle 尾
2026-08-03 重锚定——`2026-08-04-csi800-n5-bootstrap-reanchor`
RA-DP-1，v1 拒绝证据见
`docs/research/csi800_n5_bootstrap_v1_gate_refusal.md`）：

| 成员 | 训窗 | valid 窗 |
|---|---|---|
| m1（T-6m） | 2023-09-28..2025-09-29 | 2025-10-10..2026-01-09 |
| m2（T-3m） | 2023-12-29..2025-12-30 | 2026-01-06..2026-04-03 |
| m3（T）    | 2024-04-01..2026-04-01 | 2026-04-07..2026-07-07 |

fit_end 间隔 92/92 天、训窗跨度 732/732/730 天——满足 serving
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
   `--window-start 2026-05-06 --window-end 2026-07-31`（trailing
   quarter，位于三成员训练窗之后的预注册行为干跑；终点=bundle 尾前最后一个交易日，
   qlib 回测需 T+1 结算日）。该窗口与三份 preset 的 valid 窗
   一样是**预注册值**：切换执行器会逐一比对 gate 工件的被测窗口，
   改动即拒（`BOOTSTRAP_DRYRUN_WINDOW`，治理测试钉守）。

   **验证重叠，不是独立未见数据的样本外业绩验证**：m3 的 valid 窗为
   `2026-04-07..2026-07-07`，与本干跑的重叠为 `2026-05-06..2026-07-07`。
   valid 参与早停与模型选择，因此“训练窗之后”不等于“三成员都未见过”。
   证据：[m3 preset](../config/presets/csi800_n5_bootstrap_m3.yaml)、
   [m3 成员门](research/evidence/csi800_n5_runs/bootstrap_v2_gates/m3_member_gate.json)、
   [ensemble 门](research/evidence/csi800_n5_runs/bootstrap_v2_gates/ensemble_gate.json)。
   这里仅核对退化、约束与 veto 行为面；预注册日期、阈值和原始 PASS 工件不变，
   也不代表该历史期间已实时运行。净业绩仍以已认证战役证据 + 年度再认证为权威。
   m3 的 `2026-07-10..2026-07-31` test 段只是内嵌日频诊断，
   不能自动当作整个 ensemble 的独立样本外认证；
   也不能仅截取 valid_end 之后的日期就声称完全未见，本次不指定新的业绩验证窗。
5. **切换执行**（晋升路径全门→零写入拒绝；先 `--dry-run` 看门）：
   ```bash
   python scripts/bootstrap_ensemble_cutover.py --dry-run      --manifest <候选 manifest>      --member-gate <m1 gate> --member-gate <m2 gate>      --member-gate <m3 gate> --ensemble-gate <ensemble gate>      --incumbent D:/stock/phase_b_artifacts/alpha158_lgb_pit.pkl      --manifest-out <生产 manifest 路径>
   ```
   注意：`--manifest-out` 的**目录须预先建好**（含服务账户的访问
   权限）——执行器拒绝以 umask 依赖的权限临时创建目录。
   门全过后去掉 `--dry-run` 实跑：写现任备份 → 三成员 inference
   meta → 生产 manifest → baseline 记录 → **初始 WIN 状态工件**
   （`docs/promotion/csi800_recert_status.json` 的**首写**，此后由
   年检流程维护；缺它则季度轮换会因读不到有效状态而冻结）。
6. **切换后：改晨跑命令**——`--ensemble-manifest` 接管"用哪个
   模型"，宇宙/节奏/topk **自动从
   `config/serving/csi800_n5_production.yaml` 绑定**（两级绑定链
   第二级，治理测试钉死其与认证胜者的同值性；
   2026-08-05-ensemble-serving-bound-params）：
   ```bash
   python scripts/daily_recommend.py --ensemble-manifest $QUANT_ENSEMBLE_MANIFEST
   ```
   显式传 `--instruments`/`--rebalance-cadence-days`/`--topk` 仍
   允许但必须与绑定值相等，不等即拒；绑定源缺失/畸形也拒（绝不
   回退 CLI 缺省）。fit 窗由 manifest 最新成员自动解析，无须再传
   `--fit-*`。单模型 legacy 路径缺省（csi300/日频/50）逐字不变，
   但受模型↔宇宙一致性守卫把守：请求的 `--instruments` 必须等于
   模型 sidecar 记录的训练宇宙，不等/缺记录即拒（新拒绝类型见
   `docs/daily-recommend-runbook.md` 的 "When it refuses" 表）——
   按旧习惯给 legacy 单模型误传 csi800 不再静默出单。
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
   程序会核对完整训练目录中 `config.yaml` 的实际四个日期，并校验
   pickle → sidecar → config 的摘要链；不能换一个更容易通过的验证窗。
   侧文件或配置证据缺失、损坏或日期不符时不加载模型、不评分，但仍写出
   FAIL 工件，IC 留空并说明“未测量”。模型文件本身缺失或不可读仍属于
   工具错误，不会生成门禁工件。保留完整训练目录，不要手改摘要或日期绕过检查。
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
   备份和安装之前，还会把成员门工件的验证日期与摘要绑定的真实训练
   配置逐项比对。旧 PASS 工件也不豁免；日期不符即拒绝，原 manifest
   不变且不产生轮换备份。这里仍是 valid 窗的方向检查，不是独立未见
   test 窗的净收益认证，也不替代注册模型族和源码来源的治理检查。
   新成员的同一份已绑定侧文件还必须记录 `source_git_dirty: false` 和
   完整小写 `source_git_commit`；该提交须在本次执行已固定的主线版本
   历史中（或等于该版本）。缺失、脏工作区、未合并来源或 Git 查询失败
   都会在模型加载和安装前拒绝，即使旧门工件为 PASS。查询最多等待
   30 秒，不自动拉取历史、不改写来源字段；原清单及门工件保留。
   这核对的是运行开始时记录的源码状态和轮换时的主线祖先关系，不证明
   训练前已经合并，也不证明参数属于注册模型族。它不追溯检查保留成员，
   不给每日荐股增加 Git 依赖。锁会释放，但锁文件按原设计保留以避免竞争。
   另有**注册配置核验**：新成员的真实配置还须与同一固定主线版本的
   `config.yaml` 及三份 bootstrap preset 的共同模型族一致。仅排除六个
   train/valid/test 日期字段和结构字段 `extends`，日期仍受原有规则约束；
   股票池、基准、三守卫、GPU、模型/超参数及既有 same-family 字段均须
   匹配。布尔字段必须是字面的 `true/false`，数字 `1/0` 不可替代；数字参数
   也不可用布尔值代替。纯数字的等价写法（如 `50` 与 `50.0`）仍接受。
   数据目录按已提交模板的默认值和 qlib 路径规范化比较，本机
   环境变量或未提交 YAML 不能改变标准；路径相同不代表数据快照相同。
   注册文件缺失、格式错误、字段缺失、三份 preset 不一致或任一读取
   超过 30 秒均拒绝。该核验同样在加载、备份和安装前执行，不重做
   策略认证，不追溯保留成员，也不授权通过改写训练证据绕过拒绝。
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
