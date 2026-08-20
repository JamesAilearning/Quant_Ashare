# Tasks: 2026-08-20-read-boundary-exact-string

## 实现

- [x] `anchored_run_dir` 不再 strip，判据判生产者用的那个字符串
- [x] `canonical_dir_key` 只拿 strip 判空，往下传原串
- [x] 空白串仍算「没给」（既有钉子不动）

## 验证（每条要实测数字）

- [x] 本机实测前导空格目录可创建（不是 POSIX 独有）
- [x] 真实索引里带首尾空白的 `output_dir` 计数
- [x] #444 钉死的读边界用例全绿
- [x] 两处入口各变异一次
- [x] 全量 + mypy --strict + ruff
- [x] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

## 实测数字（原样）

```
本机 Windows 11：Path(tmp)/" output" 能创建，os.listdir 回显 ' output'
操作人真实索引 3560 行，output_dir 带首尾空白 0 行 —— 不重新归类现存数据

tests/logic/test_jobs_wf_reachability.py   72 passed / 1 skipped / 50 subtests
全量 tests/logic + tests/governance        (见 PR 正文)

两处入口各变异一次，均被同一条守卫抓到
  AA anchored_run_dir 退回 strip            抓到 -> ...leading_space_names_a_different_directory
  AB canonical_dir_key 退回传 strip 后的串  抓到 -> 同上
```

## 不做

- [x] 写入侧的对称修复在 #453，本 change 不重复
