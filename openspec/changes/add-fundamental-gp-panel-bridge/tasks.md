# Tasks

## 上游扩展（codex #427 P1：初版误称"零改动"，这两项是桥能跑起来的前置）

- [ ] `src/research/financial_pit_view.py`：`as_of` 增加**公开的 provenance 响应**
      （每个服务字段的 `available_from_trade_date`，与值**同一次调用**返回）。没有它，
      桥只能读私有内部/裸 store 或反推证据 —— 而反推正是"证据"该排除的东西。
      配套测试：值与 provenance 同源、缺失字段的 provenance 亦为 NA。
- [ ] `src/factor_mining/grammar.py`：注册**基本面终端组** + 其类型/taint 规则。
      现状 `FeatureRegistry.V1` 仅 12 个终端，`_random_leaf` 与 `V1_*` 取交集、
      `Terminal` 拒绝表外名字 —— 只靠参数传面板，GP **长不出**基本面表达式。
      D5 不受影响（只加终端符号与类型，不引入 qlib/PIT import），须有对应闸测试。

## 桥模块（研究侧）

- [ ] `src/research/fundamental_panel.py`：`build_fundamental_panel(view, fields,
      trade_dates, instruments, *, group_resolver=None)` → `(panels, evidence,
      periods)`；逐交易日 `view.as_of`（**一次调用同时取值、provenance、报告期**），
      实例内缓存已由 view 提供（只付过滤成本）。
- [ ] 返回前自检：每个非 NA cell 的 `available_from <= trade_date`，违反即 raise；
      无法建立证据的字段 → 拒绝返回面板（不返回"无证据面板"）。
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
      改为在**求值路径**上按该表达式**实际引用的终端**判断：跨端点时要求每个
      `(trade_date, instrument)` 的 report_period 一致，否则该 cell 为 NA；同端点
      表达式不受影响。这意味着 `src/factor_mining/evaluator.py` 也在 scope 内
      （仍不引入 qlib/PIT import，D5 不受影响）。
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

- [ ] 确认改动面与提案一致：`grammar.py`（终端注册）、`evaluator.py`（跨端点同期
      强制，expression-aware）与 `financial_pit_view.py`（provenance 响应）**有**改动；
      `pit_adapter` / `gp_engine` / `src/data/pit/*` / canonical runtime **无**改动。
- [ ] 确认 D5 gate 与 `test_financial_pit_view_isolation.py` 均照原样通过
      （**不改签**）—— 终端注册不引入 qlib/PIT import，桥仍在 `src/research/` 内。
- [ ] ruff + mypy --strict + 治理全套 + 新增测试全绿。
- [ ] 用起步三因子（① GP/A、③ 资产增长、② C3 纯 BS 应计 —— CSI800 覆盖率已实测全绿）
      跑通"面板化 → GP → 边际贡献"最小链路，**只为验证链路与防线，不做因子裁决**
      （裁决属后续预注册战役）。
- [ ] Spec delta 归档时并入 `openspec/specs/v2-fundamental-gp-panel`。
