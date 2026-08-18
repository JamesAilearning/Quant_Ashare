"""Pure helpers for the 运行中心 (run center) page.

No Streamlit imports — plain, unit-testable Python (the pages pattern:
``pages/_*_helpers.py`` pure + thin render page).

Why these two live here rather than on the page: the logic suite runs with
the project's required + ``dev`` dependencies but **without** the optional
``ui`` extra, so importing ``pages/run_center.py`` to reach a pure function
fails at ``import streamlit`` (codex #442 r6 reproduced five such failures).
A behaviour test for a pure predicate must not depend on the UI extra.
"""

from __future__ import annotations

from datetime import datetime, timedelta

#: 刚点过启动、但子进程还没来得及写 running 记录的那段窗口。期间照常
#: 轮询,否则启动后的页面会一直停在「尚无记录」直到操作人手点刷新
#: (codex #442 r1)。有界:超时即停,免得启动失败时无限轮询。
AWAIT_LAUNCH_WINDOW = timedelta(minutes=5)


def await_window_expired(awaiting_since: datetime, now: datetime) -> bool:
    """启动等待窗是否已过期(纯函数,注入 ``now`` 便于行为测试)。

    主脚本与片段**两处**都用它:片段计时只重跑片段,主脚本算出的窗口判断
    在片段注册后不会被重新求值,所以片段必须自己判过期并把整页拉起来,
    否则「有界」窗口形同虚设(codex #442 r3)。
    """
    return now - awaiting_since >= AWAIT_LAUNCH_WINDOW
