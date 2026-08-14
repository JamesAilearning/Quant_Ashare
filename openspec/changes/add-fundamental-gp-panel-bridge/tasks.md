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
      改为在**求值路径**上做，且遮蔽点必须落在**第一个跨端点子树**（操作数跨端点的
      最低节点），在任何父级滚动/横截面算子消费它之前 —— 只遮蔽最终 cell 太晚
      （codex #427 r3 P1）：`ts_mean(div_safe($revenue, $total_assets), 5)` 在 T 日
      期可能对齐，但窗口内**更早日期**的混季比率会被卷进来，在 T 产出被污染的非 NA 值。
      须加嵌套表达式回归用例。这意味着 `src/factor_mining/evaluator.py` 在 scope 内。
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
      N∈{5,10,21} 交易日重建面板。**无条件**断言**值+证据一起**的哈希必变
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
      `evaluator.py`（跨端点同期强制，expression-aware）、`validator.py`（两条求值
      调用点接 provenance）与 `financial_pit_view.py`（provenance 响应）**有**改动；
      `gp_engine.py` **仅当** period 传参通路必须经由它时才动（adapter 方案则不动）；
      `pit_adapter` / `src/data/pit/*` / canonical runtime **无**改动。
- [ ] 确认 D5 gate 与 `test_financial_pit_view_isolation.py` 均照原样通过
      （**不改签**）—— 终端注册不引入 qlib/PIT import，桥仍在 `src/research/` 内。
- [ ] ruff + mypy --strict + 治理全套 + 新增测试全绿。
- [ ] 用起步三因子（① GP/A、③ 资产增长、② C3 纯 BS 应计 —— CSI800 覆盖率已实测全绿）
      跑通"面板化 → GP → 边际贡献"最小链路，**只为验证链路与防线，不做因子裁决**
      （裁决属后续预注册战役）。
- [ ] Spec delta 归档时并入 `openspec/specs/v2-fundamental-gp-panel`。
