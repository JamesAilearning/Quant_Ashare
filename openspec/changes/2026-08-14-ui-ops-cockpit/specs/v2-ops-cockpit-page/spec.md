# v2-ops-cockpit-page（生产运维只读驾驶舱）

## ADDED Requirements

### Requirement: 驾驶舱只读，命令只展示不代跑

本页 MUST 只渲染已落盘工件与只读探针的结果，MUST NOT 触发任何作业、训练、
GPU、轮换或推断，MUST NOT 写入任何文件。运维命令 MUST 以**可复制的纯文本**
呈现，由操作人自行在终端执行。

#### Scenario: 页面不含任何执行/写入面

- **WHEN** 检查 `web/operator_ui/pages/ops_cockpit.py` 源码
- **THEN** 不出现 `JobManager` / `subprocess` / `job_runner` / `import qlib`
  等作业与训练触发面
- **AND** 不出现 `open(` / `write_text` / `write_bytes` / `mkdir` 等写侧 API

#### Scenario: 不可逆命令必须标注

- **WHEN** 页面展示会改写生产 manifest 或不可逆的命令
- **THEN** 该命令 MUST 带显著的不可逆标注，MUST NOT 与只读命令同等呈现

### Requirement: 命令由已解析的部署状态生成

页面展示的命令 MUST 由本页**已解析**的部署状态构造，MUST NOT 印出
`$QUANT_*` 这类环境变量拼写——出单侧之外的脚本并不读这些变量，未设时
操作人的 shell 会把它展开成空串，得到一条静默做错事的「可复制」命令。
无对应环境变量的参数 MUST 以显式占位符呈现，并说明其来源。

#### Scenario: 未设变量的默认部署

- **GIVEN** `QUANT_*` 均未设，页面按文档化默认解析
- **THEN** 命令中出现的是解析后的真实路径，MUST NOT 出现 `$QUANT_*`

#### Scenario: 单模型 opt-out 部署

- **GIVEN** `QUANT_ENSEMBLE_MANIFEST` 显式为 `none`
- **THEN** 晨跑命令为 `--model <已解析模型路径>` 形态
- **AND** MUST NOT 出现 `--ensemble-manifest`，MUST NOT 把 `none` 当路径传入

#### Scenario: 门命令必须显式携带已解析的数据路径

- **GIVEN** 部署覆盖了 `QUANT_PROVIDER_URI` 或 `QUANT_NAMECHANGE_PATH`
- **WHEN** 页面给出 `retrain_gate.py` 的两条 scope 命令
- **THEN** 两条命令 MUST 显式携带 `--provider` 与 `--namechange`，
  取值为本页解析到的路径
- **AND** MUST NOT 依赖 CLI 的硬编码默认值——gate 工件不记录任何数据路径，
  用错 bundle 产出的 PASS 事后无从分辨，却足以授权一次生产轮换

#### Scenario: 晨跑命令必须指明整个部署

- **GIVEN** 运行 UI 的环境与操作人终端的环境未必相同
- **WHEN** 页面给出晨跑出单命令
- **THEN** MUST 显式携带 `--provider-uri` / `--delisted-registry` /
  `--name-source`，取值为本页解析到的路径
- **AND** MUST NOT 只给模型/manifest 而让 CLI 回落到它自己的默认值——
  那会让这条命令产出一份**实盘清单**，其数据源与页面刚报告的不是同一个

#### Scenario: 现任不可解析时不给出可运行命令

- **GIVEN** 现任指针不可解析
- **THEN** 页面 MUST NOT 给出一条指向该 manifest 的可运行命令——
  那等于交给操作人一条用无法确认之模型出单的命令

### Requirement: 命令中的已解析路径必须是单个 shell 参数

页面把已解析路径插入命令文本时 MUST 按目标 shell 转义，使每个路径构成
**单个**参数。路径来自文件系统或环境覆盖，合法地可能含空格或 shell
元字符；裸插值会把一个路径拆成多个 argv 项（门于是跑在另一份数据上），
或让元字符作为 shell 语法执行。

#### Scenario: 命令必须能粘进操作人的 shell

- **GIVEN** 本仓库文档化的平台是 Windows / PowerShell
- **THEN** 生成的命令 MUST NOT 使用 POSIX 续行符 `\`——PowerShell 不认它，
  会把下一行当独立语句并报错
- **AND** 命令 MUST 以在 **PowerShell 与 POSIX shell** 中都可粘贴的形式呈现
- **AND** 页面 MUST NOT 声称支持 `cmd.exe`——它不把单引号当参数分隔符，
  含空格的路径会被切成多个参数（实测 `ARGV= ['--provider-dir', "'D:/qlib", "bundles/live'"]`）

#### Scenario: 每个值都必须加引号，而不只是 POSIX 认为需要的那些

- **GIVEN** 某路径名为 `@bundle`（POSIX 判定无需引用）
- **THEN** 页面 MUST 仍然给它加引号——PowerShell 会把裸 `@name` 读成
  splatting 语法并**整个丢掉**该参数（实测 `ARGV= ['--provider-dir']`）
- **AND** 引用 MUST NOT 依据某一种 shell 的「是否需要」判据

#### Scenario: 无法跨 shell 表达的路径必须明说

- **GIVEN** 某路径含单引号（POSIX 与 PowerShell 的转义方式不同）
- **THEN** 页面 MUST 明说无法给出通用写法，MUST NOT 输出一个在其中一种
  shell 里静默出错的命令

#### Scenario: 拒绝本身不得把该路径插回命令文本

- **GIVEN** 某路径无法跨 shell 表达，页面因此改为给出一段拒绝说明
- **THEN** 被展示的**命令文本** MUST NOT 含该路径的原值——把它插进拒绝语
  只是换个位置把同一段字符交给 shell（实测 `a'b' ; touch /tmp/pwned #`
  经拒绝语后仍在 bash 中执行，文件真被创建）
- **AND** 该命令文本的**每一行** MUST 以注释符起头，使其整体在 PowerShell
  与 POSIX shell 中均为惰性
- **AND** 原值只 MAY 出现在命令文本之外的说明字段里（操作人仍要看见是哪条
  路径出了问题）

#### Scenario: 未解析出的路径不得渲染成空参数

- **GIVEN** `config.yaml` 缺失、无法解析，或没有 `provider_uri` 字段——
  `resolve_default_provider_uri()` 于是返回空串
- **THEN** 页面 MUST 走同一道拒绝路径，MUST NOT 渲染 `--provider-uri ''` /
  `--provider ''` / `--provider-dir ''`
- **AND** 理由不是「引不了」而是**引得太好**:`''` 是一个合法参数值，而
  `Path("")` 即 `Path(".")`（实测 `Path("") == WindowsPath(".")`），照跑会把
  工具**静默指向操作人的当前工作目录**而不是部署
- **AND** 两种拒绝的说明 MUST 可区分——「修 config.yaml」与「换个不含单引号
  的路径」是两种不同的修法，共用一段文字会把操作人指错方向

#### Scenario: 够不到的东西不得给出裁定

- **GIVEN** provider 路径未解析
- **THEN** 完整性这一道 MUST 报「无法判定」，MUST NOT 报 accepted/refused
  ——改前它返回 `known=True, accepted=False`，即对一份从未定位到的 bundle
  给出了确信的拒绝裁定
- **AND** 日历尾部这一道 MUST 报同一原因，MUST NOT 报「读不到
  `calendars/day.txt`」——那句话在指责一份本页从未找到的 bundle
- **AND** 两道门 MUST NOT 去读当前工作目录:即使 CWD 下恰好存在一份合法的
  `calendars/day.txt`，答案仍 MUST 是「不知道」

#### Scenario: 没有 ensemble 就没有轮换流程

- **GIVEN** 现任是单模型（`QUANT_ENSEMBLE_MANIFEST=none`），或其 manifest
  无法解析
- **THEN** 季度轮换卡 MUST 整卡拒绝，MUST NOT 拿占位串顶替 manifest 后照常
  渲染两道门与**不可逆**的 `execute` 一步
- **AND** 理由:④ 已经说明轮换在此不适用，再把完整可跑流程印出来是页面自相
  矛盾——把不适用的流程展示成适用的，比缺一个流程更糟
- **AND** 「没有 manifest」的空串写法 MUST 与 `None` 同样处理，不得因类型
  不同而落回占位串

#### Scenario: 「没看过」与「看过是坏的」必须写法不同

- **GIVEN** 完整性这一道无法评定（provider 未解析）
- **THEN** `accepted` MUST 保持未评定（`None`），MUST NOT 写成 `False`
- **AND** 该不变式 MUST 由读取器自己保证，MUST NOT 由某个调用点就地修补
  ——修在调用点等于让其余消费者继续拿到错误值
- **AND** 对照:stamp **存在但字节不可用**是**已知**的拒绝（`known=True,
  accepted=False`）。「我看了，是坏的」与「我根本没看」不得写成同一个值

#### Scenario: 未解析状态必须一次说清

- **GIVEN** provider 路径未解析
- **THEN** 页面 MUST 在顶部一次性说明原因与后果，MUST NOT 只留下若干张各自
  写着「无法判定」的卡片让操作人自己拼出病因

#### Scenario: 不可表达的字符必须封闭列举

- **GIVEN** 某路径含换行或回车（而非单引号）
- **THEN** 页面同样 MUST 走拒绝路径——换行会把一条命令变成两条，与单引号
  同属「无法安全渲染」而不只是「引用方式不同」

#### Scenario: 拒绝必须由命令构造的边界统一执行

- **GIVEN** 页面上任一条被展示的命令
- **THEN** 该命令的构造 MUST 经过同一道拒绝边界，MUST NOT 由各构造函数各自
  判断——漏掉一处，那一处就恢复成可执行的注入面

#### Scenario: 路径含空格或元字符

- **GIVEN** `QUANT_PROVIDER_URI` 被覆盖为 `/srv/qlib bundles/live`
- **WHEN** 页面渲染任一含该路径的命令
- **THEN** 该路径在命令中 MUST 解析为单个 shell 参数

### Requirement: 驾驶舱印出的每条路径都不得是第二份实现

每条被本页印出的路径 MUST 来自其既有 owner，MUST NOT 在本页另立一份实现或
复述其字面量。本页把已解析路径**印进操作人要跑的命令**，且是以显式 flag 的
形式，所以一份漂移的默认值不只是显示错——它会用陈旧路径**覆盖**真正的默认
值，照跑即生效。

#### Scenario: 已有 owner 的解析器必须复用

- **GIVEN** `web/operator_ui/config_forms.py` 已拥有 `resolve_namechange_path()`
  且配置作业路径仍在用它
- **THEN** 本页 MUST 复用同一个可调用物（同一性，不是「取值相等」），
  MUST NOT 另写一份
- **AND** 理由:两份实现在默认值或规范化方式任一漂移时，驾驶舱印出的门命令
  与 UI 生成的作业会选中**不同的 ST 历史**，且没有任何东西会报出这个分歧

#### Scenario: 生产侧默认值必须来自生产侧自己

- **GIVEN** `RecommendationConfig.name_source_parquet` 的 `default_factory`
  是出单侧真正读到的那份默认值
- **THEN** 本页 MUST 调用它，MUST NOT 复述其字面量
- **AND** 页面 MUST NOT 顺手加出单侧没有的规范化(`.strip()`、把 `""` 当未设)
  ——本页要印的是机器会用的值，不是本页认为应该用的值
- **AND** 该字段若不再是 `default_factory`，本页 MUST fail loud，
  MUST NOT 退回字面量

#### Scenario: 复用不了 owner 的，必须连环境变量语义一起对齐

- **GIVEN** `scripts/daily_recommend.py` 以裸 `os.environ.get(VAR, DEFAULT)`
  取值——不 `.strip()`，也不把 `""` 当未设
- **WHEN** 本页解析 `QUANT_MODEL_PATH` / `QUANT_DELISTED_REGISTRY`
- **THEN** 本页 MUST 采用同一语义，MUST NOT 自加规范化
- **AND** 理由:本页把该值印成**显式 flag**，显式 flag 会覆盖默认值——所以
  多出来的规范化不是「显示得更好看」，而是让照跑的命令作用在**另一个工件**上
- **AND** 对齐 MUST 覆盖空值与带空白的取值（两种拼写正是分歧所在），
  MUST NOT 只对拍未设时的默认值

#### Scenario: 语义对齐引出的空值必须被安全处理

- **GIVEN** `QUANT_MODEL_PATH` 被设为空串——本页不再顶替，故空路径可达
- **THEN** 读取旁文件的可调用物 MUST 返回「没有」，MUST NOT 抛异常把页面
  打成 traceback（`Path("").with_suffix(...)` 会抛 empty name）
- **AND** 构造旁文件路径的可调用物 MUST 拒绝空输入，MUST NOT 臆造一对
  指向工作目录的路径
- **AND** 页面 MUST 直接指明是该环境变量为空，MUST NOT 只留一条数据源为空的
  「元信息缺失」告警让操作人自己反推

#### Scenario: 与本部署无关的取值不得被报成故障

- **GIVEN** 现任是 ensemble，而 `QUANT_MODEL_PATH` 被设为空串
- **AND** ensemble 模式下 CLI 与 `--ensemble-manifest` **互斥地拒绝**
  `--model`，根本不读该默认值
- **THEN** 页面 MUST NOT 因此报错——那是在生产实际运行的形态上报告一个
  不可能发生的故障
- **AND** 命令构造 MUST NOT 因该取值而拒绝渲染一条**根本不携带** `--model`
  的命令
- **AND** 单模型形态下同一取值 MUST 照常触发上述两者——判据是「该取值是否
  进入这条命令」，不是「该取值本身好不好」

#### Scenario: 没有 owner 可复用的默认值必须被机器锁住

- **GIVEN** 某路径默认值在仓库中没有单一 owner 可复用
- **THEN** 它 MUST 被并入既有的路径默认值治理表一起校验，
  MUST NOT 只靠人眼保持一致

### Requirement: 现任身份由两页共用的同一解析器给出

驾驶舱与今日推荐页 MUST 通过**同一个**现任解析器取得生产模型身份，
MUST NOT 各自实现一份。未设指针 MUST NOT 被推断为单模型；指针已设但校验器
拒绝时 MUST 醒目 WARN，MUST NOT 回退为单模型形态或占位值。

#### Scenario: 两页答案不可能分歧

- **WHEN** 检查两页取得现任身份的路径
- **THEN** 二者解析到同一个共享模块的同一函数

#### Scenario: 现任不可解析

- **GIVEN** 现任指针已设但规范校验器拒绝
- **THEN** 驾驶舱醒目 WARN 并给出拒绝原因，MUST NOT 显示任何单模型或占位身份

### Requirement: gate 状态的权威是入库 baseline 的摘要，不是 gate 文件本身

gate 卡片 MUST 先用 `docs/promotion/csi800_n5_bootstrap_baseline.json` 的
`authorized_by.gate_artifacts[*].sha256` 校验所读工件的实际内容摘要，
校验通过后 MUST 逐字转录工件里的 `overall` 与各具名门 `verdict`，
MUST NOT 重新推导任何 verdict。摘要不符时 MUST 显示证据链断裂，
MUST NOT 显示 PASS。

#### Scenario: 工件内容与授权摘要不符

- **GIVEN** 某份 gate 工件的实际 sha256 ≠ baseline 记录的摘要
- **THEN** 该卡片显示「证据链断裂」并给出两个摘要
- **AND** MUST NOT 显示该工件自称的 `overall`

#### Scenario: 缺门

- **GIVEN** 某作用域的工件缺少 `expected_gates(scope)` 中的某一道门
- **THEN** 页面显示缺门清单，该卡片 MUST NOT 呈现为通过

#### Scenario: 转录的必须是被哈希的那份字节

- **GIVEN** 基线记录路径下的工件在两次读取之间被替换
- **WHEN** 页面渲染该 gate 卡片
- **THEN** 每个候选文件 MUST 只读取一次，摘要与解析 MUST 取自同一块 buffer
- **AND** MUST NOT 出现「摘要按旧字节通过、结论来自新字节」的状态

#### Scenario: 非 PASS 的裁定不得被渲染成通过

- **GIVEN** 某份摘要已授权的工件其 `overall` 为 `FAIL`（或缺失），且不缺门
- **THEN** 该卡片 MUST 呈现为未通过，MUST NOT 呈现为成功或「通过」
- **AND** 任一具名门的 verdict 非 PASS 时同样 MUST 呈现为未通过——
  即使 `overall` 自称通过

#### Scenario: 贴边余量必须可见

- **WHEN** `serving_veto` 的任一指标接近其阈值
- **THEN** 页面显示该指标的实测值、阈值与余量，MUST NOT 只显示 PASS

### Requirement: recert 有效期来自执行器的判定函数与被 pin 的同一 rev

recert 状态与 15 个月有效期 MUST 由 `scripts.rotation_lib` 的
`parse_recert_status()` / `recert_validity()` 真跑得出，MUST NOT 手填、
常量化或缓存成事实。读取 MUST 先 pin 一次主线 rev，再用**同一 rev** 读状态
正文与读该路径的 tip commit 日期。页面 MUST 显示所 pin 的 rev。

#### Scenario: 正文与日期同源

- **WHEN** 页面读取 recert 状态
- **THEN** 状态正文与有效期锚日期取自同一个被 pin 的 commit

#### Scenario: 判定所用的时钟必须与执行器一致

- **GIVEN** `recert_validity` 以 `now.date()` 比较到期日且不做时区归一，
  而轮换执行器传入的是 UTC 瞬时
- **WHEN** 页面判定有效期
- **THEN** MUST 使用同一 UTC 时钟，MUST NOT 传入本地时区瞬时——
  否则在到期日边界上，页面会与执行器给出相反的结论

#### Scenario: 探针必须在执行器的那个仓库里跑

- **GIVEN** Streamlit 从 checkout 之外启动（例如以某服务工作目录运行
  `streamlit run /checkout/web/operator_ui/app.py`）
- **THEN** 探针 MUST 显式指定执行器读取的那个仓库，MUST NOT 继承进程的当前
  工作目录——**UI 从哪里启动**不是被描述的那个部署的属性
- **AND** 该仓库 MUST 复用执行器自己的常量，MUST NOT 另推一次
- **AND** 若不指定，健康的部署会一路报「无法判定」——一个本可作答却答不出的
  「不知道」，和一个错误答案一样是缺陷

#### Scenario: 既被读又被印的相对路径必须锚在命令执行处

- **GIVEN** `config.yaml` 里的 `provider_uri` 是合法的**相对**路径，且
  Streamlit 从 checkout 之外启动
- **THEN** 页面 MUST 把它按**命令将要执行的目录**（仓库根）解析后再读、再印，
  MUST NOT 用 Streamlit 的工作目录读、却把同一串相对写法印进命令
- **AND** 否则页面描述的是一个 bundle、命令跑的是另一个，且下游**无从察觉**
  这次调包
- **AND** 同一规则 MUST 覆盖所有「既被读又被印」的路径（provider、现任
  manifest、单模型 `--model`）；manifest MUST 在**读之前**就锚定
- **AND** 绝对路径与空值 MUST 原样返回——锚定只许消除歧义，不许发明 CLI 不
  共享的规范化

#### Scenario: 绝对性不得问宿主

- **GIVEN** 本仓库文档化的默认值是 Windows 路径（`D:/qlib_data/…`），而 CI
  的一半跑在 Linux 上
- **THEN** 「是否绝对」MUST 按 **Windows 与 POSIX 两种约定之一**判定，
  MUST NOT 只问运行宿主——`os.path.isabs("D:/…")` 在 Linux 上为假，锚定会
  造出 `/checkout/D:/qlib_data/…` 这样一条哪里都不存在的路径
- **AND** 在错误的宿主上，该路径 MUST 直接读不到并如实报出，MUST NOT 被改写成
  一个掩盖来源的新路径

#### Scenario: 另一套约定下的绝对路径必须被拒绝

- **GIVEN** `provider_uri` 写作 `D:/qlib_data/…`，而本机是 POSIX
- **AND** POSIX 没有盘符，读取器于是把它当**相对**路径按各自工作目录解析
  （实测：Streamlit 起于 `/tmp` 读 `/tmp/D:/qlib_data/…`，命令起于仓库根读
  `<checkout>/D:/qlib_data/…`）
- **THEN** 本页 MUST 明说该路径在本机不可用并拒绝为其生成命令，
  MUST NOT 照原样使用
- **AND** MUST NOT 把它锚到仓库根——那样页面与命令确实一致了，却一致地指向
  一个不存在的位置，会把操作人引去查「bundle 丢了」而不是「路径配错了」
- **AND** 在该写法本就成立的宿主上（Windows），一切 MUST 保持不变

#### Scenario: 判据是「本机完全限定」，不是 `os.path.isabs`

- **GIVEN** Windows 上 `provider_uri` 写作 `/srv/bundle`
- **AND** `ntpath.isabs("/srv/bundle")` 为 **True**，但该路径只是「有根」，
  它按**当前盘**解析（实测：`join("D:/checkout", "/srv/bundle")` 得
  `D:\srv\bundle`，`join("C:/checkout", …)` 得 `C:\srv\bundle`）
- **THEN** 本页 MUST 以「本机**完全限定**」（Windows 要盘符 + 根，POSIX 要
  前导 `/`）为判据，MUST NOT 用 `os.path.isabs`
- **AND** 该写法在 Windows 上 MUST 被拒绝、在 POSIX 上 MUST 正常可用——
  判据随宿主变，是因为「同一串字符指不指一处」本就随宿主变

#### Scenario: `~` 必须展开后再同时用于读与印

- **GIVEN** `QUANT_MODEL_PATH=~/model.pkl`
- **THEN** 页面 MUST 展开后再读、再印。原样返回会让页面读一个字面 `~` 目录，
  而印出的命令因本页**无条件加单引号**（r17）同样不会被 shell 展开——两边
  各错一次，且错得不一样
- **AND** `~` 无法解析时（无 HOME / `~unknown`）MUST 原样返回，
  MUST NOT 把 `~` 当成目录名去锚定

#### Scenario: 外来写法的 manifest 必须在读之前拒绝

- **GIVEN** `QUANT_ENSEMBLE_MANIFEST` 是 `D:/…`，而本机是 POSIX
- **THEN** 解析器 MUST 在**读之前**拒绝，MUST NOT 交给 serving loader
- **AND** 理由:POSIX 上该指针是相对路径，若 Streamlit 工作目录下恰好存在同名
  的 `D:/…` 目录树，两页会**静默**把那个无关 ensemble 当作生产现任报出——
  这正是本页存在的意义所要杜绝的最坏一种
- **AND** 该状态 MUST 是「不可解析」，MUST NOT 退化为单模型形态

#### Scenario: 命令的工作目录依赖必须说明

- **GIVEN** 生成的命令以 `scripts/…` 这样的仓库相对路径命名脚本
- **THEN** 页面 MUST 写明须在仓库根目录执行，MUST NOT 默认操作人已经站对
  地方——这是页面**控制不了**的那一个工作目录依赖，故只能声明
  （数据路径一律绝对，不受影响）

#### Scenario: git 探针不可用

- **GIVEN** 本机 `origin/main` 不可解析或 git 探针失败
- **THEN** 页面显式显示「无法判定」及原因，MUST NOT 显示上一次的结果，
  MUST NOT 默认为有效

### Requirement: 季度重训窗口 MUST 标明由间距硬 pin 推导

页面 MUST 把下一名成员 `fit_end` 的可接受窗口显示为
`[最新成员 fit_end + MEMBER_SPACING_DAYS_MIN, 最新成员 fit_end +
MEMBER_SPACING_DAYS_MAX]`，并 MUST 写明这是由 serving 校验器的间距硬 pin
**推导**所得。本仓库不存在「下次重训到期日」的机器可读锚，页面
MUST NOT 呈现一个看起来像仓库事实的到期日。

#### Scenario: 窗口已关闭

- **GIVEN** 今天晚于 `最新成员 fit_end + MEMBER_SPACING_DAYS_MAX`
- **THEN** 页面显示窗口已关闭及关闭天数
- **AND** 显示「用今天的数据训，间距为 N 天，超出上限，serving 校验器会拒绝」

#### Scenario: 不得伪造到期日

- **WHEN** 检查页面文案
- **THEN** 窗口一律标注为由间距硬 pin 推导
- **AND** 不出现任何未经推导说明的「下次重训到期日」

### Requirement: 每一道被展示的前置校验必须由出单侧自己的可调用物评定

页面凡是展示「出单侧会不会拒绝」这类判定，其**每一个**组成部分 MUST 由
出单侧自己的读取器/谓词评定——年龄用与 `_bundle_is_stale` **相同的算术**
（并由测试逐日对拍该谓词）与出单侧同源的日历尾，完整性用
`read_bundle_integrity` 加该门自己的三条规则。

信息性摘要（`summarise_bundle_health` 的 `status`）MUST NOT 代替任何一道门：
它刻意宽容（`training_guards` 吞掉损坏的 stamp 并回退到 `validation.json`），
因此只能**收回**「可用」，不能**授予**「可用」。

页面 MUST 写明这些校验是出单侧前置条件的**子集**，全部通过 MUST NOT 被表述为
「今天一定能出单」。

#### Scenario: 缺失或损坏的完整性 stamp

- **GIVEN** bundle 的 `_fetch_integrity.json` 缺失或损坏，而 `validation.json`
  存在使得健康摘要不报任何告警
- **THEN** 页面 MUST NOT 呈现为可用——出单侧对两者都拒绝
- **AND** 损坏 MUST 被无条件拒绝（`--allow-holey-recommend` 只接受已知的
  不完整状态，不接受不可读）

#### Scenario: 机器的可调用物够不到时

- **GIVEN** 某道校验由页面调不动的东西评定（如出单侧的 `calendar[-1]` 来自
  `D.calendar()`，需 `qlib.init()`，而本页禁 `import qlib`）
- **THEN** 页面 MUST NOT 用一个近似解析器冒充它，MUST NOT 把该近似描述为
  「与出单侧同源」
- **AND** 页面 MUST 只在输入**无歧义**时作答（近似必须是**保守**的：
  可以在机器没问题时报「无法判定」，绝不可反过来），否则报「无法判定」及原因

#### Scenario: 保守必须真的保守

- **GIVEN** `date.fromisoformat` 接受 `2026-W32-1` / `20260803` 等非规范拼写，
  而 bundle 生产端只写规范的 `YYYY-MM-DD`
- **THEN** 页面 MUST 先校验规范写法再解析——只要有一行不是规范写法即
  「无法判定」
- **AND** MUST NOT 因为「解析器能读」就作答:那会给出 `D.calendar()` 未必
  接受之字节的自信答案，正好违反本页自己声明的保守性

#### Scenario: 校验作用于原始行

- **GIVEN** 某行为 ` 2026-08-03` 或 `2026-08-03	`（含首尾空白）
- **THEN** MUST 判为不规范并报「无法判定」——校验 MUST 作用于文件里的
  **原始行**，MUST NOT 先规范化再校验（那等于给没人校验过的字节背书）

#### Scenario: 日历字节契约必须封闭列举

- **WHEN** 页面读取交易日历以判定尾部日期
- **THEN** 契约 MUST 逐条列举并全部校验:可 UTF-8 解码、行终止符只允许
  LF 或 CRLF、至多一个末尾换行、每行恰为规范 `YYYY-MM-DD`、严格递增、非空
- **AND** MUST NOT 依赖 `str.splitlines()` 的隐含断行集（它还会在
  VT / FF / NEL / LS / PS 处断行），也 MUST NOT 依赖读取时的
  universal-newline 折叠（它会把孤立 CR 折成 LF）

#### Scenario: 子集免责

- **WHEN** 页面展示这些前置校验的结论
- **THEN** MUST 写明它们只是出单侧前置条件的子集

### Requirement: 数据新鲜度复用既有读取器与既有阈值

页面 MUST 用 `RecommendationConfig.bundle_max_age_days` 作为拒绝阈值，
MUST NOT 新造第二个阈值。尾部日期 MUST 读自 `calendars/day.txt`（见下方
「尾部日期必须与出单侧同源」），MUST NOT 取自
`bundle_health.summarise_bundle_health()` 偏好的 `_fetch_integrity` identity
tail——后者在此**仅**用于健康信号（它只能收回「可用」，不能授予）。
页面 MUST 写明尾部日期取自哪一条读取路径。

#### Scenario: 显示落后天数与余量

- **WHEN** bundle 尾部日期可读
- **THEN** 显示尾部日期、落后天数、距拒绝阈值的余量

#### Scenario: 判定所用的「今天」必须与出单侧一致

- **GIVEN** `daily_recommend.py` 以宿主本地 `date.today()` 判定过期
- **WHEN** 页面预测「今天出单会不会被拒」
- **THEN** MUST 使用同一语义的「今天」，MUST NOT 使用面向操作人的
  CN 本地日期——两者在 UTC 宿主上的 CN 零点至 08:00 相差一天，
  恰在阈值边界会给出相反的结论

#### Scenario: 尾部日期必须与出单侧同源

- **GIVEN** `_fetch_integrity` 的 identity tail 与 qlib 日历尾在不完整换库后分歧
- **WHEN** 页面比较 bundle 年龄
- **THEN** MUST 读 `calendars/day.txt`（出单侧 `calendar[-1]` 的同一来源），
  MUST NOT 使用 `summarise_bundle_health` 偏好的 identity tail

#### Scenario: 年龄通过不等于可用

- **GIVEN** 日期够新，但 bundle 健康检查报 warning（如 `built_from_holey_fetch`）
- **THEN** 页面 MUST NOT 呈现为成功——出单侧在年龄检查之后还有其它前置校验
- **AND** MUST 显示该健康告警本身

#### Scenario: 生成的命令必须携带被预测的那个阈值

- **GIVEN** `scripts/daily_recommend.py` 的 `--bundle-max-age-days` 有它
  **自己的** argparse 默认值，与 `RecommendationConfig.bundle_max_age_days`
  相互独立
- **THEN** 晨跑命令 MUST 显式携带页面预测所用的那个阈值——否则页面按一个数
  判定「会不会被拒」，而粘贴出去的命令按另一个数执行

#### Scenario: 阈值不得硬编码

- **WHEN** 检查页面与其 helper 源码
- **THEN** 拒绝阈值来自 `RecommendationConfig` 的字段，而非页面里的字面量
