"""Governance: the change's prose must name the path the code actually writes.

The status artifact's location drifted between docs and implementation three
times in this change alone — first `<provider>.parent/<FILENAME>` survived the
implementation switch (codex #434 r4), then the corrected spec said
`<provider_dir>.<name>.<FILENAME>`, which for `/data/foo` denotes
`/data/foo.foo.<FILENAME>` and is a THIRD path nobody writes, while `tasks.md`
still recorded the original shared one (r5/r6).

Prose is what a maintainer reads after archiving, so it is pinned like code:
every artifact of the change must state the derivation the writer performs, and
none may state a rejected one. The check is INSTANTIATED — a sample provider is
run through the documented template and through `default_status_path`, and the
two must agree — so a notation that merely looks right (`.<name>.`) fails.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data_pipeline.daily_update import (  # noqa: E402
    STATUS_FILENAME,
    default_status_path,
)

_CHANGE = (_PROJECT_ROOT / "openspec" / "changes"
           / "2026-08-14-daily-update-run-status")

# Spellings that named a location nothing writes. Kept as DATA so a fourth
# variant is added here rather than re-derived from memory.
_REJECTED = (
    "<provider_dir>.parent/daily_update_status.json",
    "<provider_dir 同级>/daily_update_status.json",
    "<provider_dir>.<name>.daily_update_status.json",
    'provider_dir.parent / "daily_update_status.json"',
    "provider_dir.parent / STATUS_FILENAME",
    # the CLI help's original shared-location spelling
    "<provider-dir sibling>/daily_update_status.json",
)

# The one template every artifact must use for the derived default.
_DOCUMENTED = "<provider_dir>.daily_update_status.json"


# Operator-facing prose OUTSIDE the change dir that states the same path.
# The CLI's --status-path help drifted independently of the specs (codex #434
# r7) — an operator reading --help would inspect the wrong file.
_EXTRA_PROSE = (
    _PROJECT_ROOT / "scripts" / "daily_update.py",
)


def _markdown() -> dict[Path, str]:
    docs = {p: p.read_text(encoding="utf-8")
            for p in sorted(_CHANGE.rglob("*.md"))}
    for p in _EXTRA_PROSE:
        docs[p] = p.read_text(encoding="utf-8")
    return docs


class StatusPathDocsMatchImplementationTests(unittest.TestCase):
    def test_no_artifact_names_a_rejected_location(self) -> None:
        for path, text in _markdown().items():
            for bad in _REJECTED:
                with self.subTest(doc=path.name, spelling=bad):
                    self.assertNotIn(
                        bad, text,
                        f"{path.name} 仍写着一个没人写入的位置:{bad!r}")

    def test_the_documented_template_instantiates_to_the_real_path(self) -> None:
        """Reading right is not enough — instantiate it.

        `<provider_dir>.<name>.…` looks plausible and is wrong; only running a
        provider through the template catches that.
        """
        for provider in (Path("/data/foo"),
                         Path("D:/qlib_data/my_cn_data_pit"),
                         Path("D:/qlib_data/my_cn_data_pit_2015")):
            with self.subTest(provider=str(provider)):
                resolved = provider.resolve()
                documented = Path(
                    _DOCUMENTED.replace("<provider_dir>", str(resolved)))
                self.assertEqual(default_status_path(provider), documented)

    def test_every_artifact_states_that_template(self) -> None:
        # Keyed by RELATIVE PATH, not `name`: both deltas are called
        # `spec.md`, so a name-keyed set silently merges them and the count
        # can never reach 4 (this test's own first cut did exactly that).
        stated = set()
        for p, text in _markdown().items():
            rel = (p.relative_to(_CHANGE).as_posix() if _CHANGE in p.parents
                   else p.name)
            if (_DOCUMENTED in text
                    or _DOCUMENTED.replace("<provider_dir>", "<provider-dir>")
                    in text
                    or re.search(r"with_name\(.*daily_update_status", text)):
                stated.add(rel)
        self.assertGreaterEqual(
            len(stated), 5,
            f"该 change 的散文里只有 {sorted(stated)} 提到了推导规则;"
            f"proposal / tasks / 两份 spec 都应说明它")

    def test_the_spec_records_the_tmp_staging_protection(self) -> None:
        # codex #434 r8: the r7 guard validates the final target AND its
        # `.tmp` staging sibling, but the spec kept prohibiting only the
        # final `--status-path` — archived, the invariant vanishes and a
        # future spec-compliant implementation may reintroduce the staging
        # clobber. The spec must keep stating both.
        spec = (_CHANGE / "specs" / "v2-daily-data-update"
                / "spec.md").read_text(encoding="utf-8")
        self.assertIn(".tmp", spec)
        self.assertIn("staging sibling", spec)
        self.assertIn("single-flight lock", spec)

    def test_the_ui_spec_records_the_stale_running_distinction(self) -> None:
        # codex #434 r9: the r8/r9 behaviour (fresh vs stale vs unverifiable,
        # negative age never fresh, unknown age never worded as "已超过")
        # must survive archiving, or a spec-compliant implementation may
        # again render every persisted running record as active.
        spec = (_CHANGE / "specs" / "v2-operator-ui"
                / "spec.md").read_text(encoding="utf-8")
        for required in ("SHALL NOT, by itself, be rendered",
                         "NEGATIVE age", "unverifiable",
                         "starting at zero", "no** age"):
            with self.subTest(clause=required):
                self.assertIn(required, spec)

    def test_the_ui_spec_forbids_coupling_not_naming(self) -> None:
        # codex #434 r11: the spec said the page source SHALL NOT *name*
        # `daily_update`/`bundle_swap`, but the shipped page names them in
        # prose three times and the governance scan checks IMPORT lines only
        # — the spec demanded something nothing enforces and nothing
        # satisfies. It must state the enforced constraint (import/invoke)
        # and must not drift back to the unenforceable one.
        spec = (_CHANGE / "specs" / "v2-operator-ui"
                / "spec.md").read_text(encoding="utf-8")
        self.assertIn("**import or invoke**", spec)
        self.assertNotIn("SHALL NOT name the orchestrator", spec)
        # …and the claim stays TRUE of the page: prose may name it, imports
        # must not.
        page = (_PROJECT_ROOT / "web" / "operator_ui" / "pages"
                / "data_inspect.py").read_text(encoding="utf-8")
        import_lines = [ln for ln in page.splitlines()
                        if re.match(r"\s*(import|from)\s", ln)]
        for name in ("daily_update", "bundle_swap"):
            for ln in import_lines:
                self.assertNotIn(name, ln)

    def test_the_filename_constant_is_not_restated_as_a_literal(self) -> None:
        # The docs may spell the filename (they are prose), but the CODE must
        # derive it — a second literal in the writer or reader is how the
        # collision arrived in the first place.
        for module in ("src/data_pipeline/daily_update.py",
                       "web/operator_ui/update_status.py"):
            text = (_PROJECT_ROOT / module).read_text(encoding="utf-8")
            with self.subTest(module=module):
                self.assertEqual(
                    1, text.count(f'"{STATUS_FILENAME}"'),
                    f"{module} 里 {STATUS_FILENAME!r} 应只出现在常量定义处")


if __name__ == "__main__":
    unittest.main()
