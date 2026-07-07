# Sidebar 锚健康徽章(UI 轨道 PR-B)

## Why

REGEN-2 replay 锚是全系统官方指标的确定性根,但它的健康状态对操作员完全不可见:
基线文件是否被改动、上次重签是什么时候、CI 的锚腿(ubuntu-3.12)最近是否绿,
都要翻测试源码、git log 和 GitHub 页面才能拼出来。sidebar 常驻徽章把这三件事
放到每次打开控制台的眼前——锚被动过而未重签、或锚腿红着,操作员在做任何
决策/启动任何 run 之前就能看到。

设计已由 James 审定(2026-07-07):数据源选 (a) `gh` CLI(本机已认证),
缓存 + 超时 + 降级"未知";纯本地要素(sha/重签日期/evidence)不依赖网络。

## What Changes

**`web/operator_ui/anchor_health.py`(纯逻辑,零 Streamlit 依赖)**:
- `normalized_sha256(path)`:与锚测试 `_normalized_sha256` 同算法
  (CRLF→LF 规范化后 sha256,checkout-stable),注释交叉引用。
- `baseline_identity()`:基线文件短8 sha + 上次重签(`git log -1` 该文件的
  last-touch commit 日期+短sha,与 prereg"plan identity = last-touched
  commit"同款先例)+ evidence sidecar 在/缺。git 探测注入可测;浅克隆/无 git
  → 日期"未知"(fail-soft,不猜)。
- `ci_leg_status()`:`gh run list`(main 上 test.yml 最近完成 run)→
  `gh run view --json jobs` 取锚腿 job("test (ubuntu-latest, 3.12)")结论;
  gh 缺席/未认证/超时/解析失败 → "unknown" + 诚实 detail;锚腿 job 名未命中
  → 回落整 run 结论并注明。runner 注入可测,子进程带超时。

**`app.py`**:sidebar 品牌区下方渲染徽章(emoji 点 🟢/🔴/⚪ + 小字:锚腿结论、
基线 sha8、重签日期、evidence 状态);probe 经 `st.cache_data(ttl=600)` 缓存
(拉取式,无后台轮询),UI 永不因徽章阻塞或崩溃。

**测试(tests/logic)**:sha CRLF/LF 等值、真实基线文件可算、git/gh 注入的
成功/降级/超时/缺席路径、job 名回落、源码契约(模块零 streamlit import、
app.py 含缓存 TTL 与徽章渲染)。

## 边界与红线

- 徽章只读:不触发任何 run/重签/网络写操作。
- gh 是可选依赖:一切失败降级为显式"未知",绝不伪造状态、绝不阻塞页面。
- 无后台轮询:探测仅发生在页面渲染时,TTL 缓存吸收频率。
- 不碰 tests/regression/*(只读基线文件路径),不碰锚语义。

## Impact

新增 `web/operator_ui/anchor_health.py`、`tests/logic/test_anchor_health.py`;
`web/operator_ui/app.py` 加一小块 sidebar 渲染;spec delta:
`v2-operator-ui-console` ADDED ×2。
