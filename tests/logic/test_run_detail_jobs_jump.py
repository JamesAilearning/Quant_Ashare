"""详情页 →「前往作业」入口：判据、链接形状、**一处**接线 + 一道非接线守卫。

**结果页**在看一个还在跑的运行时给出一步跳转，带上该 run_id 与「运行中」
筛选。这里覆盖四件事：

1. 判据本身（运行中给 / 终态不给 / 拿不到 id 不给）；
2. **同一性**——带过去的值必须真的过得了作业页的 ``_param_guard``，而且
   ``status`` 必须在作业页状态选择器的合法值域内（值域从 ``jobs.py`` 源码
   里解析出来，不手抄），落地那侧的筛选也真跑一遍；
3. 结果页那一处接线钉（钉的是**条件整行**，不是标识符——本仓被「条件熄火」
   变异逃逸过两次）；
4. **滚动验证页刻意不接**这个入口的那三条结构性前提。首版两页都接了，评审
   指出后撤回：那一页的 ``wf_jobs`` 过滤掉没有 ``run_dir`` 的记录，而
   ``job_runner`` 只在子进程成功之后才写 ``run_dir``，所以运行中的作业不在
   那一页的任何一张表里，入口永不触发。任何一条前提变了，这个划界就该重新
   评估——所以钉的是**前提**，不是「没有接线」这个事实。
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.operator_ui import job_io  # noqa: E402
from web.operator_ui._param_guard import sanitize  # noqa: E402
from web.operator_ui.job_io import JobSummary  # noqa: E402
from web.operator_ui.jobs_jump import (  # noqa: E402
    JOBS_PAGE,
    RUNNING_STATUS,
    running_run_jobs_link,
)

_UI = PROJECT_ROOT / "web" / "operator_ui"
_PAGE_JOBS = _UI / "pages" / "jobs.py"
_PAGE_RESULTS = _UI / "pages" / "results.py"
_PAGE_WF = _UI / "pages" / "walk_forward.py"

#: 形如真实 UI 作业 id 的夹具。
_RUN_ID = "wf_20260827_120000_ab12cd"
#: 合法的一次性交接令牌（uuid4().hex 的形状）。
_TOKEN = "0123456789abcdef0123456789abcdef"


def _jobs_status_options() -> list[str]:
    """作业页状态选择器的合法值域——从 ``jobs.py`` 源码**解析**出来。

    读不出来就抛。静默返回空列表会让下面「``running`` 在值域内」这条断言
    变成空集上的真命题：覆盖面空得看不出来，正是本仓踩过的坑。
    """
    tree = ast.parse(_PAGE_JOBS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "selectbox"):
            continue
        keyword = {kw.arg: kw.value for kw in node.keywords}.get("key")
        if not (isinstance(keyword, ast.Constant) and keyword.value == "jobs_status"):
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], ast.List):
            raise AssertionError("状态选择器的选项不是列表字面量，值域读不出来")
        elements = node.args[1].elts
        options = [
            element.value
            for element in elements
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if len(options) != len(elements):
            raise AssertionError("状态选择器里有非字面量选项，值域读不全")
        return options
    raise AssertionError("jobs.py 里找不到 key='jobs_status' 的状态选择器")


def _jobs_defaults() -> dict[str, str]:
    """作业页的 ``_DEFAULTS``——同样从源码解析，读不出来就抛。"""
    tree = ast.parse(_PAGE_JOBS.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_DEFAULTS"
            and node.value is not None
        ):
            parsed = ast.literal_eval(node.value)
            if not isinstance(parsed, dict) or not parsed:
                raise AssertionError("_DEFAULTS 不是非空字典字面量")
            return {str(k): str(v) for k, v in parsed.items()}
    raise AssertionError("jobs.py 里找不到 _DEFAULTS")


class JumpVerdictTests(unittest.TestCase):
    """给不给入口。"""

    def test_a_running_run_gets_the_entry(self) -> None:
        link = running_run_jobs_link(
            run_id=_RUN_ID, status="running", handoff_token=_TOKEN
        )
        assert link is not None
        self.assertEqual(link.page, JOBS_PAGE)
        self.assertTrue((_UI / link.page).is_file(), "入口指向的页面文件不存在")
        self.assertEqual(
            dict(link.query_params),
            {"status": "running", "search": _RUN_ID, "handoff": _TOKEN},
        )

    def test_terminal_states_get_no_entry(self) -> None:
        # 终态的运行在作业页没有「活体状态」可看，入口是噪声；而且带着
        # status=running 跳过去，那一行根本不在筛选结果里。
        for status in ("completed", "failed", "partial", "stopped", "stop_failed"):
            with self.subTest(status=status):
                self.assertIsNone(
                    running_run_jobs_link(
                        run_id=_RUN_ID, status=status, handoff_token=_TOKEN
                    )
                )

    def test_not_yet_started_states_get_no_entry(self) -> None:
        # queued / pending 是作业页的**另外**两个筛选值。把它们也算成
        # 「运行中」等于在这里自造一套状态语义。
        for status in ("queued", "pending", "unknown", ""):
            with self.subTest(status=status):
                self.assertIsNone(
                    running_run_jobs_link(
                        run_id=_RUN_ID, status=status, handoff_token=_TOKEN
                    )
                )

    def test_missing_status_gets_no_entry(self) -> None:
        self.assertIsNone(
            running_run_jobs_link(run_id=_RUN_ID, status=None, handoff_token=_TOKEN)
        )

    def test_status_case_matches_the_results_page_judgement(self) -> None:
        # 结果页判自动刷新用的是 ``str(status).lower() == "running"``，
        # 这里逐字照搬——两处大小写口径不同，同一个运行会一处给入口、
        # 一处不给。
        link = running_run_jobs_link(
            run_id=_RUN_ID, status="RUNNING", handoff_token=_TOKEN
        )
        assert link is not None
        self.assertEqual(link.query_params["status"], RUNNING_STATUS)

    def test_a_missing_run_id_gets_no_entry(self) -> None:
        # 空 id 的链接会带着空 search 跳过去 = 「运行中的全部作业」，
        # 而操作人以为筛的是他刚点开的这一次。
        for run_id in (None, "", "   "):
            with self.subTest(run_id=repr(run_id)):
                self.assertIsNone(
                    running_run_jobs_link(
                        run_id=run_id, status="running", handoff_token=_TOKEN
                    )
                )

    def test_a_run_id_the_jobs_page_would_reject_gets_no_entry(self) -> None:
        # 作业页对 URL 参数一律 sanitize，不通过就**静默**落回默认值。所以
        # 出发侧就要拦：过不了的值不画入口，而不是画一个到那边变成空筛选
        # 的链接。
        for bad in ("../../etc/passwd", "a b", "run;rm -rf", "id\nfoo", "x" * 201):
            with self.subTest(run_id=bad[:12]):
                self.assertIsNone(
                    running_run_jobs_link(
                        run_id=bad, status="running", handoff_token=_TOKEN
                    )
                )

    def test_a_run_id_the_search_whitelist_rejects_gets_no_entry(self) -> None:
        # run_id 的字符集眼下是 search 白名单的子集，所以这条分支在今天的
        # 白名单下不可达。它守的是**两条白名单错开**的那一天：真发生时，
        # 链接会带着一个被静默丢弃的 search 跳过去。用一条拒收的 search
        # 校验器模拟那一天。
        with mock.patch.dict(
            "web.operator_ui._param_guard._VALIDATORS",
            {"search": lambda value: None},
        ):
            self.assertIsNone(
                running_run_jobs_link(
                    run_id=_RUN_ID, status="running", handoff_token=_TOKEN
                )
            )

    def test_a_malformed_handoff_token_is_refused_loudly(self) -> None:
        # 令牌是调用方铸的。铸坏了作业页会**静默**丢弃它，链接于是退回
        # 「可能被陈旧筛选吞掉」的形态——那是调用方的编码错误，要响。
        for bad in ("", "not-a-token", "A" * 32, _TOKEN + "0"):
            with self.subTest(token=bad[:12]):
                with self.assertRaises(ValueError):
                    running_run_jobs_link(
                        run_id=_RUN_ID, status="running", handoff_token=bad
                    )

    def test_the_token_contract_is_checked_on_every_render(self) -> None:
        # 令牌校验若排在判定之后，一个铸坏令牌的调用方在终态运行上一路安静，
        # 直到「恰好有个运行在跑」那天才炸——而那正是本入口唯一有用的时刻。
        with self.assertRaises(ValueError):
            running_run_jobs_link(
                run_id=_RUN_ID, status="completed", handoff_token="not-a-token"
            )
        with self.assertRaises(ValueError):
            running_run_jobs_link(run_id=None, status=None, handoff_token="")


class ParamIdentityTests(unittest.TestCase):
    """带过去的值必须是作业页那边**真的**认的值。"""

    def _link_params(self) -> dict[str, str]:
        link = running_run_jobs_link(
            run_id=_RUN_ID, status="running", handoff_token=_TOKEN
        )
        assert link is not None
        return dict(link.query_params)

    def test_every_param_survives_the_jobs_page_guard(self) -> None:
        # 真跑 sanitize，不在测试里抄一个字符串：抄的那个过不过与链接无关。
        params = self._link_params()
        defaults = _jobs_defaults()
        self.assertEqual(
            sanitize("status", params["status"], default=defaults["status"]),
            params["status"],
        )
        self.assertEqual(sanitize("search", params["search"], default=""), params["search"])
        self.assertEqual(sanitize("handoff", params["handoff"], default=""), params["handoff"])

    def test_status_is_inside_the_jobs_page_selectbox_domain(self) -> None:
        options = _jobs_status_options()
        self.assertIn("all", options, "值域读错了：状态下拉里必有 all")
        self.assertIn(self._link_params()["status"], options)

    def test_the_link_actually_narrows_the_filter(self) -> None:
        # 带过去的 status 若恰好等于默认值，这个入口就只是「打开作业页」，
        # 「运行中」三个字是骗人的。
        defaults = _jobs_defaults()
        self.assertIn("status", defaults)
        self.assertNotEqual(self._link_params()["status"], defaults["status"])

    def test_the_destination_filter_keeps_exactly_that_run(self) -> None:
        # 落地那侧真跑一遍：作业页没有 run_id 筛选，run_id 是靠自由文本
        # 搜索命中的（job_io._apply_filters）。链接给的两个值必须**同时**
        # 起作用——同名前缀的历史运行不能混进来。
        params = self._link_params()
        rows = [
            JobSummary(run_id=_RUN_ID, type="walk_forward", status="running", source="ui"),
            JobSummary(run_id=f"{_RUN_ID}_old", type="walk_forward", status="completed", source="ui"),
            JobSummary(run_id="another_run_9f", type="pipeline", status="running", source="ui"),
        ]
        kept = job_io._apply_filters(
            rows, "all", params["status"], "all", params["search"], "", ""
        )
        self.assertEqual([row.run_id for row in kept], [_RUN_ID])

    def _jobs_key_sets(self) -> tuple[set[str], set[str], set[str]]:
        """从 ``jobs.py`` 源码里**求值**出三个集合，而不是比对拼写。

        上一版钉的是字面串 ``handoff_keys=frozenset({"status", "search"})``。
        它钉的是**拼写**：集合改成从 ``_DEFAULTS`` 减出来之后当场失配，而它
        从来没钉住「哪些键真的会被一次性交接覆盖」这件事本身。
        """
        import ast

        tree = ast.parse(_PAGE_JOBS.read_text(encoding="utf-8"))
        wanted = {"_DEFAULTS", "_HANDOFF_EXEMPT", "_HANDOFF_KEYS"}
        namespace: dict[str, object] = {}
        for node in tree.body:
            # `_DEFAULTS: dict[str, str] = {...}` 是 AnnAssign,不是 Assign
            # ——只收 Assign 会一个也找不到,而「找不到」如果被写成静默跳过
            # 就是一条永远为空的守卫。这里两种都收，找不到就响亮地红。
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets
                         if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(
                    node.target, ast.Name):
                names = [node.target.id]
            else:
                continue
            if not (set(names) & wanted):
                continue
            exec(  # noqa: S102 - 求值的是本仓页面自己的字面量赋值
                compile(ast.Module(body=[node], type_ignores=[]),
                        str(_PAGE_JOBS), "exec"),
                namespace,
            )
        missing = wanted - namespace.keys()
        self.assertFalse(missing, f"jobs.py 里找不到这些赋值: {missing}")
        return (
            set(namespace["_DEFAULTS"]),          # type: ignore[arg-type]
            set(namespace["_HANDOFF_EXEMPT"]),    # type: ignore[arg-type]
            set(namespace["_HANDOFF_KEYS"]),      # type: ignore[arg-type]
        )

    def test_the_handoff_overrides_every_membership_key(self) -> None:
        """一次性交接必须覆盖**决定成员与位置**的全部键。

        普通分支的条件是「URL 值与**上次消费的** URL 值不同」。操作人离开前
        把 ``jobs_type`` 改成 ``provider``、而 ``jobs_last_url_type`` 仍是进来
        时的默认 ``all``——跟着新链接回来时 URL 里没有 ``type``，``_qp_read``
        给出 ``all``，``all != all`` 为假，于是**保留了** ``provider``。被请求
        的那个运行当场被筛掉，说好的「精确落到那一行」落到一个空列表上
        （codex P2 on #473）。``page`` 同理:停在第 3 页时单行结果在第 1 页。

        这与本 change 写进 OpenSpec 的那句话是同一件事:**到达的 URL 就是这次
        导航的完整筛选状态**。
        """
        defaults, exempt, handoff = self._jobs_key_sets()

        self.assertEqual(
            handoff, defaults - exempt,
            "交接键必须由 _DEFAULTS 减出来——手写清单会漏掉将来新增的筛选键",
        )
        for key in ("status", "search", "type", "source",
                    "date_from", "date_to", "page"):
            with self.subTest(key=key):
                self.assertIn(key, handoff)

    def test_only_presentation_keys_are_exempt(self) -> None:
        # 豁免的必须**只**是「改呈现、不改成员与位置」的那些。多豁免一个
        # 筛选键，就是给它开一条「跟着链接过来却被陈旧值挡住」的入口。
        _defaults, exempt, _handoff = self._jobs_key_sets()

        self.assertEqual(exempt, {"sort_by", "sort_dir", "autorefresh"})

    def test_the_call_site_passes_the_derived_set(self) -> None:
        # 求值出来的集合对了，但调用点若仍传一个手写字面量，那份推导就是死的。
        source = _PAGE_JOBS.read_text(encoding="utf-8")
        self.assertIn("    handoff_keys=_HANDOFF_KEYS,\n", source)
        # 豁免集合也必须**传下去**。只是把它从 handoff_keys 里减掉不够——
        # 那只让它改走普通分支，而普通分支照样把它重置（codex P2 on #473;
        # 实测 settled 态下 sort_by 被打回 created_at、autorefresh 被关掉）。
        self.assertIn("    handoff_preserve=_HANDOFF_EXEMPT,\n", source)


class HandoffSeedingTests(unittest.TestCase):
    """一次性交接对**两条真实链接**的作用，逐场景实测。

    首版这里只有一条用例，且喂的是 ``jobs_search="我手打的词"`` +
    ``jobs_last_url_search=""`` 这个**非典型残值态**——评审指出它掩盖了真实
    行为，是对的。真实的「settled 态」是两者都等于操作人输入的词（页面每帧
    把 session 回镜进 URL，见 ``jobs.py`` 的 ``_qp_write`` 一段）。

    两条链接的契约不同，必须同时成立：
    * 详情页链接带 ``status`` + ``search``：要精确落到那一行；
    * 今日工作台队列链接**只带** ``status``（``_today_decision_queue_helpers``
      的 ``queue_page_link`` 实测只给 ``{"status": ...}``），要显示该状态的
      **全部**作业——所以它必须把操作人手打的搜索重置掉。
    """

    def _seed(
        self, *, url: dict[str, str], session: dict[str, str],
    ) -> dict[str, str]:
        """用页面里那段 AST **真跑**一次播种，返回处理后的 session。"""
        # 被测函数的模块级依赖也要一起取:漏掉就 NameError,而那是**响亮**的
        # ——比静默少测一段好。
        wanted = {
            "_seed_session_from_url", "_iso_to_date",
            "_HANDOFF_WIDGET_MIRRORS",
        }
        body: list[ast.stmt] = []
        for node in ast.parse(_PAGE_JOBS.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                body.append(node)
            elif (isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id in wanted):
                body.append(node)
        self.assertEqual(
            len(body), len(wanted),
            f"jobs.py 里少了这些定义: {wanted - {getattr(n, 'name', None) or n.target.id for n in body}}",
        )
        defaults = _jobs_defaults()
        namespace: dict[str, object] = {
            "st": mock.Mock(session_state=session, query_params=url),
            "_qp_read": lambda k: url.get(k, defaults[k]),
            "date": __import__("datetime").date,
        }
        exec(  # noqa: S102 - 取的是本仓自己的页面源码
            compile(
                ast.Module(body=body, type_ignores=[]),
                str(_PAGE_JOBS), "exec",
            ),
            namespace,
        )
        namespace["_seed_session_from_url"](  # type: ignore[operator]
            ["status", "search"],
            handoff_token=_TOKEN,
            handoff_keys=frozenset({"status", "search"}),
        )
        return session

    _SETTLED = {
        "jobs_search": "manual", "jobs_last_url_search": "manual",
        "jobs_status": "all", "jobs_last_url_status": "all",
    }

    def test_the_detail_link_lands_on_exactly_that_run(self) -> None:
        session = self._seed(
            url={"status": "running", "search": _RUN_ID, "handoff": _TOKEN},
            session=dict(self._SETTLED),
        )

        self.assertEqual(session["jobs_search"], _RUN_ID)
        self.assertEqual(session["jobs_status"], "running")

    def test_a_repeat_jump_for_the_same_run_overrides_an_edited_search(
        self,
    ) -> None:
        # URL 值与上次消费的相同（同一个运行），但操作人此后改过搜索框。
        # 没有一次性令牌的话，普通路径会保留他手打的词，链接落在错的行上。
        session = self._seed(
            url={"status": "running", "search": _RUN_ID, "handoff": _TOKEN},
            session={
                "jobs_search": "manual", "jobs_last_url_search": _RUN_ID,
                "jobs_last_handoff_search": "f" * 32,
                "jobs_status": "all", "jobs_last_url_status": "all",
            },
        )

        self.assertEqual(session["jobs_search"], _RUN_ID)

    def test_a_status_only_queue_link_resets_the_search(self) -> None:
        # 队列链接说的是「给我看这个状态的作业」。留着一个无关的搜索词，
        # 操作人会看到空列表、以为那条队列项消失了。
        session = self._seed(
            url={"status": "failed", "handoff": _TOKEN},
            session=dict(self._SETTLED),
        )

        self.assertEqual(session["jobs_search"], "")
        self.assertEqual(session["jobs_status"], "failed")

    def test_the_queue_link_behaves_the_same_whatever_the_residue(self) -> None:
        # 同一条链接不该因为 `jobs_last_url_*` 这种**内部残值**而给出不同
        # 行为。首版那条用例正是踩在这个差异上通过的。
        settled = self._seed(
            url={"status": "failed", "handoff": _TOKEN},
            session=dict(self._SETTLED),
        )
        unsettled = self._seed(
            url={"status": "failed", "handoff": _TOKEN},
            session={
                "jobs_search": "manual", "jobs_last_url_search": "",
                "jobs_status": "all", "jobs_last_url_status": "all",
            },
        )

        self.assertEqual(settled["jobs_search"], unsettled["jobs_search"])
        self.assertEqual(settled["jobs_search"], "")

    def test_the_override_is_marked_consumed_so_it_applies_once(self) -> None:
        # 一次性：同一个令牌不该在后续重跑里反复压过操作人的新输入。
        session = self._seed(
            url={"status": "running", "search": _RUN_ID, "handoff": _TOKEN},
            session=dict(self._SETTLED),
        )
        self.assertEqual(session["jobs_last_handoff_search"], _TOKEN)

        session["jobs_search"] = "操作人在跳转之后又改了"
        again = self._seed(
            url={"status": "running", "search": _RUN_ID, "handoff": _TOKEN},
            session=session,
        )

        self.assertEqual(again["jobs_search"], "操作人在跳转之后又改了")


class PageWiringTests(unittest.TestCase):
    """**一处接线** + **一道非接线守卫**——两条都钉条件整行。

    结果页接了入口，钉的是它的条件整行。滚动验证页**刻意不接**，钉的不是
    「没有接线」这个事实（那种断言在任何一次无关重构里都会失去意义），而是
    让它不可接的**三条结构性前提**：`JobManager.start` 把 `run_dir` 初始化
    为 `None`、`job_runner.main` 只在子进程成功之后才写它、该页 `wf_jobs`
    过滤掉没有 `run_dir` 的记录。任一前提变了，这个划界就该重新评估。

    （类名里的「两个页面」曾经指「两页都接」——那是首版的前提，评审指出后
    整体撤回。把它留在回归套件里，等于让套件继续记录实现明确拒绝的设计，
    codex #473。）
    """

    def test_results_page_wires_the_entry(self) -> None:
        source = _PAGE_RESULTS.read_text(encoding="utf-8")
        self.assertIn("from web.operator_ui.jobs_jump import running_run_jobs_link\n", source)
        self.assertIn('run_id=selected_job.get("job_id"),', source)
        self.assertIn('status=selected_job.get("status"),', source)
        self.assertIn("handoff_token=uuid4().hex,", source)
        # 条件**整行**：只钉标识符或赋值行的话，`if False and ...` 这种
        # 「条件熄火」变异会原样逃逸（本仓 #470 连栽两轮）。
        self.assertIn("    if _jobs_jump is not None:\n", source)
        self.assertIn("st.page_link(\n", source)

    def test_the_walk_forward_page_structurally_cannot_show_a_running_run(
        self,
    ) -> None:
        """滚动验证详情页看不到运行中的运行——本入口在那里不适用。

        `JobManager.start()` 把 `run_dir` 初始化为 `None`，而
        `job_runner.main()` **只在子进程成功之后**才写它。滚动验证页的
        `wf_jobs` 又过滤掉没有 `run_dir` 的记录。三条合起来：一个正在跑的
        作业**不在**那一页的任何一张表里，所以在那里画这个入口，画出来的是
        一条永远不触发的分支——而对着它写的测试只能靠捏造
        `status="running"` + 有 `run_dir` 的组合，那个组合在生产里不存在
        （codex P1 on #473：首版正是这么写的）。

        入口因此只留在结果页：`viewable_jobs` 不过滤 `run_dir`，运行中的
        walk_forward 作业在那里可见。这条测试钉住那三条前提，任何一条变了
        都该重新评估这个划界。
        """
        manager = (_UI / "job_manager.py").read_text(encoding="utf-8")
        runner = (_UI / "job_runner.py").read_text(encoding="utf-8")
        wf_source = _PAGE_WF.read_text(encoding="utf-8")
        results_source = _PAGE_RESULTS.read_text(encoding="utf-8")

        self.assertIn('"run_dir": None,', manager)
        self.assertIn("    if succeeded and output_dir:", runner)
        self.assertIn('_write_job_json(job_dir, {"run_dir": run_dir})', runner)
        self.assertIn(
            'wf_jobs = [j for j in jobs if j.get("mode") == "walk_forward"'
            ' and j.get("run_dir")]',
            wf_source,
        )
        self.assertNotIn("running_run_jobs_link", wf_source)
        self.assertIn(
            '    if str(job.get("mode") or "") in {"pipeline", "walk_forward"}',
            results_source,
        )
        self.assertNotIn("run_dir", results_source.split(
            "viewable_jobs = [")[1].split("]")[0])
        self.assertIn("running_run_jobs_link", results_source)

    def test_the_page_does_not_reimplement_the_verdict(self) -> None:
        # 判据只有一份。页面自己写一份 `status == "running"` + 拼 query
        # params，必然分叉（本仓在折叠算法上已经栽过一次）。
        source = _PAGE_RESULTS.read_text(encoding="utf-8")
        self.assertNotIn('"status": "running"', source)
        self.assertNotIn('{"search":', source)

    def test_the_results_autorefresh_toggle_is_untouched(self) -> None:
        # 自动刷新是另一件事（opt-in 的轮询），本改动不碰它。
        source = _PAGE_RESULTS.read_text(encoding="utf-8")
        self.assertIn(
            '    if str(selected_job.get("status", "")).lower() == "running":\n', source
        )
        self.assertIn('key="results_autorefresh"', source)


if __name__ == "__main__":
    unittest.main()
