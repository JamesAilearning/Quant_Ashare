# Tasks

## 1. 修好预填本身

- [x] 1.1 预填改**无条件覆盖**已知键（`PIPELINE_KEYS ∪ WALK_FORWARD_KEYS ∪
      {mode}`），删掉 `f"cr_{key}" not in st.session_state` 守门
- [x] 1.2 一次性 token（`prefill_config_applied_token`）保证每份源载荷只应用
      一次，预填之后的编辑不被后续脚本重跑撤销
- [x] 1.3 被覆盖且值不同的字段逐条列出（旧值 → 新值），跨脚本重跑保持
      （`prefill_overwritten_fields`）
- [x] 1.4 只写**已知键**：源 YAML 的任意键都写 `cr_<key>` 会撞控件键
      （`cr_preset_selector` / `cr_show_diff_toggle`）
- [x] 1.5 结果页写 `prefill_config_source_mode = job["mode"]`，配置页在源
      YAML 无 `mode` 键时用它兜底

## 2. fail-loud

- [x] 2.1 `_prefill_config()` 的 YAMLError 与非映射顶层写进
      `prefill_config_error`，页面 `st.error` 报出
- [x] 2.2 结果页改严格 UTF-8 解码；失败就地报错且**不跳页**

## 3. 四桶差异（纯函数）

- [x] 3.1 `PrefillDivergence` 带 `classification`，四个分类常量
- [x] 3.2 `prefill_divergences_from_source_run(prefill, emitted, *,
      known_keys)`：changed / source_missing / mode_only / run_scoped
- [x] 3.3 `_values_agree`：数值等价不算差异，bool 与数字类型敏感
- [x] 3.4 机器本地键整体排除（与预设比较同一套 `_MACHINE_LOCAL_PRESET_KEYS`）
- [x] 3.5 `source_missing` 的源侧留空，不拿 emitted 值反填
- [x] 3.6 `divergences_of(divergences, classification)` 单一过滤入口（两处
      自己写过滤条件会分叉）
- [x] 3.7 `unsupported_prefill_keys` 摘出 run-scoped 键（否则每次重跑一句
      常驻假警告）

## 4. 复核区渲染

- [x] 4.1 `changed` → 头条警告 + 表格；空则明说「逐项一致」
- [x] 4.2 `source_missing` / `mode_only` → 各自折叠分组，附「为什么不推断
      基线」说明
- [x] 4.3 `run_scoped` → 一行说明
- [x] 4.4 顶部横幅改述（避开「⑤ 提交前复核」字样——那会撞编号区块的源码序
      守卫）

## 5. 测试

- [x] 5.1 纯函数用例：四类各一 + 数值等价 + bool 类型敏感 + 机器本地键排除
      + `divergences_of` 过滤 + run-scoped 不算 unsupported
- [x] 5.2 页面接线钉：`known_keys=` 传参、四个分桶取子集、两条渲染分支、
      无条件写入、覆盖列表、fail-loud 三处
- [x] 5.3 producer 钉：严格解码 / 不跳页 / 写源模式
- [x] 5.4 变异复验（钉条件表达式整行，不钉标识符或赋值行）
- [x] 5.5 `tests/logic` 全量 + ruff + mypy --strict + openspec validate --strict

## 6. codex #471 三条（r2）

- [x] 6.1 **P1** WF 窗口两个定义性字段接线:新增 `_prefilled_trading_day`
      （**只读**,不走 `_cr`）——`_cr` 的 seed-and-stick 正是 #300 回滚的原因,
      这里没有预填时一个字节也不写,live default 每帧照常重算
- [x] 6.2 **P1 附带** 预填写入抽成顶层 `_apply_prefill_to_session`,与
      `_prefilled_trading_day` 一起用 AST + 假 st **真跑**
      （`tests/logic/test_config_run_prefill_runtime.py`,沿用
      `test_jobs_url_handoff_source.py` 的既有做法）——源码串看不见 session
      状态,这正是 codex 说「源码测试测不到」的那一层
- [x] 6.3 **P2** 比较基线折进源模式（`prefill_baseline_with_source_mode`）+
      `mode` 进 known_keys:UI 运行的 mode 只在 job.json,不折进来的话切模式
      会被说成「逐项一致」
- [x] 6.4 **P2** `other_mode_keys` 参数:mode_only 只在键属于**对面模式的
      schema** 时成立;两个 schema 都不认识的历史键专归
      `unsupported_prefill_keys`,不再自相矛盾
- [x] 6.5 变异复验扩到 **32 条全咬住**（新增 WF 三条、基线/known/other_mode
      三条、helpers 三条）

## 6b. codex #471 第三轮（r3）

- [x] 6b.1 **P2** `_PREFILL_APPLICABLE_KEYS` 扣掉 run-scoped 键:两个后端
      KEYS 常量都含 `output_dir`,但本页从不提交它。不扣的话同一会话连着
      重跑两次,第二次把第一次的目录报成「被覆盖」——一个本页同时声明
      「随运行而生、不会携带」的字段
- [x] 6b.2 运行时用例取**页面自己那行赋值**的 AST 真跑,而不是在测试里
      抄一份集合表达式（抄的那份漏了减法照样绿）
- [x] 6b.3 变异复验扩到 **34 条全咬住**（新增「run-scoped 未扣」与「mode
      被一起扣掉」）
- 另两条 P2（基线含源模式 / mode_only 判据）是 r2 意见的重复投递，已于
  `d2a32cc` 修完，源码实地核对在位（其中一条的 AGENTS.md 链接仍指向
  `e39efea`）

## 6c. codex #471 第四轮（r4）

- [x] 6c.1 **P2** `other_mode_keys` 必须是**本页在那个模式下真的会发出**的
      键,不是后端 schema 全集。像 `run_factor_analysis` 这种「在
      PIPELINE_KEYS 里、但本页任何模式都不发」的键,用全集会被标成
      mode_only(「切模式即生效」——假的),而 unsupported 同时说「本页不
      支持」:上一轮修掉的那个自相矛盾换了个来源又回来了
- [x] 6c.2 新增 `_PIPELINE_ONLY_EMITTED` / `_WALK_FORWARD_ONLY_EMITTED`,
      由 AST 守卫钉住与页面两个 `config_dict.update({...})` 字面量同步——
      两份分叉的后果是**说错话而不报错**,没有任何东西会红
- [x] 6c.3 反向钉:两个常量 ⊆ 各自后端 schema,且与对面 schema 无交集
      (混进共享段的键会被说成「属于另一个模式」)
- [x] 6c.4 变异复验扩到 **36 条全咬住**（新增「other_mode 退回后端全集」
      「ONLY_EMITTED 漏一个键」「模式专属集合混进共享键」）

## 6d. codex #471 第五轮（r5）——修在根上

- [x] 6d.1 **P2** `_PREFILL_APPLICABLE_KEYS` 也用了后端 schema 全集,于是
      `cr_run_factor_analysis` 这种「本页没有控件、永不提交」的字段被写进
      session,下次重跑另一个值时被报成「被覆盖」,而复核区同时说「本次不会
      携带它」。这是同一个根因（后端 schema ≠ 本页发出的字段）的**第三种
      形态**——前两种是 `output_dir` 与 mode_only 判定
- [x] 6d.2 不再逐处修:新增 `_SHARED_EMITTED` / `_PAGE_EMITTED_KEYS`,把
      「本页发出什么」收成**一处定义**,三个消费者（预填写入、mode_only
      判定、对比基线）都从它派生
- [x] 6d.3 AST 守卫扩到共享段:`_SHARED_EMITTED` == `config_dict = {...}`
      字面量的键;`setdefault` 补的字段也要在 `_PAGE_EMITTED_KEYS` 里
- [x] 6d.4 **一条变异逃逸,揭示了真问题**:
      `_PAGE_EMITTED_KEYS - _RUN_SCOPED_PREFILL_KEYS` 在重构后成了 no-op
      （三份常量本就不含 `output_dir`）。删掉那道静默兜底,换成测试里响亮
      钉住「两者无交集」——no-op 的兜底恰恰会掩盖「有人把 `output_dir` 写进
      `_SHARED_EMITTED`」这种错误
- [x] 6d.5 运行时用例取**整条派生链**的 AST 真跑,不再只取最后一行、把中间
      量当外部注入（那等于把链条中段换成测试自己的版本,页面在中段漏一个键
      就测不出来）
- [x] 6d.6 变异复验 **40 条全咬住**

## 7. 划界（本 change 不做）

- 不做「预填后再改字段就把它标成脏」的持续追踪：那要给每个控件挂
  on_change，回调面比本 change 大一个量级，且复核区已给出等价答案。
- 不做「把源运行的未记录字段补成当时的默认值」：无处可查那次运行用的是哪
  版默认值，编造基线比留空更坏。
- 不做跨模式提交（源运行是 walk_forward、本次按 pipeline 提交时把 wf 键一
  并发出）：提交 schema 由 mode 定，混发会在后端构造时 raise。
