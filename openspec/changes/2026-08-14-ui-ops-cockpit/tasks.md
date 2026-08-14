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

## 验证
- [x] 新页面自己的 source-pin 只读测试（禁作业/训练/写侧 API 清单）  ← 29 passed / 22 subtests
- [x] 既有 63 个 daily_decision 钉全绿（上移不得破坏任何契约）  ← 63 passed(patch 点随函数搬家同步)
- [x] 通用扫描：page_header glob / 主题禁色值 / ruff / mypy --strict  ← 全绿
- [x] 关键守卫突变验证（每处先自检突变真落地）  ← C1..C15 十五处全落地全咬红(C11 首轮存活已补钉)
- [x] 全量快速套件 + openspec validate --strict  ← 见末次运行记录
- [ ] codex 循环至 CLEAN + CI 七绿 → STOP 等操作人 merge
