"""进度行的**运行身份**：归属不再经过日志窗口。

provider 标记（#474 首版）回答的是「谁写的这行」。它回答不了「哪一次运行写
的」——那仍要靠日志里那条运行边界来划分。而边界在生产上**读不到**：

* 读侧只取日志尾部 4000 字符（``update_runner.log_window`` 的 ``_LOG_TAIL_CHARS``）；
* 一次 fetch 每 200 支票写一行，5883 支票 ≈ 每个 endpoint×年 30 行；
* 一行 ≈ 150 字符 ⇒ 窗口装得下约 26 行，**不到一个 endpoint-年**。

也就是说边界在运行开始不久就被挤出窗口，标记路径随后一律退化成
``window_truncated``——这个改动在它唯一要服务的那个工作负载上不生效。评审
（codex P2 on #474）独立指出了同一件事，并点名要一条「截断输入里**确实没有
边界**」的回归用例。这个文件就是那件事。

盯四件：

1. **窗口里没有任何边界时仍然归属**——这是 run_id 存在的全部理由；
2. 上一次运行留在窗口里的行**不被误收**（它们带的是上一次的 id）；
3. 写侧与读侧的两端（进度行 / 状态工件）拿的是**同一个** id；
4. 放松没有放过一条本该拒绝的：别人的 id、畸形的 id、没有 id 的老行。
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.tushare.fetcher import (  # noqa: E402
    TushareFetcher,
    TushareFetcherConfig,
)
from src.data_pipeline.daily_update import (  # noqa: E402
    DailyUpdateConfig,
    build_plan,
    run_boundary_line,
)
from web.operator_ui.update_progress import (  # noqa: E402
    last_fetch_progress,
    last_fetch_progress_for_run,
)
from web.operator_ui.update_status import read_update_status  # noqa: E402

_TZ = timezone(timedelta(hours=8))
_BOUNDARY_PREFIX = "[src.data_pipeline.daily_update] INFO — "
_FETCH_PREFIX = "[src.data.tushare.fetcher] INFO — "

MINE = "a" * 32
THEIRS = "b" * 32


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _boundary(clock: str, at: datetime, provider: Path) -> str:
    return f"{clock} {_BOUNDARY_PREFIX}{run_boundary_line(at, provider)}\n"


def _progress(
    clock: str, done: int, *, run: str = "", provider: str = "",
    endpoint: str = "daily",
) -> str:
    """一条进度行。``run`` 在 ``provider`` **之前**——与写侧同序。"""
    suffix = (f" run={run}" if run else "") + (
        f" provider={provider}" if provider else "")
    return (
        f"{clock} {_FETCH_PREFIX}  {endpoint} year=2026 progress: "
        f"{done}/5883 tickers (written={done}, skipped=0){suffix}\n"
    )


class TheBoundaryReallyDoesLeaveTheWindowTests(unittest.TestCase):
    """先把前提钉住：边界法失效不是猜的。

    没有这一条，后面每一条都只是在证明「新路径能用」，证明不了「旧路径
    不够用」——而后者才是这个改动存在的理由。
    """

    def test_one_endpoint_year_of_progress_overruns_the_read_window(
        self,
    ) -> None:
        from web.operator_ui.update_runner import _LOG_TAIL_CHARS

        at = datetime(2026, 8, 27, 20, 0, tzinfo=_TZ)
        provider = PROJECT_ROOT / "output" / "mine_bundle"
        # 生产实况：5883 支票、每 200 支一行 ⇒ 一个 endpoint×年 30 行。
        lines = "".join(
            _progress(f"20:{i:02d}:00", i * 200,
                      run=MINE, provider=_key(provider))
            for i in range(1, 31)
        )
        full = _boundary("20:00:00", at, provider) + lines

        window = full[-_LOG_TAIL_CHARS:]

        self.assertGreater(
            len(full), _LOG_TAIL_CHARS,
            "一个 endpoint-年的进度就该撑破读取窗口——前提不成立则本改动无意义",
        )
        self.assertNotIn(
            "run started", window,
            "边界必须已经被挤出窗口；否则这个回归用例没有覆盖到真实形态",
        )
        # 边界法在这份窗口上确实答不出来。
        boundaryless = last_fetch_progress_for_run(
            window, provider_dir=provider, window_complete=False)
        self.assertFalse(boundaryless.attributed)
        self.assertEqual(boundaryless.unattributed_reason, "window_truncated")
        # 同一份窗口，带上运行身份就能归属。
        with_run = last_fetch_progress_for_run(
            window, provider_dir=provider, window_complete=False, run_id=MINE)
        self.assertTrue(with_run.attributed)
        self.assertEqual(with_run.attribution, "run_stamp")


class RunStampAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mine = PROJECT_ROOT / "output" / "mine_bundle"
        self.theirs = PROJECT_ROOT / "output" / "their_bundle"
        self.at = datetime(2026, 8, 27, 20, 0, tzinfo=_TZ)

    def test_a_window_with_no_boundary_at_all_still_attributes(self) -> None:
        log = _progress("20:50:30", 400, run=MINE, provider=_key(self.mine))

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=False, run_id=MINE)

        self.assertTrue(got.attributed)
        self.assertEqual(got.attribution, "run_stamp")
        assert got.progress is not None
        self.assertEqual(got.progress.done, 400)
        self.assertEqual(got.unattributed_reason, "")
        # 归属不是靠边界来的，所以没有边界戳可报——页面必须按 attribution
        # 分派，不能拿这个空串去比工件的起跑时刻。
        self.assertEqual(got.boundary_stamp, "")

    def test_a_previous_runs_leftover_line_is_not_collected(self) -> None:
        # provider 标记做不到这件事：上一次运行也是**我们**写的，标记一模
        # 一样。只有运行身份能把两次运行分开。
        log = (
            _progress("19:00:00", 5883, run=THEIRS, provider=_key(self.mine))
            + _progress("20:50:30", 400, run=MINE, provider=_key(self.mine))
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=False, run_id=MINE)

        assert got.progress is not None
        self.assertEqual(got.progress.done, 400)

    def test_only_our_own_run_id_counts(self) -> None:
        log = _progress("20:50:30", 400, run=THEIRS, provider=_key(self.mine))

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=False, run_id=MINE)

        self.assertFalse(got.attributed)
        self.assertEqual(got.attribution, "")
        self.assertEqual(got.unattributed_reason, "no_own_run_stamp")

    def test_a_malformed_stamp_is_simply_not_ours_not_a_corruption(
        self,
    ) -> None:
        # 畸形的戳只是「不匹配」。判成损坏会让一条脏行毁掉整页的归属，而
        # 那条行本可以直接落选。
        log = _progress("20:50:30", 400, run="not-a-uuid",
                        provider=_key(self.mine))

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=False, run_id=MINE)

        self.assertFalse(got.attributed)
        self.assertNotEqual(got.unattributed_reason, "corrupt_boundary")
        self.assertEqual(got.unattributed_reason, "no_own_run_stamp")

    def test_an_old_unstamped_line_falls_back_to_the_boundary_path(
        self,
    ) -> None:
        # 老日志（本字段之前的产出）没有 run=。归属退回边界法——正是本字段
        # 落地之前的行为，不是退步。
        log = (
            _boundary("20:00:00", self.at, self.mine)
            + _progress("20:50:30", 400)
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=True, run_id=MINE)

        self.assertTrue(got.attributed)
        self.assertEqual(got.attribution, "boundary")
        self.assertEqual(got.boundary_stamp, self.at.isoformat())

    def test_without_a_run_id_the_reader_behaves_exactly_as_before(
        self,
    ) -> None:
        # 状态工件没有 run_id（旧产出器）时，新路径整个不参与。
        log = (
            _boundary("20:00:00", self.at, self.mine)
            + _progress("20:50:30", 400, run=MINE, provider=_key(self.mine))
        )

        got = last_fetch_progress_for_run(
            log, provider_dir=self.mine, window_complete=True)

        self.assertTrue(got.attributed)
        self.assertEqual(got.attribution, "boundary")


class LineShapeTests(unittest.TestCase):
    def test_run_and_provider_are_parsed_independently(self) -> None:
        line = _progress("20:50:30", 400, run=MINE, provider="d:/qlib data/p")

        got = last_fetch_progress(line)

        assert got is not None
        self.assertEqual(got.run, MINE)
        self.assertEqual(got.provider, "d:/qlib data/p")

    def test_a_provider_path_containing_the_run_marker_is_not_split(
        self,
    ) -> None:
        # ``run=`` 排在 ``provider=`` 之前是**承重**的：provider 是路径、可以
        # 含空格，所以它必须占到行尾。反过来放，一个含 " run=" 的目录名会把
        # 自己的一截交出去当运行身份。
        line = _progress("20:50:30", 400, provider="/data/x run=deadbeef")

        got = last_fetch_progress(line)

        assert got is not None
        self.assertEqual(got.run, "")
        self.assertEqual(got.provider, "/data/x run=deadbeef")

    def test_an_old_line_without_a_run_marker_still_parses(self) -> None:
        line = _progress("20:50:30", 400, provider="/data/x")

        got = last_fetch_progress(line)

        assert got is not None
        self.assertEqual(got.run, "")
        self.assertEqual(got.done, 400)


class WriterStampTests(unittest.TestCase):
    def _fetcher(self, run_id: str) -> TushareFetcher:
        fetcher = TushareFetcher.__new__(TushareFetcher)
        fetcher._config = TushareFetcherConfig(
            output_dir=Path("out"), run_id=run_id)
        return fetcher

    def test_a_configured_run_id_becomes_a_suffix(self) -> None:
        self.assertEqual(
            self._fetcher(MINE)._progress_run_suffix(), f" run={MINE}")

    def test_no_run_id_stamps_nothing_at_all(self) -> None:
        # 空串而不是 " run="：读侧必须能分辨「没报身份」与「报了空身份」。
        self.assertEqual(self._fetcher("")._progress_run_suffix(), "")

    def test_a_run_id_with_whitespace_is_refused(self) -> None:
        for bad in ("dead beef", "dead\nbeef", "dead\tbeef"):
            with self.subTest(bad=bad):
                self.assertEqual(self._fetcher(bad)._progress_run_suffix(), "")


class BothEndsCarryOneIdentityTests(unittest.TestCase):
    """进度行与状态工件必须拿到**同一个** id，否则对照物永远比不上。"""

    def _config(self) -> DailyUpdateConfig:
        provider = PROJECT_ROOT / "output" / "mine_bundle"
        return DailyUpdateConfig(
            provider_dir=provider,
            tushare_dir=provider.parent / "tushare_raw",
            delisted_registry=provider.parent / "delisted.csv",
            reference_cases=provider.parent / "cases.csv",
        )

    def test_the_orchestrator_passes_the_run_id_into_the_fetch_argv(
        self,
    ) -> None:
        config = self._config()

        plan = build_plan(config, run_date=date(2026, 8, 27), run_id=MINE)

        self.assertIn("--run-id", plan.fetch)
        self.assertEqual(plan.fetch[plan.fetch.index("--run-id") + 1], MINE)

    def test_no_run_id_means_no_flag_rather_than_an_empty_one(self) -> None:
        # 空值传下去会让写侧盖一个空身份。缺省就是整个不传。
        config = self._config()

        plan = build_plan(config, run_date=date(2026, 8, 27))

        self.assertNotIn("--run-id", plan.fetch)

    def test_the_fetch_cli_forwards_the_flag_into_the_config(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_fetch_cli",
            PROJECT_ROOT / "scripts" / "data_pipeline" / "01_fetch_tushare.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        args = module._build_arg_parser().parse_args(
            ["--output-dir", "out", "--run-id", MINE])

        self.assertEqual(args.run_id, MINE)


class StatusArtifactRunIdTests(unittest.TestCase):
    """对照物那一端：工件里的 run_id 与 pid / launch_nonce 同纪律。"""

    def _write(self, tmp: Path, value: object, *, present: bool = True) -> Path:
        import json

        payload: dict[str, object] = {
            "schema_version": 1,
            "state": "running",
            "provider_dir": "d:/p",
            "run_date": "2026-08-27",
            "started_at": "2026-08-27T20:00:00+08:00",
        }
        if present:
            payload["run_id"] = value
        path = tmp / "status.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_a_well_formed_run_id_is_read_back(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            status = read_update_status(self._write(Path(tmp), MINE))

        self.assertEqual(status.run_id, MINE)

    def test_a_missing_key_is_none_not_an_error(self) -> None:
        # 旧产出器的记录。读侧退回边界法，不是「工件坏了」。
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            status = read_update_status(
                self._write(Path(tmp), None, present=False))

        self.assertEqual(status.kind, "running")
        self.assertIsNone(status.run_id)

    def test_a_malformed_run_id_is_corrupt_not_silently_absent(self) -> None:
        # 读成「没有身份」会让归属悄悄退回边界法——操作人看到「归属未知」，
        # 而真相是工件坏了。两句话对应的下一步完全不同。
        import tempfile

        for bad in (None, "", "XYZ", "A" * 32, "a" * 31, 123):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                status = read_update_status(self._write(Path(tmp), bad))
                self.assertEqual(status.kind, "corrupt")
                self.assertIn("run_id", status.error)


if __name__ == "__main__":
    unittest.main()
