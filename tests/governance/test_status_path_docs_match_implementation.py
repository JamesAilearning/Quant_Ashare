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

# 该 change 已归档（#434 shipped），delta 也已并进主 specs。钉**主 spec**
# 而不是追归档路径:归档件是历史留档,主 spec 才是活契约——实现漂了要红的是
# 后者。归档时这几条曾因路径搬家而断,正是「钉在会搬的东西上」的代价。
_SPECS = _PROJECT_ROOT / "openspec" / "specs"
_CHANGE = (_PROJECT_ROOT / "openspec" / "changes" / "archive"
           / "2026-08-18-2026-08-14-daily-update-run-status")

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


SPAWN_SHAPES_PATTERN = (
    r"os\.(?:system|exec\w*|spawn\w*|popen\w*|posix_spawn\w*|fork\w*|startfile)"
    r"\s*\(|__import__\s*\("
)


pathlib_Path = Path


def _is_spawnish(leaf: str) -> bool:
    """Names on os/posix/nt that reach a new process.

    ONE list for both the import check and the alias-aware call check — two
    copies drifted twice already (posix_spawn in r22, aliases in r23).
    """
    return (leaf in ("system", "startfile", "fork", "forkpty")
            or leaf.startswith(("exec", "spawn", "popen", "posix_spawn")))


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
        spec = (_SPECS / "v2-daily-data-update"
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
        spec = (_SPECS / "v2-operator-ui"
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
        spec = (_SPECS / "v2-operator-ui"
                / "spec.md").read_text(encoding="utf-8")
        self.assertIn("**import or invoke**", spec)
        self.assertNotIn("SHALL NOT name the orchestrator", spec)

    def test_the_page_itself_needs_no_os_at_all(self) -> None:
        """The page's EXTRA restriction beyond the closure rules.

        Everything else this block used to check (spawning modules, dynamic
        importers, call shapes, orchestrator imports) is enforced for the
        page BY THE CLOSURE WALK below — the page is its seed module. Two
        policy copies had already diverged over `os` (r28), so the shared
        rules live only in the walk and THIS test keeps only the page-local
        increment: the page is pathlib-only, so even `os`/`builtins` —
        closure-legal for helpers that read env vars — are banned here. If
        the page ever needs os, the exemption must be argued here, not
        assumed.
        """
        import ast
        page = (_PROJECT_ROOT / "web" / "operator_ui" / "pages"
                / "data_inspect.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(page)):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                with self.subTest(imported=name):
                    self.assertNotIn(
                        name.split(".")[0], ("os", "builtins"),
                        f"检视页(pathlib-only)import 了 {name!r}")

    def test_the_pages_import_closure_cannot_reach_the_orchestrator(self) -> None:
        """The helper door (codex #434 r18, uniform walk since r27).

        `from web.operator_ui.update_runner import run_update` is an allowed
        import and a plain call — the single-page scan sees nothing, while
        the helper spawns the updater. Helper-mediated subprocess IS an
        established pattern here (pit_validation_runner), so the enforceable
        rule is over the page's TRANSITIVE closure under `web`:

        * no module may import the orchestrator / swap machinery / anything
          under scripts (the CLI wrappers reach the same orchestrator);
        * no module may import a process-spawning module — except the ONE
          audited runner, whose argv is pinned to the 06 validator below;
        * modules that legally import `os` may not touch its spawning names
          (attribute shapes AND alias-bound calls AND from-imported names).

        ONE loop over ABSOLUTE module names — the r26 special branch for
        `web/__init__.py` ran a reduced check and its relative-import anchor
        mis-resolved package initializers; two copies of this logic have
        drifted twice, so there is exactly one now (codex #434 r27).

        Import-level on purpose: cockpit helpers legitimately carry
        `scripts/daily_update.py` inside COMMAND TEXT they print, so a
        string-level sweep cannot distinguish printing from invoking.
        """
        import ast
        spawn_banned = {"subprocess", "runpy", "multiprocessing",
                        "asyncio", "concurrent", "pty", "posix", "nt",
                        "_winapi", "_posixsubprocess", "importlib",
                        "builtins", "ctypes"}
        spawn_shapes = re.compile(SPAWN_SHAPES_PATTERN)
        exempt_spawner = "pit_validation_runner"
        runner_src = (_PROJECT_ROOT / "web" / "operator_ui"
                      / "pit_validation_runner.py").read_text(encoding="utf-8")
        self.assertIn("06_validate_pit_data", runner_src)
        self.assertNotIn("daily_update.py", runner_src)

        def resolve(name: str) -> tuple[pathlib_Path, bool] | None:
            path = _PROJECT_ROOT / (name.replace(".", "/") + ".py")
            if path.is_file():
                return path, False
            path = _PROJECT_ROOT / name.replace(".", "/") / "__init__.py"
            if path.is_file():
                return path, True
            return None

        seen: set[str] = set()
        queue = ["web.operator_ui.pages.data_inspect"]
        while queue:
            name = queue.pop()
            if name in seen or not name.startswith("web"):
                continue
            seen.add(name)
            # package initializers run before any submodule import — every
            # ancestor package is part of the closure (codex #434 r26).
            if "." in name:
                queue.append(name.rsplit(".", 1)[0])
            resolved = resolve(name)
            if resolved is None:
                continue
            path, is_pkg = resolved
            module_src = path.read_text(encoding="utf-8")
            with self.subTest(module=name, check="spawn call shapes"):
                self.assertIsNone(
                    spawn_shapes.search(module_src),
                    f"{name} 出现 os 派生调用形状")
            module_ast = ast.parse(module_src)
            # alias-aware calls: `import os as x; x.posix_spawn(...)`
            os_aliases = {
                (a.asname or a.name)
                for n in ast.walk(module_ast) if isinstance(n, ast.Import)
                for a in n.names
                if a.name.split(".")[0] in ("os", "posix", "nt")
            }
            for n in ast.walk(module_ast):
                if (isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name)
                        and n.value.id in os_aliases
                        and _is_spawnish(n.attr)):
                    self.fail(
                        f"{name} 经别名 {n.value.id!r} 调用派生原语 "
                        f"{n.attr!r}(第 {n.lineno} 行)")
            for node in ast.walk(module_ast):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # relative imports anchored correctly for BOTH module
                    # kinds: a package initializer's level-1 anchor is the
                    # package itself, a plain module's is its parent
                    # (codex #434 r27).
                    if node.level:
                        pkg_parts = name.split(".") if is_pkg                             else name.split(".")[:-1]
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
                    for alias in node.names:
                        with self.subTest(module=name,
                                          imported_name=alias.name):
                            self.assertNotIn(
                                alias.name,
                                {"__import__", "import_module"},
                                f"{name} 导入了动态取模块的名字")
                else:
                    continue
                for imported in names:
                    root = imported.split(".")[0]
                    with self.subTest(module=name, imported=imported):
                        self.assertNotIn(
                            imported, ("src.data_pipeline.daily_update",
                                       "src.data_pipeline.bundle_swap"),
                            f"{name} 把编排器/换库机器接进了检视页闭包")
                        self.assertNotEqual(
                            root, "scripts",
                            f"{name} import 了 scripts 下的入口"
                            f" —— 检视页闭包不得触碰任何 CLI 包装")
                        if root in ("os", "posix", "nt"):
                            leaf = imported.rsplit(".", 1)[-1]
                            self.assertFalse(
                                _is_spawnish(leaf) and leaf != imported,
                                f"{name} 从 {root} 直接导入派生函数 "
                                f"{leaf!r} —— 裸名调用没有属性形状可查")
                        if (root in spawn_banned
                                and name.split(".")[-1] != exempt_spawner):
                            self.fail(
                                f"{name} import 了可派生进程的模块 "
                                f"{imported!r}(检视页闭包内仅 "
                                f"{exempt_spawner} 获豁免)")
                    if imported.startswith("web."):
                        queue.append(imported)


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
