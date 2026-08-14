# Tasks

## 上游扩展（codex #427 P1：初版误称"零改动"，这两项是桥能跑起来的前置）

- [ ] `src/research/financial_pit_view.py`：`as_of` 增加**公开的 provenance 响应**
      （每个服务字段的 `available_from_trade_date`，与值**同一次调用**返回）。没有它，
      桥只能读私有内部/裸 store 或反推证据 —— 而反推正是"证据"该排除的东西。
      配套测试：值与 provenance 同源；**被服务记录的字段值为 NA 时仍带其可用日**
      （证据记的是"服务了哪条披露"，不是"值是否有效"；只有尚无任何已公告记录时
      证据才为 NA）——否则"两期皆 NA"的字段在公告日平移下值与证据都不变，
      平移诊断会误拒正确实现（codex #427 r3 P2）。
- [ ] `src/factor_mining/grammar.py`：注册**基本面终端组** + 其类型/taint 规则。
      现状 `FeatureRegistry.V1` 仅 12 个终端，`_random_leaf` 与 `V1_*` 取交集、
      `Terminal` 拒绝表外名字 —— 只靠参数传面板，GP **长不出**基本面表达式。
      D5 不受影响（只加终端符号与类型，不引入 qlib/PIT import），须有对应闸测试。
      **且必须在 `FeatureRegistry.V1` 默认集之外、只由基本面白名单激活**
      （codex #427 r8 P1）：直接 append 进 V1 是最自然的实现，但
      `pit_adapter._default_fields()` 就是 `tuple(FeatureRegistry.V1)` —— 所有
      `fields=()` 的存量 PIT run 会开始索取非 qlib 字段；而 `GPEngine` 的
      `_allowed_terminals` 默认 `None` = 不限制，等于让存量战役能繁殖研究专用终端。
      须加回归断言：默认 V1 面板仍是既有的十二个终端，新组 opt-in。
      **opt-in 还不够，点变异池得一起迁移**（codex #427 r9 P2）：
      `gp_engine._random_terminal_same_type`（`gp_engine.py:557-572`）的替换池只从
      `FeatureRegistry.V1_SCALE_FREE` / `V1_RAW_PRICE` 取，再与白名单求交 —— 只含
      基本面终端的白名单交出来是**空集**，`mutate_point` 又 `except GrammarError:
      return expr`，于是整个基本面战役的点变异**静默退化为 no-op**。替换池改为按
      **同类型的已注册终端**取（而非只认 legacy 组），并加合成白名单回归。
      因此 `gp_engine.py` 不再只是"传参可能要动"——这条**无条件**把它带进 scope。
      白名单合法地只含某类型**一个**终端时无从替换：那要**可见地记录并报告**
      "该白名单下无可替换"，与"池搭错了"区分开，不得静默返回原式
      （codex #427 r10 P2）;变异回归用**至少两个**同类型白名单终端来测。
- [ ] **终端名 ↔ charter 字段名的映射**（codex #427 r4 P1）：注册终端还不够 ——
      `evaluator` 只认 `$` 开头并按字面查面板 key（`evaluator.py:82`），而
      `financial_pit_view.as_of` 把 `$revenue` 判为 unknown charter field
      （`_FIELD_ENDPOINT` 白名单）、只收裸名 `revenue`。须把映射写进契约：桥收 charter
      名、发终端形 key；测试要**端到端**（GP 生成的终端 → 桥 → view 字段值），不是
      注册表和 view 各自能跑；无映射的终端**fail-loud**，不得静默丢字段。
- [ ] **prior 期做成一等公民**（codex #427 r4 P1）：`__prior` 只挂在面板旁边是**长不出
      表达式**的 —— evaluator 只能消费已注册且在值 mapping 里的 key，AST 根本引用不到
      它，于是起步三因子里的**资产增长与 C3 应计写不出来**（而"跑通链路"任务又要求
      它们跑）。须落成带类型的 prior 期终端或"相邻报告期"算子，进面板 key 与战役
      白名单，并测试 **GP 能生成** + **求值确为相邻期差分**（相邻期缺失 → NA）。

## 桥模块（研究侧）

- [ ] `src/research/fundamental_panel.py`：`build_fundamental_panel(view, fields,
      trade_dates, instruments, *, group_resolver=None)` → `(panels, evidence,
      periods)`；逐交易日 `view.as_of`（**一次调用同时取值、provenance、报告期**），
      实例内缓存已由 view 提供（只付过滤成本）。
- [ ] 返回前自检：`available_from <= trade_date`，**键在证据上而非值上**
      （codex #427 r4 P2）—— 每个**非 NA 证据** cell 都要查。若按"非 NA 值"来查，
      r3 引入的"被服务记录值为 NA 仍带可用日"那批 cell 恰好全部逃检，提前公告且
      该字段为 NA 的记录就能带着未来日期的证据活着出厂，与金丝雀①自相矛盾。
      违反即 raise；无法建立证据的字段 → 拒绝返回面板（不返回"无证据面板"）。
- [ ] **跨期 provenance**：面板携带每个字段的 `report_period`
      （用 `include_report_periods`）与差分类字段的 `__prior` 及其自身 provenance
      （用 `include_prior_period`）。
- [ ] **命名空间冻结**（codex #427 r2 P1）：面板 instrument 标签必须与 GP 面板
      /forward-return **同命名空间**。view 归一化为 store 原生 `ts_code`
      （`600000.SH`），而 factor_mining 面板用 qlib 标签（`SH600000`），两者
      **零交集** —— repo 已有 `test_namespace_mismatch_refuses` 把这种混用记为硬
      错误。桥必须转换并加测试**逐列精确比对** forward-return 的列（不是"有交集"）。
- [ ] **跨端点同期强制放到 expression-aware 层**（codex #427 r2 P1）：面板层做不到 ——
      桥在表达式产生**之前**运行，`evaluator` 又把每个终端各自解析为值帧，所以桥无从
      知道候选是否跨端点：全局遮蔽会误杀合法的同端点表达式，不遮蔽则混季比率仍可达。
      改为在**求值路径**上做，遮蔽落在**终端层**：按该表达式引用的**端点集合**算联合
      对齐掩码，在**任何算子消费之前**就把各字段帧上"这些端点报告期不一致"的
      (日期, instrument) 置 NA。单端点表达式的端点集只有一个元素，天然不被遮蔽。
      **任何内部节点上遮蔽都太晚，两个方向都漏**：
      ①父级滚动之下 —— `ts_mean(div_safe($revenue, $total_assets), 5)` 在 T 可能同期，
      但窗口内更早日期的混季比率被卷进来（r3 P1）；
      ②滚动子节点之上 —— `add(ts_mean($revenue, 5), ts_mean($total_assets, 5))` 的
      第一个跨端点节点是 `add`，到那时两个子树各自已把错期历史聚合完了（r5 P1）。
      终端层是唯一不留拓扑口子的位置。两种拓扑都要有回归用例。
      这意味着 `src/factor_mining/evaluator.py` 在 scope 内。
- [ ] **把 period provenance 接进求值路径**（codex #427 r3 P1）：桥把 `periods` 作为
      第三个返回对象，而现有 GP 调用是 `evaluate_factor(expr, panel, ...)` 只传值面板 ——
      新纳入 scope 的 evaluator **没有途径拿到** report periods。须定义并接线一个带
      provenance 的面板/求值参数（或明确一个 adapter，在求值前把 period 帧打包进
      mapping）；若这条通路需要动 `gp_engine.py` 的传参，则它也进 scope。
- [ ] **接线要覆盖每一个求值调用点，不止搜索路径**（codex #427 r4 P1）：
      `validator.py:157` 的 `_evaluate_segment` 调 `evaluate_factor(expr, seg_panel,
      seg_fwd, ...)`、`validator.py:293` 的 `filter_correlated` 调
      `evaluate_expression(entry.expr, panel)`，两处都不带 provenance。若 provenance
      变成必需，验证会把**每个**基本面因子判为无效；若保持可选，验证就在**未遮蔽的
      混季值**上算指标并据此裁决 —— 两种都让"决定晋升的指标"不是"遮蔽定义的指标"。
      验证的分段切分与两条求值路径一并进 scope，测试同步迁移（AGENTS.md「改契约必须
      迁移调用方与测试」）。
- [ ] **晋升入口也要接**（codex #427 r5 P1）：`promote.py:376` 的
      `panel, fwd = build_panel_for_data(config.data)` 只造 `(panel, fwd)`，随后
      386 行 `validate_pool` 与 389 行 `filter_correlated` 都拿不到 `periods`。
      这条是**真正做裁决的路径** —— provenance 可选则晋升在未遮蔽值上裁决，必需则
      晋升直接失败。造面板的 adapter（`build_panel_for_data`）与其调用方一并进 scope，
      并有端到端晋升测试。
- [ ] **注入缝：面板构造器由脚本层传进来，不由 `src/factor_mining/` 导入**
      （codex #427 r11 P1）：`build_panel_for_data` 在 `src/factor_mining/miner.py:395`，
      挖掘与晋升都直接调它；而桥与 `FinancialPITDataView` 在 `src/research/`，
      `test_no_canonical_src_imports_research` 拒绝 `src/` 内**任何**非 research 模块
      导入 `src.research.*`。所以"该 adapter 自己重建基本面面板 + 闸不改签"这组要求
      **没有可实现路径**：导入即破闸，不导入则根本造不出面板。
      解法 = `run_mining` 与 `promote_run` 各接一个**可注入的面板工厂**（不传时行为
      与今天完全一致），由 `scripts/research/` 的战役脚本注入 —— 那是唯一同时看得见
      两侧、且本 change 已用于编排的层。注入的工厂**消费 run 持久化的契约**，
      重建保证不因此打折。端到端测试必须走**真工厂**，不得用绕过缝的替身。
      （对应用户既定治理：测试没拦住 ≠ 允许 —— 这里测试拦得住，只能走缝。）
      **缝不能变成不可核验的自由度**（codex #427 r12 P1）：注入意味着挖掘与晋升
      可以拿到**不同的 callable**，而配置值、data digest、store/日历内容指纹**全都
      对得上** —— 晋升照样能在另一份语义不同的面板上裁决。故 run 里要记**工厂身份**，
      晋升时与拿到的工厂核对，不符即拒，并拒绝工厂身份记录之前的老 run
      （换 builder 与换数据一样会移动面板，待遇必须一致）。
      **身份不得是工厂自报的元数据**（codex #427 r13/r14 P1）：名字 + 版本串由工厂
      自己宣称，两个语义不同的工厂报同一对值就能过关 —— 正好复现要堵的那个调包。
      身份须是二者之一（并用时任一不符即拒）：
      ① 由**受信代码**（不是工厂本身）对冻结实现及其影响行为的依赖算出的摘要；
      ② 工厂**确定性输出**的摘要，晋升时按 run 记录的输入重算比对 —— 且要哈希
      **全部影响行为的输出**：值、可用性证据、**以及 `periods`（当期与 prior 的
      报告期 provenance）**。漏掉 periods 则两个工厂可以给出相同的值+证据、不同的
      报告期帧，通过身份校验后在**不同的终端层对齐掩码**下裁决同一批表达式
      （codex #427 r14 P1）。②更强 —— 它绑的是**行为**而非关于行为的声明。
- [ ] **金融排除要一并进宇宙掩码**（codex #427 r12 P1）：`run_mining` 另行取
      `build_universe_mask(config)`（`miner.py:423/526`），其底座是 qlib membership
      帧，**照样把金融 issuer 标记为成员**；而 view 已把它们全部剔除。于是这些名字
      的 cell 留在 `evaluator._coverage` 的**分母**里、永远算作未覆盖，压低覆盖率 ——
      而覆盖率喂给候选准入与适应度。必须用 run **持久化的**排除集（而非造掩码时
      重新推导，否则挖掘与晋升会漂移）同样地裁掉，并测覆盖率分母。
- [ ] **`DataConfig` 要记全重建面板的输入**（codex #427 r6 P1）：`promote_run` 只能把
      持久化的 `DataConfig` 交给 `build_panel_for_data`，而 `DataConfig`（`miner.py:42`）
      只有 `pit_provider_uri` / `delisted_registry_path` / `universe_name` / 起止日 /
      `fields` —— **没有财报 store 路径、没有日历身份、没有金融排除集、没有基本面
      模式**，而 `FinancialPITDataView` 这三样都在构造时就要。缺了它们，晋升只能靠
      未记录的外部/全局依赖去重建面板，"端到端晋升测试"要么用不了真 adapter、要么
      验的是另一份数据。这些输入进 run-bound 数据契约，并进其 load / hash / migration
      范围（hash 不覆盖 = 两份不同面板算同一个 run）。
- [ ] **内容指纹，不只是配置值**（codex #427 r7 P1）：store / 日历 artifact 被
      **就地刷新**时路径与身份都不变，只哈希扩展后的 `DataConfig` 仍会把两份不同面板
      算作同一个 run。照 PIT 侧既有做法办 —— `promote._verify_pit_binding`
      （`promote.py:259`）在挖掘时记内容指纹、晋升时按当前路径重算并拒绝漂移，且拒绝
      指纹记录之前的老 run。财报 store 与日历要有等价的内容指纹与复核。
      **且必须照搬「挖掘前后各取一次」那一半**（codex #427 r13/r14 P1）：
      `run_mining` 对 PIT 输入已经是 build **前**取一次、挖完**再**取一次、不符则
      **在落任何 artifact 之前**报错（`miner.py:520-542`，注释写明「build 后才取，
      会把新身份记到用旧字节挖出的池上」）。只记一次 + 晋升复核挡不住**面板构建
      期间**的刷新：run 会把新字节的身份记给用旧/混合读挖出的池，此后每一项检查
      都通过，而晋升重建的是另一份面板。财报 store 与日历要有同款前后稳定性检查，
      两次不符即在持久化任何 artifact 之前拒绝。
- [ ] **生产物化边界:机器可执行的拒绝**（codex #427 r6 P1）：
      `src/data/mined_factor_handler.py:213` 的 `evaluate_expression(entry.expr,
      resolved_panel)` 只吃 `FactorMiningDataView` 的 qlib 面板、不带 period
      provenance。基本面池若走到这里，要么因财务终端无法解析而炸，要么（若注入面板）
      **在没有终端层对齐掩码的情况下物化** —— 用一把尺裁决、用另一把尺出厂。
      接线该消费者**不在本 change 范围**（本 change 只交付桥与防线），因此本 change
      须把这条边界做成**机器可执行的 fail-loud 拒绝**：含基本面终端的池写入生产因子
      目录即拒绝，并在报错里点名"要先落地哪个后续 change"。**文档注记不算边界** ——
      拒绝必须可执行且有测试；同时测"纯量价池照旧放行"（非空性）。
      **拒绝点必须落在写盘方，且早于 `target_dir.mkdir()`**（codex #427 r7/r8 P1）：
      `promote.py:394-403` 里 `target_dir.mkdir(parents=True, exist_ok=True)` 发生在
      组装 survivor pool **之前**，所以"在 `save` 前拒绝"仍会**留下一个空的生产版本
      目录** —— 下一次尝试撞上"目录已存在"直接失败，等于一次被拒的晋升**改动了生产
      并永久吃掉那个版本号**。拒绝必须早于 mkdir，回归断言要查**目标路径本身不存在**
      （不是"没写出 pool 文件"）。只放在 `mined_factor_handler` 里太晚 —— 那时晋升**已经把基本面池写进
      生产目录了**，拒绝要等到某次物化才响、甚至永远不响。handler 侧的检查可作为
      纵深防御保留，但不能替代写盘方的拒绝。
- [ ] `group_resolver` 参数今天恒传 `None`（PIT 行业 artifact 属后续 change）；
      签名与文档写明"绝不以当前快照兜底"。
- [ ] 性能：先测全历史 × CSI800 的墙钟时间；若逐日 Python 循环过慢，改为按
      `available_from_trade_date` 的 `merge_asof` 向量化 —— **但等价性须由 (i) 的
      抽样一致性断言守住**（两种实现必须给出同一面板）。

## 四件套防线（与桥同批落地，不可后补）

- [ ] (i) `tests/logic/research/test_fundamental_panel_pit.py`：合成小店属性测试 —
      公告前 NA / 严格次日起可见 / 公告日当天仍 NA / restated 期服务 original /
      missing-stays-missing；抽样 + 边界日（公告日、前一日、后首个交易日）核对
      `panel[T] == view.as_of(T)`。
- [ ] (ii) `tests/logic/research/test_fundamental_panel_canaries.py` —— **金丝雀已按
      codex #427 P1 重设计**：初版的"未来值金丝雀"是装饰性的（带着看似合法证据的
      未来派生值，可用性断言无法与真值区分；省略证据则只测到"拒绝无证据输入"）。
      改为两个 builder **能自行计算**的不变量：①提前公告（证据 > 交易日）
      ②可用日单调性（同一 instrument 的证据序列随交易日非递减；回填会打破它，
      纯结构判定、无需解释值的语义）。二者均断言"拒绝发生在进入因子评估之前"。
      **显式排除**"证据/值不同源"金丝雀（codex #427 r2 P1）：无独立记录身份时
      builder 无法判定该谎言，若唯一路径被绕过则 builder 根本不运行 —— 它是
      **view 的不变量**，由 canonical PIT battery 守，不在面板层重述。
- [ ] (iii) `scripts/research/fundamental_ann_shift_sensitivity.py`：公告日整体后移
      N∈{5,10,21} 交易日重建面板。**先确认相关性再断言**（codex #427 r9 P2）：
      被平移的披露未必触及所请求的字段/端点/instrument —— 只取 revenue 的面板本就
      该无视一份纯资产负债表申报，此时值与证据都不变，无条件规则会**误拒正确实现**。
      须先核实"至少有一条被平移的披露确实被该面板在某个采样日服务"，否则报
      **INCONCLUSIVE**（提示扩大采样或改用确定性 fixture），**不得**判 REFUSE。
      这条前提要**贯穿全部平移场景**（codex #427 r10 P2）：旧写法里"平移必改哈希"
      与"哈希不变即 REFUSE"两条若不带同一前提，就与 INCONCLUSIVE 规则直接冲突，
      实现与测试无法同时满足。确认相关性之后，断言**值+证据一起**的哈希必变
      （只哈希值会误拒：延迟的申报可能重复上期同值、或该字段两期皆 NA，
      值不变而 provenance 变 —— codex #427 r2 P2），**不变即 REFUSE**
      （报告"公告日未被消费"）。**IC 断言只挂在确定性 fixture 上**（该 fixture 构造成
      平移必然改变被评估值或横截面 rank）并写明容差 —— 正确实现也可能只改字节不改 IC
      （平移落在采样日之间 / rank 未变 / 常数候选），无条件要求两者都变会误拒合法实现
      （codex #427 P2）。
- [ ] (iv) prereg 挂接（随基本面战役的预注册包）：(i)(ii) 进该战役门的 PIT battery
      （append-only，canonical battery 不可替换）；两个 rehearsal 场景（注入探针 /
      金丝雀存活 → 门 REFUSE，仿 R6）；桥模块 + (iii) 脚本进 `FROZEN_ARTIFACTS`；
      终端集经 `grammar.allowed_terminals` 白名单冻结。

## 验证与边界

- [ ] 确认改动面与提案一致：`grammar.py`（终端注册 + 名字映射 + prior 期终端/算子）、
      `evaluator.py`（跨端点同期强制，终端层联合掩码）、`validator.py`（两条求值
      调用点接 provenance）、`promote.py` 与 `build_panel_for_data`（晋升入口带
      provenance）、`financial_pit_view.py`（provenance 响应）**有**改动；
      `miner.py`（`DataConfig` 补全重建面板所需输入 + 内容指纹 + 工厂身份 +
      hash/migration，给 `run_mining` / `build_panel_for_data` 加面板工厂注入缝，
      并让 `build_universe_mask` 套用持久化的金融排除集）、
      `promote.py`（写盘前拒绝 + 财报侧内容指纹复核）与 `mined_factor_handler.py`
      （纵深防御拒绝）**有**改动；
      `gp_engine.py`（点变异替换池迁移 = **无条件**改动，与 adapter 方案无关；
      provenance 传参通路**仅当**必须经由它时才一并动）；
      `pit_adapter` / `src/data/pit/*` / canonical runtime **无**改动。
- [ ] 确认 D5 gate 与 `test_financial_pit_view_isolation.py` 均照原样通过
      （**不改签**）—— 终端注册不引入 qlib/PIT import，桥仍在 `src/research/` 内。
- [ ] ruff + mypy --strict + 治理全套 + 新增测试全绿。
- [ ] 用起步三因子（① GP/A、③ 资产增长、② C3 纯 BS 应计 —— CSI800 覆盖率已实测全绿）
      跑通"面板化 → GP → 边际贡献"最小链路，**只为验证链路与防线，不做因子裁决**
      （裁决属后续预注册战役）。
- [ ] Spec delta 归档时并入 `openspec/specs/v2-fundamental-gp-panel`。
