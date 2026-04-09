## 1. Canonical contract

- [x] 1.1 在 `src/core/canonical_backtest_contract.py` 顶部 import `from src.contracts import _shared_validators as _sv`。
- [x] 1.2 `validate_input` 中"非空"检查之后，调用 `_sv.parse_iso_date(request.evaluation_start, error_cls=CanonicalBacktestContractError)` 拿到 `start_d`，同理 `end_d`。
- [x] 1.3 `if start_d > end_d:` 抛 `CanonicalBacktestContractError("evaluation_start (X) must be <= evaluation_end (Y).")`。
- [x] 1.4 `parse_iso_date` 在 `evaluation_start` / `evaluation_end` 是脏字符串时直接通过 `error_cls` 抛出，不用额外 try/except。

## 2. Publisher

- [x] 2.1 在 `src/data/benchmark_artifact_publisher.py` 加 `@staticmethod _parse_iso_strict(value, field_name)`：调用 `date.fromisoformat`，失败抛 `BenchmarkArtifactPublisherError(f"{field_name} must be ISO date YYYY-MM-DD, got '{value}'.")`，成功返回 `date`。
- [x] 2.2 `publish` 方法中现有 `_require_non_empty_str(start_time, "start_time")` 后调用 `_parse_iso_strict(start_time, "start_time")` 拿到 `start_d`，同理 `end_d`。
- [x] 2.3 `if start_d > end_d:` 抛 `BenchmarkArtifactPublisherError(f"start_time '{start_time}' must be <= end_time '{end_time}'.")`。
- [x] 2.4 这两步都必须在调用 `D.features` 之前完成（边界优先于运行时调用）。

## 3. Tests — canonical contract

- [x] 3.1 `test_evaluation_start_bad_format_raises`：`evaluation_start="banana"`，断言抛 `CanonicalBacktestContractError` 且消息含 `banana`。
- [x] 3.2 `test_evaluation_end_bad_format_raises`：`evaluation_end="2026/02/27"`（非 ISO），同上。
- [x] 3.3 `test_evaluation_start_after_end_raises`：合法 ISO 但 start > end。
- [x] 3.4 `test_evaluation_start_equal_end_passes`：start == end（单日窗口）应通过。

## 4. Tests — publisher

- [x] 4.1 `BenchmarkPublisherInitGuardTests` 中先 init canonical runtime（沿用直接修改 `_CANONICAL_CONFIG` / `_CANONICAL_QLIB_INITIALIZED` 的模式），然后：
- [x] 4.2 `test_publish_bad_start_time_raises`：`start_time="banana"`，断言抛 `BenchmarkArtifactPublisherError`，文件未落地。
- [x] 4.3 `test_publish_bad_end_time_raises`：`end_time="2026/02/27"`，同上。
- [x] 4.4 `test_publish_start_after_end_raises`：start > end 合法格式但反向，断言抛 + 文件未落地。
- [x] 4.5 这些 case **不**依赖 qlib bundle —— 校验先于 qlib 调用。

## 5. Quality Gates

- [x] 5.1 `python -m unittest discover -s tests` 全套通过。
- [x] 5.2 grep `evaluation_start` 与 `start_time` 验证两处 validate 路径里都有解析调用。
- [x] 5.3 既有 happy-path 测试（141 个）全部维持通过。

## 6. Spec promotion

- [x] 6.1 把新 Requirement 追加到 `openspec/specs/v2-canonical-backtest-contract/spec.md`。
- [x] 6.2 把新 Requirement 追加到 `openspec/specs/v2-benchmark-artifact-publisher/spec.md`。
- [x] 6.3 归档到 `openspec/changes/archive/2026-04-09-validate-iso-date-boundaries/`。
