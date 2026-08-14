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
from datetime import date, timedelta
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
        execute = [c for c in rotation_commands("M", provider_uri="P", namechange_path="N") if "execute" in c.command]
        self.assertEqual(1, len(execute))
        self.assertTrue(execute[0].irreversible, "轮换执行改写生产 manifest")
        # ...and the read-only one is NOT flagged, so the flag keeps meaning
        # something.
        self.assertFalse(morning_command(
            IncumbentIdentity(kind="ensemble", manifest_path="m.json",
                              manifest_sha256="a" * 64),
            model_path="x.pkl", provider_uri="P",
            delisted_registry="R", name_source="N",
            bundle_max_age_days=14).irreversible)

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
            morning_command(self._ensemble(), model_path="/srv/m.pkl", provider_uri="/srv/bundle",
                            delisted_registry="/srv/reg.parquet",
                            name_source="/srv/ns.parquet", bundle_max_age_days=14),  # type: ignore[arg-type]
            morning_command(IncumbentIdentity(kind="single"),
                            model_path="/srv/m.pkl", provider_uri="/srv/bundle",
                            delisted_registry="/srv/reg.parquet",
                            name_source="/srv/ns.parquet", bundle_max_age_days=14),
            morning_command(IncumbentIdentity(kind="unresolvable", error="x"),
                            model_path="/srv/m.pkl", provider_uri="/srv/bundle",
                            delisted_registry="/srv/reg.parquet",
                            name_source="/srv/ns.parquet", bundle_max_age_days=14),
            data_update_command(provider_uri="/srv/bundle",
                                delisted_registry="/srv/reg.parquet"),
            *rotation_commands("/srv/prod_manifest.json",
                              provider_uri="/srv/bundle",
                              namechange_path="/srv/nc.parquet"),
        ]
        for cmd in commands:
            with self.subTest(cmd=cmd.title):
                self.assertNotIn("$QUANT", cmd.command,
                                 "未设时 shell 会展开成空串")

    def test_ensemble_deployment_gets_the_resolved_manifest(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import morning_command
        cmd = morning_command(self._ensemble(), model_path="/srv/m.pkl", provider_uri="/srv/bundle",
                            delisted_registry="/srv/reg.parquet",
                            name_source="/srv/ns.parquet", bundle_max_age_days=14)  # type: ignore[arg-type]
        self.assertIn("--ensemble-manifest /srv/prod_manifest.json", cmd.command)

    def test_single_model_deployment_gets_a_single_model_command(self) -> None:
        # The opt-out's pointer value is literally `none`; passing that to
        # --ensemble-manifest is rejected by the CLI.
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import morning_command
        cmd = morning_command(
            IncumbentIdentity(kind="single"), model_path="/srv/m.pkl", provider_uri="/srv/bundle",
                            delisted_registry="/srv/reg.parquet",
                            name_source="/srv/ns.parquet", bundle_max_age_days=14)
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
            model_path="/srv/m.pkl", provider_uri="/srv/bundle",
                            delisted_registry="/srv/reg.parquet",
                            name_source="/srv/ns.parquet", bundle_max_age_days=14)
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

    def test_commands_paste_into_the_operators_shell(self) -> None:
        # codex #431 r15: this repo's documented platform is Windows /
        # PowerShell, where a trailing `\` is NOT a line continuation —
        # verified empirically that PowerShell errors on the next line
        # ("Missing expression after unary operator '--'"). The whole value
        # of this page is commands that can be pasted, so they are rendered
        # single-line: correct in PowerShell, cmd AND a POSIX shell.
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import (
            data_update_command,
            morning_command,
            rotation_commands,
        )
        commands = [
            data_update_command(provider_uri="P", delisted_registry="R"),
            morning_command(
                IncumbentIdentity(kind="ensemble", manifest_path="/m.json",
                                  manifest_sha256="a" * 64),
                model_path="/x.pkl", provider_uri="/b",
                delisted_registry="/r", name_source="/n",
                bundle_max_age_days=14),
            *rotation_commands("/m.json", provider_uri="/b",
                               namechange_path="/n"),
        ]
        for cmd in commands:
            with self.subTest(cmd=cmd.title):
                self.assertNotIn("\\", cmd.command, "不得有 POSIX 续行符")
                self.assertNotIn("\n", cmd.command, "命令本体必须单行")

    def test_a_single_quote_in_a_path_is_refused_not_faked(self) -> None:
        # POSIX and PowerShell escape an embedded single quote differently
        # ('"'"' vs ''), so no one rendering is correct in both. Say so
        # rather than emit something silently wrong in the operator's shell.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            data_update_command,
        )
        cmd = data_update_command(
            provider_uri="D:/it's here/live", delisted_registry="R")
        self.assertIn("无法给出 PowerShell 与 POSIX 通用的写法", cmd.command)
        self.assertNotIn('"' + "'" + '"', cmd.command)

    def test_both_gate_commands_name_the_resolved_data_paths(self) -> None:
        # codex #431 r2 (P1): retrain_gate.py has hardcoded
        # --provider/--namechange defaults that BOTH scopes consume, and the
        # gate artifact records NEITHER path. Omit the flags on a deployment
        # that overrides the bundle and the gate PASSes on different data
        # than the cockpit is describing — then authorizes a production
        # rotation, with nothing downstream able to notice.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            rotation_commands,
        )
        gates = [c for c in rotation_commands(
            "M", provider_uri="/srv/bundle", namechange_path="/srv/nc.parquet")
            if "retrain_gate.py" in c.command]
        self.assertEqual(2, len(gates), "member 与 ensemble 两道 scope")
        for cmd in gates:
            with self.subTest(cmd=cmd.title):
                self.assertIn("--provider /srv/bundle", cmd.command)
                self.assertIn("--namechange /srv/nc.parquet", cmd.command)

    def test_the_page_feeds_gate_commands_the_bundle_it_reports_on(self) -> None:
        # One resolution, used by both section ④ and section ⑤: two calls
        # could name two different bundles on the same screen.
        page = _PAGE.read_text(encoding="utf-8")
        self.assertEqual(
            1, page.count("resolve_default_provider_uri()"),
            "provider 只应解析一次")
        self.assertIn("provider_uri=_provider,", page)
        self.assertIn("namechange_path=resolve_namechange_path()", page)

    def test_resolved_paths_survive_as_single_shell_arguments(self) -> None:
        # codex #431 r3: every path here comes from a filesystem or an env
        # override, so a space is legal (`/srv/qlib bundles/live`). Raw
        # interpolation splits it into two argv entries — the gate then runs
        # against a different bundle than the one shown — and a
        # metacharacter would execute as shell syntax.
        import shlex

        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import (
            data_update_command,
            morning_command,
            rotation_commands,
        )
        hostile_dir = "/srv/qlib bundles/live"
        hostile_file = "/srv/a b;touch pwned/x.json"
        cases = [
            (morning_command(  # type: ignore[arg-type]
                IncumbentIdentity(kind="ensemble", manifest_path=hostile_file,
                                  manifest_sha256="a" * 64),
                model_path="/srv/m.pkl", provider_uri="/srv/bundle",
                            delisted_registry="/srv/reg.parquet",
                            name_source="/srv/ns.parquet", bundle_max_age_days=14), "--ensemble-manifest", hostile_file),
            (morning_command(IncumbentIdentity(kind="single"),
                             model_path=hostile_file, provider_uri="/p",
                             delisted_registry="/r", name_source="/n",
                             bundle_max_age_days=14),
             "--model", hostile_file),
            (data_update_command(provider_uri=hostile_dir,
                                 delisted_registry=hostile_file),
             "--provider-dir", hostile_dir),
            (data_update_command(provider_uri=hostile_dir,
                                 delisted_registry=hostile_file),
             "--delisted-registry", hostile_file),
        ]
        gates = [c for c in rotation_commands(
            hostile_file, provider_uri=hostile_dir,
            namechange_path=hostile_file) if "retrain_gate.py" in c.command]
        for gate in gates:
            cases.append((gate, "--provider", hostile_dir))
            cases.append((gate, "--namechange", hostile_file))
        for cmd, flag, want in cases:
            with self.subTest(cmd=cmd.title, flag=flag):
                tokens = shlex.split(cmd.command)
                self.assertIn(flag, tokens)
                self.assertEqual(want, tokens[tokens.index(flag) + 1],
                                 "路径必须是单个 shell 参数")

    def test_the_morning_command_names_the_whole_deployment(self) -> None:
        # codex #431 r5 (P1): daily_recommend.py defines its OWN
        # --provider-uri/--delisted-registry/--name-source defaults from ITS
        # environment. Streamlit may hold a QUANT_PROVIDER_URI the operator's
        # terminal never inherits, so a command carrying only the model can
        # produce a LIVE list from a different bundle than sections ④/⑤ just
        # reported on.
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import morning_command
        for incumbent in (
            IncumbentIdentity(kind="ensemble", manifest_path="/srv/m.json",
                              manifest_sha256="a" * 64),
            IncumbentIdentity(kind="single"),
        ):
            with self.subTest(kind=incumbent.kind):
                cmd = morning_command(
                    incumbent, model_path="/srv/x.pkl",
                    provider_uri="/srv/bundle",
                    delisted_registry="/srv/reg.parquet",
                    name_source="/srv/ns.parquet", bundle_max_age_days=14)
                self.assertIn("--provider-uri /srv/bundle", cmd.command)
                self.assertIn("--delisted-registry /srv/reg.parquet", cmd.command)
                self.assertIn("--name-source /srv/ns.parquet", cmd.command)

    def test_the_morning_command_carries_the_predicted_threshold(self) -> None:
        # codex #431 r14: scripts/daily_recommend.py has its OWN argparse
        # default for --bundle-max-age-days (a literal 14), independent of
        # RecommendationConfig.bundle_max_age_days that section ⑤ reads. Omit
        # the flag and the page predicts a refusal against one number while
        # the pasted command applies another.
        from web.operator_ui.incumbent import IncumbentIdentity
        from web.operator_ui.pages._ops_cockpit_helpers import morning_command
        cmd = morning_command(
            IncumbentIdentity(kind="ensemble", manifest_path="/m.json",
                              manifest_sha256="a" * 64),
            model_path="/x.pkl", provider_uri="/b",
            delisted_registry="/r", name_source="/n",
            bundle_max_age_days=21)
        self.assertIn("--bundle-max-age-days 21", cmd.command)

    def test_the_page_feeds_the_command_the_threshold_it_predicts_with(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("bundle_max_age_days=serving_bundle_max_age_days()", page)

    def test_the_page_feeds_the_morning_command_the_same_bundle(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        block = page[page.index("_render_command(morning_command("):
                     page.index("st.subheader(\"② ")]
        self.assertIn("provider_uri=_provider,", block)
        self.assertIn("name_source=resolve_name_source()", block)

    def test_name_source_resolver_follows_the_env_var(self) -> None:
        import os
        from unittest.mock import patch

        from web.operator_ui.pages._ops_cockpit_helpers import (
            DEFAULT_NAME_SOURCE,
            ENV_NAME_SOURCE,
            resolve_name_source,
        )
        with patch.dict(os.environ, {ENV_NAME_SOURCE: ""}, clear=False):
            self.assertEqual(DEFAULT_NAME_SOURCE, resolve_name_source())
        with patch.dict(os.environ, {ENV_NAME_SOURCE: "/x/ns.parquet"},
                        clear=False):
            self.assertEqual("/x/ns.parquet", resolve_name_source())

    def test_namechange_resolver_follows_the_env_var(self) -> None:
        import os
        from unittest.mock import patch

        from web.operator_ui.pages._ops_cockpit_helpers import (
            DEFAULT_NAMECHANGE_PATH,
            ENV_NAMECHANGE_PATH,
            resolve_namechange_path,
        )
        with patch.dict(os.environ, {ENV_NAMECHANGE_PATH: ""}, clear=False):
            self.assertEqual(
                DEFAULT_NAMECHANGE_PATH, resolve_namechange_path())
        with patch.dict(os.environ, {ENV_NAMECHANGE_PATH: "/x/nc.parquet"},
                        clear=False):
            self.assertEqual("/x/nc.parquet", resolve_namechange_path())

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


class GateCardStatusTests(unittest.TestCase):
    """codex #431 r4: the page transcribed the verdict into the data and then
    contradicted it when picking a colour — an artifact whose own `overall`
    was FAIL rendered as a GREEN success, and with a tight metric as a yellow
    banner reading 通过. The status is now a pure function over the card."""

    def _card(self, **over: object) -> object:
        from web.operator_ui.pages._ops_cockpit_helpers import (
            GateCard,
            GateMetric,
            NamedGate,
        )
        base: dict[str, object] = {
            "key": "ensemble", "authorized_sha256": "a" * 64,
            "authorized_path": "p.json", "evidence_intact": True,
            "overall": "PASS",
            "gates": (NamedGate(name="degeneracy", verdict="PASS",
                                metrics=(GateMetric(name="w", value=0.1,
                                                    limit=1.0,
                                                    exclusive=False),)),),
            "missing_gates": (),
        }
        base.update(over)
        return GateCard(**base)  # type: ignore[arg-type]

    def _tight_gate(self) -> object:
        from web.operator_ui.pages._ops_cockpit_helpers import (
            GateMetric,
            NamedGate,
        )
        return NamedGate(
            name="serving_veto", verdict="PASS",
            metrics=(GateMetric(name="csi500_weight", value=0.7484,
                                limit=0.75, exclusive=False),))

    def test_every_status_is_reachable_and_correct(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import (
            GATE_STATUS_BROKEN,
            GATE_STATUS_FAILED,
            GATE_STATUS_MISSING,
            GATE_STATUS_OK,
            GATE_STATUS_TIGHT,
            GATE_STATUSES,
            NamedGate,
            gate_card_status,
        )
        cases = {
            GATE_STATUS_BROKEN: self._card(evidence_intact=False,
                                           overall="PASS"),
            GATE_STATUS_MISSING: self._card(missing_gates=("ic_direction",)),
            GATE_STATUS_FAILED: self._card(overall="FAIL"),
            GATE_STATUS_TIGHT: self._card(gates=(self._tight_gate(),)),
            GATE_STATUS_OK: self._card(),
        }
        self.assertEqual(set(GATE_STATUSES), set(cases), "五态必须都被覆盖")
        for want, card in cases.items():
            with self.subTest(status=want):
                self.assertEqual(want, gate_card_status(card))  # type: ignore[arg-type]
        # ...and a named gate that did not pass fails the card even when the
        # summary claims otherwise.
        self.assertEqual(GATE_STATUS_FAILED, gate_card_status(  # type: ignore[arg-type]
            self._card(gates=(NamedGate(name="x", verdict="FAIL"),))))

    def test_a_failing_verdict_is_never_shown_as_passing(self) -> None:
        # The exact defect: FAIL + a tight metric previously rendered yellow
        # with the word 通过 in it.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            GATE_STATUS_FAILED,
            gate_card_status,
        )
        for overall in ("FAIL", None, "", "pass", "UNKNOWN"):
            with self.subTest(overall=overall):
                self.assertEqual(GATE_STATUS_FAILED, gate_card_status(
                    self._card(overall=overall,  # type: ignore[arg-type]
                               gates=(self._tight_gate(),))))

    def test_pass_is_the_gate_libs_own_constant(self) -> None:
        # Not a restated "PASS" literal that could drift from the producer.
        from scripts.retrain_gate_lib import PASS
        from web.operator_ui.pages._ops_cockpit_helpers import (
            GATE_STATUS_OK,
            gate_card_status,
        )
        self.assertEqual(GATE_STATUS_OK,
                         gate_card_status(self._card(overall=PASS)))  # type: ignore[arg-type]

    def test_the_page_shows_green_only_for_the_passing_status(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        block = page[page.index("_status = gate_card_status(_card)"):
                     page.index("with st.expander(")]
        self.assertIn("elif _status == GATE_STATUS_OK:\n            st.success(",
                      block)
        self.assertEqual(1, block.count("st.success("), "只有一处绿色")
        self.assertIn("GATE_STATUS_FAILED", block)
        self.assertIn("未通过", block)
        # The old order-based branching must be gone.
        self.assertNotIn("if _card.missing_gates:", block)


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

    def test_the_default_clock_is_the_executors_utc_one(self) -> None:
        # codex #431 r2: recert_validity compares now.date() WITHOUT
        # normalizing zones, and the executor passes UTC. A +08:00 instant
        # makes the page disagree with the machine for eight hours around
        # the expiry boundary — reporting rotation frozen while the executor
        # would still permit it.
        from datetime import datetime, timezone

        from web.operator_ui.recert_health import executor_now_iso
        stamp = datetime.fromisoformat(executor_now_iso())
        self.assertIsNotNone(stamp.tzinfo)
        self.assertEqual(timezone.utc, stamp.tzinfo)

    def test_the_page_passes_no_clock_of_its_own(self) -> None:
        # Every caller getting the clock right is weaker than there being
        # nothing to get wrong.
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("probe_recert_health()", page)
        self.assertNotIn("cn_now_iso", page)

    def test_the_probe_defaults_to_the_executor_clock(self) -> None:
        from unittest.mock import patch

        from web.operator_ui import recert_health
        with patch.object(recert_health, "executor_now_iso",
                          return_value="2026-08-14T04:00:00+00:00") as fake:
            recert_health.probe_recert_health(run=lambda cmd: "")
        fake.assert_called_once_with()

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

    def test_the_clock_matches_the_recommenders_not_the_operators(self) -> None:
        # codex #431 r3: daily_recommend.py judges staleness against
        # host-local date.today(); a CN-local clock puts the cockpit a day
        # ahead on a UTC host between CN midnight and 08:00, so at exactly
        # the 14-day boundary it predicts a refusal that will not happen.
        #
        # Asserting `recommender_today() == date.today()` is NOT enough: this
        # dev box runs at +08:00, where the CN clock and the host clock agree,
        # so that comparison passes with the bug reintroduced (caught by
        # mutation C20 on this very pin). Assert the CALL instead.
        from unittest.mock import patch

        from web.operator_ui.pages import _ops_cockpit_helpers as h
        with patch.object(h, "date") as fake_date:
            fake_date.today.return_value = date(2026, 1, 1)
            got = h.recommender_today()
        fake_date.today.assert_called_once_with()
        self.assertEqual(date(2026, 1, 1), got)

    def test_the_helpers_never_reach_for_the_cn_display_clock(self) -> None:
        # A second, independent guard on the same rule: the module that
        # reproduces machine decisions must not IMPORT the operator-facing
        # date-bucketing clock. (Naming it in prose is fine — the docstring
        # explains why it is the wrong clock here.)
        import ast
        tree = ast.parse(_HELPERS.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("cn_today", imported)
        self.assertNotIn("cn_now_iso", imported)

    def test_the_page_passes_no_freshness_clock_of_its_own(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        section = page[page.index('st.subheader("⑤'):]
        self.assertNotIn("cn_today()", section)

    def test_freshness_defaults_to_the_recommender_clock(self) -> None:
        from unittest.mock import patch

        from web.operator_ui.pages import _ops_cockpit_helpers as h
        with patch.object(h, "recommender_today",
                          return_value=date(2026, 8, 14)) as fake:
            fresh = h.bundle_freshness(
                tail_date="2026-08-03", provider_uri="X", max_age_days=14)
        fake.assert_called_once_with()
        self.assertEqual(11, fresh.days_behind)

    def test_the_boundary_day_is_the_recommenders_boundary(self) -> None:
        # The whole point of matching clocks: at exactly max_age_days the
        # recommender still accepts, and one day later it refuses. A page
        # off by one day flips a live go/no-go.
        from web.operator_ui.pages._ops_cockpit_helpers import bundle_freshness
        exactly = bundle_freshness(today=date(2026, 8, 17),
                                   tail_date="2026-08-03",
                                   provider_uri="X", max_age_days=14)
        self.assertEqual(14, exactly.days_behind)
        self.assertFalse(exactly.refuses_today, "恰好等于阈值不拒绝")
        past = bundle_freshness(today=date(2026, 8, 18), tail_date="2026-08-03",
                                provider_uri="X", max_age_days=14)
        self.assertEqual(15, past.days_behind)
        self.assertTrue(past.refuses_today)

    def test_the_page_agrees_with_the_recommenders_own_predicate(self) -> None:
        # Not a restatement of my own arithmetic: drive the SERVING module's
        # staleness predicate with the same inputs and require the same
        # answer on both sides of the boundary.
        from src.inference.daily_recommend import _bundle_is_stale
        from web.operator_ui.pages._ops_cockpit_helpers import bundle_freshness
        tail = date(2026, 8, 3)
        for offset in range(12, 18):
            today = tail + timedelta(days=offset)
            with self.subTest(days_behind=offset):
                mine = bundle_freshness(
                    today=today, tail_date=tail.isoformat(),
                    provider_uri="X", max_age_days=14)
                self.assertEqual(
                    _bundle_is_stale(tail, today, 14), mine.refuses_today)

    def _calendar(self, body: str | None) -> str:
        import tempfile
        root = Path(tempfile.mkdtemp())
        if body is not None:
            (root / "calendars").mkdir()
            (root / "calendars" / "day.txt").write_text(body, encoding="utf-8")
        return str(root)

    def test_the_tail_comes_off_the_recommenders_calendar_file(self) -> None:
        # codex #431 r5: summarise_bundle_health PREFERS the _fetch_integrity
        # identity tail; the recommender's calendar is built from
        # calendars/day.txt. After a partial bundle replacement they diverge,
        # and at the age boundary that flips accept/refuse.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            bundle_calendar_tail,
        )
        got = bundle_calendar_tail(
            self._calendar("2026-07-30\n2026-07-31\n2026-08-03\n"))
        self.assertTrue(got.known)
        self.assertEqual(date(2026, 8, 3), got.tail)
        self.assertFalse(bundle_calendar_tail(self._calendar(None)).known)

    def test_ambiguous_calendar_bytes_yield_unknown_not_a_guess(self) -> None:
        # codex #431 r7: the recommender reads this file through QLIB's
        # loader, which a read-only page cannot invoke. training_guards'
        # reader silently DROPS malformed rows and sorts/dedupes, so a
        # corrupt calendar still produces a confident (possibly wrong) tail.
        # This reader is deliberately SOUND rather than exact: it may say
        # "unknown" where qlib is fine; it must never do the reverse.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            bundle_calendar_tail,
        )
        for name, body in (
            ("malformed row", "2026-07-30\nnot-a-date\n2026-08-03\n"),
            ("blank row in the middle", "2026-07-30\n\n2026-08-03\n"),
            ("duplicate", "2026-07-30\n2026-08-03\n2026-08-03\n"),
            ("out of order", "2026-08-03\n2026-07-30\n"),
            ("empty file", "\n\n"),
            # codex #431 r8: date.fromisoformat swallows these (both parse to
            # 2026-08-03), but the producer writes canonical YYYY-MM-DD and
            # qlib's calendar parser need not accept them. Answering
            # known=True here would break the sound-not-exact guarantee this
            # reader makes — a confident tail for bytes D.calendar() rejects.
            ("ISO week spelling", "2026-07-30\n2026-W32-1\n"),
            ("compact spelling", "2026-07-30\n20260803\n"),
            ("trailing junk on a row", "2026-07-30\n2026-08-03x\n"),
            # Shape-valid but not a real date — the branch behind the shape
            # check, reachable only through rows like this.
            ("impossible date", "2026-07-30\n2026-13-45\n"),
            ("impossible day-of-month", "2026-07-30\n2026-02-30\n"),
        ):
            with self.subTest(case=name):
                got = bundle_calendar_tail(self._calendar(body))
                self.assertFalse(got.known, "有歧义就必须说不知道")
                self.assertIsNone(got.tail)
                self.assertTrue(got.reason)

    def _calendar_bytes(self, body: bytes) -> str:
        # Exact bytes — write_text() would translate \n to os.linesep on
        # Windows and silently change the fixture out from under the test.
        import tempfile
        root = Path(tempfile.mkdtemp())
        (root / "calendars").mkdir()
        (root / "calendars" / "day.txt").write_bytes(body)
        return str(root)

    def test_whitespace_padded_rows_are_refused(self) -> None:
        # codex #431 r9: stripping BEFORE the shape check certifies bytes
        # nobody validated — qlib's acceptance of a padded row is not
        # established, so answering known=True for one breaks the
        # sound-not-exact contract.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            bundle_calendar_tail,
        )
        for name, body in (
            ("leading space", b"2026-07-30\n 2026-08-03\n"),
            ("trailing space", b"2026-07-30\n2026-08-03 \n"),
            ("trailing tab", b"2026-07-30\n2026-08-03\t\n"),
        ):
            with self.subTest(case=name):
                got = bundle_calendar_tail(self._calendar_bytes(body))
                self.assertFalse(got.known, "首尾空白也不是规范写法")

    def test_only_lf_and_crlf_terminate_a_calendar_row(self) -> None:
        # codex #431 r11: str.splitlines() ALSO breaks on VT, FF, NEL, LS and
        # PS, and folds a lone CR — so bytes the producer never writes would
        # be read as a well-formed calendar here while qlib need not agree.
        # The contract is now closed: LF or CRLF, nothing else.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            bundle_calendar_tail,
        )
        for name, body in (
            ("LS", "2026-08-01 2026-08-03".encode()),
            ("PS", "2026-08-01 2026-08-03".encode()),
            ("VT", b"2026-08-01\x0b2026-08-03"),
            ("FF", b"2026-08-01\x0c2026-08-03"),
            ("NEL", "2026-08-01\x852026-08-03".encode()),
            ("CR", b"2026-08-01\r2026-08-03"),
        ):
            with self.subTest(separator=name):
                got = bundle_calendar_tail(self._calendar_bytes(body))
                self.assertFalse(got.known, f"{name} 不是本契约支持的行终止符")
                # ...and say WHICH separator. The canonical shape check would
                # refuse these rows anyway, so the explicit scan exists for
                # the DIAGNOSIS: "含 LS 分隔符" points the operator at the
                # producer, "第 1 行不是规范写法" points them at the date.
                self.assertIn(name.split("(")[0], got.reason)
                self.assertIn("只支持 LF / CRLF", got.reason)
        # ...while the two supported terminators still work, with or without
        # a final newline.
        for name, body in (
            ("LF", b"2026-08-01\n2026-08-03\n"),
            ("CRLF", b"2026-08-01\r\n2026-08-03\r\n"),
            ("no trailing newline", b"2026-08-01\n2026-08-03"),
        ):
            with self.subTest(supported=name):
                got = bundle_calendar_tail(self._calendar_bytes(body))
                self.assertTrue(got.known, name)
                self.assertEqual(date(2026, 8, 3), got.tail)

    def test_more_than_one_trailing_newline_is_ambiguous(self) -> None:
        # Exactly ONE final terminator is the producer's shape; extra blank
        # rows are content this page will not interpret.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            bundle_calendar_tail,
        )
        got = bundle_calendar_tail(self._calendar_bytes(b"2026-08-01\n\n"))
        self.assertFalse(got.known)

    def test_undecodable_calendar_bytes_are_unknown_not_a_traceback(self) -> None:
        # codex #431 r10: UnicodeDecodeError is a ValueError, NOT an
        # OSError. Corrupt or partially-copied bytes would escape the read
        # guard and take the whole Streamlit page down with a traceback,
        # instead of the 无法判定 state this function promises.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            bundle_calendar_tail,
        )
        got = bundle_calendar_tail(
            self._calendar_bytes(b"2026-07-30\n\xff\xfe not utf-8\n"))
        self.assertFalse(got.known)
        self.assertIn("UnicodeDecodeError", got.reason)

    def test_a_crlf_calendar_is_still_accepted(self) -> None:
        # Guards against over-strictness in the other direction: the REAL
        # production bundle writes CRLF, and Python's universal-newline read
        # normalizes the terminator itself (not the row content). Refusing
        # those would leave section ⑤ permanently 无法判定 on this machine.
        from web.operator_ui.pages._ops_cockpit_helpers import (
            bundle_calendar_tail,
        )
        got = bundle_calendar_tail(
            self._calendar_bytes(b"2026-07-30\r\n2026-08-03\r\n"))
        self.assertTrue(got.known, "CRLF 是生产 bundle 的真实格式")
        self.assertEqual(date(2026, 8, 3), got.tail)

    def test_non_canonical_spellings_parse_but_are_still_refused(self) -> None:
        # Guards the REASON the shape check exists: these strings are valid
        # inputs to date.fromisoformat, so a parse-only reader accepts them.
        import sys
        from datetime import date as _date

        from web.operator_ui.pages._ops_cockpit_helpers import (
            bundle_calendar_tail,
        )
        for spelling in ("2026-W32-1", "20260803"):
            with self.subTest(spelling=spelling):
                if sys.version_info >= (3, 11):
                    # The PREMISE only holds from 3.11, where
                    # date.fromisoformat gained "most ISO 8601 formats";
                    # on 3.10 it raises. CI runs 3.10/3.11/3.12, so
                    # asserting it unconditionally fails the 3.10 leg —
                    # and the premise is not what this test is for.
                    self.assertEqual(_date(2026, 8, 3),
                                     _date.fromisoformat(spelling),
                                     "前提:这些拼写在 3.11+ 确实能被解析")
                # The BEHAVIOUR holds on every version: refused as
                # non-canonical, whatever the parser would have done.
                got = bundle_calendar_tail(
                    self._calendar(f"2026-07-30\n{spelling}\n"))
                self.assertFalse(got.known)
                self.assertIn("规范", got.reason)

    def test_the_page_does_not_claim_to_be_the_recommenders_parser(self) -> None:
        # The wording matters: same FILE, different PARSER.
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("解析器不是 qlib 自己的", page)
        self.assertNotIn("与出单侧的 `calendar[-1]` 同源", page)

    def test_an_unknown_tail_reports_the_reason_not_a_green(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import bundle_freshness
        fresh = bundle_freshness(
            today=date(2026, 8, 14), tail_date=None, provider_uri="X",
            message="交易日历第 2 行不是合法日期", max_age_days=14)
        self.assertFalse(fresh.known)
        self.assertFalse(fresh.usable)
        self.assertIn("不是合法日期", fresh.message)

    def test_the_page_feeds_freshness_the_calendar_tail(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        block = page[page.index("_fresh = bundle_freshness("):]
        self.assertIn("tail_date=_cal_tail.tail.isoformat()", block)
        self.assertNotIn("tail_date=_summary.tail_date", block)

    def test_integrity_gate_matches_the_recommenders_three_rules(self) -> None:
        # codex #431 r6: summarise_bundle_health SWALLOWS a bad stamp
        # (training_guards: "the UI banner must not crash on a bad stamp") and
        # falls back to validation.json/manifest.json, so a bundle whose
        # _fetch_integrity.json is MISSING or CORRUPT can come back with no
        # warnings at all — while _assert_bundle_fetch_complete refuses both.
        # Drive the recommender's own reader with synthetic stamps.
        import tempfile

        from src.data.pit.bundle_integrity import INTEGRITY_FILENAME
        from web.operator_ui.pages._ops_cockpit_helpers import (
            recommender_integrity_check,
        )

        def stamp(body: str | None) -> Path:
            root = Path(tempfile.mkdtemp())
            if body is not None:
                (root / INTEGRITY_FILENAME).write_text(body, encoding="utf-8")
            return root

        clean = json.dumps({
            "schema_version": 1, "built_from_holey_fetch": False,
            "built_at": "2026-08-03T00:00:00+00:00", "holes": []})
        holey = json.dumps({
            "schema_version": 1, "built_from_holey_fetch": True,
            "built_at": "2026-08-03T00:00:00+00:00", "holes": []})

        # 1. corrupt → refused REGARDLESS of the override
        for override in (False, True):
            with self.subTest(case="corrupt", allow_holey=override):
                got = recommender_integrity_check(
                    str(stamp("{ not json")), allow_holey=override)
                self.assertTrue(got.known)
                self.assertFalse(got.accepted, "损坏 stamp 无条件拒绝")
        # 2. missing → refused unless the operator overrides
        with self.subTest(case="missing"):
            self.assertFalse(recommender_integrity_check(str(stamp(None))).accepted)
            self.assertTrue(recommender_integrity_check(
                str(stamp(None)), allow_holey=True).accepted)
        # 3. holey → same
        with self.subTest(case="holey"):
            self.assertFalse(recommender_integrity_check(str(stamp(holey))).accepted)
            self.assertTrue(recommender_integrity_check(
                str(stamp(holey)), allow_holey=True).accepted)
        # 4. clean → accepted
        with self.subTest(case="clean"):
            self.assertTrue(recommender_integrity_check(str(stamp(clean))).accepted)

    def test_a_refused_stamp_is_never_usable_however_fresh(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import bundle_freshness
        for accepted in (False, None):
            with self.subTest(integrity_accepted=accepted):
                fresh = bundle_freshness(
                    today=date(2026, 8, 14), tail_date="2026-08-13",
                    provider_uri="X", max_age_days=14,
                    integrity_accepted=accepted,
                    integrity_reason="stamp 问题")
                self.assertTrue(fresh.age_ok, "年龄仍然是过的")
                self.assertFalse(fresh.usable)

    def test_the_page_evaluates_integrity_with_the_recommenders_reader(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        self.assertIn("_integrity = recommender_integrity_check(_provider)", page)
        self.assertIn("integrity_accepted=_integrity.accepted", page)

    def test_a_health_warning_is_never_rendered_as_usable(self) -> None:
        # codex #431 r5: the recommender runs further preconditions AFTER the
        # age guard — a fresh-dated bundle stamped built_from_holey_fetch is
        # refused by _assert_bundle_fetch_complete. Age alone must not be
        # shown as usable.
        from web.operator_ui.pages._ops_cockpit_helpers import bundle_freshness
        fresh = bundle_freshness(
            today=date(2026, 8, 14), tail_date="2026-08-13",
            provider_uri="X", max_age_days=14, integrity_accepted=True)
        self.assertTrue(fresh.age_ok)
        self.assertTrue(fresh.usable)
        for status, warns in (("warning", ()), ("error", ()),
                              ("ok", ("built_from_holey_fetch",))):
            with self.subTest(status=status, warnings=warns):
                flagged = bundle_freshness(
                    today=date(2026, 8, 14), tail_date="2026-08-13",
                    provider_uri="X", max_age_days=14, integrity_accepted=True,
                    health_status=status, health_warnings=warns)
                self.assertTrue(flagged.age_ok, "年龄仍然是过的")
                self.assertFalse(flagged.usable, "但整体不可判为可用")

    def test_the_page_gates_green_on_bundle_health(self) -> None:
        page = _PAGE.read_text(encoding="utf-8")
        block = page[page.index("if _fresh.refuses_today:"):
                     page.index("st.caption(\n    f\"provider = ")]
        self.assertIn("elif not _fresh.usable:", block)
        self.assertEqual(1, block.count("st.success("), "只有一处绿色")
        i_health = block.index("elif not _fresh.usable:")
        i_green = block.index("st.success(")
        self.assertLess(i_health, i_green, "健康检查必须排在上绿之前")

    def test_unreadable_tail_is_unknown_not_zero(self) -> None:
        from web.operator_ui.pages._ops_cockpit_helpers import bundle_freshness
        for bad in (None, "", "not-a-date"):
            with self.subTest(tail=bad):
                f = bundle_freshness(
                    today=date(2026, 8, 14), tail_date=bad,
                    provider_uri="X", max_age_days=14)
                self.assertFalse(f.known)
                self.assertIsNone(f.days_behind)


class SpecSelfConsistencyTests(unittest.TestCase):
    """codex #431 r12: the requirement body still mandated
    ``summarise_bundle_health()`` as the tail source while a scenario below
    it forbade exactly that. A spec carrying both cannot be satisfied — and
    worse, it lets a future implementation revert the corrected behaviour
    while claiming compliance. Archived, that becomes governance history
    contradicting itself.

    A machine guard for a rule that was already mine to apply by hand every
    round (re-read the spec after editing it) and that I missed."""

    def _spec(self) -> str:
        return (_ROOT / "openspec" / "changes" / "2026-08-14-ui-ops-cockpit"
                / "specs" / "v2-ops-cockpit-page"
                / "spec.md").read_text(encoding="utf-8")

    @staticmethod
    def _clauses(text: str) -> list[str]:
        """Logical clauses, the way Markdown actually groups them.

        Three earlier shapes of this guard each missed a different way of
        writing the same contradictory mandate (all found by mutation on
        this pin):

        * per PARAGRAPH — satisfied by a ``MUST NOT`` elsewhere in it;
        * per PUNCTUATION over the whole file — headings and bullets carry
          no 。/，, so one "clause" ran across dozens of lines;
        * per LINE — an ordinary Markdown reflow splits
          ``尾部日期 MUST 使用`` from ``summarise_bundle_health()`` and
          neither line holds both terms (codex #431 r13).

        So: blank lines separate blocks, a new bullet or heading starts a
        new statement, wrapped continuation lines are rejoined, and only
        then is the statement split on Chinese sentence punctuation.
        """
        import re
        out: list[str] = []
        for block in re.split(r"\n\s*\n", text):
            # Every Markdown list marker starts a new statement: `-`, `*`,
            # `+`, ordered `1.`/`1)`, and headings. Recognising only `-*#`
            # let a positive mandate merge with an unrelated MUST NOT item
            # once the spec was reformatted as an ordered list (codex r14).
            for statement in re.split(
                    r"\n(?=\s*(?:[-*+#]|\d+[.)]))", block):
                joined = " ".join(
                    line.strip() for line in statement.split("\n"))
                out.extend(re.split(r"[。；，]", joined))
        return out

    def test_the_other_tail_source_only_ever_appears_as_a_prohibition(self) -> None:
        # Encode the contradiction risk itself rather than "every paragraph
        # must name the file": the byte-CONTRACT scenario legitimately talks
        # about the tail without naming its source. What must never happen
        # is `summarise_bundle_health` appearing as a POSITIVE mandate for
        # the tail, which is exactly the stale MUST codex found.
        #
        offenders = [
            clause.strip()[:80]
            for clause in self._clauses(self._spec())
            if "尾部日期" in clause
            and "summarise_bundle_health" in clause
            and "MUST NOT" not in clause
        ]
        self.assertEqual([], offenders,
                         "identity tail 只能以禁止形式出现在尾部来源的规定里")

    def test_the_clause_parser_survives_every_way_of_writing_it(self) -> None:
        # The guard is only as good as its notion of "one statement". Drive
        # the parser directly with each shape that fooled an earlier version
        # — same line, reflowed across lines, and inside a bullet — so a
        # future rewrite of the spec cannot smuggle the mandate back in
        # through formatting alone.
        offending_shapes = (
            "尾部日期 MUST 使用 `summarise_bundle_health()`。",
            "尾部日期 MUST 使用\n`summarise_bundle_health()`。",
            "- **THEN** 尾部日期 MUST 使用\n  `summarise_bundle_health()`。",
            # Valid list syntax the first boundary regex did not recognise —
            # the mandate merged with the next item's unrelated MUST NOT and
            # the guard fell silent (codex #431 r14).
            "1. 尾部日期 MUST 使用 `summarise_bundle_health()`\n"
            "2. 其它路径 MUST NOT 使用",
            "+ 尾部日期 MUST 使用 `summarise_bundle_health()`\n"
            "+ 其它路径 MUST NOT 使用",
            "1) 尾部日期 MUST 使用 `summarise_bundle_health()`\n"
            "2) 其它路径 MUST NOT 使用",
            "页面 MUST 用 `summarise_bundle_health()` 取 bundle 尾部日期，\n"
            "MUST NOT 新造第二个阈值。",
        )
        for shape in offending_shapes:
            with self.subTest(shape=shape.replace("\n", "\\n")[:50]):
                bad = [
                    c for c in self._clauses(shape)
                    if "尾部日期" in c and "summarise_bundle_health" in c
                    and "MUST NOT" not in c
                ]
                self.assertTrue(bad, "这种写法必须被判为违规")
        # ...and the legitimate prohibition is NOT flagged.
        allowed = "尾部日期 MUST 读自 `calendars/day.txt`，MUST NOT 取自 " \
                  "`summarise_bundle_health()` 偏好的 identity tail。"
        self.assertEqual(
            [], [c for c in self._clauses(allowed)
                 if "尾部日期" in c and "summarise_bundle_health" in c
                 and "MUST NOT" not in c])

    def test_the_calendar_file_is_named_as_the_tail_source(self) -> None:
        # ...and the positive side is stated somewhere, so the prohibition
        # above is not vacuously satisfied by saying nothing at all.
        spec = self._spec()
        mandates = [
            para for para in spec.split("\n\n")
            if "尾部日期" in para and "calendars/day.txt" in para
        ]
        self.assertTrue(mandates, "必须有一处正面指明尾部来源是 calendars/day.txt")

    def test_the_health_summary_is_never_promoted_to_a_gate(self) -> None:
        spec = self._spec()
        self.assertIn("只能收回", spec)
        self.assertIn("不能授予", spec)
        # ...and the implementation agrees: `usable` consults the
        # recommender's own gate, not the summary alone.
        self.assertIn("self.integrity_accepted is True",
                      _HELPERS.read_text(encoding="utf-8"))
