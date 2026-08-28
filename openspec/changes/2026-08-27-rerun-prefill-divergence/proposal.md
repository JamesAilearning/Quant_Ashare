# 用此配置重跑：修好预填，并把提交前的差异变成明账

## Why

「用此配置重跑」从一次历史运行预填配置。实测下来它**在最常见路径上一个
字段也预填不进来**，而横幅照说「已预填」：

`web/operator_ui/pages/config_run.py` 的预填块以
`f"cr_{key}" not in st.session_state` 守门，而同文件的 `_cr(key, default)`
只要被调用过就把对应的 `cr_*` 键**种满**（没有值就写 `default`）。页面渲染
一次即会调遍 `_cr()`。因此：操作人只要打开过一次配置页，之后再从结果页点
「用此配置重跑」，预填块看到的每个键都已存在 → 全部跳过。

后果分两层：

1. **预填坏了**——操作人以为在重跑「那一次运行」，实际提交的是本页当前值。
2. **坏得没有声音**——页面此前一个字都不说，无处可核对。

这不是体验问题，是**数据正确性**问题：研究结论会被挂到一份从未跑过的配置
上。同一路径上还有两处静默降级：`_prefill_config()` 把 YAMLError 与「顶层
不是 dict」都吞成空 dict（页面照说已预填），结果页把 config.yaml 按
`errors="replace"` 解码（坏字节变 U+FFFD 后原样交给 YAML，运气不好是解析
成功但某个值被悄悄改写）。

（起因是 2026-08-26 的「研究与验证 UI 优化建议报告」评审：报告把只读 diff
列为 P2 体验项；顺着查下去发现底层的预填本身已失效，于是先修功能。）

## What Changes

**修功能——预填即权威**：点「用此配置重跑」是显式指令，且时序上晚于本会话
此前的任何编辑。预填改为**无条件覆盖**已知键（`PIPELINE_KEYS ∪
WALK_FORWARD_KEYS ∪ {mode}` 的并集，跨模式取并以便切模式后有值可用）；
一次性 token 保证每份源载荷只应用一次，预填之后的编辑照常生效。被覆盖且
值不同的字段在横幅里**逐条列出**（旧值 → 新值），覆盖不是静默的。

**fail-loud**：`_prefill_config()` 的 YAMLError 与非映射顶层都写进
`prefill_config_error` 由页面响亮报出；结果页改**严格** UTF-8 解码，失败
就地报错且**不跳页**（不带一份被污染的配置进配置页）。

**提交前差异表**：新增纯函数
`prefill_divergences_from_source_run(prefill, emitted, *, known_keys)`，把
差异**分成四类**分开呈现——混成一句的话，一次老运行重跑会被十几行 schema
演进噪音淹掉真正需要确认的值改动：

| 类 | 含义 | 呈现 |
| --- | --- | --- |
| `changed` | 两侧都有、值不同 | 头条警告 + 表格 |
| `source_missing` | 源运行没记这个键（旧 schema） | 折叠分组 |
| `mode_only` | 键属于另一个模式，本次不提交 | 折叠分组 |
| `run_scoped` | 随运行而生（`output_dir`） | 一行说明 |

比较语义：数值等价（`50` vs `50.0`，YAML 原生类型 vs 表单控件类型）不算
差异；bool 与数字类型敏感（`True` 与 `1` 判为不同）；机器本地键
（provider_uri / namechange_path）整体排除，与预设比较同一套。

**绝不推断缺失基线**：`source_missing` 的源侧留空，不拿本页当前值反填——
那等于替一次没记录的运行编造基线。

**带上源运行的模式**：结果页把 `job["mode"]` 写进
`prefill_config_source_mode`，配置页用它决定预填哪一套字段 schema（归档
config.yaml 未必带 `mode`，CLI 跑出的就没有）。

## Impact

- Affected specs: `v2-operator-ui-console`
- Affected code:
  - `web/operator_ui/pages/_config_run_helpers.py`（四桶纯函数 + 分类常量 +
    `unsupported_prefill_keys` 摘出 run-scoped 键）
  - `web/operator_ui/pages/config_run.py`（预填改无条件覆盖 + fail-loud +
    复核区四桶渲染）
  - `web/operator_ui/pages/_results_render.py`（严格解码 + 写源模式）
- **有行为改变**：预填现在真的会覆盖本会话已有的字段值。这正是修复本身；
  覆盖逐条列出，操作人看得见。
- 不改提交 schema、不改作业启动路径、不碰生产 serving。
