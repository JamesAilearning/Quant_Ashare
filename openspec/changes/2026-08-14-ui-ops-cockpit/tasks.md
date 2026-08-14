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
- [x] 写明 tail 的取数路径（provider 元数据，非出单侧 calendar[-1]）  ← provider 元数据,非出单侧 calendar[-1]

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
- [x] 正文那条 MUST（用 summarise_bundle_health 取尾部）与场景直接冲突,已改  ← 两条并存无法同时满足
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

## 验证
- [x] 新页面自己的 source-pin 只读测试（禁作业/训练/写侧 API 清单）  ← 29 passed / 22 subtests
- [x] 既有 63 个 daily_decision 钉全绿（上移不得破坏任何契约）  ← 63 passed(patch 点随函数搬家同步)
- [x] 通用扫描：page_header glob / 主题禁色值 / ruff / mypy --strict  ← 全绿
- [x] 关键守卫突变验证（每处先自检突变真落地）  ← C1..C50:49 杀 + 1 已论证等价(C11/C20/C35/C44/C46/C48 首轮存活已补钉)
- [x] 全量快速套件 + openspec validate --strict  ← 见末次运行记录
- [ ] codex 循环至 CLEAN + CI 七绿 → STOP 等操作人 merge
