# 每日决策页(今日推荐)— PR-A,拆分为 A1(工件契约)+ A2(UI 页)

## Why

生产晨间流程是 `scripts/daily_recommend.py` 落盘推荐工件 → 操作员肉眼决策。当前
决策环节完全在 UI 之外:没有一处能同时看到「这份推荐是哪个模型、什么训练窗口、
什么时候晋升的产物」;采纳/拒绝的理由散落在脑子里,无法回溯复盘。suspended-guard
一类事故的根因正是"元信息缺位时系统静默用默认值继续跑"——决策页把元信息放到每次
决策的眼前,缺失即醒目 WARN,是根因级防复发。

Step0 侦察发现工件契约有一个真实缺口(见下),故按开工单预案拆 **A1(工件契约,
先行)+ A2(UI 页)** 两个 PR,本 change 同时覆盖两者的规格,tasks 按 A1→A2 分段。

## Step0 侦察结论(开工单 §1,逐项)

**1. daily_recommend 输出形态 — 有落盘工件,但缺模型元信息(→ 触发 A1)**
`src/inference/daily_recommend.py::write_outputs`(L893)已落盘三件套(带日期版本):
`daily_recommendation_{as_of}.csv`(买入清单)、`…_{as_of}.json`(清单 + n_scored/
n_masked/n_st_excluded)、`…_{as_of}_scored_full.csv`(全量审计帧)。**但 JSON 不含
任何生成语境**:无生成时刻、无模型标识、无 fit_end_for_inference、无 bundle 标识——
横幅无法把"这份工件"与"生产模型"绑定,陈旧工件(旧模型生成)与当前模型元信息会
静默错配。A1 补齐:工件内嵌 `meta` 块(见 spec delta)。影响面已核实:`write_outputs`
仅 2 个调用方(`scripts/daily_recommend.py:266` + `tests/logic/inference/
test_daily_recommend.py:729`),无其他 JSON 消费者,可安全做契约迁移。

**2. 模型 meta 字段清单(横幅数据源)— 齐备,无需新增**
双 sidecar 机制(`scripts/daily_recommend.py::_model_meta_paths`,晋升 meta
`<stem>.meta.json` 优先、trainer sidecar `<model>.pkl.meta.json` 兜底,fail-loud)。
生产模型晋升 meta 实测 20 字段,横幅四项全覆盖:`fit_end_for_inference` ✓、
训练窗口=`train_window` ✓、promote 日期=`promoted_at` ✓、模型标识=`model_path`+
`model_type`(trainer sidecar 另有 `pkl_sha256` 可做工件↔模型交叉核对)✓。

**3. 工件根目录与 env var 约定 — 决策日志需一个新 env var(命名论证如下)**
现约定:repo 内 `output/`(gitignored L20,**可弃置**——jobs 批量清理/`git clean`
都会碰)存运行产物;repo 外 `D:/qlib_data/*` 存市场数据、`D:/stock/phase_b_artifacts`
存模型工件;`QUANT_*` env var 4 个已在 `docs/operations-env-vars.md` 集中登记。
决策日志是 append-only 的**人工决策记录**(珍贵、不可弃置),放 `output/` 树违背其
弃置语义 → 采纳开工单"repo 外"决定。**新 env var:`QUANT_DECISION_JOURNAL_DIR`**,
默认 `D:/stock/operator_journal`。命名论证:`QUANT_` 前缀跟随既有 5 变量族;`_DIR`
后缀因指向目录(既有后缀 `_URI/_PATH/_REGISTRY/_SOURCE` 均指向文件/URI,无可复用);
默认根 `D:/stock/` 跟随 phase_b_artifacts(同为"非市场数据的操作员资产")先例;
在 operations-env-vars.md 登记新行(非静默新增)。

**4. ST/停牌/PIT 标志 — 现成字段可透传,UI 零新算**
买入清单行已带 `tradable_flag` + `unavailable_reason`(suspension/one-price-lock);
审计帧 `scored_frame` 逐行携带 `unavailable_reason`,**含 `"st"`**(daily_recommend.py
L666:`n_st = (scored_frame["unavailable_reason"]=="st").sum()`)。PIT 无逐行列
(bundle 本身即 PIT,无需列)。候选表只透传上述既有字段;不新增任何 UI 侧计算标志。

**5. pages 模式 — 全部沿用,不发明新模式**
导航:`app.py::_navigation` dict,"运行"组加 `st.Page(pages/daily_decision.py,
title="今日推荐")`。页面:`pages/_daily_decision_helpers.py` 纯函数 + 薄渲染页。
工件读取:`artifact_reader.read_json_artifact`(ArtifactReadResult/Issue 模式)+
`_path_guard` 守卫 repo 内 output 读路径。日志 I/O 模块:`web/operator_ui/
decision_journal.py`,镜像 `job_io.py` 先例(页面拥有的 I/O 层,可无 Streamlit 单测)。

## What Changes

**A1 — 推荐工件契约 v2(先行,小)**:`recommend()` 组装运行元信息(生成时刻
Asia/Shanghai ISO、model_path、model_pkl_sha256、解析后 fit_start/fit_end_for_
inference、provider_uri、bundle 标识[取自 _fetch_integrity identity,缺失记 null]、
instruments、topk),作为 `DailyRecommendationResult.run_meta` 必填字段(无默认值,
强制迁移 = fail-loud);`write_outputs` 将其序列化进 JSON 顶层 `meta` 块 +
`artifact_schema_version: 2`。CSV 不变。两个调用方同 commit 迁移。

**A2 — 每日决策页(UI)**:导航"运行"组新增"今日推荐",三区块:
1. **模型元信息横幅**(常驻页顶):fit_end_for_inference、训练窗口、promoted_at、
   模型标识,数据源 = 生产模型晋升 meta(QUANT_MODEL_PATH 解析);任一字段缺失 →
   醒目 WARN,**绝不默认值/静默降级**。加一道交叉核对:所选工件 `meta.model_pkl_
   sha256` ≠ 当前模型 sidecar `pkl_sha256` → WARN"工件由其他模型生成";工件为
   v1(无 meta 块)→ WARN"旧版工件,无生成语境"。
2. **候选表**(只读):rank/code/name/score/tradable_flag/unavailable_reason(含
   st)透传 + 每行成本参照列(`score − 30bps 往返`,纯展示算术,常量注明出处)。
   日期选择器列出 `output/daily_recommend/daily_recommendation_*.json`,默认最新。
3. **决策日志**:每候选 采纳/拒绝/观望 + 一句话理由,append-only JSONL(契约见
   spec delta;威胁对表见下)。

**顺手项(唯一)**:`web/README.md` 从 "Skeleton only" 更新为真实页面清单 + 本页
边界声明(日志永不作官方指标输入,src/ 零依赖)。

## 威胁对表(开工单 §3,每条拦截手段 + 单测)

| # | 威胁 | 拦截手段 | 单测 |
|---|------|----------|------|
| 1 | Streamlit rerun 双重追加 | 显式按钮提交;表单渲染时铸造 uuid4 nonce 存 session_state;**nonce 落盘进行内(schema 第 10 字段,codex P2)**;`append_decision()` 落盘前扫描文件,`(trade_date, code, nonce)` 已存在则拒绝追加——重放(同 nonce)可判别、有意更正(新表单=新 nonce)绝不被抑制 | 同 nonce 连调两次 append → 文件恰 1 行;新 nonce 同 (trade_date,code) → 正常追加 |
| 2 | 部分写入留半行 | 先拼完整行字节(含行尾)再单次 write+flush(binary append);读取端逐行 json 解析,坏行跳过并计数,页面 WARN 显示坏行数,不崩 | fixture 含截断行 → 读取返回完好行 + issue 计数 |
| 3 | Windows CRLF 污染 | binary 模式写 + 显式 `b"\n"`(#321 教训) | 字节级断言:文件无 `b"\r"`,行尾恰为 `b"}\n"` |
| 4 | 时钟/时区错位 | `trade_date` 只取自所选推荐工件的 `as_of_date`(绝不取本机日期);`decided_at` = `zoneinfo("Asia/Shanghai")` 带偏移 ISO8601 | 断言 trade_date 与工件一致;decided_at 含 +08:00 偏移 |
| 5 | 日志被当分析数据源滥用 | `tests/logic` 源码扫描测试:`src/` 全树对 `decision_journal` 零引用;README 边界声明 | 扫描测试本身(违者红) |

## 既定设计决定(不重开,原样落地)

页面结构三区块、横幅缺字段 WARN 不降级、候选表只读附 30bps 成本参照、日志 schema
(`journal_version:1` + 8 字段,**另加 `nonce` 落盘字段**——调和工单 §2 schema 与 §3
威胁-1 幂等键的必要一致解,codex P2 on #328)、append-only + 同 `(trade_date,code)` 以 `decided_at`
最新者为准的 supersede 读取语义、UTF-8 无 BOM + `\n`、日志归 web/ 层所有——均按开工单
§2 原样执行,本提案不重议。

## 红线合规声明

- 不动 `src/` 官方指标语义(A1 只加推荐工件元信息,推荐工件非官方指标);不碰
  `config_run.py` / `jobs.py`;不碰 prereg/compare 工件。
- 无 CI-only 执行路径:journal 模块与 helpers 为纯 Python(无 Streamlit 硬依赖),
  全部威胁单测本机可跑;页面渲染本机 streamlit 验证 + 截图。
- 无文件搬移 → AGENTS.md whole-file-diff 机械搬移验证不触发;若实现中出现搬移,
  按该规则出全文件内容 diff 证明。
- 新测试全部进 `tests/logic`。

## Impact

- **A1**:`src/inference/daily_recommend.py`(run_meta 组装 + write_outputs 序列化)、
  `scripts/daily_recommend.py`(调用方迁移)、`tests/logic/inference/
  test_daily_recommend.py`(构造器/调用方迁移 + meta 块断言)。规格:
  `v2-daily-stock-recommendation` ADDED。
- **A2**:新增 `web/operator_ui/pages/daily_decision.py`、`pages/_daily_decision_
  helpers.py`、`web/operator_ui/decision_journal.py`;`app.py` 导航 +1 行;
  `web/README.md`(顺手项);`docs/operations-env-vars.md` +1 行(新 env var);
  `tests/logic/` 新测试文件。规格:`v2-daily-decision-page` 新能力。
- 不触碰:官方指标路径、config_run/jobs、prereg/compare、REGEN-2 一切。

## STOP 点

本提案经 James 确认后才开始实现;A1 PR 合并后才开 A2 PR(工件契约先行)。
