# Proposal: narrow-publisher-exception-handlers

## Why

`BenchmarkArtifactPublisher._flatten_close_frame` 当前用三个 `except Exception:`
吞掉所有错误并安静返回 `[]` 或继续：

```python
try:
    if hasattr(frame, "empty") and frame.empty:
        return []
except Exception:
    return []

try:
    working = working.reset_index()
except Exception:
    return []

...

for _, record in working.iterrows():
    raw_date = record[datetime_col]
    try:
        iso_date = raw_date.strftime("%Y-%m-%d")
    except AttributeError:
        iso_date = str(raw_date)[:10]
    try:
        close_value = float(raw_close)
    except (TypeError, ValueError):
        continue
```

宽 `except Exception` 把 KeyboardInterrupt 之外的所有错误（包括
真正的 bug：AttributeError on the frame object 因 None、ValueError
from a misshapen index、ImportError 重入）都映射成"返回空 list"，
然后 publisher 就会抛 "qlib provider returned no rows"，operator
看到的根因和真正的根因相差十万八千里。

V1 教训："凡是 `except Exception: return None` 的地方，最后都变成了
凌晨 4 点 grep 日志找元凶"。

## Goals

- 把 `_flatten_close_frame` 中 3 处 `except Exception` 收窄到**真正
  会发生的、来自 pandas/qlib API 的预期类型**：`AttributeError`,
  `TypeError`。
- 不删除任何 fallback 路径（pandas 形状容错仍然存在），只是不再
  屏蔽程序逻辑 bug。
- 不引入新依赖。

## Non-Goals

- 不重写 `_flatten_close_frame` 的整体结构。
- 不接入 pandas 类型注解。
- 不改 `_flatten_close_frame` 之外的代码（其他 try/except 块本就是
  窄类型，无需改动）。

## What Changes

1. `try: if hasattr(frame, "empty") and frame.empty: return [] except Exception: return []`
   → 收窄到 `except (AttributeError, TypeError):`。
   - `hasattr` 不抛常规异常，但访问 `.empty` 在 pandas-like 对象上
     可能触发 `AttributeError`（属性 missing）或 `TypeError`
     （`__bool__` 实现错误）。这两个是合法 fallback 触发器。
2. `try: working = working.reset_index() except Exception:`
   → 收窄到 `except (AttributeError, TypeError, ValueError):`。
   - 非 DataFrame 输入会触发 `AttributeError`。
   - 索引层级冲突会触发 `ValueError`。
   - `TypeError` 处理 reset_index 的参数误用。
3. 测试：在已有 `BenchmarkPublisherSnapshotAtDerivationTests` 旁边
   加 `BenchmarkPublisherFlattenFrameTests`，直接调用
   `BenchmarkArtifactPublisher._flatten_close_frame` 输入：
   - `None` → `[]`
   - 一个没有 `.reset_index` 的 dummy 对象 → `[]`（验证 AttributeError 路径）
   - 一个 `.empty` 是 True 的 stub → `[]`（验证 empty 路径）
   - 一个完整的伪 DataFrame（minimal duck-typed）→ 正常解析

## Impact

- **Affected specs**: `v2-benchmark-artifact-publisher`（追加一条 Requirement
  描述错误处理边界）。
- **Affected code**: `src/data/benchmark_artifact_publisher.py`，仅改 2 行。
- **Affected tests**: `tests/logic/test_benchmark_publisher_e2e.py`，加 ~4 个 case。
- **Backward compatibility**: 完全向后兼容。
  - 之前被 `except Exception` 吞掉的"合法 pandas 形状错误"仍然被吞。
  - 之前被吞掉的"程序逻辑 bug"现在会显式抛出，让 operator 看到真正的栈。
- **Risk**: 极低。改动局限于 2 行 except 类型，且新增测试覆盖。
