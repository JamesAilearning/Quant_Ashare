# v2-run-center-page（运行中心：数据更新与出单的 UI 触发）

## ADDED Requirements

### Requirement: 页面注册与既有只读承诺不动

运行中心页 SHALL 注册于 `web/operator_ui/app.py` 的 `st.navigation`
「运行」分组，标题为「运行中心」，并调用 `render_page_header`。本页承担
「代跑」职责的同时，既有页面的只读承诺 MUST 保持不动：驾驶舱
（`v2-ops-cockpit-page`）仍只展示不代跑，数据检视页的 import 闭包守卫与
`pit_validation_runner` 的源码钉（含 `06_validate_pit_data`、不含
`daily_update.py`）仍然成立。

#### Scenario: 页面已注册

- **WHEN** 读取 `web/operator_ui/app.py`
- **THEN** `st.navigation` 的「运行」组含 `run_center.py`、标题「运行中心」
- **AND** `_ICON_MAP` 含「运行中心」条目

#### Scenario: 驾驶舱与检视页承诺不因本页改变

- **GIVEN** 本 change 合入
- **WHEN** 运行既有 source-pin 守卫（驾驶舱禁 `st.button`/`subprocess`、
  检视页闭包守卫、`pit_validation_runner` 源码钉）
- **THEN** 全部保持通过，无需任何豁免扩列

### Requirement: 数据更新手动启动为 detached 子进程

页面 SHALL 经独立模块 `web/operator_ui/update_runner.py` 启动
`scripts/daily_update.py` 子进程；页面自身 MUST NOT import `subprocess`。
argv SHALL 镜像调度器（`run_daily_update.bat`）的参数形状：
`--tushare-dir <provider 父目录/tushare_raw> --provider-dir <provider>
--delisted-registry <registry> --reference-cases
<仓库>/tests/pit/reference_cases.yaml --start-date 20180101`，首两元素为
`<python 解释器>` 与仓库布局推导的脚本绝对路径。

子进程 SHALL detached 于 UI 会话（Windows：`CREATE_NEW_PROCESS_GROUP |
CREATE_NO_WINDOW`；POSIX：`start_new_session=True`），`stdin=DEVNULL`，
stdout/stderr 追加写入调度器同一条日志流（`<provider 父目录>/logs/
daily_update.log`），追加前 SHALL 写入一行携带完整日期时间的
「launch attempt」标记（既有日志行仅有时分秒；措辞 MUST NOT 宣称进程
已存在——标记先于 `Popen`），`Popen` 失败路径 SHALL 追加失败标记，
避免调度器后续输出被误归因到未发生的 UI 运行；子进程 env SHALL 经
`utf8_child_env()` 钉 UTF-8。

启动前 SHALL 预检并 fail-loud 拒绝（不启动进程）：(a) argv 三路径
（provider/tushare/registry）任一不是绝对路径——空串会被解析成当前
工作目录，异约定拼写会把数据落到别处；(b) 子进程将继承的 env 中
`TUSHARE_TOKEN` 缺失或为空白；(c) 状态工件属于该 provider 且
state=running 且按 reader 语义分类为新鲜。预检 (c) 是 advisory——并发的
权威仲裁是 `daily_update` 自身的单飞锁（撞锁 exit 17 落日志）；runner
MUST NOT 触碰锁文件。

`launched` 结果仅表示进程已创建（携 pid 与日志路径），MUST NOT 被呈现为
「更新成功」——成败由状态工件与日志承载。

#### Scenario: 非绝对路径拒绝启动

- **GIVEN** 三路径中任一为空串（解析为 `.`）或相对/异约定拼写
- **WHEN** 操作人点击启动
- **THEN** 返回 `unusable_path` 并指名是哪个旗标，进程未被创建

#### Scenario: token 缺失拒绝启动

- **GIVEN** 子进程将继承的 env 无 `TUSHARE_TOKEN`（或仅空白）
- **WHEN** 操作人点击启动
- **THEN** 返回 `no_token`，进程未被创建，页面展示修复指引

#### Scenario: 正在运行时拒绝重复启动

- **GIVEN** 状态工件属于该 provider、state=running 且新鲜
- **WHEN** 操作人点击启动
- **THEN** 返回 `already_running`，进程未被创建
- **AND** 陈旧 running 记录或外来 provider 的记录不触发此拒绝
  （交由单飞锁权威仲裁）

#### Scenario: 成功启动只报告事实

- **WHEN** 预检通过且 `Popen` 成功
- **THEN** 返回 `launched`，携 pid 与日志路径
- **AND** 页面文案不将其呈现为更新成功

#### Scenario: 启动失败响亮

- **WHEN** 日志目录不可创建或解释器无法启动（OSError）
- **THEN** 返回 `launch_failed` 并携原因，绝不静默

### Requirement: 数据更新状态展示复用 reader 语义

页面 SHALL 复用 `web/operator_ui/update_status` reader 的既有语义展示
「上次数据更新」：missing / corrupt / running（新鲜、陈旧、不可核实三态）/
finished 成功 / finished 失败（exit code + 失败阶段），并拒绝展示属于
其他 provider 的记录。页面 SHALL 提供手动刷新；running 且新鲜时启动按钮
SHALL 禁用。

页面 SHALL 让「刷新生效了没」可见：展示本次读取状态工件的时刻，并在
点击刷新后给出即时确认——状态未变时整页重绘与未点击不可区分，无痕迹
的刷新等价于坏按钮。更新进行中（本 provider、running、新鲜）SHALL 自动
轮询状态工件；检测到状态跃迁 SHALL 触发**整页**重绘，MUST NOT 只更新
片段（出单闸门依赖主脚本作用域的判断，只刷片段会让两处显示自相矛盾）。

日志尾部 SHALL 逐行解码（UTF-8 优先、失败回退 GBK、再失败按替换符
降级）：这条共享日志有两个写入者，本页启动的运行钉 UTF-8 而计划任务的
包装脚本未钉（中文落在控制台代码页），整块按单一编码解码会把另一方的
行显示成乱码。

#### Scenario: 刷新可见

- **WHEN** 渲染状态区
- **THEN** 显示本次读取时刻（时分秒）
- **AND** 点击刷新后给出即时确认（读取时刻随之更新）

#### Scenario: 运行中自动轮询并在完成时整页刷新

- **GIVEN** 状态为本 provider 的 running 且新鲜
- **WHEN** 页面驻留
- **THEN** 按固定间隔自动重读状态工件
- **AND** 状态跃迁（如 running→finished）时触发整页重绘，出单闸门随之解锁

#### Scenario: 历史遗留的 GBK 行按解码结果回退

- **GIVEN** 日志同时含 UTF-8 行与编码钉之前写下的 GBK 行
- **WHEN** 渲染日志尾部
- **THEN** UTF-8 行**原样**显示（含 Latin-1／希腊等扩展字符，绝不被改写）
- **AND** UTF-8 解码失败的 GBK 行按旧编码回退，不出现替换符

#### Scenario: 无法还原的历史行如实披露而不是猜

- **GIVEN** 一条 GBK 行的字节**恰好也是**合法 UTF-8（如 `"抓取".encode("gbk")`
  解出 `ץȡ`）
- **WHEN** 渲染日志尾部
- **THEN** 该行按 UTF-8 显示（即显示为乱码），页面明示这类历史行读侧无法还原
- **AND** 系统**不得**按字符区段猜测编码——那会损坏合法的新行（`José` →
  `Jos茅`），是拿新行的损坏换历史行的修复

#### Scenario: 运行中的展示与按钮禁用

- **GIVEN** 状态工件 state=running 且新鲜
- **WHEN** 渲染页面
- **THEN** 展示进行中（始于时间），启动按钮为禁用态

#### Scenario: 失败记录如实展示

- **GIVEN** 状态工件 finished 且 exit_code≠0
- **WHEN** 渲染页面
- **THEN** 展示失败、exit code 含义与失败阶段，绝不用默认值粉饰

### Requirement: 出单同步执行且参数与驾驶舱同源

页面 SHALL 以 `st.code` 展示 `morning_command` 生成的权威命令文本（终端
复制路径保持可用），并 SHALL 经独立模块
`web/operator_ui/recommend_runner.py` 同步运行
`scripts/daily_recommend.py`；执行所用参数值 SHALL 与展示命令来自同一组
解析器取值（`resolve_incumbent` / provider / `resolve_delisted_registry` /
`resolve_name_source` / `serving_bundle_max_age_days`）。

按钮 SHALL 仅在现任为 ensemble、命令文本可渲染（非拒绝态）、**且数据
更新未在进行**（状态工件 running 且新鲜时不提供——`bundle_swap` 的
两段 rename 不与读者并发，出单读者不得撞进换库瞬间的路径真空）时
提供；单模型与不可解析现任只展示说明。argv MUST NOT 含 `--model` /
`--fit-start` / `--fit-end` / `--topk` / `--instruments` /
`--rebalance-cadence-days`——宇宙/节奏/topk 留给 serving config 两级
绑定链在 CLI 内解析。

页面的状态闸门只是 UX；**权威串行化 SHALL 由锁承担**：执行期间
runner SHALL 持有更新器自身的 provider 单飞锁（web 侧镜像模块
`provider_lock`，不 import 管线层；镜像正确性 SHALL 由与
`src.data_pipeline.single_flight` 的**双向互斥行为测试**实证——持此
锁时真更新器拒绝启动，真锁被持时此处拒绝）。锁忙或锁文件不可用 →
`blocked_by_update` fail-closed 拒绝，不派生子进程；锁只覆盖子进程
读窗口，发布（只写 `output/`）在释放后进行。状态工件写失败或
running 记录陈旧时，这把锁就是防止出单撞进换库真空的唯一防线
（状态是 advisory，锁是权威）。

子进程 SHALL 同步运行且以**自有进程组/会话**启动（win32
`CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`；POSIX
`start_new_session`）：管道捕获 + `text/encoding="utf-8"/
errors="replace"`，`cwd=仓库根`，env 经 `utf8_child_env()`，超时默认
900s。超时 SHALL 先终止**整棵进程树**（win32 `taskkill /F /T`；POSIX
`killpg`）再作宽限 drain——joblib 孙进程握着捕获管道，只杀顶层会让
drain 永久阻塞且锁不释放；`taskkill` 非零 SHALL 一律视为终止不完整
（顶层进程已退出**不证明**子孙已终止——死顶层 pid 让 `/T` 无从走树，
孤儿工作进程可能仍持有管道与 bundle）；终止不完整时 SHALL 保留暂存
（避免与残留写者赛跑）、指名顶层 pid、并如实声明锁将在返回后释放。产物 SHALL 写入
`output/daily_recommend/` 下的**每次一新的暂存目录**（`--out-dir`），
完成（exit 0）后逐文件经同卷 `os.replace` 原子发布——超时被杀/退出≠0
只清理暂存，已发布工件 MUST NOT 被触碰。发布 SHALL 带回滚账本：被
替换的旧版本先移入暂存 `.prior`——账本移动 SHALL 直接尝试，仅真实
缺失（`FileNotFoundError`）视为无旧版，其余检查/移动错误一律走整体
回滚（`exists()` 式预检会把瞬态 stat 错误折成 False，让旧版未入账即
被覆盖）；任一步失败 SHALL 整体回滚（新文件退回暂存、旧版本复位）
——顺序发布 MUST NOT 留下混批工件集；仅回滚
不完整才是撕裂态，且残留 SHALL 逐名指出（un-publish 失败的名字不再
复位旧版——那会砸掉新文件的唯一副本，改为指名滞留）。所有发布失败
情形暂存目录 SHALL 保留。
结果 SHALL fail-loud：exit 0 → 展示 stdout 尾部（含 entry_date 横幅）并
引导至「今日推荐」页；exit≠0 → **优先展示 stdout 尾部**——本仓 CLI 的
拒绝原因经 logger 落 stdout（`StreamHandler(sys.stdout)`、
`propagate=False`），stderr 多为 import 期环境噪音，SHALL 次序靠后
（折叠展示）；timeout / launch 失败 / 脚本缺失各自如实展示。

#### Scenario: ensemble 现任成功出单

- **GIVEN** 现任为 ensemble 且命令可渲染
- **WHEN** 操作人点击出单且 CLI exit 0
- **THEN** 展示成功、耗时与 stdout 尾部，并引导至今日推荐页

#### Scenario: 非 ensemble 现任不提供按钮

- **GIVEN** 现任为单模型或不可解析
- **WHEN** 渲染页面
- **THEN** 不出现执行按钮，仅展示说明与（可渲染时的）命令文本

#### Scenario: 绑定参数不进 argv

- **WHEN** runner 组装 argv
- **THEN** argv 恰含 `--ensemble-manifest/--provider-uri/
  --delisted-registry/--name-source/--bundle-max-age-days` 五个同源
  旗标加暂存 `--out-dir`（闭列表，测试以全列表相等钉死）
- **AND** 不含 `--model/--fit-start/--fit-end/--topk/--instruments/
  --rebalance-cadence-days` 中任何一个

#### Scenario: 更新进行中不提供出单按钮

- **GIVEN** 状态工件属于该 provider、state=running 且新鲜
- **WHEN** 渲染出单区
- **THEN** 不出现执行按钮，并说明换库与读者不并发的原因

#### Scenario: 状态失真时锁仍拦得住

- **GIVEN** 更新器仍在运行，但状态工件缺失/写失败/running 记录已被
  分类为陈旧（按钮因此可见）
- **WHEN** 操作人点击出单
- **THEN** runner 因拿不到 provider 单飞锁返回 `blocked_by_update`，
  子进程未被派生，页面如实展示锁为权威的拒绝

#### Scenario: 超时/失败不触碰已发布工件

- **GIVEN** 前一交易日的工件已在 `output/daily_recommend/`
- **WHEN** 本次运行超时被杀或退出≠0
- **THEN** 已发布工件逐字节不变，暂存目录被清理
- **AND** 发布在第二/第三个文件处失败时整体回滚：发布目录恢复为
  旧集合，本次产物完整退回暂存
- **AND** 回滚不完整时残留逐名指出，暂存与回滚目录保留为证据

#### Scenario: CLI 拒绝如实转述

- **WHEN** CLI exit≠0
- **THEN** 页面展示 exit code，并优先展示 stdout 尾部（拒绝原因所在），
  绝不吞掉拒绝原因、绝不用 stderr 的环境噪音顶替它

#### Scenario: 超时被杀且如实报告

- **WHEN** 子进程超过超时上限
- **THEN** 整棵进程树被终止（含 joblib 孙进程），页面展示超时结果
- **AND** 进程树终止不完整时，暂存保留、顶层 pid 被指名、锁的释放
  被如实声明

### Requirement: 执行边界与 runner 目标钉死

spawn 只 SHALL 发生在 `update_runner` 与 `recommend_runner` 两个模块；
页面源码 MUST NOT 含 `subprocess` / `Popen` / `os.system` / 写 API /
`src.data_pipeline` import。每个 runner SHALL 恰指一个 CLI 目标并被
测试钉死：`update_runner` 源码含 `daily_update.py` 且不含
`06_validate_pit_data` / `daily_recommend.py`；`recommend_runner` 源码含
`daily_recommend.py` 且不含 `daily_update.py` / `06_validate_pit_data`。
两 runner MUST NOT import `src.data_pipeline` 下任何模块——与编排器的
唯一耦合是 CLI 进程边界。argv 形状 SHALL 由 fake subprocess 的 logic
测试钉住（首两元素、旗标集合、UTF-8/detach kwargs）。

#### Scenario: 页面不直接 spawn

- **WHEN** 扫描 `run_center.py` 源码
- **THEN** 无 `subprocess`/`Popen`/`os.system`/`open(`/写 API/
  `src.data_pipeline`，且引用了两个 runner 模块名

#### Scenario: runner 目标互斥钉死

- **WHEN** 扫描两个 runner 源码
- **THEN** 各自含且仅含自己的 CLI 目标文件名，互不出现对方目标，
  也不出现 `06_validate_pit_data`
