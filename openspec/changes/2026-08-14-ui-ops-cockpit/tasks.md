# Tasks: 2026-08-14-ui-ops-cockpit

## W1 现任身份（共享，不重写）
- [x] `resolve_incumbent` / `IncumbentIdentity` / `load_ensemble_manifest_identity`  ← web/operator_ui/incumbent.py
      + 环境变量常量上移到 `web/operator_ui/incumbent.py`
- [x] `pages/_daily_decision_helpers.py` re-export（`# noqa: F401`），  ← 显式 `X as X`(mypy --strict 要求)
      今日推荐页与其 63 个钉子一字不改
- [x] 钉：两页解析到同一个共享模块的同一函数（分歧不可能）  ← assertIs 同一函数对象;C9 咬住

## W2 四份 gate 工件（转录 + 哈希绑定）
- [x] 只读读取器：baseline 摘要为权威 → 逐份校验 sha256 → 逐字转录 verdict  ← 按内容定位,避开被否决的 v1 批次
- [x] 用 `retrain_gate_lib.expected_gates(scope)` 只检缺门，不重推判定  ← 不重推任何 verdict
- [x] 摘要不符 → 证据链断裂，不显示 PASS（钉）  ← C1 咬住
- [x] `serving_veto` 贴边余量显示（csi500_weight 0.7484 / 上限 0.75）  ← 实测 0.7484/0.75 判为贴边;C8 咬住

## W3 recert 状态与 15 个月有效期
- [x] 复用 `rotation_lib.parse_recert_status` / `recert_validity` 真跑  ← 实跑得 valid until 2027-11-05
- [x] 先 pin rev，再用同一 rev 读正文与 tip 日期（钉：同源）  ← C3 咬住(只允许一次 rev-parse)
- [x] 页面显示所 pin 的 rev  ← 供操作人判断本地 origin/main 新鲜度
- [x] git 探针失败 → 显式「无法判定」，不默认有效、不显示陈旧结果（钉）  ← C2/C4 咬住;探针对任何异常都降级

## W4 季度重训窗口
- [x] 由 `MEMBER_SPACING_DAYS_MIN/MAX` 推导下一成员 fit_end 可接受窗口  ← C10 咬住
- [x] 显示窗口开/关、关闭天数、以及「今天训会不会被拒绝」  ← 实测:已关闭 35 天,今天训会被拒
- [x] 钉：文案必须标注「由间距硬 pin 推导」；不得出现无说明的到期日  ← C5 首轮被邻居文案空过,已改为按段锚定

## W5 数据新鲜度
- [x] 复用 `summarise_bundle_health` + `RecommendationConfig.bundle_max_age_days`  ← 实测尾 2026-08-03,落后 11,余 3
- [x] 钉：阈值来自配置字段而非页面字面量  ← C6 首轮因 14==14 巧合空过,已改为挪动配置验证跟随
- [x] 写明尾部日期的取数路径  ← 见规格「尾部日期必须与出单侧同源」；初稿写的 provider 元数据口径已于 W9/W11 推翻，此处不复述契约以免二次漂移

## W6 页面与注册
- [x] `pages/ops_cockpit.py`（薄渲染）+ `pages/_ops_cockpit_helpers.py`（纯）  ← + _ops_cockpit_helpers.py(纯)+ recert_health.py(探针)
- [x] `app.py` 注册进「运行」组 + `_ICON_MAP` 图标（`\U0001xxxx` 转义写法）  ← 生产运维 + 🛠
- [x] `render_page_header`；不得出现 `render_breadcrumbs`；不得硬编码十六进制色值  ← 通用 glob 扫描绿
- [x] 可复制 CLI 命令文本；不可逆命令显著标注  ← 换库/轮换执行两条;C7 咬住
- [x] `docs/operations-env-vars.md`：`QUANT_ENSEMBLE_MANIFEST` 的  ← 已补第二个消费者 + QUANT_PROVIDER_URI 行
      「Consumed by: operator UI ONLY (今日推荐 …)」一行需同步（新增消费者）

## W7 codex #431 r1（两条 P2，均属实）
- [x] gate 读取改为**单次读**：摘要与解析取自同一 buffer  ← 原实现先哈希后重读,中间可被换文件
- [x] 命令改由**已解析部署状态**生成，不印 `$QUANT_*`  ← 未设时 shell 展开成空串
- [x] 单模型 opt-out → `--model` 形态（`none` 不能当路径传）  ← C13 咬住
- [x] 现任不可解析 → 不给可运行命令  ← C14 咬住;不交出指向未确认模型的命令
- [x] `resolve_model_path` 一并上移 incumbent.py（驾驶舱也要用）  ← 再 re-export
- [x] 续行符修复（heredoc 曾吞掉一个反斜杠，命令印成字面 `\n`）  ← 新增钉守真续行

## W8 codex #431 r2（P1 + P2，均属实）
- [x] **P1** 两条门命令显式携带 `--provider` / `--namechange`  ← CLI 默认写死且工件不记录数据路径
- [x] provider 全页只解析一次，④ 与 ⑤ 用同一个值  ← 两次解析可能指向不同 bundle
- [x] 新增 `QUANT_NAMECHANGE_PATH` 解析器 + 登记进环境变量文档  ← 此前全仓未登记
- [x] **P2** 认证判定改用执行器的 UTC 时钟（默认即是，非调用点选择）  ← 边界处曾会与执行器相反
- [x] 移除 `cn_now_iso`（引入它的唯一调用点已改）  ← 不留死代码

## W9 codex #431 r3（两条 P2，均属实）
- [x] 新鲜度改用出单侧的「今天」（宿主本地 date.today()）  ← 与 r2 同类:我的时钟≠机器的时钟
- [x] 页面不再传新鲜度时钟（默认即出单侧语义）  ← C20 咬住
- [x] 边界回归:12..17 天逐日与 `_bundle_is_stale` 对拍  ← 直接驱动出单侧谓词,非复述我的算术
- [x] 所有已解析路径经 `shlex.quote`,构成单个 shell 参数  ← C21 咬住(含空格/分号路径)
- [x] 重训窗保留 CN 时钟并写明理由（下游无「今天」判定，属展示量）

## W10 codex #431 r4（P2，属实）
- [x] 卡片状态抽成纯函数 `gate_card_status`（五态，优先级写进 docstring）  ← 判断不再留在页面里
- [x] `overall` 非 PASS（含缺失）→ 未通过；任一具名门非 PASS 同样  ← C23/C24 咬住
- [x] 页面只在 `GATE_STATUS_OK` 时上绿；旧的按序分支删除  ← C25 咬住
- [x] PASS 复用 `retrain_gate_lib.PASS`，不复述字面量  ← C26 咬住

## W11 codex #431 r5（P1 + 两 P2，均属实；同一族的第 8-10 例）
- [x] **P1** 晨跑命令显式携带 provider/registry/name-source  ← CLI 有自己的默认值,会跑在另一份 bundle 上
- [x] 新增 `QUANT_NAME_SOURCE` 解析器  ← C27 咬住
- [x] **P2** 尾部日期改读 `calendars/day.txt`（复用 training_guards 的读取器）  ← C28 咬住;不写第二套解析
- [x] **P2** 健康 warning 时不得上绿,并显示告警本身  ← C29/C30 咬住
- [x] `BundleFreshness` 拆出 `age_ok` / `usable` 两个语义  ← 年龄过 ≠ 可用
- [x] 修正 ⑤ 的取数说明（此前自认「两条路径不同」却仍当拒绝预测用）

## W12 codex #431 r6（P2，属实；同族第 11 例 → 改为规则收口）
- [x] 完整性门改用出单侧自己的 `read_bundle_integrity` + 该门三条规则  ← 不再用信息性摘要代替门
- [x] 损坏 stamp 无条件拒绝；缺失/holey 仅在显式 override 下接受  ← C31/C32 咬住
- [x] `usable` 必须 `integrity_accepted is True`；摘要只能收回不能授予  ← C33/C34 咬住
- [x] 合成 stamp 用例（缺失/损坏/holey/干净 × override 两态）  ← codex 明确要求
- [x] **规则入规格**：被展示的每一道前置校验必须由出单侧自己的可调用物评定
- [x] **子集免责入页面**：两道门全绿 ≠ 今天一定能出单

## W13 codex #431 r7（P2，属实；规则的第一次「够不到」判例）
- [x] 日历尾改用严格读取器：字节无歧义才作答,否则「无法判定」+原因  ← C35/C36 咬住
- [x] 不再自称「与出单侧 `calendar[-1]` 同源」——同文件、不同解析器  ← C37 咬住
- [x] 保守性写进 docstring 与规格：可多报 unknown,绝不可少报  ← qlib 解析器页面调不动
- [x] 歧义用例（坏行/空行/重复/乱序/空文件）全覆盖

## W14 codex #431 r8（P2，属实；不是规则失效,是我没做到自己声明的不变量）
- [x] 解析前先校验规范 `YYYY-MM-DD` 写法  ← fromisoformat 接受 2026-W32-1 / 20260803
- [x] 补 shape 合法但日期非法的用例（2026-13-45 / 2026-02-30）  ← C35 首轮存活的真实覆盖缺口
- [x] C39(search vs fullmatch) 经枚举验证为**等价变异**,显式标注  ← 无字符串能 search 过而 fromisoformat 也过

## W15 codex #431 r9（P2，属实；与 r8 同类:校验前先规范化）
- [x] shape 校验改为作用于**原始行**,不再先 strip  ← C40 咬住
- [x] 首尾空白用例（前导空格/尾随空格/尾随 tab）  ← 都判不规范
- [x] 反向钉:真实 CRLF bundle 仍须被接受  ← 生产 day.txt 实为 CRLF,过严会永久「无法判定」
- [x] C41(read_text vs read_bytes) 经五种行尾验证为**等价变异**,显式标注

## W16 codex #431 r10 + CI 3.10 红
- [x] `UnicodeDecodeError` 是 ValueError 不是 OSError,原会把整页打成 traceback  ← C42 咬住
- [x] **CI ubuntu-3.10 失败已定位并修**:我那条「前提」断言依赖 3.11 才有的
      `date.fromisoformat` 宽松拼写;3.10 上必然抛。改为按版本守前提,行为断言不变
- [x] 补不可解码字节用例(非 UTF-8)

## W17 codex #431 r11（P2，属实；日历字节契约就此**封闭**）
- [x] 不再用 `splitlines()`（它在 VT/FF/NEL/LS/PS 处也断行）  ← C43 咬住
- [x] 不再用 `read_text` 的 universal newline（它把孤立 CR 折成 LF）  ← C41 咬住
- [x] 显式只支持 LF/CRLF,其它断行字符逐个具名拒绝  ← C44 咬住(钉的是诊断信息)
- [x] 至多一个末尾换行,多余空行拒绝  ← C45 咬住
- [x] 契约六条逐条入规格,不再逐轮补丁

## W18 codex #431 r12（P2，属实；规格自相矛盾——我自己的纪律没做到）
- [x] 规格正文与其场景直接冲突,已改（两条并存无法同时满足）  ← 见规格,此处不复述其内容
- [x] 全篇读回,确认三处提法一致
- [x] **加机器守卫**:identity tail 只能以禁止形式出现在尾部来源的规定里  ← C46 咬住
- [x] 守卫本身被 C46 空过两次(段落级被别处 MUST NOT 骗;纯标点切分在 markdown 里
      跨几十行),最终改为按行再按标点

## W19 codex #431 r13（P2，属实；同一守卫的第三次失败 → 定义清「一条语句」）
- [x] 守卫改为按 markdown 的真实分组:空行分块、新项目符号/标题起新语句、
      折行续接先合并,再按句读切分  ← 前三版分别太粗/太粗/太细
- [x] 解析器本身入钉:四种写法(同行/折行/项目符号内折行/带无关 MUST NOT)
      逐一驱动 `_clauses`  ← C48 咬住(变异解析器即红)
- [x] 折行形态的规格变异  ← C47 咬住

## W20 codex #431 r14（两 P2，均属实）
- [x] 晨跑命令显式携带 `--bundle-max-age-days <页面预测所用阈值>`  ← C49 咬住
      （CLI 有自己独立的 argparse 默认 14,与 config 字段不同源）
- [ ] **越界项已登记为独立任务**:让 CLI 的默认值改读 config 字段(单一事实源)
      —— 本 change 边界写明「不改 CLI」,故不在此处做
- [x] 规格守卫认全部 markdown 列表标记（`-` `*` `+` 有序 `1.`/`1)` 与标题）  ← C50 咬住
- [x] 三种列表写法入解析器钉

## W21 codex #431 r15（P2，属实；本轮最要害的一条）
- [x] 去掉全部 POSIX 续行符,命令改单行  ← 实证 PowerShell 报错「一元运算符 -- 后缺少表达式」
- [x] 实证:页面生成的命令在真实 powershell.exe 中跑通,含空格路径作为单个 argv 到达
- [x] 内嵌单引号 → 明说无法跨 shell 表达,不输出静默出错的命令  ← C52 咬住
- [x] 六处变异锚点随之重锚(C15/C16/C17/C21/C27/C49)

## W22 codex #431 r16（P2，属实；我验了 PowerShell 却顺口写上 cmd）
- [x] 实证 cmd.exe:单引号被当字面量,含空格路径被切成两个参数
      `ARGV= ['--provider-dir', "'D:/qlib", "bundles/live'"]`
- [x] 声称范围收窄为 **PowerShell + POSIX**,并显式写明不支持 cmd.exe  ← C53 咬住
- [x] 规格/docstring/测试注释三处同步

## W23 codex #431 r17（两 P2，均属实）
- [x] 引用改为**无条件**，不再用 shlex 的 POSIX「是否需要」判据  ← C54 咬住
      实测:裸 `@bundle` 在 PowerShell 中被当 splatting,参数**整个消失**
- [x] proposal 的 W5 重写为最终的日历尾契约  ← r12 我只改了 spec,漏了 proposal
- [x] 自洽守卫扩到该 change 的**全部** .md（不只 spec.md）  ← C55 咬住
- [x] 命令类断言改为按 shlex token 比对,对引用方式不敏感

## W24 codex #431 r18（P2，属实；同一矛盾第三次换了措辞藏起来）
- [x] tasks.md 的 W5 行改写；两处历史行**不再复述契约**  ← 少一份可漂移的散文
- [x] 守卫词表**钉成数据**（尾部/tail/calendar[-1] × 五种被否决来源的说法）  ← C56 咬住
- [x] 判据改为「正面 mandate」:对比句/GIVEN/历史注记不再被误伤
- [x] 语句级 + 子句级**两级并用**  ← C57/C58 各咬一级
      (语句级抓跨子句写法;子句级抓「同语句内正面 mandate + 无关否定」)
- [x] 词表测试改用**共享判据**,不再自带一份实现

## W25 codex #431 r19（两 P2，均属实；同一守卫连续第五轮）
- [x] 补中文祈使词（必须/应当/须/使用/采用/来自/给）  ← C59 咬住
- [x] **否定与「命名被否决来源的那个子句」绑定**,不再看整句里有没有否定词
      ← C60 咬住;否则一句无关的「冲突/已改」就能放行一条正面 mandate
- [x] 三处新样例各自隔离一个结构决定（中文祈使 / 跨子句 mandate / 无关否定）
      ← C61 首轮存活:原样例的命名子句恰好含「给」,没隔离出该决定
- [x] **如实声明守卫范围**:它是「已知措辞的回归钉」,不是通用矛盾检测器;
      绿 ≠ 文档自洽。真正保护操作人的是实现钉 + 人工审查

## W26 codex #431 r20（P1，属实；r15 的拒绝路径自己是注入面）
- [x] 复现:`provider_uri="a'b' ; touch /tmp/pwned_by_cockpit #"` 经 r15 的拒绝语
      渲染后**在 bash 中真的执行了**,文件被创建 —— 拒绝语把原值插回了命令文本
- [x] `_arg` 改为**抛** `_UnrenderablePath`,不再自己编一段含原值的文本
- [x] 三个命令构造器统一挂 `_refuses_unrenderable` 边界  ← C63 咬住
      漏挂一处 = 那一处恢复成注入面,故守的是「边界统一」而非「某函数正确」
- [x] 拒绝产物**逐行**以 `#` 起头(PowerShell 与 POSIX 同为注释),原值只进 note
      ← C62 咬住;实测两种 shell 下均无输出、无副作用、无文件生成
- [x] 不可渲染字符封闭列举为 `("'", "\n", "\r")`  ← C64 咬住
      换行会把一条命令变成两条,与单引号同属「无法安全渲染」

## W27 codex #431 r21（P2，属实；空 provider 是「引得太好」而非引不了）
- [x] 实证:`resolve_default_provider_uri()` 对缺失/坏掉/无字段的 config.yaml 返回 `""`;
      `Path("") == WindowsPath(".")` —— `--provider-dir ''` 会把 daily_update 指向 CWD
- [x] `_arg` 的边界从「无法渲染」扩到「不可用」:空/纯空白同样抛,三个构造器
      经既有装饰器自动拒绝  ← C65 咬住;修的是边界不是某个调用点
- [x] 两种拒绝原因**分开命名**(`_WHY_UNRESOLVED` / `_WHY_UNRENDERABLE`)  ← C69 咬住
      修法不同:一个是「修 config.yaml」,一个是「换路径」
- [x] `recommender_integrity_check("")` 由 `known=True, accepted=False`(对没找到的
      bundle 给确信裁定)改为 `known=False`  ← C67 咬住
- [x] `bundle_calendar_tail("")` 不再报「读不到 calendars/day.txt」(指责一份从未
      找到的 bundle),改报真实病因  ← C66 咬住
- [x] 两道门**不得读 CWD**:CWD 下放一份合法日历,答案仍须是「不知道」  ← 单独钉
- [x] 页面顶部一次性说明,不让操作人从四张「无法判定」卡里拼病因  ← C70 咬住
- [x] `integrity_accepted` 传 `None` 而非 `False`  ← C68 咬住

## W28 codex #431 r22（两 P2，均属实；第二条是我 r21 修法自己的破绽）
- [x] 无 ensemble 时整卡拒绝,不再拿 `<现任 manifest（当前不可解析）>` 顶替后
      照常渲染两道门 + **不可逆** execute  ← C71 咬住
      ④ 已说轮换不适用,再把完整可跑流程印出来是页面自相矛盾
- [x] 空串 manifest 与 `None` 同等处理  ← C72 首轮**存活**:`or` 占位符在
      `manifest_path=""` 时仍会渲染。同一缺陷换了个类型穿回来
- [x] `recommender_integrity_check` 未评定时 `accepted` 保持 `None`  ← C73 咬住
      r21 我写了 `accepted=False` + `known=False` —— 正是该分支要制止的
      「对没看过的东西下确信裁定」,同函数的兄弟分支本来就没写
- [x] 不变式**修在读取器**,不修在调用点;页面改回直传  ← C34 重锚
      修在调用点 = 其余消费者继续拿错值;C68 因此作废(判断已不在页面)
- [x] 补源码级钉:该函数任何 `known=False` 的返回都不得写 `accepted=`
- [x] 「看过是坏的」(`known=True, accepted=False`)与「没看过」对照钉

## W29 codex #431 r23（P2，属实；与 W1 同一类，我只在现任解析器上做了)
- [x] `resolve_namechange_path` 改为**复用** `config_forms` 的那一份(同一性钉,
      不是取值相等)  ← C74 咬住;两份实现漂移时,门命令与 UI 作业会选中不同 ST 历史
- [x] 同类扫一遍,不只修被点名的那一处:
      - `resolve_name_source` 改为调用 `RecommendationConfig` 自己的
        `default_factory`  ← C75 咬住
      - `resolve_delisted_registry` / `resolve_model_path` 仓库中无单一 owner
        可复用,并入既有路径默认值治理表锁死  ← C76 咬住(需把该测试纳入范围)
- [x] 顺手删掉本页自造的规范化:出单侧 factory **不** `.strip()`、不把 `""` 当未设。
      本页要印的是机器会用的值,不是本页认为应该用的值 —— `QUANT_NAME_SOURCE=""`
      现在照实传出并被命令边界拒绝
- [x] factory 形状变了就 fail loud,不退回字面量(静默退回正是这份重复的来路)
- [x] 复用钉改为**结构钉**(无本地 def + 显式从 owner import + 行为一致),
      不用 `assertIs`:`test_operator_ui_config_validation` 会把 config_forms
      踢出 `sys.modules` 再重导,同一函数存在两代对象 —— 单跑绿、全量红。
      「看起来更强的断言」前提不成立时反而更弱

## W30 codex #431 r24（P2，属实；r23 我只删了 name_source 那一处规范化）
- [x] `resolve_model_path` / `resolve_delisted_registry` 改为裸
      `os.environ.get(VAR, DEFAULT)`,与 CLI 完全同语义  ← C77/C78 咬住
      显式 flag 会**覆盖**默认值,多余的规范化不是显示问题,是跑在另一个工件上
- [x] 对拍覆盖**空值与带空白值**(两种拼写的分歧点),不只对拍未设时的默认值
      实测四态(未设/""/"  "/" /x/y ")UI 与 CLI 逐一相符
- [x] 处理该改动引出的空路径:`Path("").with_suffix()` 抛 empty name,
      会把今日推荐页打成 traceback  ← C79 咬住(44 个既有钉同时红)
      两个读取器返回 None(契约本就是 best-effort-or-None);
      `model_meta_paths` 拒绝空输入,不臆造一对指向工作目录的路径
- [x] 今日推荐页直接指明「QUANT_MODEL_PATH 被设为空值」,不让操作人从一条
      数据源为空的「元信息缺失」告警里反推
- [x] `config_forms.resolve_namechange_path` **不动**:它的消费者是 YAML 的
      `${VAR:-default}` 与 UI 作业写入,不是这个 CLI;归属别的模块与别的测试

## W31 codex #431 r25（P2，属实；r24 的告警我加得太宽）
- [x] 空 `QUANT_MODEL_PATH` 的告警**仅在单模型现任下**触发  ← C80 咬住
      ensemble 模式下 CLI 与 `--ensemble-manifest` 互斥地拒绝 `--model`,
      根本不读该默认值 —— 在生产实际运行的形态上报一个不可能发生的故障
- [x] 守卫移到 `_incumbent` 解析之后(位置本身入钉)
- [x] 同一规则在驾驶舱侧对拍:空 model_path **不得**拒绝一条根本不带
      `--model` 的 ensemble 晨跑命令,而单模型下必须拒绝  ← C81 咬住
      判据是「该取值是否进入这条命令」,不是「该取值本身好不好」

## W32 codex #431 r26（P2，属实；探针的答案取决于 UI 从哪启动）
- [x] 探针显式 `cwd=` 执行器读的那个仓库,不再继承进程工作目录  ← C82 咬住
      实测:改前从别处启动 → 一路「无法判定」;改后仍答出 WIN valid until 2027-11-05
- [x] 该仓库**复用执行器自己的** `PROJECT_ROOT`,不另推一次  ← C83 咬住
- [x] 「本可作答却答不出的不知道」按缺陷处理,与错误答案同级 —— 这一条入规格
- [x] 同类扫:页面生成的命令以 `scripts/…` 仓库相对路径命名脚本,只在仓库根
      有效。这个工作目录依赖页面**控制不了**,故显式声明  ← C84 咬住
      (能控制的那一个是修掉,不是写文档 —— 两者处置不同)

## W33 codex #431 r27（**P1**，属实；r26 只修了探针,没扫到被读的路径本身）
- [x] 新增 `anchored_to_repo()`:相对路径按**命令将要执行的目录**(仓库根)解析
      —— 即机器真正会有的 CWD。绝对路径与空值原样返回  ← C86/C87 咬住
      (锚定只许消歧义,不许发明 CLI 不共享的规范化 —— r23/r24 的教训)
- [x] 覆盖全部「既被读又被印」的路径,不只被点名的 provider:
      - `_provider`(日历/完整性/健康三个读取器 + 三条命令)  ← C85 咬住
      - 现任 manifest:在 `load_ensemble_manifest_identity` **读之前**锚定  ← C88 咬住
      - 单模型 `--model`:今日推荐页在其旁读 sidecar  ← C89 咬住
- [x] 只被印不被读的路径(registry / name-source / namechange)**不动**:
      它们原样交给一个已被告知站在仓库根的 shell,与机器一致
- [x] 仓库根第四次派生取消,统一复用执行器的常量(incumbent / helpers / recert)
- [x] 页面 import 钉改为钉**属性**而非某一种书写(多行 import 让原钉误红)

## W34 codex #431 r28（**P1**，属实；我 r27 的锚定实现自己踩了跨平台坑）
- [x] **CI 实红**:ubuntu 3.10/3.12 在 a524779 上失败,codex 比我先看到
      (我的监控只在全部 check 非 pending 时才播报,还没触发)
- [x] 绝对性改按 **Windows 与 POSIX 两种约定之一**判定  ← C90 咬住
      posix 规则下复现旧实现:`D:/qlib_data/my_cn_data_pit` →
      `/home/runner/.../Quant_Ashare/D:/qlib_data/my_cn_data_pit`(哪里都不存在)
- [x] `~` 改为**展开后**同时用于读与印  ← C91 咬住
      原样返回:页面读字面 `~` 目录,命令因无条件单引号也不展开 —— 两边各错一次
- [x] `~` 无法解析(无 HOME)时原样返回,不把 `~` 当目录名锚定  ← C92 咬住
- [x] 该类缺陷**在 Windows 上无行为可观测**(两种判定在本机处处一致),
      故补一条宿主无关的源码钉;且钉的是**编译后的 co_names**,不是源码文本
      —— 文本扫描会匹配到解释「为何不能用 isabs」的 docstring,正确实现也报红
      (这个错是变异 harness 通过一个无关等价变异转红暴露的)

## 验证
- [x] 新页面自己的 source-pin 只读测试（禁作业/训练/写侧 API 清单）  ← 29 passed / 22 subtests
- [x] 既有 63 个 daily_decision 钉全绿（上移不得破坏任何契约）  ← 63 passed(patch 点随函数搬家同步)
- [x] 通用扫描：page_header glob / 主题禁色值 / ruff / mypy --strict  ← 全绿
- [x] 关键守卫突变验证（每处先自检突变真落地）  ← C1..C92(C57/C58/C68 随重构作废):88 杀 + 1 已论证等价
- [x] 全量快速套件 + openspec validate --strict  ← 见末次运行记录
- [ ] codex 循环至 CLEAN + CI 七绿 → STOP 等操作人 merge
