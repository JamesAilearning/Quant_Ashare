## 1. Code

- [x] 1.1 把 `_flatten_close_frame` 中第一个 `except Exception:` 改为 `except (AttributeError, TypeError):`。
- [x] 1.2 把 `_flatten_close_frame` 中第二个 `except Exception:` 改为 `except (AttributeError, TypeError, ValueError):`。

## 2. Tests

- [x] 2.1 在 `tests/logic/test_benchmark_publisher_e2e.py` 加 `BenchmarkPublisherFlattenFrameTests`。
- [x] 2.2 case `frame=None` → `[]`。
- [x] 2.3 case dummy 对象没有 `.reset_index` → `[]`。
- [x] 2.4 case `.empty == True` 的 stub → `[]`。
- [x] 2.5 case minimal duck-typed DataFrame（含 `columns`, `iterrows`, `reset_index`, `datetime` + `$close` 列）→ 正常解析为 sorted tuples。

## 3. Quality Gates

- [x] 3.1 全套测试通过。
- [x] 3.2 grep 验证 publisher 中 `except Exception` 不再出现在 `_flatten_close_frame` 内部（公共 wider try/except 不在本次范围内）。

## 4. Spec promotion

- [x] 4.1 把新的 Requirement 追加到 `openspec/specs/v2-benchmark-artifact-publisher/spec.md`。
- [x] 4.2 归档到 `openspec/changes/archive/2026-04-08-narrow-publisher-exception-handlers/`。
