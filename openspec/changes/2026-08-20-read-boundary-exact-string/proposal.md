# 读边界先 strip 再解析，于是判的不是产物真正的位置

## Why

写入侧（#453）刚发现并修掉一个同形缺陷：判据先 `strip()` 再 `Path(...)`，
看的就不是生产者用的那个字符串。**读侧有完全对称的一处**，而且它决定的是
「这一行列不列出来、点开去哪」。

## 问题（实测）

`web/operator_ui/job_io.py` 两处对 `run_dir` 先 strip 再当路径用：

```python
def canonical_dir_key(run_dir: str) -> str | None:
    text = str(run_dir or "").strip()      # ← 往下传的是 strip 后的串
    ...
def anchored_run_dir(run_dir: str) -> Path:
    candidate = Path(str(run_dir or "").strip())   # ← 又 strip 一次
```

前导空格是**合法的文件名字符**。本机实测（Windows 11）：

```
Path(tmp) / " output"  →  能创建，os.listdir 回显 ' output'
```

而引擎是把 `config.output_dir` 原样交给 `Path` 的。于是一行
`output_dir=" output/runs/x"`：

- 产物真正在 `<repo>/ output/runs/x` —— **树外**
- 判据 strip 后去看 `<repo>/output/runs/x` —— 树内，**判成可检视并列出来**
- 操作人点开，看到的是**另一次运行**的产物

这正是本条判据自己写明「绝不能发生」的那个方向（admitting a row that is
actually outside）。

## 方案

只拿 `strip` 判「是不是空的」，**绝不把 strip 后的串当路径往下传**：

- `canonical_dir_key`：空判用 `text.strip()`，传给 `anchored_run_dir` 的是原串
- `anchored_run_dir`：不再 strip

空白串仍然算「没给」（`run_dir_is_inspectable("   ")` 照旧 False），这条已有
钉子，不动。

## 影响面

- `web/operator_ui/job_io.py`（读边界的两处入口）
- 操作人真实索引 3560 行里 `output_dir` 带首尾空白的 **0 行** —— 不重新归类
  任何现存数据
- 写入侧的对称修复在 #453，本 change 只管读侧
