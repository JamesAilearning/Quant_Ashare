# 每日决策 Cockpit — UI 工单(Gate-3 冻结后交 CC 起 OpenSpec)

> **Status:** backlog / 待 CC 在 Gate-3 冻结后拾取。**低优先并行,不抢主线(Gate-4A)。**
> **性质:** 本文件 = 工单 / 设计输入。CC 据此走 `/opsx:propose` 起 OpenSpec change,再实现。
> **归属:** `web/` 层(operator-facing only,AGENTS.md 层边界);不碰 canonical runtime。

## 1. 为什么

今日决策页(#330,`web/operator_ui/pages/daily_decision.py`)现在是 **只读查看器**:读 `output/daily_recommend/` 的 `daily_recommendation_*.json` + 决策日志,页头明写"本页不重跑推断、不触发任何作业;推荐由 `scripts/daily_recommend.py` 晨间产出"。跑 daily_recommend 仍是 CLI;UI 的 `job_runner` 只支持 `pipeline` / `walk_forward` 两种 job。

把"**跑 + 看 + 记决策**"合到一处能明显改善每早实操。但 daily_recommend 的价值在于 **fail-closed 守卫**,UI 化不能把这个信任语义做没了(见 §3 红线)。这是 serving / ergonomics 便利,**不是研究主线**。

## 2. 分两期(各自独立可验收 PR)

- **P1 — Run-from-UI(先做,小):** 在 UI 里手动触发 daily_recommend,守卫透明地显示结果,喂现有查看器。
- **P2 — 三分推荐(方案乙,后做,大):** 真实持仓输入 + 买入/持有/卖出三分;需新数据面(持仓 ∩ 今日信号比对)。

## 3. ★ 非可协商红线(两期都守)

daily_recommend 的核心信任信号 = **exit 0 且出清单 → 所有守卫(前视/陈价/ST源/完整性)都过了**。UI 跑**不能做成"点一下出清单"的黑箱**,必须把 CLI 强制你看的东西照样摆出来:

1. **按 exit-code 分二态渲染**:成功(guards 全过)显示清单;**refuse(exit 1)显示 domain 错误原文 + 修法**(bundle STALE / 无完整性戳 / ST 源陈旧 / T 无 T+1 …),**绝不吞错、绝不降级成空清单**。
2. **`entry_date` 摆醒目位**:默认 T+1 = bundle 尾、**已完成** session,**不是明天开盘**;UI 必须标清"这是给哪个 session 的清单",不许让人误读成"明早买"。
3. **漏斗** `scored / untradable_masked / st_excluded` + `buy_list` 照显示;funnel 与审计表(`_scored_full.csv`)可下钻查"某只为啥被剔"。
4. **仍是人工手动触发**(按钮 = 人点,不是自动链;**绝不 schedule daily_recommend** —— runbook 红线,阶段5 PR-P 只跑 data update)。
5. **降级开关**(`--allow-holey-recommend` / 抬 `--bundle-max-age-days`):UI 里默认不暴露或深折叠,一旦启用**显著红色警告 + 二次确认**(它们让清单对交易不可信、只供研究)。
6. **决策辅助非订单**:不下单、不定仓位;买卖仍人定。

## 4. P1 详细 spec —— Run daily_recommend from UI

### 4.1 范围
- `job_manager` / `job_runner` 加第三种 job mode `daily_recommend`(现 `JobMode = "pipeline" | "walk_forward"`),子进程跑 **`scripts/daily_recommend.py`**(**用 `scripts/` 路径、不用 `python -m`**:Windows `__main__` guard + `freeze_support()` 必需,否则 joblib fork 炸)。
- **run 表单**(今日决策页顶部或新 tab):
  - 常用:`topk`(默认 50)、`instruments`(默认 csi300)、`--as-of`(默认空 = 最新)、`out-dir`(默认 `output/daily_recommend`)。
  - 高级(折叠 + 红):`--bundle-max-age-days`、`--st-max-age-days`、`--allow-holey-recommend` —— 默认不动,动了即按红线 5 警告。
- **执行**:流式显示 stdout/stderr(复用 job 日志流);结束按 exit-code 二态渲染(红线 1)。
- **成功 → 产物落 `output/daily_recommend/`** → 现有查看器自动选到并渲染(**工件流不变**,复用现成渲染 + 决策日志)。

### 4.2 复用(别重造)
- 复用现有 job 子进程框架(`job_runner` 的 subprocess + 日志流 + run dir)。
- 复用现有 `daily_decision.py` 查看器 —— **不改它"只读渲染"的契约**;run 是**新动作**,产物落盘后由查看器读(run 与 view 仍分离,只是同页可达)。
- 复用 `training_guards.py` 已有的完整性戳检查(它已知 daily_recommend 会 refuse holey bundle)。
- 模型/路径默认沿用 `_daily_decision_helpers.py` 现有的 `DEFAULT_MODEL_PATH` / env(`QUANT_PROVIDER_URI` 等)约定。

### 4.3 治理测试(BLOCKING)
- job mode 注册 + 参数透传:表单值正确到子进程 argv(`topk`/`--as-of`/`instruments`)。
- **exit-code 二态**:mock exit 1 + domain 错误 → UI 显示错误 + 修法、**不显示清单**;exit 0 → 显示清单 + `entry_date` + 漏斗。
- **`entry_date` 必显**:产物缺该字段 → UI 报警,不静默。
- 降级开关默认关;启用时警告 + 二次确认存在。
- **不进自动链**:断言 daily_recommend 未被任何 scheduler / 自动作业调用(呼应 runbook + 阶段5)。

### 4.4 不许碰
- 不改 daily_recommend CLI/脚本的守卫语义(UI 只包一层触发 + 展示,守卫仍在脚本内)。
- 不下单、不定仓;不动 canonical runtime / 训练 / 回测 / Alpha158 模型。

## 5. P2 详细 spec —— 三分推荐(方案乙,后做)

> 来自交接文档 §5 的设计要点,原样落成 spec。P2 依赖 P1 的 run + 查看器。

### 5.1 数据面(新)
- **持仓来自用户真实输入**(UI 表单 / 上传);**不做系统推演**(推演漂移会误导——交接明确)。
- **比对引擎**:当前持仓 ∩ 今日 daily_recommend 候选/打分 → 生成三分。

### 5.2 三分逻辑
- **买入**:在今日候选、不在持仓 → **诚实标注**"IC ~0.02 微弱信号,不是确信会涨"。
- **持有**:在持仓且仍在候选(或分数高于卖出边界)。
- **卖出**:在持仓但跌出候选 / 低于边界 **N**(**N 可配** —— 用户可能持仓 10 只而非 50)。
- **边界平手缓冲**:分差 < 阈值 → 标"可不执行"(压噪声换手)。
- **停牌股**:统一 `suspended` / `one_price` 标签**单列**,不混进买/卖。
- **宇宙外持仓**(非当前 universe)→ 标"超出模型覆盖范围",**绝不给排名 / 买卖建议**(交接红线)。

### 5.3 复用 / 治理
- 复用 P1 的 run + 查看器;三分是查看器上的一层(读同一产物 + 用户持仓)。
- 现有决策日志(`adopt`/`reject`/`watch`)→ 扩成买/持/卖(或并存)。
- 治理测试钉死:持仓输入不落 canonical、不推演;**宇宙外绝不排名**;停牌单列。

## 6. OpenSpec 框架

- P1 / P2 各自一个 change(建议 `add-ui-daily-recommend-run` / `add-ui-three-way-recommend`),或一个 cockpit capability 下两个 phase —— CC 起 propose 时定。
- 归属/扩展 spec:`v2-operator-ui` / `v2-daily-decision-page`。
- 全程 `web/` 层、operator-facing;不碰 canonical runtime(AGENTS.md 层边界)。
- push 前跑本地 review loop(`docs/codex/local-review-loop.md`)。

## 7. 时机

Gate-3 已冻结 → 现在主线是 **Gate-4A(因子验证)**。本工单排在 **Gate-4A 之后或其空窗**,**P1 先于 P2**。不抢主线的卡。

## 8. 审注（codex #362 入库 review，append-only；原文未动）

实现 P1 时两条为 BLOCKING 约束，与 §3 红线同级：

1. **任何非默认守卫放宽下的 exit 0 都不等于"守卫全过"**（r2 扩展：
   不止 `--allow-holey-recommend`——`--bundle-max-age-days` /
   `--st-max-age-days` 抬高同样会让默认守卫本应拒绝的输入通过）：
   `_assert_bundle_fetch_complete` 在 override 下刻意放行缺失/带洞
   完整性戳（`src/inference/daily_recommend.py:373-430`），且 run meta
   目前**不落盘任何放宽标志/阈值**（`_assemble_run_meta`）。实现要求：
   **全部非默认守卫放宽（开关+实际阈值）持久化进工件 meta**；UI 渲染
   按 meta（而非表单瞬时态）区分——降级成功 = 红色横幅 + "清单仅供
   研究"，页面重开/工件重读后该状态必须保持；回归测试覆盖"重开已
   降级工件仍渲染为降级"。
2. **同日重复触发必须串行化**：`write_outputs` 直接写三个确定性
   `daily_recommendation_<as_of>` 文件（`daily_recommend.py:966-1007`），
   两个操作员或重复点击同一 as-of 会交错/覆写。job mode 需按 as-of
   加锁（同日已有 run 在跑即拒绝新触发，或原子写+末次完整性校验），
   治理测试补对应断言。
3. **out-dir 必须锁定 canonical 工件根**（r2）：查看器只枚举
   `output/daily_recommend`（`list_recommendation_artifacts`），表单若
   允许自定义 `out-dir`，成功产物会落在查看器视野之外——"自动选中
   渲染"承诺失效、操作人停留在旧工件上。实现三选一：移除该表单字段 /
   锁死为 canonical 根 / 为自定义位置定义受守卫的读取行为——默认推荐
   移除。
