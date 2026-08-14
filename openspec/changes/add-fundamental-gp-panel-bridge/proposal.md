# Fundamental GP panel bridge + leakage defenses (阶段8 基本面方向 · 第2步)

## Why

四条独立证据线已证明量价因子在 CSI800 上到头（阶段8 GP 穷尽搜索只收敛到换手率变体
pv001，加进完整策略后边际贡献为负，被纪律拦截）。战场转向基本面/质量信息源。

勘察（2026-08-13）确认地基已就位：财报 PIT 层（版本保留 store + PIT 契约 +
`FinancialPITDataView`）已投产，CSI800 财报已 ingest（627→2142 issuers），覆盖率
经实测不劣于 CSI300（19 项里 16 项 floor 更严或持平，仅 3 项更松且均为已知弱字段）。

**唯一缺的是把 as-of 财务值变成 GP 能吃的 date×instrument 面板的那一步 —— 它今天
不存在，也没有任何机器检查守它。**

这一步是整条链路的泄漏风险集中点。GP 会穷尽搜索并精准放大任何 PIT 泄漏：数据里若
有未来信息，它一定会挖出来，造出"OOS t=10 的神因子"，纯属泄漏。而财务数据与现有
量价终端在 PIT 性质上**根本不同**：

* 现有 `$pe`/`$turnover_rate` 等 daily_basic 终端按 `trade_date` 键入、当日可观测，
  面板化天然 PIT（`qlib_bin_builder` 直接写 bin）；
* 财务报表是**事件日期数据**，PIT 语义全在 `available_from_trade_date`（有效公告日
  之后**严格第一个**交易日）。最典型的泄漏写法就是按 `end_date`/报告期键入，或从
  公告日**当日**（而非严格次日）ffill —— 两者都会静默产生未来信息。

因此本 change 的核心主张：**面板化模块与它的泄漏防线必须同时落地，防线不可后补。**
模块尚不存在正是设计防线的最好时机 —— 一旦有了"能跑出因子"的桥而防线滞后，第一个
诱人的高 IC 结果就会成为放松防线的压力源。

## What changes

### 1. 面板化桥模块 + 它依赖的两处上游扩展

新增 `src/research/fundamental_panel.py`：把 `FinancialPITDataView.as_of` 的单日横
截面堆叠成 GP 消费的 `dict[field -> date×instrument DataFrame]`。

**gate 不需要改签**（这一点经实测成立）：

* 隔离 gate 的 reverse 向禁止的是 **`src/` 内非 `src/research/` 模块** import
  `src.research.*`（`tests/governance/test_financial_pit_view_isolation.py`）；桥模块
  本身在 `src/research/` 内，import view 完全合法；
* GP 引擎**通过参数接收** panel（`gp_engine.py:11`；`miner._build_pit_panel` 是既有的
  "外部构造 panel 再传入"范式）；
* 编排放在 `scripts/research/` 层 —— 不在 `src/` 下，不受 reverse gate 约束，
  `scripts/research/gate4a_ic_evaluator.py` 是既有先例。

数据以**参数**流入 GP 而非以 **import 依赖**流入，D5 gate 与隔离 gate 保持原样。

**但"零改动"是错的（codex #427 P1，本提案的核心更正）。** 初版由"不必改 gate"错误
推广出"不必改任何东西"，实测证否 —— 桥要能真正跑起来，还需要两处上游扩展：

* **GP 终端注册**（`src/factor_mining/` 非零改动）：`FeatureRegistry.V1` 只有 12 个
  终端（4 价 + 2 量 + 6 daily_basic），`grammar._random_leaf` 把 `allowed_terminals`
  与 `V1_*` 取交集，`Terminal` 拒绝注册表外的名字。因此裸财务字段名产生不出可用白
  名单，而改写成 `$…` 形式仍是未知终端 —— 无论面板怎么传，GP 都长不出基本面表达式。
  本 change 因此把**基本面终端注册**（新终端组 + 其类型/taint 规则 + 对应测试）纳入
  scope。这不削弱 D5：注册的是终端符号与类型，不引入任何 qlib/PIT import。
  **但新组必须在 `FeatureRegistry.V1` 默认集之外**（codex #427 r8 P1）：append 进 V1
  是最自然的实现，而 `pit_adapter._default_fields()` 正是 `tuple(FeatureRegistry.V1)`
  —— 所有 `fields=()` 的存量 PIT run 会开始索取非 qlib 字段；`GPEngine` 的
  `_allowed_terminals` 默认为 `None`（不限制），等于让存量战役能繁殖研究专用终端。
  新组只由基本面白名单激活，并以回归钉住"默认 V1 仍是既有十二个终端"。
  **而且 opt-in 还不够**（codex #427 r9 P2）：`_random_terminal_same_type` 的替换池
  只从 `V1_SCALE_FREE` / `V1_RAW_PRICE` 取再与白名单求交，只含基本面终端的白名单交出
  **空集**，而 `mutate_point` 把 `GrammarError` 吞掉返回原表达式 —— 整个基本面战役的
  点变异**静默退化为 no-op**（一个不出声就失效的搜索算子，比会报错的更坏）。替换池改
  为按**同类型的已注册终端**取，并加合成白名单回归；这条独立、**无条件**地把
  `gp_engine.py` 带进 scope。白名单合法地只含某类型**一个**终端时确实无从替换 ——
  那属于另一回事，须**可见地记录并报告**"该白名单下无可替换"，不得与成功变异
  不可区分地静默返回原式（codex #427 r10 P2）。
  **注册还不够，另有两处名字/形状的断点**（codex #427 r4 P1）：
  ① **终端名 ↔ charter 字段名**：`evaluator` 只认 `$` 开头并按字面查面板 key，而
  `financial_pit_view.as_of` 把 `$revenue` 判为 unknown charter field、只收裸名
  `revenue` —— 映射必须写进契约并做**端到端**测试（GP 生成的终端 → 桥 → view 字段）；
  ② **prior 期必须是一等公民**：`__prior` 只挂在面板旁边，AST 引用不到（evaluator 只
  消费已注册且在值 mapping 里的 key），于是**资产增长与 C3 应计根本写不出来** —— 而
  它们正是本 change 要跑通链路的起步因子。须落成带类型的 prior 期终端或"相邻报告期"
  算子，进面板 key 与战役白名单。
* **view 的公开 provenance 响应**（`src/research/financial_pit_view.py` 非零改动）：
  `as_of` 今天返回值列 + 可选的 `_report_period__<endpoint>` / `__prior`，**不返回
  `available_from_trade_date`**。若同时冻结 view，桥就只能读私有内部/裸 store，或从
  抽样日期与值变化去反推 —— 那正是"证据"该排除的东西。本 change 因此把**给 view 增
  加一个公开的、携带 provenance 的响应**纳入 scope，让证据由**唯一门**给出，而不是
  由桥私自推断。证据记的是"**服务了哪条披露**"，不是"值是否有效"：被服务记录的字段
  值为 NA 时**仍带其可用日**，只有尚无任何已公告记录时证据才为 NA（codex #427 r3
  P2）—— 否则"两期皆 NA"的字段在 (iii) 平移下值与证据都不变，会误拒正确实现。

桥模块的输出契约：面板 **加上每个 field 的 `available_from` 证据帧**（同形状），
**外加 view 已有的 `report_period` / `prior` provenance**（见 §1b）。证据帧不是可选
的调试品，而是防线 (i) 的机器可验对象。

### 1b. 面板必须携带跨端点与跨期的对齐 provenance

view 的契约明确：**各 endpoint 独立服务**，消费者须用 `include_report_periods` 防止
跨端点混季比率；差分类公式须用 `include_prior_period` 取相邻期。而起步三因子恰好两
者都要：GP/A 跨 income×balancesheet（比率必须同期），资产增长与 C3 应计要相邻期差分。

若面板只堆当前值（初版契约），最小链路会**静默算出混季比率**，或根本算不出差分。
因此面板契约扩展为携带这两种 provenance。

**但同期强制不能放在面板层**（codex #427 r2 P1）：桥在表达式产生之前运行，evaluator
又把每个终端各自解析为值帧，所以桥无从知道候选是否跨端点 —— 全局遮蔽会误杀合法的
同端点表达式，不遮蔽则混季比率仍可达。强制点因此放在**求值路径**（按该表达式实际
引用的终端判断），这使 `evaluator.py` 进入 scope。

**遮蔽必须落在终端层**：按该表达式引用的**端点集合**算联合对齐掩码，在任何算子消费
之前把各字段帧上"这些端点报告期不一致"的 (日期, instrument) 置 NA；单端点表达式的
端点集只有一个元素，天然不被遮蔽。**在内部节点上遮蔽两个方向都漏**：
① 父级滚动之下（codex #427 r3 P1）—— `ts_mean(div_safe($revenue, $total_assets), 5)`
在 T 可能同期，但滚动窗内更早日期的混季比率仍被平均进来；
② 滚动子节点之上（codex #427 r5 P1）—— `add(ts_mean($revenue, 5), ts_mean($total_assets, 5))`
的第一个跨端点节点是 `add`，到那时两个子树各自**已经**把错期历史聚合完了，T 日对齐
救不回来。终端层是唯一不留拓扑口子的位置；两种拓扑都要有回归用例。

**且 provenance 得有路径抵达求值。** 桥把 `periods` 作为第三个返回对象，而现有 GP 调用
是 `evaluate_factor(expr, panel, ...)` 只传值面板 —— 新纳入 scope 的 evaluator 根本
**拿不到** report periods（codex #427 r3 P1）。本 change 因此须定义并接线一个带
provenance 的面板/求值参数（或明确一个在求值前把 period 帧打包进 mapping 的 adapter）；
若这条通路需要动 `gp_engine.py` 的传参，则 `gp_engine.py` 也进 scope。

**且接线要覆盖每一个求值调用点，不止搜索路径**（codex #427 r4 P1）：`validator.py:157`
的 `_evaluate_segment` 与 `validator.py:293` 的 `filter_correlated` 都在自己的分段上
求值且都不带 provenance。若 provenance 变必需，验证会把每个基本面因子判为无效；若保持
可选，验证就在**未遮蔽的混季值**上算指标并据此裁决 —— 两种都让"决定晋升的指标"不是
"遮蔽定义的指标"。`validator.py` 因此一并进 scope（AGENTS.md：改契约必须迁移调用方与
测试）。**晋升入口同理，而且更硬**（codex #427 r5 P1）：`promote.py:376` 的
`build_panel_for_data` 只造 `(panel, fwd)`，386/389 行的 `validate_pool` /
`filter_correlated` 拿不到 `periods` —— 而这条是**真正做裁决**的路径。造面板的
adapter 与其调用方一并进 scope，并有端到端晋升测试。

**但在谈"谁记了什么"之前，先有一个可实现性问题**（codex #427 r11 P1）：
`build_panel_for_data` 在 `src/factor_mining/miner.py:395`，挖掘与晋升都直接调它；桥与
`FinancialPITDataView` 在 `src/research/`，而 `test_no_canonical_src_imports_research`
拒绝 `src/` 内任何非 research 模块导入 `src.research.*`。于是"该 adapter 自己重建基本面
面板 + 闸不改签"**没有可实现路径** —— 导入即破闸，不导入则造不出面板。解法是
**注入缝**：`run_mining` 与 `promote_run` 各接一个可注入的面板工厂（不传时行为与今天
完全一致），由 `scripts/research/` 的战役脚本注入 —— 那是唯一同时看得见两侧、且本
change 已用于编排的层（`gate4a_ic_evaluator.py` 是先例）。注入的工厂**消费 run 持久化
的契约**，所以下面的重建保证不因此打折；端到端测试走真工厂，不用绕过缝的替身。
**但缝本身要可核验**（codex #427 r12 P1）：注入意味着挖掘与晋升可以拿到**不同的
callable**，而配置值、data digest、store/日历指纹全都对得上 —— 晋升照样能在另一份
语义不同的面板上裁决。故 run 里记**工厂身份**，晋升时核对、不符即拒，并拒绝身份记录
之前的老 run。换 builder 与换数据一样会移动面板，待遇必须一致。
**而身份不能是自报元数据**（codex #427 r13 P1）：名字 + 版本串由工厂自己宣称，两个
语义不同的工厂报同一对值就能过关 —— 正好复现要堵的那个调包。身份须是 ①**受信代码**
（非工厂本身）对冻结实现及其影响行为的依赖算出的摘要，或 ②工厂**确定性 provenance
输出**的摘要、晋升时按 run 记录的输入重算比对；②更强，因为它绑的是**行为**而不是
关于行为的声明。并用时任一不符即拒。②要哈希**全部影响行为的输出**：值、可用性证据、
**以及 `periods`（当期与 prior 的报告期 provenance）**（codex #427 r14 P1）—— 漏掉
periods，两个工厂可以给出相同的值+证据、不同的报告期帧，过了身份校验后在**不同的
终端层对齐掩码**下裁决同一批表达式。**而②在扩窗晋升下还不够**（codex #427 r15 P1）：
`promote.py:357-363` 允许 `validation_end_date` 扩窗，晋升会评估挖掘 run 从未覆盖的
日期，按**原窗**重算的输出摘要对它们一无所知 —— 调包的 callable 可以在原窗逐位复现、
却在新增 OOS 日期上发散。扩窗晋升须由**二者之一**绑定（codex #427 r16/r17 P1）：
①实现/依赖摘要，或②由**独立可信过程**在晋升前落盘的预期有效窗输出摘要。被排除的是
第三种 —— **基线由被晋升的工厂自己产出**：`validation_end_date` 只在晋升时给出，挖掘
只可能存下原窗摘要（拿有效窗比它必然不等），而哈希"工厂自己在有效窗上的输出"等于
**自签证书**。两条可走的路都成立，因为基线都来自晋升不曾产出的地方；而扩窗下的输出
摘要基线**不能来自挖掘**（codex #427 r18 P1）—— 挖掘只覆盖原窗，拿它当基线要么放过
新增 OOS 日期、要么必然不等，故扩窗只能用那份独立可信的预授权基线。

**另有一处口径必须同步**（同轮 P1）：`run_mining` 另行取 `build_universe_mask(config)`，
其底座是 qlib membership 帧，**照样把金融 issuer 当成员**，而 view 已把它们剔除 ——
这些 cell 就留在 `evaluator._coverage` 的**分母**里永远算未覆盖，压低覆盖率，而覆盖率
喂给候选准入与适应度：候选会被一个「把它数据源拒绝服务的名字也算进去」的分母评判。
掩码须套用 run **持久化的**排除集（不是造掩码时重推，否则挖掘与晋升会漂移）。

**而"端到端"要成立，`DataConfig` 得先记全重建面板的输入**（codex #427 r6 P1）：
`promote_run` 只能把持久化的 `DataConfig` 交给 `build_panel_for_data`，而它今天只有
`pit_provider_uri` / `delisted_registry_path` / `universe_name` / 起止日 / `fields`，
**没有财报 store 路径、日历身份、金融排除集、基本面模式** —— 而 `FinancialPITDataView`
这三样在构造时就要。缺了就只能靠未记录的外部/全局依赖重建，等于"晋升裁决的数据没人
能证明与挖掘时相同"。这些输入进 run-bound 契约，并进其 load / hash / migration 范围。**而且光记值不够**
（codex #427 r7 P1）：store 或日历 artifact **就地刷新**时路径与身份都不变，只哈希配置
仍会把两份不同面板算作同一个 run。PIT 侧早有对策 —— `promote._verify_pit_binding`
挖掘时记内容指纹、晋升时按当前路径重算并拒绝漂移，且拒绝指纹记录之前的老 run；财报
store 与日历要有**等价的内容指纹与复核** —— 包括「挖掘前后各取一次、不符则在落任何
artifact 之前报错」那一半（codex #427 r13 P1）：`miner.py:520-542` 对 PIT 输入正是这么
做的，注释里写明"build 后才取，会把新身份记到用旧字节挖出的池上"。只取一次 + 晋升
复核挡不住**面板构建期间**的刷新。**晋升侧也要两次**（codex #427 r15 P1）：晋升自己
也重建面板并求值，那段窗口无人看守，期间刷新会让结果来自混合/新字节而身份仍对得上；
须在读面板前取一次、求值后且 `target_dir.mkdir()` 之前再取一次，不符即拒 —— 拒绝落在
碰生产之前。

**生产物化这条边界，本 change 选择"拒绝"而不是"接线"**（codex #427 r6 P1）：
`mined_factor_handler.py:213` 的 `evaluate_expression(entry.expr, resolved_panel)`
不带 provenance，基本面池走到那里要么炸、要么在**没有终端层掩码**的情况下物化 ——
一把尺裁决、另一把尺出厂。接线该消费者属后续 change（它还牵涉 bundle 输入），本
change 只把边界做成**机器可执行的 fail-loud 拒绝**（含基本面终端的池写入生产目录即
拒绝并点名后续 change），并测"纯量价池照旧放行"。文档注记不算边界。
**拒绝点落在写盘方**（codex #427 r7 P1）：`promote.py:394-403` 的
`survivor_pool.save(target_dir)` 才是往生产目录写的那一步，拒绝必须在它**之前**触发；
只放在 handler 里太晚 —— 那时基本面池**已经写进生产目录**了。handler 侧作纵深防御保留。
**而且要早于 `target_dir.mkdir()`**（codex #427 r8 P1）：mkdir 发生在组装 survivor pool
之前，"在 save 前拒绝"会留下一个**空的生产版本目录**，下次尝试撞上"目录已存在"直接
失败 —— 一次被拒的晋升就这样改动了生产并**永久吃掉那个版本号**。回归断言查的是
**目标路径本身不存在**，而不是"没写出 pool 文件"。

**另有一处致命实现细节**（同轮 P1）：view 把 instrument 归一化为 store 原生 `ts_code`
（`600000.SH`），而 factor_mining 面板与 forward-return 用 qlib 标签（`SH600000`），
两者**零交集**（repo 已有 `test_namespace_mismatch_refuses` 记录）。面板必须冻结为与
GP 输入同一命名空间，并以**逐列精确比对**的测试守住。

### 2. 四件套泄漏防线（与桥同一个 change 落地）

* **(i) as-of 断言** — 返回前自检的键**落在证据上而非值上**（codex #427 r4 P2）：
  r3 让"被服务记录值为 NA 仍带可用日"，若断言仍按"非 NA 值"筛，这批 cell 恰好全部
  逃检，提前公告且该字段为 NA 的记录能带着未来日期的证据出厂，与金丝雀①自相矛盾。
  属性层：合成小店上断言"该期 `available_from` 前**不被服务**（有更早已可用期就继续
  服务那一期、**没有则才 NA**，而**不是**一律把 cell 置 NA —— codex #427 r18 P1：写成置 NA 会在每次申报前后
  制造人为缺口，同时移动覆盖率与因子值）、自该日起等于 disclosure-of-record 值、
  **公告日当天仍服务前一可用期**（严格次日）、**只有尚无任何已可用期时才 NA**、
  restated 期仍服务 original"；运行时层：桥输出证据帧后自检 `(available_from <= T).all()`，并对抽样日期
  与边界日（公告日、前一日、后首个交易日）核对 `panel[T] == view.as_of(T)`。
* **(ii) 合成金丝雀（已按 codex #427 P1 重设计）** — 初版的"未来值金丝雀"是**装饰性
  的**：一个由 forward return 派生、却带着满足 `available_from <= T` 的**看似合法证据**
  的值，可用性断言无法与真实财报值区分；让 fixture 省略证据，测到的只是"拒绝无证据
  输入"，而同样的语义泄漏只要抄一份元数据就能存活。金丝雀必须腐蚀 builder **能真正
  **计算出**的不变量，因此定为两个：
  ① **提前公告**：证据 > 交易日 —— 直接违反 `available_from <= T`，可计算；
  ② **可用日单调性**：同一 instrument 的证据序列必须随交易日**非递减**（carry-forward
     只会前进到更新的已公告期）—— 未来信息回填会打破单调性，纯结构不变量，无需
     知道值的语义即可判定。
  **显式排除**"证据-值不同源"金丝雀（codex #427 r2 P1）：初次修订曾把它列为第三条，
  但 builder 没有独立的记录身份可用来识破"P₂ 的值贴着 P₁ 的 provenance"这个谎；若
  唯一路径被完全绕过，builder 更是根本不运行。值与 provenance 的对应性是 **view 的
  不变量**，由 canonical PIT battery 守，不在面板层重述 —— 否则那条测试只是在验证
  自己的 mock。
  **任何金丝雀存活到 factor_pool 准入 = 红。**
* **(iii) 公告日平移敏感性（最锋利的一把刀；断言按 codex #427 P2 分层）** — 把店内有效
  公告日整体后移 N∈{5,10,21} 交易日重建面板：
  - **先确认相关性**（codex #427 r9/r19 P2）：被平移的披露未必触及所请求的字段/端点/
    instrument —— 只取 revenue 的面板本就该无视一份纯资产负债表申报，此时值与证据
    都不变，无条件规则会**误拒正确实现**。相关性的正确定义是**平移跨过了某个采样日**：
    某条被请求字段的披露，其**原**可用日与**平移后**可用日之间至少含一个采样交易日。
    **且该判定只看源数据、绝不查重建面板**（codex #427 r20 P1）—— 用"基线服务而平移后
    不服务"来定义是**循环论证**：对公告日不敏感的构建器（按报告期取值）平移后照样服务
    同一条披露，相关性永远建立不起来、诊断永远 INCONCLUSIVE，"哈希不变即 REFUSE"
    **永不触发**，防线正好对它要抓的缺陷失效。面板输出只用于断言本身。
    「在某个采样日被服务」还不够 —— 原可用日与平移后可用日若落在采样日的同一侧
    （都早于首个采样日、或夹在同两个稀疏采样日之间），它相关且被服务，却没有任何
    采样 cell 会动。无跨越则报 **INCONCLUSIVE**（扩大采样或改用确定性 fixture），
    **不得**判 REFUSE —— 诊断不出结论不等于实现有罪。
  - **确认相关性后的断言：被服务的记录必须换人**（codex #427 r21 P1）。在跨越区间内的
    每个采样日，平移后的重建须服务**前一条**披露（无则 NA），基线服务跨越的那条 ——
    **不换即 REFUSE**（面板化根本没消费公告日，极可能按 `end_date` 键入 = 泄漏在别处）。
    这条不可绕过 —— 它从**行为**上验证公告日真被消费。
    **只比"值+证据"的哈希挡不住**：按报告期取值的构建器可以继续服务同一条（仍属未来的）
    披露与其值，只把该记录平移后的 `available_from` 抄进证据 —— 字节变了、哈希变了，
    而值的选择依旧对公告日盲目。**证据可以抄，"服务了哪一期"抄不了。** 值在平移前后
    允许相等（延迟申报可能重复上期同值、或两期皆 NA），故不要求值不等；哈希可留作
    记录但不作判据。
  - **相关性只能建立在 disclosure-of-record 选择之后的源行上**（codex #427 r21 P2）：
    原始行含再公告与 `update_flag=1` 重述，view 从不服务它们；让这类行确立相关性，
    就会要求正确面板做出它本不该做的移动。
  - **条件断言**：IC 序列的变化只在**刻意构造的确定性 fixture** 上要求（该 fixture 保
    证平移会改变被评估日的取值或横截面 rank），并明确容差。理由：一个**正确的**
    公告日感知实现也可能只改字节不改 IC —— 平移后的披露仍落在采样评估日之间、rank
    未变、或候选本身为常数/全缺失。无条件要求两者都变会误拒合法实现。
* **(iv) prereg 门挂接** — 上述测试进战役门的 PIT battery（沿 `--extra-pit-case` 的
  **append-only、不可替换**语义）；新增两个 rehearsal 场景（注入探针 / 金丝雀存活均须
  致门 REFUSE，仿 R6）；桥模块与平移脚本进 `FROZEN_ARTIFACTS`；终端集经
  `grammar.allowed_terminals` 白名单冻结（pv_incremental 先例），使 GP 只能在预注册的
  基本面字段上繁殖。

### 3. PIT 行业 as-of 接口预留（本 change 只留接口，不实现）

行业中性化已定走**路径 B（真 PIT range 模式）**：静态快照的 PIT 不正确性正好会被 GP
放大（用 2026 行业标签给 2018 截面分组 = 系统性未来信息），花大力气把财报 PIT 做到
0.1-0.4% 残余，不能在行业这一步引入跨年污染。

路径 B 本身是**独立的后续 change**（publisher v2 保留 `in_date`/`out_date` 发 range
artifact + range 模式 as-of 消费者 + `within_industry_rank` + 一次性抓取签收 + 新注册）。
但本 change 必须**预留接口**以免返工：桥模块的面板构造签名接受一个可选的
"as-of 分组标签解析器"（`(trade_date, instruments) -> labels`），今天传 `None`，将来
接 PIT 行业 artifact 时无需改动桥的形状或其防线。

## Impact

* 新增：`src/research/fundamental_panel.py`（桥）、其测试、金丝雀测试、平移敏感性
  诊断脚本（`scripts/research/`）。
* **修改**（初版误称零改动，codex #427 逐轮更正至此）：
  - `src/factor_mining/grammar.py` —— 注册基本面终端组 + 其类型/taint 规则；
  - `src/factor_mining/evaluator.py` —— 跨端点同期强制（见 §1b：必须在**知道表达式**
    的地方做，面板层做不到）；
  - `src/research/financial_pit_view.py` —— 增加公开的 provenance 响应，使
    `available_from_trade_date` 由唯一门给出而非被桥反推。
  - `src/factor_mining/validator.py` —— 两条求值调用点（`_evaluate_segment` /
    `filter_correlated`）接 provenance，使验证与搜索用同一把尺；
  - `src/factor_mining/promote.py` 与其造面板 adapter `build_panel_for_data` ——
    晋升入口带 provenance，使**做裁决的那条路径**用的也是同一把尺；
  - `src/factor_mining/miner.py` —— `DataConfig` 补全重建基本面面板所需输入并纳入
    load / hash / migration；`run_mining` / `build_panel_for_data` 增加面板工厂
    **注入缝**（不注入时行为不变），使桥不必被非 research 模块导入；
  - `src/data/mined_factor_handler.py` —— 纵深防御的 fail-loud 拒绝（只加拒绝，
    不接 provenance；接线属后续 change；**主拒绝点在 `promote.py` 的写盘前**）；
  - `src/factor_mining/gp_engine.py` —— **无条件改动**：点变异替换池必须从"同类型的
    已注册终端"取（`_random_terminal_same_type`），否则基本面白名单下点变异静默失效
    （codex #427 r10 P2：这条与 adapter 方案无关，任何实现都得改）。**此外**，period
    provenance 的传参通路**仅当**必须经由它时才一并改（见 §1b 末段）；若 adapter 方案
    足够，改动就只有替换池这一处。

  上述 `src/factor_mining/` 内的各处**均不引入任何 qlib/PIT import**（加的是终端
  符号、类型、传参与拒绝逻辑），D5 gate 不受影响；`src/data/mined_factor_handler.py`
  本就不在 D5 管辖内，且本 change 只给它加一条拒绝。
* **仍然零改动**：`src/factor_mining/pit_adapter.py`、`src/data/pit/*`、canonical
  runtime。
* **零 gate 改签**：D5 gate 与财务 PIT 隔离 gate 均保持原样并继续通过。
* 新 spec capability `v2-fundamental-gp-panel`：面板化的 PIT 契约 + 防线要求。
* 不训练、不挖因子、不动生产数据：本 change 只交付桥与防线；真正的基本面 GP 战役是
  后续的预注册包（新 charter + 新未触碰窗 + 新 ledger）。
