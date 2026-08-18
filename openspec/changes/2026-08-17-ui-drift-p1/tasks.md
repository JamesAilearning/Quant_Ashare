# Tasks: 2026-08-17-ui-drift-p1

## W1 状态词汇归一

- [x] `_normalise_cli_entry`：`ok → completed`（同源于既有
  `success → completed`）；`partial` 原样保留；未知词汇透传

## W2 run_dir 与可检视判据

- [x] `JobSummary.run_dir`（UI 取 `run_dir`，CLI 取 `output_dir`；带默认
  值，既有构造点与 `to_dict` 同步）
- [x] `run_dir_is_inspectable`：纯路径判据（无 I/O），边界 = `output/`
  两棵树，相对路径锚定**仓库根**而非进程 CWD

## W3 搁置 + 报数

- [x] `list_all_jobs` 跳过产物在读边界外的 CLI 行
- [x] `count_cli_rows_outside_output_tree()`；作业页显示条数与根因
  （实测本机 3404 条搁置 / 117 条可用）

## W4 滚动验证页

- [x] 运行清单并入 CLI 行（`type_filter="walk_forward"` +
  `source_filter="cli"`），跳转不再是死路（实测 92 条可打开）
- [x] 运行身份行（含 anchor 高亮说明 + git 溯源，脏树显式标注）
- [x] `metric_status` 四分支，**缺失分支排在最前**且绝不落 official；
  `metrics_purpose` 不一致时并排展示

## W5 测试

- [x] `tests/logic/test_jobs_wf_reachability.py`（14 用例）：词汇归一
  三态 / 可检视五态（含相对路径不随 CWD 变、越界拒绝）/ run_dir 透传与
  默认值 / 四条页面源码钉（含缺失分支必须先于 official 判定的**顺序**钉）

## codex #444 r1（三条 P1 全实修）

- [x] **分页控件被吞**:披露段插在 pg_indicator 与 pg_next 之间,缩进把
  `with pg_next:` 一起吞进 `if _set_aside:`——无搁置行时「下一页」消失。
  修:披露整体移到分页块之后,只让 caption 受条件控制
- [x] **路径锚定不一致**:可检视判据把相对 output_dir 锚在仓库根,而页面
  下游 `Path(selected)` → guard_output_path 走进程 CWD → 在仓库根之外
  启动 UI 时,「判定可达」的运行反被守卫拒绝。修:选项存同样锚定后的路径
- [x] **治理含义写反(最严重)**:我把 iso_week 说成「认证胜者」。治理钉
  明写 winner=fold_phase(csi800_cadence5_conservative)、isoweek 复核=
  iso_week,两者是不同 schedule;写反会让**合法的认证证据被当成参照运行**。
  修:两个 anchor 各给一条准确说明(fold_phase=认证胜者锚 / iso_week=
  生产服务锚,经单独门控)
- [x] 三条各配钉,其中 anchor 那条**直接对着治理钉的事实断言**(读两份
  preset 的 rebalance_anchor),而不是断言某句措辞——钉子挪了测试会跟着响

## codex #444 r2（两条 P1 全实修）

- [x] 同目录多次运行被折叠且**静默**换成别的运行:同一 preset 反复跑会把
  报告写回同一个 output_dir(实测 92 条折叠成 20 个目录、8 个目录被反复
  覆盖),点旧行匹配不上就落到 index 0。修:保留「每目录取最新」(旧运行
  产物已被覆盖,列出来只会渲染同一份最新报告),但匹配失败**明确告警**+
  统计被覆盖条目数;钉住告警必须在 selectbox 之前
- [x] 治理说法只改了展示文案、漏改源码注释与 proposal:两处残留仍写
  「胜者只由 anchor=iso_week 决定」。修:全文一致化(fold_phase=认证胜者 /
  iso_week=生产服务锚),grep 复查无残留

## codex #444 r3（两条 P1 全实修）

- [x] UI 与 CLI **共用同一 output_dir**:UI 启动的滚动验证会同时留下一条
  UI 作业和一条 CLI 目录记录(JobManager 把结果目录写进 config["output_dir"],
  引擎再按它编目),而选择器每目录只存一个 id → 另一个 id 的跳转永远匹配
  不上。修:新增 `_run_id_to_dir` 索引(同时收 UI 与 CLI 两套 id),跳转先按
  id→目录定位、再退回旧比法
- [x] spec delta 仍写「anchor 单独决定谁是生产」:r2 只清了 proposal 与源码
  注释。归档时这句会把治理错误重新引回来。修:改述为两级链(认证胜者跑
  fold_phase;iso_week 是单独门控的复核,其净超额>0 是晋升条件之一,之后才是
  服务参数绑定),并显式写下「anchor 单独 SHALL NOT 被当作生产判据」

## codex #444 r4（两条 P1 + 一条 P2 全实修）

- [x] **治理文案只凭 anchor 推身份**——这正是本 change 的 delta 自己写下的
  禁令,而我的文案恰好犯了它:`stage7_daily_h5` / csi300 参照运行只因用
  fold_phase 就被标成「认证胜者」。修:先确认整族身份(csi800 + SH000906TR +
  N=5 + phase=0)才谈治理链;族外只解释 schedule 语义并明说不给治理判断
- [x] **r3 的别名表把被覆盖的历史 id 也收了**,点旧行反而 `_requested_found=
  True`、绕过 r2 的告警、静默渲染最新报告——等于把 r2 修的东西废掉。修:
  别名只覆盖**同一次调用**的 UI/CLI 两个 id(每目录第一条 CLI 记录),被覆盖
  的历史 id 留在告警路径。实测分流:20 可别名 / 72 走告警
- [x] 披露在两处空态 `st.stop()` **之后**:目录全是越界记录时(正是本改动
  针对的重污染场景),页面说「暂无作业」就停住,反而不提搁置了多少。修:
  披露前移到空态之前(且仍不在分页块内——r1 那条约束同时成立)

## codex #444 r5（一条 P1，修在共享层）

- [x] **CLI 流水线行仍是死链**:作业页把它们路由到 `results.py`,而该页选择器
  只由 `JobManager.list_jobs()` 构成,请求的 id 不在 UI 作业目录里就直接
  「运行未找到」。实测本机 13 条可检视的 CLI 流水线行,全部有
  `pipeline_report.json`,却一条都打不开——正是本 delta 禁止的死链。
  选「接纳」而非「从作业页排除」:产物真实存在,排除等于把能看的东西藏起来
- [x] **修在共享层而不是再抄一遍**:r1(锚定)/r2(被覆盖不静默换人)/r4(被覆盖
  不进别名)三轮改的是**同一个**算法,再抄一份到结果页必然分叉。折叠提到
  `job_io.fold_catalog_by_dir`(纯函数),两页共用;锚定与可检视判据共用
  `anchored_run_dir`,两处不同锚这个 bug 类被结构性消除
- [x] **等价性核验**:新旧算法在真目录上逐项比对——滚动验证 92 行 → 20 目录
  / 72 被覆盖,流水线 13 行 → 13 目录 / 0 被覆盖,`superseded` / `id→dir` /
  `run_options` 三张表全部逐键相等
- [x] 结果页把「产物被覆盖」与「运行未找到」分成两条路:前者明说是谁覆盖了它,
  后者才是记录不存在。且比较**解析后**的 id 而非原始请求——比原始请求会立刻
  改写 query_params → 重跑 → 告警只闪一下就没了
- [x] 测试改锚:两条源码钉升级为对 `fold_catalog_by_dir` 的**行为**测试
  (锚定 / 首条即最新 / 被覆盖者两表不相交 / 大小写折叠 / 空目录行 / 空输入 /
  真目录不变式),另加结果页四条钉

## codex #444 r6（两条 P1 + 一条 P2 全实修）

- [x] **灵敏度臂被标成认证胜者**:`csi800_cadence5_base` 四个旧谓词全中
  (csi800 / SH000906TR / N=5 / phase=0),却是 **5 bps** 灵敏度臂而非 20 bps 的
  认证胜者——base 与 conservative 恰差 `{output_dir, slippage_bps}`。修:入族
  条件补上成本口径,且**取自 `EVAL_PROFILES["csi800_n5"]` 本身**而不是抄字面量
  (晋升族语义钉在那里,治理测试也钉着它);去掉 `rebalance_anchor`——族**跨**
  两个锚,那是族内的区分维度。全 36 个预设扫一遍:**恰好 2 个入族**,就是认证对
- [x] **被覆盖的 id 被扔到全局第一条**:它们故意不进 `_run_id_to_dir`(那是静默
  别名的路),但也不能就这么丢——`_target_dir` 为空 → `_default_index` 落到 0 →
  渲染的是**另一个目录**的运行,告警还说不出是谁覆盖了它。修:用
  `_folded.superseded_dir_of_run` 定位到它**自己那个目录**,告警指名现在住在
  里面的是谁;定位成功**不算**「找到」,告警照发(否则退回 r2 修掉的静默换人)
- [x] **折叠键没折平 `..`**:`output/runs/a` 与 `output/x/../runs/a` 都判可达、
  指向同一份产物,却被当成两次运行,被覆盖的历史行于是静默渲染当前报告。修:
  `anchored_run_dir` 用 `os.path.normpath` 折平(纯词法、无 I/O);并让
  `run_dir_is_inspectable` **调用**它——此前我在注释里写「共用同一段代码」,
  其实是两份拷贝,那句话当时是假的
- [x] 判据迁到 streamlit-free 的 `pages/_walk_forward_helpers.py`:为了一个纯
  谓词让测试导入页面(连带 streamlit),正是 #442 r6 抓到的错法
- [x] 测试:新增 `GovernedFamilyPredicateTests`(判据源自 profile / 含成本口径 /
  认证对入族而 base 不入 / 全预设恰好两个 / int-float 拼写等价)与 `..` 折平、
  判据共用锚定、被覆盖 id 路由四组;两条失效的源码钉重新对准新实现

## codex #444 r7（两条 P1 + 一条 P2）+ 并行自审扫描

- [x] **入族条件漏了约束语义**:profile 里的 `risk_constraint_scope`
  (canonical 默认 `all_days`,晋升族预注册 `rebalance_days`)与
  `campaign_constraints` 也是门的一部分。少比它们,一个把风控约束关掉、或换成
  别的标定的运行照样顶着「认证胜者」文案被读。修:`risk_constraint_scope` 直接
  入表;`campaign_constraints` 是语义开关、报告 config 里无同名键,显式映射到
  `risk_constraints_enabled` + `risk_constraints_calibration=campaign_v1`。
  三把新钥匙各自能踢出跑偏运行(逐个实测),全预设仍恰好两个入族
- [x] `_knob_matches` 补布尔特判:`bool` 是 `int` 子类,不特判的话
  `risk_constraints_enabled: 1` 会静默等于 `True`
- [x] **代码身份说不出来时反而静默**:`git_commit` 为 null(引擎在**续跑**且
  各折来源不一致时就这么标)或整键缺失时,此前整条代码身份都不渲染——最不可
  溯源的报告一个字都不说,还照打认证族文案。修:两种情况各给一条明确告警,
  且分开(null=混合来源 / 缺键=该字段落地之前),不含糊成一句
- [x] **判据「纯路径算术」是假话**(codex P2 与自审扫描独立抓到同一条):
  它逐行 `Path.resolve()`(走符号链接、真碰盘)。本机 3527 行实测每次渲染
  **771 ms**;行侧改词法后 409 ms(余量在逐行 `allowed_output_roots()` 的
  2×N 次 resolve),两个根按 `_ALLOWED_ROOTS` **身份**缓存后 **23 ms**——33×。
  十一条语义回归逐条对齐(含 `..` 逃逸、`output_extra` 前缀陷阱、patch 边界)。
  规格与 proposal 里那句话同步改成实话
- [x] 放弃行侧 `resolve()` = 放弃**此处**的符号链接逃逸检测,这是**有意**的:
  真正读文件时仍走 `guard_output_path`(它 resolve),安全检查留在文件访问处;
  词法 `..` 逃逸由 `anchored_run_dir` 先折平,仍然拦得住
- [x] **7 条数据源作业是死链**(自审扫描):`tushare_provider` 行列在作业页,点
  「查看详情」路由到 results.py 得到「运行未找到,可能已被删除」——**假消息**,
  记录在、产物在,只是 U3 下线了那个视图。修:按钮禁用 + 说明原因;规格补上
  「够不着就当场说清,不得路由到否认它存在的页面」
- [x] 两条注释与实现不符(自审扫描):`metric_status` 那条把方向写反了——指标算
  在**未裁剪**、已违约束的持仓上(`positions` 绑 qlib 实际执行),clip 是事后
  动作、落旁路字段 `positions_clipped`,不进指标,所以这类数字可能系统性**偏
  高**而非偏保守;`run_options` 那条说自己在去重,其实去重早由
  `fold_catalog_by_dir` 接管,它真正的职责是不让 CLI id 顶掉 UI 作业的标签

## codex #444 r8（一条 P2）

- [x] **两侧不在同一个命名空间**:候选侧改成纯词法之后,根侧仍走
  `allowed_output_roots()` 的 `resolve()`。若 `output` 本身是符号链接/联接,
  解析后的根变成挂载目标,**词法候选一条都对不上**——整份目录记录被判不可
  检视,而产物守卫却接受同一条路径。本机用 `mklink /J` 复现(symlink 需特权,
  junction 不需要):判据 False / 守卫 True
- [x] 修:根键**同时收词法与解析两种拼写**。只留解析形会丢掉相对行,只留词法
  形又会丢掉已写成解析路径的行;根只有两个,两种形都存也是常数,且仍不产生
  逐行 I/O(实测仍 22.7 ms)
- [x] 词法根不在 job_io 里另抄一份:`allowed_output_roots` 加 `resolve=False`
  参数,根的定义仍只留在 `_path_guard` 一处(抄一份正是本 PR 反复在修的那类)
- [x] 回归测试:junction 场景下两种拼写都必须与产物守卫给出同一答案,边界外
  仍拒绝;无法建联接/该文件系统上两形相同时 skip 而不是假过

## codex #444 r9（两条 P1）

- [x] **r8 放宽准入之后开的新口子**:两种拼写都可入了,折叠键却仍是纯词法——
  `output_link/runs/a` 与 `real/runs/a` 被当成两次运行,同一份产物出现两个
  选择器条目,被覆盖的历史行又能静默渲染当前报告(正是 r2/r4 修掉的那个)。
  修:根键改为 `(某种拼写, 规范键)` 对(规范键取解析形——两种拼写指的是同一个
  物理目录),`canonical_dir_key` 逐行只做前缀匹配 + 替换,仍不碰盘;判据也改为
  **委托**它,一处锚定、一处规范化(r1 是锚点分叉、r9 是规范化分叉,同一类修了
  两次,这次修在共享层)
- [x] 联接场景实测:两种拼写 key 相同 → `newest` 一条、`older` 进被覆盖表;
  真目录折叠不变式不变(wf 92→20/72、pipeline 13→13/0,两表不相交);耗时 22.5 ms
- [x] **「声明只能让判定更差」被反着执行**:`metric_status=official` 而
  `metrics_purpose=predictions_only_non_canonical` 时,页面先照 status 打 ✓、
  再补一句中性说明——非 canonical 的数字被呈现为可用于晋升裁决。修:**渲染前**
  先算 `_effective_status`(声明更弱则采信声明),告警展示生效后的状态,并说明
  「按更弱的那个采信」
- [x] 测试:联接场景升级为同时验准入与折叠;新增降级行为钉(降级须在渲染前算出、
  告警展示 `_effective_status`);三条因实现变化失效的旧钉重新对准
- [x] 顺手修掉自己测试里的一个隐患:按固定字节窗口切函数体会渗进**下一个**函数
  的 docstring(相邻那段正好在讲「为什么不能 resolve」,断言因此误红)。改为按
  下一个顶层 `def` 精确切片

## codex #444 r10（一条 P1 + 一条 P2）——判据改从权威工件推导

- [x] **`topk` 不在入族条件里**(codex):换掉 topk 就是另一个组合,却仍被标成
  认证胜者。顺着查**还漏了 `attribution_sleeve_grouping`**——手挑键名这已经是
  第三次漏(r6 `slippage_bps`、r7 约束三件、r10 这两个)。**修在层次上**:入族
  条件不再手挑,改为 `config/serving/csi800_n5_production.yaml`(两级绑定链第二
  级,治理钉死它与 iso_week 复核 preset 逐值相等)的**全部语义字段**减去族内
  区分维度 `rebalance_anchor`;另加一条测试直接对着治理钉的 `SEMANTIC_KEYS`
  断言键集相等——将来那边加字段,这里会红
- [x] 真实报告实证:盘上 csi800 报告逐个跑判据,认证对(fold_phase 与 iso_week
  各若干)全部入族,pv_* 灵敏度臂被 `slippage_bps/rebalance_cadence_days/
  risk_constraint_scope` 踢出;改 `topk=30` 或 `attribution_sleeve_grouping=False`
  各自被单独踢出
- [x] 预设扫描测试改用**解析后**配置:`topk` 只写在 `config_walk.yaml` 基座里,
  只读原始 preset 会把每个预设都判成不符(raw 扫描从「恰好两个」变成「零个」)
- [x] **两页的合并键仍是各自的词法键**(codex P2):r9 只统一了折叠,页面把 UI
  作业与目录记录合并时还在按原始串比。符号链接根下 UI 作业记一种拼写、目录
  镜像记另一种时会漏配,同一份产物多出一个选择器条目。两页的合并、别名表、
  被覆盖表**全部**改走 `canonical_dir_key`
- [x] 规范键**只用于比对**:选择器键变成规范键后,`Path(selected)` 就不能直接
  拿它去读盘——normcase 在 Windows 上把路径压成小写,在大小写敏感的文件系统上
  直接读不到。新增 `_dir_display` 保存真实路径,读产物与告警文案都用它
- [x] 三条源码钉改为钉**语义**而非值表达式的拼写(`_run_id_to_dir.setdefault(
  _job.run_id,` 前缀):随改名而碎的钉子容易被随手放宽

## codex #444 r11（两条 P1 + 两条 P2）

- [x] **实验语义不在判据里**:改 `ensemble_window=1` / 换模型 / 换训练窗,零不符
  项,一个**实质不同的实验**仍顶着认证胜者文案。服务清单只钉服务语义,钉不住
  「这是哪个实验」。修:身份 = **认证 preset 链**(conservative + 它 extends 的
  基座)∪ **生产服务参数**,两份权威工件整取;实测 `ensemble_window` / `model_type`
  / `train_months` / `topk` / `attribution_sleeve_grouping` 各自能单独踢出
- [x] 身份**收窄到报告契约会记录的字段**:报告 config 是
  `asdict(WalkForwardConfig)`,不含 `provider_uri` / `region`。不做这道交集,
  认证运行自己都会因「缺这两个键」被判出族,标签全灭(实测)。字段名用 `ast`
  从 `walk_forward/config.py` 源码读——**不 import**:导进来会把 **qlib 与 gym**
  拉进这个号称「纯」的 helper(实测 1.19s / 2042 模块;改 ast 后 0.29s / 635,
  qlib 与 gym 均不加载)
- [x] 判据在真实报告上双向核验:20 个 wf 目录 → **恰好 2 个入族**(认证对),
  18 个出族;pv_* 灵敏度臂按 `overall_start/overall_end/slippage_bps/
  rebalance_cadence_days` 出族
- [x] **权威读不出来时不得 fail-open**:此前 `yaml.safe_load` 返回非映射就退化
  成 `{}`,而空要求让 `governed_family_mismatches` 对**任何**配置都返回「无不符
  项」——权威恰恰读不到的时候,页面给每个运行都打认证族标签。修:新增
  `GovernedFamilyUnavailableError`,空/列表/标量一律抛;三种坏载荷各有测试
- [x] **结果页的 owner 表少了 mode 维度**:UI 的滚动验证作业与 CLI 的流水线记录
  可能落在同一个 output_dir,只按目录合并会把流水线 id 别名到那条滚动验证作业
  上,点开渲染的是**另一种模式**的报告。改为按 `(mode, 规范键)` 编排
- [x] **spec delta 与实现不符**:delta 仍写「从晋升 profile 推导」,而实现已改。
  归档会把一条实现不遵守的契约写进 specs/。已改述为两份权威工件的并集、收窄到
  报告契约字段、只排除 `{rebalance_anchor, output_dir}`,并显式写下「读不出来
  必须 fail-loud,MUST NOT 退化成空要求」
- [x] 测试助手补环境变量展开:报告 config 里的路径是展开后的,测试不展开会让
  认证预设自己判不符——那是测试失真不是实现问题;引用未设默认值环境变量的
  战役预设跳过而非崩掉

## codex #444 r12（一条 P1）——身份纳入 dataclass 默认值

- [x] **只取 YAML 的键 = 留着盲区**:YAML 没写的字段运行时落 dataclass 默认值,
  而那些默认值同样是认证身份的一部分。把 `label_horizon_days` 从默认的 1 改成
  5、或改 `risk_constraints_mode` / `metrics_purpose`,都是**实质不同的实验**,
  却因为 YAML 里没这个键而零不符项。修:`_reported_config_defaults()` 用 `ast`
  连**默认值**一起读出(53 个字面量默认 + 3 个模块常量默认,常量也用 ast 从
  各自源文件解析),与 YAML 值叠加成「认证运行实际跑的那套参数」;解析不出默认值
  的字段一律抛错,不默默跳过
- [x] 做的过程中撞出两个**自己的** bug,都已修:
  - `_knob_matches` 对 `None` 判错——`str(None or "")` 是 `""` 而 `str(None)` 是
    `"None"`,于是 `None == None` 被判成**不符**,认证运行自己因四个 None 默认键
    (`stamp_tax_schedule` / `dataset_cache_dir` / 两个 `industry_*`)出族
  - 报告**未记录**的键被当成不符:该字段落地之前的运行根本没有它,那次运行当时
    就跑在契约默认值上。把「缺失」判成「不符」会让**每一份历史报告**出族(本机
    20 个目录全灭)。改为不算不符,但**如实披露**:页面报「覆盖 N 个旋钮,其中
    M 个本报告未记录,按契约默认值采信」,并列出是哪些
- [x] 真实报告复验:20 个目录 → **恰好 2 个入族**(认证对,各 3 个未记录键);
  `label_horizon_days=5` / `ensemble_window=1` / `seed=7` / `model_type=XGB` /
  `topk=30` 逐个能被**单独**踢出
- [x] 测试助手同步:预设扫描要模拟**报告**形态(dataclass 默认值打底 + 解析后的
  预设覆盖)——不打底的话,预设没写的键全落进「未记录」而被跳过,像 `csi800`
  这种只写两三个键的预设也会「入族」
- [x] spec delta 改述为「认证运行的**解析后**参数集(契约默认值 + 权威工件覆盖)」,
  并写下「未记录的键不算不符,但必须披露覆盖数与未记录清单」

## codex #444 r13（一条 P1 + 一条 P2）

- [x] **我在 r12 亲手打断了族门**:把「未记录键」的披露写成了**同级**的第二个
  `if`,后面 anchor 的 `elif` 就挂到了它身上。于是一份**完整记录、只是不入族**
  的报告(`_unrecorded` 为空)会直接落进 `elif _anchor ==`,在「不属于认证族」
  那句后面紧接着拿到「认证胜者」文案——自相矛盾,且正是这一整条 P1 线要防的事。
  修:治理文案**整段嵌进** `else` 分支,标签结构上不可能再脱离族门;测试钉住
  缩进与顺序(`        if _anchor ==` 存在、`    elif _anchor ==` 不得存在)
- [x] **身份随看页面的人的环境漂移**:权威里的数据源路径写成
  `${QUANT_NAMECHANGE_PATH:-...}`,认证运行落盘的是**跑它那个进程**展开的值,
  而我按 **Streamlit 进程**的环境又展开了一次——同一份报告在别的机器/别的环境
  变量下会突然「换族」、丢掉治理标签。修:权威值本身长成 `${...}` 模板的键
  一律不进身份(数据位置不是实验语义);判据**可推导**而非手挑键名
- [x] 跨环境实证:改 `QUANT_NAMECHANGE_PATH` / `QUANT_DELISTED_REGISTRY` /
  `QUANT_PROVIDER_URI` 后重载模块,同一份认证报告的 `(不符项, 未记录项)` 完全
  不变;被剔除的三个键 `provider_uri` / `namechange_path` /
  `delisted_registry_path` 逐个断言不在身份里
- [x] spec delta 补两条:环境模板键 SHALL 按**值的形状**排除(不得手挑清单);
  治理标签 SHALL 渲染在族门**内部**,同级分支不算门

## 验证

- [x] 定向 + governance：490 passed / 1 skipped；jobs 源码钉 11 passed
- [x] mypy CI 精确命令 220 文件 Success；ruff 全过
- [x] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge
