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
    # the pre-r19 fixed staging claim: writes stage at a UNIQUE per-write
    # name now, and a spec re-asserting the fixed `.tmp` would permit a
    # future implementation to reintroduce the shared-staging race
    "staged through a `<target>.tmp`",
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
        # r20: the spec must describe the UNIQUE per-write staging — keyword
        # presence alone could not tell the fixed-name claim from the fix
        # (this pin's own reverse validation caught that).
        self.assertIn("UNIQUE" + chr(10) + "per-write sibling", spec)

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
        # The INVOKE half (codex #434 r12): an import-line scan alone lets
        # `import subprocess` + `subprocess.run([... "scripts/daily_update.py"])`
        # through. The page needs no process-spawning primitive at all (its
        # PIT-validation subprocess lives in pit_validation_runner), so ban
        # the mechanism, not the target string — prose like "in a SUBPROCESS."
        # stays legal because the patterns require a call shape.
        # Alias escape (found by this test's own reverse validation, not by
        # review): `import subprocess as _sp` + `_sp.run(...)` matches none
        # of the attribute patterns. The IMPORT of a spawning module is what
        # aliasing cannot hide, so ban that on import lines and keep the
        # call-shape patterns for the os.* primitives (importing os is
        # legitimate).
        # Parsed with AST, not substring-matched: `import pathlib,
        # subprocess as _sp` contains neither the substring
        # `import subprocess` nor any call-shape pattern, so the previous
        # string scan stayed green while the page could invoke the
        # orchestrator (codex #434 r15). AST sees the module NAME
        # regardless of grouping, aliasing, or line layout. `importlib`
        # and the `__import__` builtin are banned alongside — the
        # remaining spellings of "get me a module without writing its
        # import". `os` stays on the list because this page is
        # pathlib-only; if it ever needs os, the exemption must be argued
        # here, not assumed.
        import ast
        banned = {"subprocess", "runpy", "multiprocessing", "os",
                  "asyncio", "concurrent", "pty",
                  "posix", "nt", "_winapi", "_posixsubprocess",
                  "importlib", "builtins",
                  # ctypes reaches libc's system() without any of the above
                  # (`CDLL(None).system(b"...")` on POSIX) — codex #434 r17.
                  "ctypes"}
        # THREAT MODEL, stated so the list stops growing forever: this guard
        # exists to catch ACCIDENTAL drift — someone wiring a convenient
        # spawn into a read-only page. Deliberate evasion (eval/exec over
        # assembled strings, getattr chains over allowed modules) is not
        # spelled like an accident, cannot be enumerated away, and is the
        # reviewer's job, not this test's.
        for node in ast.walk(ast.parse(page)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # BOTH halves: the module AND the imported names. `from
                # builtins import __import__ as load` carries an allowed
                # module while the alias hides every call-shape pattern —
                # load("subprocess") then invokes at will (codex #434 r16).
                names = [node.module or ""]
                for alias in node.names:
                    with self.subTest(imported_name=alias.name):
                        self.assertNotIn(
                            alias.name, {"__import__", "import_module"},
                            f"检视页导入了动态取模块的名字 {alias.name!r}")
            else:
                continue
            for name in names:
                with self.subTest(imported=name):
                    self.assertNotIn(
                        name.split(".")[0], banned,
                        f"检视页 import 了可派生进程的模块 {name!r}")
        for pattern, label in (
            (r"subprocess\.\w", "subprocess.<attr>"),
            (r"\bPopen\b", "Popen"),
            (r"os\.system\s*\(", "os.system("),
            (r"os\.exec\w*\s*\(", "os.exec*("),
            (r"os\.spawn\w*\s*\(", "os.spawn*("),
            (r"\brunpy\b", "runpy"),
            (r"__import__\s*\(", "__import__("),
        ):
            with self.subTest(primitive=label):
                self.assertIsNone(
                    re.search(pattern, page),
                    f"检视页出现进程派生原语 {label} —— 只读页面无法证明它"
                    f"没在调用编排器")
        # proposal/tasks must not resurrect the unenforceable naming claim —
        # r11 fixed only the delta spec and both still carried it (r12).
        for doc in ("proposal.md", "tasks.md"):
            text = (_CHANGE / doc).read_text(encoding="utf-8")
            with self.subTest(doc=doc):
                self.assertNotIn("不出现 `daily_update`", text)
                self.assertNotIn("字样", text.split("Non-goals")[0]
                                 if doc == "proposal.md" else text)

    def test_the_pages_import_closure_cannot_reach_the_orchestrator(self) -> None:
        """The helper door (codex #434 r18).

        `from web.operator_ui.update_runner import run_update` is an allowed
        import and a plain call — the single-page scan sees nothing, while
        the helper spawns the updater. Helper-mediated subprocess IS an
        established pattern here (pit_validation_runner), so the enforceable
        rule is over the page's TRANSITIVE web.operator_ui closure:

        * no module may import the orchestrator or swap machinery;
        * no module may import a process-spawning module — except the ONE
          audited runner, whose argv is pinned to the 06 validator below.

        Import-level on purpose: cockpit helpers legitimately carry
        `scripts/daily_update.py` inside COMMAND TEXT they print, so a
        string-level sweep cannot distinguish printing from invoking.
        """
        import ast
        base = _PROJECT_ROOT / "web" / "operator_ui"
        # NARROWER than the page-only list above: closure helpers
        # legitimately import `os` (env vars, path plumbing in theme /
        # bundle_health / update_status), so `os` and `builtins` stay legal
        # at closure level and their SPAWNING call shapes are forbidden by
        # regex instead — command TEXT ("python scripts/…") never matches an
        # `os.system(` call shape, so the cockpit's printed commands are
        # unaffected.
        spawn_banned = {"subprocess", "runpy", "multiprocessing",
                        "asyncio", "concurrent", "pty", "posix", "nt",
                        "_winapi", "_posixsubprocess", "importlib",
                        "ctypes"}
        spawn_shapes = re.compile(
            r"os\.(?:system|exec\w*|spawn\w*|popen\w*|startfile)\s*\(|__import__\s*\(")
        # The audited exemption: it exists to spawn ONE thing.
        exempt_spawner = "pit_validation_runner"
        runner_src = (base / "pit_validation_runner.py").read_text(
            encoding="utf-8")
        self.assertIn("06_validate_pit_data", runner_src)
        self.assertNotIn("daily_update.py", runner_src)

        seen: set[str] = set()
        queue = ["pages.data_inspect"]
        while queue:
            rel = queue.pop()
            if rel in seen:
                continue
            seen.add(rel)
            path = base / (rel.replace(".", "/") + ".py")
            if not path.is_file():
                continue
            module_src = path.read_text(encoding="utf-8")
            with self.subTest(module=rel, check="spawn call shapes"):
                self.assertIsNone(
                    spawn_shapes.search(module_src),
                    f"{rel} 出现 os 派生调用形状")
            module_ast = ast.parse(module_src)
            for node in ast.walk(module_ast):
                # RESOLVED names, not just node.module: `from web.operator_ui
                # import update_runner` exposes the submodule only through
                # its alias (module == "web.operator_ui"), and `from
                # src.data_pipeline import daily_update` names the orchestrator
                # only as module+alias — checking node.module alone misses
                # both, leaving the helper outside the closure and the
                # orchestrator import invisible (codex #434 r19).
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # RELATIVE imports resolved against the current module
                    # (codex #434 r20): `from . import update_runner` has
                    # module=None/level=1 and `from .update_runner import
                    # run_update` only the relative suffix — ignoring
                    # node.level let both escape the closure entirely.
                    if node.level:
                        pkg_parts = ("web.operator_ui." + rel).split(".")[:-1]
                        if node.level > 1:
                            pkg_parts = pkg_parts[:-(node.level - 1)]
                        base_pkg = ".".join(pkg_parts)
                        prefix = (f"{base_pkg}.{node.module}"
                                  if node.module else base_pkg)
                    else:
                        prefix = node.module or ""
                    names = [prefix] + [
                        f"{prefix}.{a.name}" if prefix else a.name
                        for a in node.names
                    ]
                else:
                    continue
                for name in names:
                    root = name.split(".")[0]
                    with self.subTest(module=rel, imported=name):
                        self.assertNotIn(
                            name, ("src.data_pipeline.daily_update",
                                   "src.data_pipeline.bundle_swap"),
                            f"{rel} 把编排器/换库机器接进了检视页闭包")
                        # `from os import system; system(...)` puts a BARE
                        # name at the call site — no attribute shape to
                        # match, and `os` itself is closure-legal. Reject
                        # the spawning NAMES at their import instead
                        # (codex #434 r21).
                        if root in ("os", "posix", "nt"):
                            leaf = name.rsplit(".", 1)[-1]
                            spawnish = (leaf in ("system", "startfile")
                                        or leaf.startswith(
                                            ("exec", "spawn", "popen")))
                            self.assertFalse(
                                spawnish and leaf != name,
                                f"{rel} 从 {root} 直接导入派生函数 {leaf!r}"
                                f" —— 裸名调用没有属性形状可查")
                        if (root in spawn_banned
                                and rel.split(".")[-1] != exempt_spawner):
                            self.fail(
                                f"{rel} import 了可派生进程的模块 {name!r}"
                                f"(检视页闭包内仅 {exempt_spawner} 获豁免)")
                    if name.startswith("web.operator_ui."):
                        queue.append(name[len("web.operator_ui."):])

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
