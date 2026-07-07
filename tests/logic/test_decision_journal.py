"""Threat-vector tests for the operator decision journal (每日决策页 A2).

Each test class maps to one row of the 开工单 §3 threat table (T1–T5); the
journal is pure Python (no Streamlit), so every threat is machine-verified
locally. See openspec ``add-daily-decision-page`` / spec
``v2-daily-decision-page``.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from web.operator_ui.decision_journal import (
    ACTIONS,
    DecisionJournalError,
    append_decision,
    journal_path,
    make_entry,
    read_journal,
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _entry(**overrides: object):  # type: ignore[no-untyped-def]
    """A valid baseline entry; tests override the fields under scrutiny."""
    kwargs: dict[str, object] = {
        "trade_date": "2026-07-03",
        "code": "SH600000",
        "action": "adopt",
        "reason": "动量强,成本参照后仍有余量",
        "rank": 1,
        "score": 0.0123,
        "model_id": "ab" * 32,
        "nonce": "nonce-0001",
        "decided_at": "2026-07-03T18:30:00+08:00",
    }
    kwargs.update(overrides)
    return make_entry(**kwargs)  # type: ignore[arg-type]


class T1RerunIdempotencyTests(unittest.TestCase):
    """T1 — a Streamlit rerun replaying one submission must not double-append,
    while a deliberate correction (fresh nonce) must never be suppressed."""

    def test_same_nonce_appends_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entry = _entry()
            self.assertTrue(append_decision(entry, journal_dir=tmp))
            self.assertFalse(append_decision(entry, journal_dir=tmp))
            raw = journal_path(tmp).read_bytes()
            self.assertEqual(raw.count(b'"nonce-0001"'), 1)
            self.assertEqual(len(read_journal(journal_dir=tmp).entries), 1)

    def test_new_nonce_same_pair_appends_and_supersedes(self) -> None:
        # A correction is a NEW form render -> NEW nonce -> must append, and
        # the read side must return it as the effective decision.
        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(), journal_dir=tmp)
            correction = _entry(
                action="reject", reason="复盘后改判:成本吃掉边际",
                nonce="nonce-0002", decided_at="2026-07-03T19:00:00+08:00",
            )
            self.assertTrue(append_decision(correction, journal_dir=tmp))
            result = read_journal(journal_dir=tmp)
            self.assertEqual(len(result.entries), 2)  # history survives
            effective = result.effective[("2026-07-03", "SH600000")]
            self.assertEqual(effective.action, "reject")
            self.assertEqual(effective.nonce, "nonce-0002")

    def test_equal_decided_at_tie_breaks_to_later_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(nonce="n1"), journal_dir=tmp)
            append_decision(
                _entry(action="watch", nonce="n2"), journal_dir=tmp,
            )  # identical decided_at
            effective = read_journal(journal_dir=tmp).effective[
                ("2026-07-03", "SH600000")
            ]
            self.assertEqual(effective.nonce, "n2")


class T2TornLineToleranceTests(unittest.TestCase):
    """T2 — a partial line from an interrupted process must be skipped and
    counted, and must not poison later reads OR the append-side dedupe scan."""

    def test_torn_line_skipped_counted_then_append_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(nonce="n1"), journal_dir=tmp)
            # Simulate a torn write: half a JSON object, no closing brace.
            with journal_path(tmp).open("ab") as fh:
                fh.write(b'{"journal_version": 1, "trade_da')
                fh.write(b"\n")
            append_decision(_entry(nonce="n2", code="SZ000001"), journal_dir=tmp)
            result = read_journal(journal_dir=tmp)
            self.assertEqual(len(result.entries), 2)
            self.assertEqual(result.malformed_count, 1)
            # And the dedupe scan across a torn line still catches replays.
            self.assertFalse(
                append_decision(_entry(nonce="n1"), journal_dir=tmp)
            )

    def test_trailing_torn_line_without_newline(self) -> None:
        # The classic interrupted-final-write shape: no trailing newline.
        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(nonce="n1"), journal_dir=tmp)
            with journal_path(tmp).open("ab") as fh:
                fh.write(b'{"half": ')
            result = read_journal(journal_dir=tmp)
            self.assertEqual(len(result.entries), 1)
            self.assertEqual(result.malformed_count, 1)

    def test_append_after_unterminated_tail_quarantines_fragment(self) -> None:
        # codex P1 on #330: appending directly after a newline-less torn tail
        # would FUSE the new entry onto the fragment — one combined malformed
        # line, and the operator's new decision silently vanishes. The writer
        # must isolate the fragment (leading newline in the same single write)
        # so the new entry survives as a clean line.
        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(nonce="n1"), journal_dir=tmp)
            with journal_path(tmp).open("ab") as fh:
                fh.write(b'{"half": ')  # torn tail, NO newline
            new = _entry(
                nonce="n2", code="SZ000001",
                decided_at="2026-07-03T19:00:00+08:00",
            )
            self.assertTrue(append_decision(new, journal_dir=tmp))
            result = read_journal(journal_dir=tmp)
            # The NEW decision is not lost — present in history AND effective.
            self.assertEqual(len(result.entries), 2)
            self.assertEqual(result.malformed_count, 1)
            self.assertEqual(
                result.effective[("2026-07-03", "SZ000001")].nonce, "n2",
            )
            # Byte shape: fragment isolated on its own line, new line clean.
            raw = journal_path(tmp).read_bytes()
            self.assertIn(b'{"half": \n{"journal_version"', raw)
            self.assertNotIn(b"\r", raw)


class T3ByteLevelLineEndingTests(unittest.TestCase):
    """T3 — the writer must emit UTF-8 without BOM and pure LF endings on
    every platform (byte-level assertions; the #321 CRLF lesson)."""

    def test_bytes_are_lf_only_no_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(nonce="n1"), journal_dir=tmp)
            append_decision(
                _entry(nonce="n2", code="SZ000001", reason="观察一手流动性"),
                journal_dir=tmp,
            )
            raw = journal_path(tmp).read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "BOM detected")
        lines = raw.split(b"\n")
        self.assertEqual(lines[-1], b"", "file must end with a newline")
        for line in lines[:-1]:
            self.assertTrue(line.endswith(b"}"), line)


class T4ClockAndTimezoneTests(unittest.TestCase):
    """T4 — trade_date comes from the ARTIFACT (never the local clock);
    decided_at is tz-aware ISO8601 in +08:00."""

    def test_trade_date_is_callers_artifact_date_not_today(self) -> None:
        artifact_as_of = "2026-01-02"  # deliberately != any plausible "today"
        entry = _entry(trade_date=artifact_as_of, decided_at=None)
        self.assertEqual(entry.trade_date, artifact_as_of)

    def test_default_decided_at_is_cn_offset_and_aware(self) -> None:
        entry = _entry(decided_at=None)
        self.assertTrue(entry.decided_at.endswith("+08:00"), entry.decided_at)
        parsed = datetime.fromisoformat(entry.decided_at)
        self.assertIsNotNone(parsed.tzinfo)

    def test_injected_decided_at_used_verbatim_and_naive_rejected(self) -> None:
        entry = _entry(decided_at="2026-07-03T18:30:00+08:00")
        self.assertEqual(entry.decided_at, "2026-07-03T18:30:00+08:00")
        with self.assertRaisesRegex(DecisionJournalError, "tz-aware"):
            _entry(decided_at="2026-07-03T18:30:00")  # naive -> refused

    def test_non_cn_offset_rejected_write_and_read(self) -> None:
        # codex P2 on #330: the contract pins +08:00 rows. Another offset must
        # be refused by the writer AND counted malformed by the reader —
        # otherwise valid-looking lines violate the persisted schema and
        # render inconsistent operator-local times.
        import json as _json

        with self.assertRaisesRegex(DecisionJournalError, r"\+08:00"):
            _entry(decided_at="2026-07-03T10:00:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(nonce="n1"), journal_dir=tmp)
            utc_row = {
                "journal_version": 1, "trade_date": "2026-07-03",
                "code": "SH600000", "action": "reject", "reason": "utc",
                "rank": 1, "score": 0.1, "model_id": "m",
                "decided_at": "2026-07-03T23:00:00+00:00", "nonce": "n-utc",
            }
            with journal_path(tmp).open("ab") as fh:
                fh.write(_json.dumps(utc_row).encode("utf-8") + b"\n")
            result = read_journal(journal_dir=tmp)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.malformed_count, 1)
        self.assertEqual(
            result.effective[("2026-07-03", "SH600000")].action, "adopt",
        )


class T5SrcBoundaryTests(unittest.TestCase):
    """T5 — the journal is web-layer state: zero references from src/ (it must
    never feed official metrics, backtests, training or promotion)."""

    def test_src_tree_has_zero_journal_references(self) -> None:
        offenders: list[str] = []
        for py in _SRC_ROOT.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            if "decision_journal" in text or "QUANT_DECISION_JOURNAL_DIR" in text:
                offenders.append(str(py))
        self.assertEqual(
            offenders, [],
            "src/ must not reference the decision journal (web-owned state; "
            f"never an official-metrics input): {offenders}",
        )


class FailLoudValidationTests(unittest.TestCase):
    """Non-threat twins: make_entry refuses ambiguous input (no silent rows)."""

    def test_action_whitelist(self) -> None:
        self.assertEqual(ACTIONS, ("adopt", "reject", "watch"))
        with self.assertRaisesRegex(DecisionJournalError, "action"):
            _entry(action="buy")

    def test_empty_nonce_reason_code_and_bad_date_refused(self) -> None:
        with self.assertRaisesRegex(DecisionJournalError, "nonce"):
            _entry(nonce="  ")
        with self.assertRaisesRegex(DecisionJournalError, "reason"):
            _entry(reason="")
        with self.assertRaisesRegex(DecisionJournalError, "code"):
            _entry(code="")
        with self.assertRaisesRegex(DecisionJournalError, "trade_date"):
            _entry(trade_date="20260703")

    def test_reader_rejects_compact_trade_date_rows(self) -> None:
        # codex P2 on #330: a valid-JSON row carrying a compact "20260703"
        # (prior buggy build / manual edit) must be counted malformed, not
        # admitted into history/effective where it splits the supersede key.
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(nonce="n1"), journal_dir=tmp)
            bad = {
                "journal_version": 1, "trade_date": "20260703",
                "code": "SH600000", "action": "adopt", "reason": "x",
                "rank": 1, "score": 0.1, "model_id": "m",
                "decided_at": "2026-07-03T20:00:00+08:00", "nonce": "n-bad",
            }
            with journal_path(tmp).open("ab") as fh:
                fh.write(_json.dumps(bad).encode("utf-8") + b"\n")
            result = read_journal(journal_dir=tmp)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.malformed_count, 1)
        self.assertNotIn(("20260703", "SH600000"), result.effective)

    def test_reader_rejects_unsupported_journal_version(self) -> None:
        # codex P2 on #330: a valid-JSON row with journal_version != 1 (future
        # writer / manual repair) must be counted malformed — it must never
        # supersede a real v1 decision silently.
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(nonce="n1"), journal_dir=tmp)
            # Includes the coercion traps (codex P2 round 2): int() would
            # normalize 1.9 -> 1 and True -> 1 (bool subclass, True == 1), and
            # "1" would int() cleanly — the RAW value must equal exactly int 1.
            for bad_version in (2, 1.9, True, "1"):
                row = {
                    "journal_version": bad_version, "trade_date": "2026-07-03",
                    "code": "SH600000", "action": "reject", "reason": "future",
                    "rank": 1, "score": 0.1, "model_id": "m",
                    "decided_at": "2026-07-03T23:00:00+08:00",
                    "nonce": f"n-{bad_version!r}",
                }
                with journal_path(tmp).open("ab") as fh:
                    fh.write(_json.dumps(row).encode("utf-8") + b"\n")
            result = read_journal(journal_dir=tmp)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.malformed_count, 4)
        # The v1 decision still wins — the v2 row did not supersede it.
        self.assertEqual(
            result.effective[("2026-07-03", "SH600000")].action, "adopt",
        )

    def test_journal_dir_under_repo_output_fails_loud(self) -> None:
        # codex P2 on #330: output/ is DISPOSABLE (cleanup / git clean); a
        # journal there would make human decisions discardable. Refuse both
        # the explicit arg and the env-var route.
        from unittest.mock import patch

        repo_output = Path(__file__).resolve().parents[2] / "output"
        with self.assertRaisesRegex(DecisionJournalError, "disposable"):
            journal_path(repo_output / "operator_journal")
        with patch.dict(
            "os.environ",
            {"QUANT_DECISION_JOURNAL_DIR": str(repo_output / "j")},
        ):
            with self.assertRaisesRegex(DecisionJournalError, "disposable"):
                journal_path()

    def test_journal_dir_relative_or_anywhere_in_checkout_refused(self) -> None:
        # codex P2 on #330 round 2: on POSIX the Windows-style default is NOT
        # absolute and would land the journal INSIDE the checkout relative to
        # the cwd — require an absolute path, and refuse the whole checkout
        # (git-clean territory), not just output/.
        with self.assertRaisesRegex(DecisionJournalError, "absolute"):
            journal_path("relative/journal_dir")
        repo_root = Path(__file__).resolve().parents[2]
        with self.assertRaisesRegex(DecisionJournalError, "checkout"):
            journal_path(repo_root / "journal_data")

    def test_io_failures_translate_to_domain_error(self) -> None:
        # codex P2 on #330: read-only dir / permissions / full disk raise raw
        # OSError from open/write/mkdir — the journal API must translate them
        # to DecisionJournalError so the page renders its error path instead
        # of a Streamlit traceback.
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                Path, "open", side_effect=OSError("read-only filesystem"),
            ):
                with self.assertRaisesRegex(DecisionJournalError, "NOT recorded"):
                    append_decision(_entry(), journal_dir=tmp)
        with tempfile.TemporaryDirectory() as tmp:
            append_decision(_entry(), journal_dir=tmp)
            with patch.object(
                Path, "read_bytes", side_effect=OSError("disk unavailable"),
            ):
                with self.assertRaisesRegex(DecisionJournalError, "read failed"):
                    read_journal(journal_dir=tmp)

    def test_journal_dir_existing_as_file_fails_loud_read_and_append(self) -> None:
        # codex P2 on #330: a base that EXISTS as a FILE previously read as an
        # "empty journal" (is_file() False on <file>/decision_journal.jsonl),
        # silently hiding the misconfiguration until the first append. The
        # single resolution point now refuses it for BOTH directions.
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked"
            blocked.write_text("a FILE where the journal dir should be")
            with self.assertRaisesRegex(DecisionJournalError, "not a directory"):
                journal_path(blocked)
            with self.assertRaisesRegex(DecisionJournalError, "not a directory"):
                read_journal(journal_dir=blocked)
            with self.assertRaisesRegex(DecisionJournalError, "not a directory"):
                append_decision(_entry(), journal_dir=blocked)

    def test_env_var_resolution_and_missing_file_reads_empty(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ", {"QUANT_DECISION_JOURNAL_DIR": tmp},
            ):
                self.assertEqual(journal_path().parent, Path(tmp))
                result = read_journal()
        self.assertEqual(result.entries, ())
        self.assertEqual(result.malformed_count, 0)


if __name__ == "__main__":
    unittest.main()
