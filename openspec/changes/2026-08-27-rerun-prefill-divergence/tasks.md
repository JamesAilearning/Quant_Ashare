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

## 6e. codex #471 第六轮（r6）——把判据改成构造性的

- [x] 6e.1 **P2** `namechange_path` 不该进预填:它由
      `config_dict.setdefault(..., resolve_namechange_path())` 无条件补上,
      本页没有对应控件,也从不读 `cr_namechange_path`。预填它的值进得了
      session 却到不了发出的配置,而下次重跑另一份归档配置时会被如实报成
      「被覆盖」——一条关于「哪个值会生效」的假消息
- [x] 6e.2 **这是我 r5 主动引入的**:当时把它加进 `_PAGE_EMITTED_KEYS`,
      理由是「它确实随配置发出」——但本 change 的 spec 自己写着「预填写进去
      的每个字段都必须被提交它的控件**读回**」。两个判据不是一回事
- [x] 6e.3 判据改成**构造性**的:守卫用 AST 从源码算出「本页读回的键」
      （所有 `_cr(...)` 的第一参、`_prefilled_trading_day(...)` 的第一参、
      带 `key="cr_*"` 的控件），再与 `_PAGE_EMITTED_KEYS` 求差,差集必须
      **正好**等于 `_EMITTED_WITHOUT_READBACK`。手写第四份名单只会漂——
      前三轮都是这样漂的
- [x] 6e.4 变异复验 **42 条全咬住**（新增:可用键退回后端全集 / 写进本页
      从不读回的字段 / 不读回名单被清空）

## 6f. codex #471 第七轮（r7）

- [x] 6f.1 **P1 预设选择器没重置——预填会被下一帧撤销。** 预填把字段改成源
      运行的值 ⇒ `_detect_preset()` 记 `Custom`；而 `cr_preset_selector` 是
      selectbox 的 widget 键，**粘着**操作人上次选的预设（通常 Default）⇒
      下一次任何控件触发的重跑里 `preset_choice != current_preset` 成立 ⇒
      `_apply_preset()` 把源运行的值整片覆盖回去，而横幅照说「已按该次运行
      覆盖」。直接违反本 change 自己的场景「预填之后的编辑存活」
- [x] 6f.2 修法：应用新载荷时把 `cr_preset_selector` 与 `cr_preset` 同步成
      `Custom`。写 Custom 而不是检测结果——检测要等字段控件落定，而这里在
      它们之前；预填来自一次运行的归档配置，本来就不是「选了某个预设」
- [x] 6f.3 运行时用例**真跑**页面里那段判据表达式：喂同步后的状态断言不触发
      `_apply_preset`，再喂缺陷本体的状态断言它**会**触发（否则这条测试自己
      就是空的）
- [x] 6f.4 **P2** 预填令牌的 md5 加 `usedforsecurity=False`：FIPS 受限的
      Python 构建下不带它会 raise——点「用此配置重跑」在预填生效之前把整页
      打崩。同文件的按钮键生成早就是这么写的
- [x] 6f.5 变异复验 **45 条全咬住**（新增：预设选择器不同步 / 选择器同步成
      Default / md5 未声明非安全用途）

## 6g. codex #471 第八轮（r8）

- [x] 6g.1 **P2 一份合法但为空的归档配置（YAML 就是 `{}`）被静默吞掉。**
      `if PREFILL_CONFIG:` 把「载荷解析出零个字段」与「压根没点重跑」混成
      同一格：预填不跑、横幅不出、复核区也不出，点「用此配置重跑」看起来
      **什么都没发生**——而那恰恰是最该说话的时刻，操作人有理由怀疑按钮
      坏了。新增 `_HAS_PREFILL_PAYLOAD`（只看那个 session 键在不在，不看
      解析结果），空配置单独响亮呈报；解析失败仍走 `prefill_config_error`
      那条路，两者不合并
- [x] 6g.2 **P2 属于另一模式的键被同时报成「本页不支持」。** 那类键按定义
      就不在本次 emitted 里，所以**每一个** mode_only 键都必然落进
      unsupported 名单——同一个 `overall_start` 在展开区被说成「切模式即
      生效」，又在黄色警告里被说成「本页不支持」，两句直接打架
      （我自己那轮独立审查也独立测到这个重叠是 100%）。
      `unsupported_prefill_keys` 加 `other_mode_keys` 参数并减掉它们：
      一个键只能有**一种**归属
- [x] 6g.3 新增交叉断言：两份报告的**交集必须为空**（此前两条用例各测各的，
      从没有一条把它们放在一起比过）
- [x] 6g.4 另五条是重复投递；其中「预设选择器」与「md5」两条已于 r7
      (`490b5bf`) 修完，我自己那轮独立核实也判 ALREADY_FIXED
- [x] 6g.5 变异复验扩到 **49 条全咬住**（新增：unsupported 不减 other_mode /
      调用不传 other_mode / 合法空配置退回静默 / 载荷在场判据退回看解析结果）

## 6g. codex #471 第七轮：一条 P1——重复重选同一个运行不会重新武装预填

- [x] 令牌原来只由「源运行 + 配置内容」构成。操作人预填之后改了几个字段、
      回结果页对**同一个运行**再点一次「用此配置重跑」，令牌不变 ⇒ 应用分支
      被跳过 ⇒ 他的改动原样留着，而横幅照说「已按该次运行覆盖」——**启动的
      实验与他明确重选的那次运行不一致**
- [x] 修法：结果页每一次**按下**都铸一个一次性动作身份
      （`prefill_config_action = uuid4().hex`），令牌带上它
- [x] 幂等性靠「nonce 只在**按钮回调里**换」保住：普通的 Streamlit 重绘
      （任何控件交互）不经过那个回调，同一次预填在整个会话里仍只应用一次
- [x] 令牌**仍然**带配置内容摘要：万一将来有第二个写入方忘了铸 nonce，
      内容变了照样能重新武装
- [x] 用例**真跑**令牌表达式（从页面 AST 抽出来求值），不查源码串：要证明的
      是「令牌随动作变、不随重绘变」，源码串证明不了——把 nonce 拼进一个从没
      被求值的分支，串守卫照样命中
- [x] 另一端用 AST 钉：nonce 必须在**按钮分支内**铸（每帧铸会让每一次重绘都
      重新覆盖操作人的编辑），且全文件只写这一处
- [x] 变异复验 3 条全咬（令牌里去掉 nonce / nonce 换成固定值 / nonce 挪到
      按钮分支之外）

## 6h. codex #471 第八轮：一条 P1——滚动验证结果页根本产不出预填状态

- [x] 「用此配置重跑」原来只长在 `_render_header_actions` 里，而那个函数只被
      `_render_pipeline_dashboard` 调用。一份**正常的**滚动验证结果（有
      `walk_forward_report.json`、没有根级 `pipeline_report.json`）走的是
      `_render_walk_forward_summary` 那一支 ⇒ **本 change 为「源运行是
      walk_forward」写下的窗口恢复与跨模式重跑场景，在那一侧全都不可达**
- [x] 抽出 `_render_rerun_action(job, config_bytes)`，pipeline 侧的动作条
      **委派**给它，滚动验证分支直接调它——**一份实现，两条路径**。两份实现
      里只要有一份忘了铸动作 nonce 或忘了写 `prefill_config_source_mode`，
      症状都是「预填看起来没生效」，而那不像个 bug
- [x] 入口放在滚动验证分支的 `if wf_report:` **之前**：没有报告的那一支恰恰
      是「这次跑挂了，想改改参数重跑」最常见的时刻
- [x] **路由级覆盖**（codex 点名要）：用 AST 找结果页模块级的引擎分派链，
      逐支确认调用；并钉住按钮实现全仓只有一处
- [x] 顺手修掉一条**钉排版**的旧守卫：`test_rerun_prefill_decodes_strictly_
      and_carries_the_source_mode` 把 `except` 与 session_state 写入连同
      **缩进**一起钉进串里，抽函数（缩进少一层）当场失配——而它从来没钉住
      「解码严格 / 失败不跳页 / 带上源模式」这三件事本身。改成在
      `_render_rerun_action` 的 AST 上问（#474 同款教训）
- [x] 变异复验 6 条全咬:路由三条（滚动验证分支删调用 / 动作条不再委派 /
      调用挪进 `if wf_report` 内）+ 守卫三条（解码加 `errors="replace"` /
      不写 source_mode / 解码失败也跳页）

## 6i. codex #471 第九轮：一条 P1——预填的日期没有绑到真实控件状态

- [x] **先用 `AppTest` 实测复现**，不靠推理。不带 key 的 streamlit 控件按
      「参数变了就是另一个控件」认身份:``index`` 一变就重置成新 default，
      没变就粘住。于是**预填值恰好等于 live default 时**控件不动，操作人
      先前的编辑留着，而横幅照说「已按该次运行覆盖」——启动的窗口与源运行
      不同，页面上没有任何迹象。实测三行:`wanted=2021 / picked=2023`
- [x] **反方向也实测**:直接给控件加 `key` 会让 session 说了算、`index` 被
      忽略——那正是 #300 回滚的病根（换 provider 之后 live default 冻结）。
      候选修法 v1 就栽在这一格上，实测当场看出来
- [x] 修法 `_bind_trading_day_state`:``wanted`` 与上一帧不同就写（复刻不带
      key 版本「ID 变了」的正确语义），**外加**来了新的预填动作就写（补上它
      漏掉的那一格），其余情况一个字节也不写
- [x] 一处改动覆盖**全部八个日期字段**（六个 pipeline + 两个滚动验证窗口）
      ——它们都走 `_select_trading_day`，而页面上其余控件本来就带
      `key="cr_*"`、预填天然绑得上。**核过范围**，不是只修被点名的那两个
- [x] `AppTest` 用例五组:缺陷本体 / 预填后编辑不被撤销 / 再点一次重新生效 /
      **#300 病根不复现** / 无预填时编辑粘住
- [x] 宿主用 **AST 取函数**而不是 `import` 配置页——首版宿主 import 之后
      `selectbox[0]` 是那一页自己的「模式」下拉，测的根本不是被测控件（实测
      当场看出来，已写进宿主的 docstring）
- [x] 变异复验 4 条全咬（去掉动作那一半 / 去掉 wanted 变化那一半 / 每帧都写 /
      控件不带 key = 回到原缺陷）
- [x] `tests/logic` + `tests/governance` **5092 passed**

## 6j. codex #471 第十轮：一条 P1——强制改写作用到了「这次没被预填的」字段上

- [x] 动作 nonce 让 `fresh_action` 对**每一个**日期控件都为真。而源运行的
      归档 config 是合法空 YAML / 解析失败 / 旧 schema 缺这个字段时，
      `_apply_prefill_to_session` **一个字节也没写**——控件却被强行改写成
      live default，**默默丢掉操作人已经改好的日期**，而页面那一刻正说着
      「本次没有任何字段可预填」
- [x] `_PREFILL_SUPPLIED = PREFILL_CONFIG ∩ _PREFILL_APPLICABLE_KEYS`；
      每个控件传 `prefill_supplied="<自己那个字段>" in _PREFILL_SUPPLIED`，
      `fresh_action` 只在 `supplied` 时成立
- [x] **评审点名的测试盲区属实**:此前的 AppTest 每次都先写
      `cr_overall_start` 再推进 nonce，所以这条路整个没被走到。补一条
      「新动作 + 这次没带这个字段 ⇒ 编辑必须留着」
- [x] 首轮变异 3 条只咬住 1 条:AppTest 宿主自己注入旗标，**页面那一侧的
      接线不在它的覆盖里**（写死 `prefill_supplied=True`、或把推导算成整个
      applicable 集合，AppTest 全绿）。补两条页面级守卫——**真求值**
      `_PREFILL_SUPPLIED` 的表达式（三组载荷），并逐个 AST 核对八个调用点
      「问的是它自己那个字段」
- [x] 变异复验 3/3（去掉 supplied 前提 / 写死 True / 推导算错）
- [x] `tests/logic` + `tests/governance` **5095 passed**

## 6k. codex #471 第十一轮：两条（一条是我上一轮引入的回归）

- [x] **P1（我上一轮引入的）**:默认值落在日历外时，`_bind_trading_day_state`
      被调用**两次**——一次拿日历外的 `default`、一次拿 `options[0]`。于是
      `__last_wanted` 在两个值之间**每帧来回摆**，「wanted 变了」永远成立，
      控件被每帧改写:操作人在预填之后选的任何合法日期都会被打回日历的第一天。
      修法:**先解析回退，再只绑一次**（绑 `options[resolved_index]`）
- [x] AppTest 补一条:日历外默认值 → 有可见警告 → 操作人改成合法日期 →
      一次空重绘后**编辑必须还在**。变异（退回绑两次）当场红
- [x] **P2**:合法空 YAML（`{}`）也是一份**成功解析**的载荷。源运行的 `mode`
      写在 `job.json` 而不是归档 config.yaml，结果页单独带过来——用
      `if PREFILL_CONFIG:` 当应用判据，重跑一次空归档的 walk_forward 运行时
      页面会停在当前的 pipeline 上，模式对比也整个不出。判据改成
      `if _HAS_PREFILL_PAYLOAD and not _PREFILL_ERROR:`
- [x] 空配置的提示语跟着改:原来说「本次没有任何字段可预填」，模式被带过来
      之后那句就不准了 → 改成「**归档里**没有任何字段」，并在台账记了模式时
      明说它仍会被带过来
- [x] 变异复验 2/2（绑两次 / 判据退回 `if PREFILL_CONFIG:`）
- [x] `tests/logic` + `tests/governance` **5099 passed**

## 6l. codex #471 第十二轮：三条 P2——同一个判据要一致地用

- [x] **判据抽成具名常量** `_HAS_PARSED_PREFILL = _HAS_PREFILL_PAYLOAD and
      not _PREFILL_ERROR`,三处（应用分支 / 预设初始化 / 复核区）都引用它。
      在三处各写一遍就会漏——本 PR 上已经漏过两次:先是应用分支还在用「解析
      出几个字段」，改对之后**预设初始化**那一处又把台账带来的模式打回
      pipeline（新会话里 `cr_preset_initialized` 不在 session，于是套上
      `default.yaml`），上一轮那个修法只对「此前打开过配置页」的操作人生效
- [x] **复核区**同一判据:合法空归档下 `_prefill_baseline` 里仍有台账带来的
      `mode`、横幅也承诺了会逐项列出，而用字段数当判据会让整块复核区不出
- [x] **覆盖账本读操作人真正看到的那个值**:日期控件有自己的 key
      （`cr_dt_<field>`），操作人改过之后他看到的值在那里，而 `cr_<field>`
      还停在旧值。只读后者会报「2020 → 2022」而屏幕上明明是 2021;更坏的一格
      是新值恰好等于那个旧值时**一条覆盖都不报**，而可见的选择照样被重置
- [x] 变异复验 4/4。首轮逃 1 条（预设初始化）——补一条 AST 守卫:共享判据只
      定义一处、恰好三处引用、且预设初始化那一处是**取反**用它
- [x] `tests/logic` + `tests/governance` **5103 passed**

## 6m. codex #471 第十三轮：一条 P2——零字节归档把按钮永久禁掉且不说话

- [x] 存在但**零字节**的 `config.yaml` 让 `_read_config` 返回 `b""`。按钮
      用 `disabled=not config_bytes` 当判据 ⇒ 永久禁用、一个字不说;而空
      YAML 文档的顶层不是映射，本页早已承诺这种形态要被**响亮报出**
- [x] 判据改成「归档 config **在不在**」（`config_present`），让它走进验证
- [x] `_prefill_config` 补上「键在场但内容为空」那一格:此前 `if not raw:`
      把它与「压根没点重跑」混成同一格、静默返回空 dict
- [x] `_HAS_PREFILL_PAYLOAD` 改成**键在不在**——这一行原本就有一段注释写着
      「载荷在场与否只看那个 session 键在不在」，而代码在测真值。**代码与
      自己的注释对不上**，零字节归档正是那个差异的兑现处
- [x] 变异复验 3/3。首轮逃 1 条（按钮判据）→ 补一条 AST 守卫:按钮的
      `disabled=` 必须是 `not config_present`
- [x] 顺带把一条钉**调用字面拼写**的守卫换成 AST 位置关系（给
      `_render_rerun_action` 加参数当场把它打红，而它要钉的「在报告分叉之前
      渲染」根本没变）
- [x] `tests/logic` + `tests/governance` **5107 passed**

## 6n. codex #471 第十四轮：一条 P2——上一轮那个 `is_file()` 绕开了守卫

- [x] 上一轮把按钮判据从「内容非空」改成 `config_path.is_file()`,而那**绕开
      了守卫式读取**:一份存在、但落在允许的输出根之外（或读不出来）的归档
      会被当成可读 ⇒ 按钮点得动 ⇒ 跳过去之后被讲成「零字节空文件」,而真正
      的失败原因（已记进 `artifact_issues`）被丢掉
- [x] 判据改成**读取结果本身**:`_read_bytes_artifact_checked` 返回
      `(内容, 这次是否真的拿到了这份工件)`,`_read_config` 把它作为第四个
      返回值交出去,两个调用点都传它
- [x] 判据不能只看任一半:``result.issue is None``(守卫/OS 层没出问题) **且**
      文件确实在。三种情形的内容都是 `b""`——读成功的零字节、被守卫拒绝、
      压根不存在
- [x] 用例分三格实测,并且**夹具建在允许的输出根之内**——首版建在系统临时
      目录里,连「读成功」那一格都被守卫拒掉,测到的不是它自称要测的事
      (实测当场看出来,已写进用例的 docstring)
- [x] 变异复验 3/3。首轮逃 1 条(调用点退回自己 `is_file()`)→ 补一条 AST
      守卫:每个调用点的 `config_present` 必须是**算好的名字**,不许是就地
      拼的表达式;两个文件里也不许再出现 `config_path.is_file()`
- [x] `tests/logic` + `tests/governance` **5113 passed**

## 7. 划界（本 change 不做）

- 不做「预填后再改字段就把它标成脏」的持续追踪：那要给每个控件挂
  on_change，回调面比本 change 大一个量级，且复核区已给出等价答案。
- 不做「把源运行的未记录字段补成当时的默认值」：无处可查那次运行用的是哪
  版默认值，编造基线比留空更坏。
- 不做跨模式提交（源运行是 walk_forward、本次按 pipeline 提交时把 wf 键一
  并发出）：提交 schema 由 mode 定，混发会在后端构造时 raise。
