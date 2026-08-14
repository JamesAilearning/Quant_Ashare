"""Contract guards for the 生产运维 (ops cockpit) page.

The page shows five facts about live production and hands over the commands
to act on them. Two failure classes matter more than anything else here, and
each has its own block below:

* **The page acting.** It must never launch, train, rotate, or write. The
  acts it describes replace the live bundle and rewrite the production
  manifest; a click must not be able to start one.
* **The page asserting.** A number it cannot establish must render as
  unknown-with-a-reason. Showing a stale answer, a default, or a digest that
  nothing authorized is worse than showing a gap, because the operator
  cannot tell the difference from the outside.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PAGE = _ROOT / "web" / "operator_ui" / "pages" / "ops_cockpit.py"
_HELPERS = _ROOT / "web" / "operator_ui" / "pages" / "_ops_cockpit_helpers.py"
_RECERT = _ROOT / "web" / "operator_ui" / "recert_health.py"
_APP = _ROOT / "web" / "operator_ui" / "app.py"
_DAILY_HELPERS = (
    _ROOT / "web" / "operator_ui" / "pages" / "_daily_decision_helpers.py")


class PageBoundaryTests(unittest.TestCase):
    """生产运维 renders and explains; it never acts."""

    def setUp(self) -> None:
        self.page = _PAGE.read_text(encoding="utf-8")

    def test_no_job_training_or_execution_surfaces(self) -> None:
        for forbidden in (
            "JobManager", "job_runner", "config_run", "import qlib",
            "subprocess", "os.system", "Popen",
            # the acts this page DESCRIBES must not be reachable from it
            "rotate_ensemble_member", "retrain_gate.py --", "daily_update(",
        ):
            self.assertNotIn(forbidden, self.page, forbidden)

    def test_no_write_side_api_at_all(self) -> None:
        # Not "only writes the journal" like 今日推荐 — this page writes
        # NOTHING.
        for write_api in (
            "open(", "write_text", "write_bytes", "mkdir", "os.remove",
            "shutil", "unlink", "rename",
        ):
            self.assertNotIn(write_api, self.page, write_api)

    def test_commands_are_shown_as_copyable_text(self) -> None:
        # st.code renders a copy button; a button that RUNS something would
        # be st.button + a call, which the surfaces test above forbids.
        self.assertIn("st.code(", self.page)
        self.assertNotIn("st.button(", self.page)

    def test_page_registered_with_icon(self) -> None:
        app = _APP.read_text(encoding="utf-8")
        self.assertIn('ops_cockpit.py"), title="生产运维"', app)
        self.assertIn('"生产运维": "\\U0001f6e0"', app)


class IrreversibleCommandTests(unittest.TestCase):
    """A command that replaces the live bundle or the production manifest
    must not read like the read-only ones next to it."""

    def test_the_two_irreversible_commands_are_flagged(self) -> None:
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import (
            data_update_command,
            morning_command,
            rotation_commands,
        )
        self.assertTrue(
            data_update_command(provider_uri="P", delisted_registry="R"
                                ).irreversible, "换库是不可逆的")
        execute = [c for c in rotation_commands("M") if "execute" in c.command]
        self.assertEqual(1, len(execute))
        self.assertTrue(execute[0].irreversible, "轮换执行改写生产 manifest")
        # ...and the read-only one is NOT flagged, so the flag keeps meaning
        # something.
        self.assertFalse(morning_command(
            IncumbentIdentity(kind="ensemble", manifest_path="m.json",
                              manifest_sha256="a" * 64),
            model_path="x.pkl").irreversible)

    def test_the_page_renders_the_irreversible_marker(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("cmd.irreversible", page)
        self.assertIn("不可逆", page)


class ResolvedCommandTests(unittest.TestCase):
    """Commands are built from the deployment state the page RESOLVED.

    codex #431 r1: printing `$QUANT_PROVIDER_URI` looks portable and is
    worse than useless — only ``daily_recommend.py`` reads QUANT_* itself, so
    on the supported layout (variables unset, UI falling back to documented
    defaults) the operator's shell expands it to an empty string. Under the
    single-model opt-out the variable's literal value is ``none``, so the
    pasted command is rejected outright. The page already knows the real
    values.
    """

    def _ensemble(self) -> object:
        from web.operator_ui.incumbent import IncumbentIdentity
        return IncumbentIdentity(
            kind="ensemble", manifest_path="/srv/prod_manifest.json",
            manifest_sha256="a" * 64)

    def test_no_generated_command_prints_a_shell_variable(self) -> None:
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import (
            data_update_command,
            morning_command,
            rotation_commands,
        )
        commands = [
            morning_command(self._ensemble(), model_path="/srv/m.pkl"),  # type: ignore[arg-type]
            morning_command(IncumbentIdentity(kind="single"),
                            model_path="/srv/m.pkl"),
            morning_command(IncumbentIdentity(kind="unresolvable", error="x"),
                            model_path="/srv/m.pkl"),
            data_update_command(provider_uri="/srv/bundle",
                                delisted_registry="/srv/reg.parquet"),
            *rotation_commands("/srv/prod_manifest.json"),
        ]
        for cmd in commands:
            with self.subTest(cmd=cmd.title):
                self.assertNotIn("$QUANT", cmd.command,
                                 "未设时 shell 会展开成空串")

    def test_ensemble_deployment_gets_the_resolved_manifest(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import morning_command
        cmd = morning_command(self._ensemble(), model_path="/srv/m.pkl")  # type: ignore[arg-type]
        self.assertIn("--ensemble-manifest /srv/prod_manifest.json", cmd.command)

    def test_single_model_deployment_gets_a_single_model_command(self) -> None:
        # The opt-out's pointer value is literally `none`; passing that to
        # --ensemble-manifest is rejected by the CLI.
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import morning_command
        cmd = morning_command(
            IncumbentIdentity(kind="single"), model_path="/srv/m.pkl")
        self.assertIn("--model /srv/m.pkl", cmd.command)
        self.assertNotIn("--ensemble-manifest", cmd.command)
        self.assertNotIn("none", cmd.command)

    def test_unresolvable_incumbent_gets_no_runnable_command(self) -> None:
        # Naming a manifest here would hand over a command to score with a
        # model the page just refused to vouch for.
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import morning_command
        cmd = morning_command(
            IncumbentIdentity(kind="unresolvable",
                              manifest_path="/srv/broken.json", error="boom"),
            model_path="/srv/m.pkl")
        self.assertTrue(cmd.command.lstrip().startswith("#"))
        self.assertNotIn("/srv/broken.json", cmd.command)
        self.assertNotIn("daily_recommend.py --", cmd.command)

    def test_data_update_embeds_every_resolved_path(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import (
            TUSHARE_DIR_PLACEHOLDER,
            data_update_command,
        )
        cmd = data_update_command(
            provider_uri="/srv/bundle", delisted_registry="/srv/reg.parquet")
        self.assertIn("--provider-dir /srv/bundle", cmd.command)
        self.assertIn("--delisted-registry /srv/reg.parquet", cmd.command)
        # The one argument with no documented env var stays an honest
        # placeholder rather than a variable that expands to nothing.
        self.assertIn(TUSHARE_DIR_PLACEHOLDER, cmd.command)
        self.assertIn("不读", cmd.note)

    def test_line_continuations_are_real(self) -> None:
        # A literal backslash-n would paste as one broken line.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            data_update_command,
        )
        cmd = data_update_command(provider_uri="P", delisted_registry="R")
        self.assertIn("\\\n", cmd.command)
        self.assertNotIn("\\n ", cmd.command)

    def test_delisted_registry_resolver_follows_the_env_var(self) -> None:
        import os
        from unittest.mock import patch

        from web.operator_ui.pages._ops_cockpit_helpers import (
            DEFAULT_DELISTED_REGISTRY,
            ENV_DELISTED_REGISTRY,
            resolve_delisted_registry,
        )
        with patch.dict(os.environ, {ENV_DELISTED_REGISTRY: ""}, clear=False):
            self.assertEqual(
                DEFAULT_DELISTED_REGISTRY, resolve_delisted_registry())
        with patch.dict(os.environ, {ENV_DELISTED_REGISTRY: "/x/y.parquet"},
                        clear=False):
            self.assertEqual("/x/y.parquet", resolve_delisted_registry())


class SharedIncumbentTests(unittest.TestCase):
    """今日推荐 and 生产运维 must be incapable of naming different models."""

    def test_both_pages_resolve_through_the_same_module(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        daily = (_ROOT / "web" / "operator_ui" / "pages"
                 / "daily_decision.py").read_text(encoding="utf-8")
        self.assertIn(
            "from web.operator_ui.incumbent import resolve_incumbent", page)
        # 今日推荐 keeps its existing import surface, which now re-exports
        # from the same shared module rather than defining a second copy.
        self.assertIn("resolve_incumbent", daily)
        helpers = _DAILY_HELPERS.read_text(encoding="utf-8")
        self.assertIn("from web.operator_ui.incumbent import", helpers)
        self.assertNotIn("def resolve_incumbent(", helpers,
                         "第二份实现 = 两页可能给出不同答案")

    def test_the_resolver_has_exactly_one_definition(self) -> None:
        from web.operator_ui import incumbent as shared
        from web.operator_ui.pages import _daily_decision_helpers as daily
        self.assertIs(shared.resolve_incumbent, daily.resolve_incumbent)


class GateCardTests(unittest.TestCase):
    """Authority is the tracked baseline's digest — not the artifact's own
    claim about itself."""

    def _write(self, tmp: Path, name: str, payload: dict[str, object]) -> Path:
        path = tmp / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _gate(self, **over: object) -> dict[str, object]:
        from scripts.retrain_gate_lib import GATE_PROFILE, GATE_SCHEMA_VERSION
        base: dict[str, object] = {
            "schema_version": GATE_SCHEMA_VERSION,
            "profile": GATE_PROFILE,
            "scope": "member",
            "overall": "PASS",
            "gates": {
                "trainer_integrity": {"verdict": "PASS"},
                "ic_direction": {"verdict": "PASS"},
            },
            "missing_gates": [],
        }
        base.update(over)
        return base

    def _baseline(self, entries: dict[str, object]) -> dict[str, object]:
        return {"authorized_by": {"gate_artifacts": entries}}

    def test_production_baseline_binds_all_four_artifacts(self) -> None:
        # The real thing, end to end: every authorized artifact is locatable
        # and its bytes match what authorized the cutover.
        from web.operator_ui.pages._ops_cockpit_helpers import read_gate_cards
        cards, fatal = read_gate_cards()
        self.assertIsNone(fatal)
        self.assertEqual(4, len(cards))
        for card in cards:
            with self.subTest(gate=card.key):
                self.assertTrue(card.evidence_intact, card.error)
                self.assertEqual("PASS", card.overall)
                self.assertEqual((), card.missing_gates)

    def test_a_tampered_artifact_shows_no_verdict(self) -> None:
        # The failure this whole digest dance exists for: a file that no
        # longer matches the authorization must not get to state its own
        # conclusion, however confidently it does so.
        import hashlib
        import tempfile

        from web.operator_ui.pages._ops_cockpit_helpers import read_gate_cards
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            good = json.dumps(self._gate()).encode("utf-8")
            digest = hashlib.sha256(good).hexdigest()
            # On disk: a DIFFERENT file (still claiming PASS) at that path.
            self._write(tmp, "g.json", self._gate(overall="PASS", scope="member"))
            (tmp / "g.json").write_bytes(good + b" ")
            bl = tmp / "baseline.json"
            bl.write_text(json.dumps(self._baseline(
                {"member[0]": {"path": "g.json", "sha256": digest}})),
                encoding="utf-8")
            cards, fatal = read_gate_cards(
                baseline_path=bl, evidence_dir=tmp, root=tmp)
        self.assertIsNone(fatal)
        self.assertEqual(1, len(cards))
        self.assertFalse(cards[0].evidence_intact)
        self.assertIsNone(cards[0].overall, "未与授权绑定的文件不得陈述结论")

    def test_the_parsed_bytes_are_the_hashed_bytes(self) -> None:
        # codex #431 r1: hashing the path and then re-READING it to parse
        # leaves a window — the baseline's recorded path is under the mutable
        # output/ tree — in which a swapped file passes the digest check and
        # then supplies the verdict that gets displayed as authorized.
        #
        # Simulated deterministically: the file on disk holds the authorized
        # bytes, but any second read returns different content. A single-read
        # implementation never sees the swap; a re-reading one shows FAIL
        # while still claiming the evidence is intact.
        import hashlib
        import tempfile
        from unittest.mock import patch

        from web.operator_ui.pages._ops_cockpit_helpers import read_gate_cards
        authorized = json.dumps(self._gate(overall="PASS")).encode("utf-8")
        swapped = json.dumps(self._gate(overall="FAIL")).encode("utf-8")
        real_read_text = Path.read_text

        def swapping(self_path: Path, *a: object, **kw: object) -> str:
            if self_path.name == "g.json":
                return swapped.decode("utf-8")
            return real_read_text(self_path, *a, **kw)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "g.json").write_bytes(authorized)
            bl = tmp / "baseline.json"
            bl.write_text(json.dumps(self._baseline({"member[0]": {
                "path": "g.json",
                "sha256": hashlib.sha256(authorized).hexdigest()}})),
                encoding="utf-8")
            with patch.object(Path, "read_text", swapping):
                cards, _ = read_gate_cards(
                    baseline_path=bl, evidence_dir=tmp, root=tmp)
        self.assertEqual(1, len(cards))
        self.assertTrue(cards[0].evidence_intact)
        self.assertEqual(
            "PASS", cards[0].overall,
            "显示的必须是被授权的那份字节,不是之后被换上去的")

    def test_a_missing_gate_block_is_reported(self) -> None:
        import hashlib
        import tempfile

        from web.operator_ui.pages._ops_cockpit_helpers import read_gate_cards
        payload = self._gate(gates={"trainer_integrity": {"verdict": "PASS"}})
        raw_bytes = json.dumps(payload).encode("utf-8")
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "g.json").write_bytes(raw_bytes)
            bl = tmp / "baseline.json"
            bl.write_text(json.dumps(self._baseline({"member[0]": {
                "path": "g.json",
                "sha256": hashlib.sha256(raw_bytes).hexdigest()}})),
                encoding="utf-8")
            cards, _ = read_gate_cards(
                baseline_path=bl, evidence_dir=tmp, root=tmp)
        self.assertTrue(cards[0].evidence_intact)
        self.assertIn("ic_direction", cards[0].missing_gates)

    def test_an_unlocatable_artifact_is_not_silently_omitted(self) -> None:
        import tempfile

        from web.operator_ui.pages._ops_cockpit_helpers import read_gate_cards
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            bl = tmp / "baseline.json"
            bl.write_text(json.dumps(self._baseline(
                {"ensemble": {"path": "nope.json", "sha256": "ab" * 32}})),
                encoding="utf-8")
            cards, _ = read_gate_cards(
                baseline_path=bl, evidence_dir=tmp, root=tmp)
        self.assertEqual(1, len(cards), "缺失的授权工件必须成卡呈现,不能消失")
        self.assertFalse(cards[0].evidence_intact)
        self.assertIsNone(cards[0].overall)

    def test_tight_margin_is_computed_from_the_artifacts_own_thresholds(self) -> None:
        # 0.7484 against a 0.75 limit passed with 0.0016 to spare. "PASS"
        # alone hides that; the operator should see how close it came.
        from web.operator_ui.pages._ops_cockpit_helpers import read_gate_cards
        cards, _ = read_gate_cards()
        veto = [g for c in cards for g in c.gates if g.name == "serving_veto"]
        self.assertEqual(1, len(veto))
        weights = {m.name: m for m in veto[0].metrics}
        self.assertIn("csi500_weight", weights)
        self.assertTrue(weights["csi500_weight"].is_tight,
                        "0.7484 / 0.75 必须被判为贴边")
        self.assertAlmostEqual(0.0016, weights["csi500_weight"].margin, places=6)

    def test_thresholds_are_not_restated_in_the_ui(self) -> None:
        # A second copy of the limits could drift from the ones the gate
        # actually applied.
        helpers = _HELPERS.read_text(encoding="utf-8")
        for literal in ("0.75", "0.80", "1.5,", "0.10,"):
            self.assertNotIn(literal, helpers, literal)


class RecertProbeTests(unittest.TestCase):
    """The certification clock comes from the executor's own functions, read
    under ONE pinned mainline rev."""

    def _runner(self, mapping: dict[str, str]) -> object:
        def run(cmd: list[str]) -> str:
            key = " ".join(cmd)
            for pat, out in mapping.items():
                if pat in key:
                    return out
            raise RuntimeError(f"unexpected command: {key}")
        return run

    def _status(self, verdict: str = "WIN") -> str:
        return json.dumps({
            "schema_version": "csi800_recert_status_v1",
            "verdict": verdict,
            "verdict_sidecar_path": "docs/research/x.json",
            "verdict_sidecar_sha256": "ab" * 32,
            "evidence_anchor_commit": "c" * 40,
            "note": "fixture",
        })

    def test_body_and_date_come_from_the_same_pinned_rev(self) -> None:
        # origin/main is a moving ref: resolving it twice could date an old
        # WIN body with a newer commit and silently extend the clock.
        from web.operator_ui.recert_health import probe_recert_health
        seen: list[list[str]] = []

        def run(cmd: list[str]) -> str:
            seen.append(cmd)
            if "rev-parse" in cmd:
                return "d" * 40 + "\n"
            if "show" in cmd:
                return self._status()
            return "2026-08-05T11:19:22+08:00\n"

        health = probe_recert_health(
            now_iso="2026-08-14T12:00:00+08:00", run=run)
        self.assertTrue(health.known)
        self.assertEqual("WIN", health.verdict)
        self.assertEqual("d" * 40, health.pinned_rev)
        resolves = [c for c in seen if "rev-parse" in c]
        self.assertEqual(1, len(resolves), "主线只能解析一次")
        for cmd in seen[1:]:
            self.assertTrue(any("d" * 40 in part for part in cmd),
                            f"后续读取必须用被 pin 的 rev: {cmd}")

    def test_a_failed_probe_is_unknown_never_valid(self) -> None:
        from web.operator_ui.recert_health import probe_recert_health

        def boom(cmd: list[str]) -> str:
            raise RuntimeError("no git here")

        health = probe_recert_health(now_iso="2026-08-14T12:00:00+08:00",
                                     run=boom)
        self.assertFalse(health.known)
        self.assertIsNone(health.verdict)
        self.assertIsNone(health.rotation_allowed)
        self.assertFalse(health.is_frozen, "未知 ≠ 冻结,也 ≠ 有效")
        self.assertIn("git", health.reason)

    def test_lose_freezes_rotation(self) -> None:
        from web.operator_ui.recert_health import probe_recert_health

        def run(cmd: list[str]) -> str:
            if "rev-parse" in cmd:
                return "e" * 40
            if "show" in cmd:
                return json.dumps({
                    "schema_version": "csi800_recert_status_v1",
                    "verdict": "LOSE",
                    "evidence_anchor_commit": "c" * 40,
                    "note": "fixture",
                })
            return "2026-08-05T11:19:22+08:00"

        health = probe_recert_health(
            now_iso="2026-08-14T12:00:00+08:00", run=run)
        self.assertTrue(health.known)
        self.assertEqual("LOSE", health.verdict)
        self.assertTrue(health.is_frozen)

    def test_an_unparsable_status_is_not_second_guessed(self) -> None:
        # The executor's parser defines what a valid status artifact is; the
        # UI must not salvage a displayable verdict out of one it refused.
        from web.operator_ui.recert_health import probe_recert_health

        def run(cmd: list[str]) -> str:
            if "rev-parse" in cmd:
                return "f" * 40
            if "show" in cmd:
                return '{"schema_version": "wrong", "verdict": "WIN"}'
            return "2026-08-05T11:19:22+08:00"

        health = probe_recert_health(
            now_iso="2026-08-14T12:00:00+08:00", run=run)
        self.assertFalse(health.known)
        self.assertIsNone(health.verdict)

    def test_the_page_never_shows_a_previous_answer(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("无法判定", page)
        self.assertIn("不显示上一次的结果", page)
        self.assertIn("pinned_rev", page)


class RetrainWindowTests(unittest.TestCase):
    """Derived from the serving spacing pin — and labelled as derived,
    because the repository holds no retrain due date to read."""

    def _incumbent(self, newest: str) -> object:
        from web.operator_ui.incumbent import IncumbentIdentity
        return IncumbentIdentity(
            kind="ensemble", manifest_path="m.json", manifest_sha256="a" * 64,
            members=({"fit_start": "2024-04-01", "fit_end": newest},))

    def test_window_is_the_serving_pin_applied_to_the_newest_member(self) -> None:
        from src.inference.ensemble_serving import (
            MEMBER_SPACING_DAYS_MAX,
            MEMBER_SPACING_DAYS_MIN,
        )
        from web.operator_ui.pages._ops_cockpit_helpers import retrain_window
        w = retrain_window(  # type: ignore[arg-type]
            self._incumbent("2026-04-01"), date(2026, 8, 14))
        self.assertTrue(w.known)
        self.assertEqual(MEMBER_SPACING_DAYS_MIN, w.spacing_min)
        self.assertEqual(MEMBER_SPACING_DAYS_MAX, w.spacing_max)
        self.assertEqual("2026-06-15", w.opens_on)
        self.assertEqual("2026-07-10", w.closes_on)

    def test_a_closed_window_says_today_would_be_refused(self) -> None:
        # The operationally load-bearing statement: fitting to today's data
        # produces a manifest load_ensemble_manifest will not accept.
        from web.operator_ui.pages._ops_cockpit_helpers import retrain_window
        w = retrain_window(  # type: ignore[arg-type]
            self._incumbent("2026-04-01"), date(2026, 8, 14))
        self.assertEqual("closed", w.state)
        self.assertEqual(35, w.days_closed)
        self.assertEqual(135, w.gap_if_fit_today)
        self.assertTrue(w.refused_if_fit_today)

    def test_states_before_open_and_inside(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import retrain_window
        inc = self._incumbent("2026-04-01")
        for today, state, refused in (
            (date(2026, 5, 1), "before", True),
            (date(2026, 6, 15), "open", False),
            (date(2026, 7, 10), "open", False),
            (date(2026, 7, 11), "closed", True),
        ):
            with self.subTest(today=today):
                w = retrain_window(inc, today)  # type: ignore[arg-type]
                self.assertEqual(state, w.state)
                self.assertEqual(refused, w.refused_if_fit_today)

    def test_unresolvable_incumbent_yields_no_window(self) -> None:
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import retrain_window
        w = retrain_window(
            IncumbentIdentity(kind="unresolvable", error="boom"),
            date(2026, 8, 14))
        self.assertFalse(w.known)
        self.assertIsNone(w.opens_on)

    def test_the_page_labels_the_window_as_derived(self) -> None:
        # Presenting it as a repository fact would be the invention this
        # repo keeps catching.
        #
        # Anchored INSIDE section ④ and on phrases no other section uses:
        # bare "没有"/"推导" also occur in the gate and error copy nearby, so
        # a page-wide assertIn stays green with this whole disclosure
        # deleted (caught by mutation C5 on this very pin).
        page = _PAGE.read_text(encoding="utf-8")
        section = page[page.index('st.subheader("④'):page.index('st.subheader("⑤')]
        self.assertIn("机器可读锚", section)
        self.assertIn("每季度末", section)
        self.assertIn("**推导**出来的", section)
        self.assertIn("spacing_min", section)


class BundleFreshnessTests(unittest.TestCase):
    def test_threshold_is_read_from_the_serving_config(self) -> None:
        # Comparing the helper's answer to the config's CURRENT value is
        # satisfied by any literal that happens to equal it today — a copy
        # would pass and then drift silently the day the serving threshold
        # moves, promising headroom that no longer exists (mutation C6).
        # So move the config and check the helper follows.
        from unittest.mock import patch

        from src.inference.daily_recommend import RecommendationConfig
        from web.operator_ui.pages._ops_cockpit_helpers import (
            serving_bundle_max_age_days,
        )
        self.assertEqual(
            RecommendationConfig.bundle_max_age_days,
            serving_bundle_max_age_days())
        for moved in (7, 21):
            with self.subTest(threshold=moved):
                with patch.object(
                    RecommendationConfig, "bundle_max_age_days", moved,
                ):
                    self.assertEqual(moved, serving_bundle_max_age_days())

    def test_threshold_is_not_a_page_literal(self) -> None:
        for src in (_PAGE, _HELPERS):
            with self.subTest(file=src.name):
                text = src.read_text(encoding="utf-8")
                self.assertNotIn("14 天", text)
                self.assertNotIn("= 14", text)

    def test_headroom_and_refusal(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import bundle_freshness
        f = bundle_freshness(
            today=date(2026, 8, 14), tail_date="2026-08-03",
            provider_uri="X", max_age_days=14)
        self.assertTrue(f.known)
        self.assertEqual(11, f.days_behind)
        self.assertEqual(3, f.headroom_days)
        self.assertFalse(f.refuses_today)
        stale = bundle_freshness(
            today=date(2026, 8, 20), tail_date="2026-08-03",
            provider_uri="X", max_age_days=14)
        self.assertTrue(stale.refuses_today)

    def test_unreadable_tail_is_unknown_not_zero(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import bundle_freshness
        for bad in (None, "", "not-a-date"):
            with self.subTest(tail=bad):
                f = bundle_freshness(
                    today=date(2026, 8, 14), tail_date=bad,
                    provider_uri="X", max_age_days=14)
                self.assertFalse(f.known)
                self.assertIsNone(f.days_behind)
