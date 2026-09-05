# 运行工件没有身份：契约要求的三个字段，滚动验证一个都没写

## Why

`v2-run-artifact-contract` 已经签过一条要求：

> The run-artifact contract SHALL define required manifest metadata for
> reproducibility and auditability, including **run identity, config
> fingerprint**, lineage context, and timestamps.

而产出器今天写进 `walk_forward_report.json` 的顶层键是（`aggregate.py:106`
起的字面量，源码实读）:

```
generated_at, git_commit, git_dirty, comparison_provenance, config, folds,
aggregate_metrics, metric_status, metrics_purpose, test_window_coverage,
num_folds
```

**`run_id` 与 `config_fingerprint` 不在其中。** `grep -rn "run_id"
src/core/walk_forward/` 零命中;`config_fingerprint` 只在 `_resume.py` 里为
resume manifest 算，**从不写进报告**。lineage（`git_commit` / `git_dirty`）
与 timestamps 早已写了——契约四项里缺的正是**身份**那两项。

身份只活在 `output/runs/_index.jsonl` 的目录行里,而那一行**不在产物目录
里**——磁盘上那个目录说不出自己属于谁。

> 一处更正:本提案初稿把 `git_commit` 也列成「没有」。那是拿磁盘上一份
> **2026-05-22** 的旧工件（只有 6 个顶层键）去论证今天的产出器——那份工件
> 早于 `git_commit` / `comparison_provenance` / `metric_status` 被加进报告。
> 结论不变（身份仍然缺），但缺的是**两项**不是三项。

代价可以量出来。本机 catalog:

| 指标 | 实测 |
| --- | --- |
| catalog 总行数 | **105** |
| 不同输出目录 | **33** |
| 指向**已被后来的运行覆盖**的产物的行 | **72** |
| 单个 `output/walk_forward` 目录上的行 | **59**，带 **3 个不同的配置指纹** |

那 59 行不是一次配置的幂等重跑——三个不同指纹意味着**真正不同的配置互相
覆盖掉了**。UI 侧那些「产物已被覆盖」的警告（`job_io.fold_catalog_by_dir`、
两个详情页的横幅）是**读侧止损**:早先那次运行的产物已经从磁盘上没了。

现状之所以如此，是因为三件事恰好凑齐:

1. `engine.py:134-135` 只做 `Path(config.output_dir)` + `mkdir(exist_ok=True)`，
   路径里没有任何身份成分;preset 把目录名钉死（`csi800_cadence5_base.yaml`
   等），同一个 preset 每次跑都落在同一个目录;
2. 引擎**已经算出**了配置指纹并**已经发现**了冲突——`engine.py:281-285`
   在 `fingerprint_mismatch` 时打一条 `warning ... re-running and overwriting
   prior manifest`，然后覆盖。它知道，但不拦;
3. 目录级没有互斥。`run_catalog` 的锁只覆盖索引文件本身
   （`run_catalog.py:211-259`），引擎侧对 `lock|flock|msvcrt` 全量 grep 无命中。

对照组:pipeline **已经**做对了——`pipeline.py:937-963` 的 `_make_run_dir`
生成 `runs/{时间戳}_{uuid8}_{指纹}` 且 `exist_ok=False`。滚动验证没有等价物。

## What Changes

**范围只有身份本身。** UI 侧的「证据状态」（当前可检视 / 已被覆盖 / 缺失 /
读边界外）是下一个 change（A2）的事——它要消费本 change 落下的字段。

### 1. 产物里写上身份

每一次**非 dry-run** 的滚动验证运行铸一个 `run_id`，并把契约里**尚缺的那
两项**写进 `walk_forward_report.json` 顶层:`run_id` / `config_fingerprint`
（`git_commit` / `git_dirty` 产出器已经在写，不动）。指纹用**既有的** `compute_config_fingerprint`（`_resume.py:116`）
——它已经刻意排除 `output_dir`，所以改目录名不会让同一份配置换指纹，这正是
「两次同配置运行可被认成同一配置」所需要的语义。不新造第二套哈希。

### 2. 目录写上「现在归谁」

目录里落一份 owner 标记（`run_owner.json`:`run_id` + `config_fingerprint` +
`started_at`）。它回答的是磁盘上那个目录自己回答不了的问题:**这堆字节属于
哪一次运行**。

### 3. 指纹冲突从「警告后覆盖」改成**拒绝**

目录当前的 owner 指纹与本次不同 ⇒ **拒绝启动**并说清两个指纹与那个目录，
除非显式带 `--allow-overwrite`。今天那条 warning 已经在正确的位置上，只是
后面跟着的是覆盖而不是停下。

指纹**相同**时照常复用目录——这是承重的:`engine.py:186` 的
`FoldManifest.discover(output_dir)` 让「同目录」成为 resume 的**前提**，
给每次运行铸一个新目录会把 resume 整个废掉。所以本 change 不动目录布局。

### 4. 目录级互斥

运行期间持有一把以 `output_dir` 为名的锁（复用 `single_flight.lock_path_for`
的机制，不新造）。同一目录上的第二个进程**拒绝启动**，而不是把 fold 文件
交错写进去、让聚合报告变成「谁后完成算谁」。

生产 runbook 今天只有一句文字规定「严格串行，绝不并发」
（`docs/csi800-n5-production-runbook.md:59`），没有机器守卫。

### 5. 向后兼容

老工件没有这三个字段 → 读侧一律当 **legacy**，不是损坏。本 change **不**
改任何读侧行为;它只让新产出带上身份。

## Impact

- Affected specs: `v2-run-artifact-contract`（把「契约应当要求」落成**产出器
  必须写**）、`v2-factor-mining-walk-forward`（引擎行为）
- Affected code:
  - `src/core/walk_forward/engine.py`（铸 id、写 owner、指纹冲突拒绝、目录锁）
  - `src/core/walk_forward/aggregate.py`（报告顶层三个字段）
  - `scripts/run_walk_forward.py`（`--allow-overwrite`）
- **不改**目录布局、不改 resume 语义、不改任何官方指标、不动 UI
- 风险:这是 `src/core/` 生产路径。指纹冲突改成拒绝会让**今天能跑通的某些
  重跑失败**——那正是本 change 的目的，但需要 runbook 同步说明逃生门
  （`--allow-overwrite`）

## 划界（本 change 不做）

- 不做 UI 的证据状态（A2）
- 不给每次运行铸独立目录——resume 依赖同目录（见上）
- 不改 pipeline 那一侧:它已经有 `_make_run_dir`，重复实现只会多一份会漂的
  推导
- 不做产物完整性清单（每个文件的大小/摘要）——那是更下一层，且与本 change
  的「身份」是两个问题
- **不回填**历史工件的身份:那 72 行指向的产物已经没了，凭空补一个 id 等于
  替一次无从复原的运行编造证据
