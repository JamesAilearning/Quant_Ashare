# Tasks: 2026-08-26-workbench-todays-answer

## 实现

- [x] `todays_buy_answer`：三态 + 如实边缘的纯合成层，零自造判定
      （陈旧/完整性=出单侧裁决；节奏=已核验信号分类；说给今天=
      `entry_date == cn_today()`）
- [x] 优先级：出单侧判据先行——会拒时即使工件看似今天也拒答
- [x] 流程态与异常态分开：`no_instruction`（点名最新指令面向哪天）≠
      `unanswerable`（带原因）
- [x] 每态带「不是订单」免责声明
- [x] 页面顶部插槽回填（容器占位，不打乱既有计算顺序），四态显式配色

## 验证（每条要实测数字）

- [x] 新守卫 13 条：四态各自的文案与数字如实（含 15/14 落后天数）、优先级
      钉、完整性 None 不当放行、裁决不可达带原因、entry 早/晚各点名日期、
      无节奏标记拒合成、四态免责声明齐、接线源码钉 + 配色四态齐
- [x] 变异实测：短路 `refuses_today` 分支 → 优先级钉 1 failed（咬住）
- [x] 定向 30 passed / 17 subtests；logic 全量见 PR 实测数字
- [x] openspec validate --strict valid

## 第二轮（codex 2×P1）

- [x] 数据包前置改为全额消费 `usable`：年龄+完整性之外补健康摘要的
      「只扣分」份额（漏了会与同页健康卡自相矛盾）
- [x] `summarise_daily_signal` 传出候选基数 `pick_count`；空清单再平衡日
      → 「不买 · 清单为空」（合法产出，非错误态）；基数缺失 → 拒答；
      买入态点名候选数
- [x] 新守卫 5 条（空清单/基数缺失/健康扣分带原因/基数流经核验/买入数
      如实）；变异见 PR 实测

## 第三轮（codex P1：entry 不是执行日）

- [x] 撤销「entry==今天→今天执行日」——违反基线契约
      （v2-daily-decision-page：entry 是**已收盘会话**、非「明早买入」、
      收敛归执行惯例）；合成句不再给任何执行时点，也不再消费挂钟
      （`cn_today` 参数移除）
- [x] 「指令新不新」改为 `entry_date` 对出单侧日历尾（出单器就是从那份
      bundle 跑的）：对上=最新；数据走过=流程态（两日期点名→运行中心）；
      工件超前=异常态（产出器出不了未收盘会话的清单）
- [x] 态名 buy→rebalance；有指令态点名候选数+已收盘披露+执行惯例句
- [x] 新守卫：禁执行日措辞（三态扫「执行日」「今天（」）、已收盘披露
      跟工件走、两方向日期错位各点名、无尾拒比

## 第四轮（codex P1 来源绑定 + P2 披露补流程态）

- [x] 比日期前先绑数据来源：`summarise_daily_signal` 留存产出器写的
      `meta.provider_uri`/`meta.bundle_tag`；integrity 读取处暴露
      `identity_tag` 并随 `BundleFreshness` 走；provider 用出单器自己的
      归一化比对，双 tag 在场才比——mismatch/缺 provider/当前侧不可辨
      各自拒答点名两侧，单侧缺 tag 按 provider 绑定放行（合法态）
- [x] 已收盘披露跟到流程态（数据走过最新指令的分支）；超前拒绝态豁免
      （呈现的是被拒的声称，规格措辞同步收窄）
- [x] 新守卫 6 条（异 provider/异 tag/缺 meta 来源/当前侧不可辨/单侧缺
      tag 放行×2/来源随核验留存）+ 披露循环加流程态

## 第五轮（codex P1 相对拼写异锚）

- [x] provider 比对先同锚仓根（anchored_to_repo，绝对拼写契约原样放行）
      再走出单器归一化——仓外启动时相对拼写按进程 CWD 归一会假拒同一份
      bundle；chdir 仓外的回归 + 变异去同锚咬住

## 第六轮（codex 2×P2：NUL 崩页 + 违约行计数）

- [x] 比对前两侧对称过既有 unusable_path_reason 边界（NUL 先于任何文件
      系统调用）——内嵌 NUL 让 realpath 抛 ValueError 整页 traceback，
      规格要求的是拒答
- [x] 基数只数合约内的行：产出器 RecommendationPick 六键六型穷尽钉
      （_pick_row_violation），`picks: [{}]` 不再抬成「1 只候选」；违约=
      需核查不做静默缩数；display 层 pass-through（工单 §1.4）不动
- [x] 变异两发（去 NUL 门 2f / 去行验约 7f）全咬；逐键坏形态七用例

## 第七轮（codex 2×P2：类型降级借道 + 字面钉可交易）

- [x] meta.provider_uri / bundle_tag「在场但类型违约」＝需核查，不再静默
      降成 None 借道「合法缺身份块」绕开 bundle 比对（产出器只写
      str / str|null）
- [x] 行验约从验型升为验**字面**：产出器只落已过筛的行（构造器写死
      tradable_flag=True / unavailable_reason=""）——工件自标不可交易的
      行不再计入候选数
- [x] 变异两发全咬（无缓存 -B 复验；顺带抓到并记档变异 harness 的 pyc
      秒级 mtime 假绿坑）

## 第八轮（codex P1：tag 非精确绑定——同日历原地重建看不见）

- [x] 产出器把 stamp 的 built_at（每次重建都刷新的 nonce）落进
      meta.bundle_built_at（stamp 已在手，零新 I/O；无 stamp 记 null）
- [x] 读侧全链：BundleIntegrityCheck.built_at → BundleFreshness.built_at →
      summarise 留存（str|null 同型验约）→ 合成比对（双侧在场才比，
      mismatch 点名两时刻拒答；单侧缺席=合法态按 provider/tag 放行）
- [x] 变异（去 nonce 比对，-B 无缓存）1 failed 咬住；产出器侧源码钉
      （nonce 真从 stamp 来、真落 meta）

## 第九轮（codex P2：空白拼写借道 CWD）

- [x] 空/全空白 meta.provider_uri（产出器产不出：config 有非空守卫；路径
      边界刻意放行空串）会被归一化解析成进程 CWD——Streamlit 恰从 bundle
      目录启动时损坏工件假绑定。比对前两侧各设空白门；变异首跑没咬暴露
      断言过弱（空白被 mismatch 兜住也算 unanswerable——正是 CWD==provider
      时会放行的形状），钉到具体原因后 2 failed 咬住

## 第十轮（codex P1 cadence-1 哑火 + P2 孤立代理崩页）

- [x] "daily"（缺 rebalance_day 的 cadence-1 legacy 形态）不再拒答——契约
      明文 ABSENT=每日可执行清单（hold_state），详情页同款；并入再平衡
      基数逻辑，整个部署形态的头卡不再哑火
- [x] 孤立代理字符拼写（JSON 可表示，POSIX realpath 编码不了）在比对前
      与 NUL 同门拒答；变异去门 2 failed（Windows 走措辞断言/POSIX 走
      崩溃，双向咬）

## 第十一轮（codex P1：fixture 拼写平台绑定）

- [x] 测试 fixture 的 `D:/data/prov` 是 Windows 专属拼写——Ubuntu CI 腿上
      被路径边界判外来，快乐路径全体在门口 unanswerable、Linux 矩阵红。
      改按宿主构造（os.path.join(abspath(os.sep), ...)），两平台皆合法
      完全限定；留存测试的字面拼写保留（只验 pass-through 不过边界）

## 第十二轮（codex P2：损坏节奏日期上头卡）

- [x] next_rebalance_date 验约（严格 ISO 或 null——产出器值域；日历尾附近
      合法 None；缺键=cadence-1 合法形态）：hold_state 刻意宽容（展示层
      不动），核验层把 `123`/"tomorrow" 判需核查，不再当已核验 HOLD 宣布
      「无需动作」；变异去验约 3 failed 咬住

## 第十三轮（codex P2：分支落后 main——合并会倒车）

- [x] #468 叠在 #467 的 8d18072 上，而 #467 后又追了两笔（known=False
      原因原样 / 时效行接全三分支）才并入 main——本分支树没有它们，合并
      会把 main 的修复连测试一起退回（重砌丢证人类）。merge origin/main
      进分支，冲突按 HEAD 解决后跑丢证人防线：函数名差集=无丢失，但
      **断言级**削弱（三分支计数钉在同名测试体内被覆盖）差集抓不到——
      grep 逐模式核对抓回并回填。教训：同名测试体的断言也要差集

## 第十四轮（codex P2：HOLD 的过去日期照样上头卡）

- [x] 严格 ISO 之外补时序关系：产出器契约 next_rebalance_date(d) >= d 且
      HOLD 日 as_of 非再平衡日 → 严格大于——过去/当日值产出器产不出，头
      卡把它宣布成「下一再平衡日」是拿损坏工件报日程。再平衡日 next==
      as_of 合法不误伤（专用用例钉）；变异去时序验约 2 failed 咬住

## 流程说明

实现与规格同 PR，遵循 #467 的先例（codex 判 UI 新契约必须有配套 change）。
