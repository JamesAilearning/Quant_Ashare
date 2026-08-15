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
