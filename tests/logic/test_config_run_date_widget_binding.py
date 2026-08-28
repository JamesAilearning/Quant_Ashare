"""预填的日期必须落到**真实的控件状态**上——用 ``AppTest`` 实测。

这一组是本仓第一次用 ``streamlit.testing`` 的 ``AppTest``。用它的理由很具体:
要证的命题是「控件在一串真实交互之后**返回什么**」，而那取决于 streamlit 自己
的控件身份语义。源码串、假 ``st``、单帧调用都证不了它——首版那条守卫只跑了
``_prefilled_trading_day``（一个纯函数），从没实例化过控件，于是这个缺陷在它
底下完全看不见（codex P1 on #471）。

缺陷本身（已实测复现）:``_select_trading_day`` 建的是**不带 key** 的控件。
streamlit 对这种控件按「参数变了就是另一个控件」认身份——``index`` 一变就重置
成新 default，没变就粘住上一次的值。于是有一格是错的:**预填值恰好等于 live
default 时** ``index`` 不变、控件不动，操作人先前的编辑留着，而横幅照说「已按
该次运行覆盖」。启动的窗口与源运行不同，页面上没有任何迹象。

反方向也实测过:直接给控件加 ``key`` 会让 session 说了算、``index`` 被忽略
——那正是 #300 回滚的病根（live default 冻结，换 provider 之后窗口不再按新
日历重算）。所以修法必须**同时**满足下面五组，缺一不可。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_APP = str(
    (PROJECT_ROOT / "tests" / "logic" / "fixtures"
     / "trading_day_widget_app.py").resolve()
)

try:  # streamlit.testing 在很老的版本里没有
    from streamlit.testing.v1 import AppTest
except ImportError:  # pragma: no cover - 本仓 CI 钉的版本有它
    AppTest = None  # type: ignore[assignment]


@unittest.skipIf(AppTest is None, "streamlit.testing.v1 不可用")
class TradingDayWidgetBindingTests(unittest.TestCase):
    """五组场景。任何一组不成立，这个修法就是错的。"""

    def _app(self):  # type: ignore[no-untyped-def]
        app = AppTest.from_file(_APP)  # type: ignore[union-attr]
        app.run()
        self.assertFalse(
            app.exception,
            app.exception[0].message if app.exception else "",
        )
        # 宿主只渲染被测的那一个控件——首版宿主 `import` 了整个配置页脚本，
        # 于是 `selectbox[0]` 是那一页自己的「模式」下拉，用例测的根本不是
        # 它自称要测的控件（实测当场看出来）。
        self.assertEqual(len(app.selectbox), 1, "宿主不该渲染别的控件")
        return app

    def _picked(self, app) -> str:  # type: ignore[no-untyped-def]
        return str(app.text[1].value).split("=", 1)[1]

    def _wanted(self, app) -> str:  # type: ignore[no-untyped-def]
        return str(app.text[0].value).split("=", 1)[1]

    # ---------------------------------------------------------------- 缺陷本体
    def test_a_prefill_equal_to_the_live_default_still_applies(self) -> None:
        """这一格是缺陷本体:预填值 == live default。

        ``index`` 因此不变 ⇒ 不带 key 的控件认为「还是同一个控件」⇒ 粘住操作
        人的编辑，而横幅说的是「已按该次运行覆盖」。
        """
        app = self._app()
        self.assertEqual(self._picked(app), "2021-01-04")

        app.selectbox[0].select("2023-01-03").run()
        self.assertEqual(self._picked(app), "2023-01-03", "操作人的编辑没生效")

        # 预填一个**与 live default 相同**的值。
        app.session_state["cr_overall_start"] = "2021-01-04"
        app.session_state["prefill_config_action"] = "action-1"
        app.run()

        self.assertEqual(self._wanted(app), "2021-01-04")
        self.assertEqual(
            self._picked(app), "2021-01-04",
            "预填值等于 live default 时被静默吞掉——横幅却说已覆盖",
        )

    # ------------------------------------------------------------ 不许过度修正
    def test_an_edit_made_after_the_prefill_is_not_undone(self) -> None:
        app = self._app()
        app.session_state["cr_overall_start"] = "2021-01-04"
        app.session_state["prefill_config_action"] = "action-1"
        app.run()

        app.selectbox[0].select("2022-01-04").run()
        self.assertEqual(self._picked(app), "2022-01-04")

        app.run()  # 一次什么也没发生的重绘
        self.assertEqual(
            self._picked(app), "2022-01-04",
            "预填之后的编辑被下一帧撤销了——那比原缺陷更坏",
        )

    def test_pressing_rerun_again_reapplies_the_same_value(self) -> None:
        # 与 #471 的动作 nonce 是同一条语义:每一次**按下**都是一次新的预填
        # 事件，即使值一模一样。
        app = self._app()
        app.session_state["cr_overall_start"] = "2021-01-04"
        app.session_state["prefill_config_action"] = "action-1"
        app.run()
        app.selectbox[0].select("2022-01-04").run()

        app.session_state["prefill_config_action"] = "action-2"
        app.run()

        self.assertEqual(self._picked(app), "2021-01-04")

    # -------------------------------------------------------- #300 的病根不复现
    def test_the_live_default_still_recomputes_without_a_prefill(self) -> None:
        """换 provider 之后窗口必须按新日历重算。

        这正是 #300 那次改动被回滚的原因:把 live default 种进 session 会让
        第一帧的回退被冻结、后续重算被无视。修法若靠「给控件加 key」了事，
        这一条就会红。
        """
        app = self._app()
        self.assertEqual(self._picked(app), "2021-01-04")

        app.session_state["_probe_live_default"] = "2022-01-04"
        app.run()

        self.assertEqual(self._wanted(app), "2022-01-04")
        self.assertEqual(
            self._picked(app), "2022-01-04",
            "live default 被冻结了——#300 的病根复现",
        )

    def test_an_action_that_supplied_nothing_does_not_clobber_the_edit(
        self,
    ) -> None:
        """动作是新的，但这次载荷**没带这个字段**——不许改写控件。

        源运行的归档 config 是一份合法空 YAML、或解析失败、或旧 schema 里
        压根没有这个日期字段时，``_apply_prefill_to_session`` 一个字节也没
        写，而动作 nonce 照样是新的。不加「这次真的带了」这个前提，控件会
        被强行改写成 live default，**默默丢掉操作人已经改好的日期**——而页面
        那一刻正说着「本次没有任何字段可预填」（codex P1 on #471）。

        此前的用例每次都先写 ``cr_overall_start`` 再推进 nonce，所以这条路
        整个没被走到（评审点名指出，属实）。
        """
        app = self._app()
        app.selectbox[0].select("2023-01-03").run()
        self.assertEqual(self._picked(app), "2023-01-03")

        # 新动作，但这次载荷没有带 overall_start。
        app.session_state["_probe_supplied"] = False
        app.session_state["prefill_config_action"] = "action-empty"
        app.run()

        self.assertEqual(
            self._picked(app), "2023-01-03",
            "载荷没带这个字段，控件却被改写了——操作人的编辑被默默丢掉",
        )

    def test_an_out_of_calendar_default_does_not_overwrite_every_frame(
        self,
    ) -> None:
        """默认值落在日历外时，只许绑**解析之后**的那个值，且只绑一次。

        绑两次——一次拿日历外的 ``default``、一次拿 ``options[0]``——会让
        ``__last_wanted`` 在两个值之间**每帧来回摆**，于是「wanted 变了」
        永远成立，控件被每帧改写:操作人选的任何合法日期都会被打回日历的
        第一天（codex P1 on #471）。
        """
        app = self._app()
        app.session_state["_probe_live_default"] = "1999-01-01"  # 日历外
        app.run()
        # 回退到日历第一天,并给出警告。
        self.assertEqual(self._picked(app), "2020-01-02")
        self.assertTrue(app.warning, "落到日历外时必须有可见警告")

        app.selectbox[0].select("2022-01-04").run()
        self.assertEqual(self._picked(app), "2022-01-04")

        app.run()  # 一次什么也没发生的重绘
        self.assertEqual(
            self._picked(app), "2022-01-04",
            "日历外默认值下控件被每帧改写——操作人的选择被打回第一天",
        )

    def test_an_edit_survives_without_a_prefill(self) -> None:
        app = self._app()
        app.selectbox[0].select("2023-01-03").run()
        self.assertEqual(self._picked(app), "2023-01-03")

        app.run()
        self.assertEqual(self._picked(app), "2023-01-03")


if __name__ == "__main__":
    unittest.main()
