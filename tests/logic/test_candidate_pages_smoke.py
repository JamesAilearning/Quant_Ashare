"""Real Streamlit empty-state page execution, with no operational actions.

The actual app registers the navigation used by ``st.page_link``. This smoke
does not click buttons, start a server, or claim coverage of candidate tables.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_APP = _ROOT / "web" / "operator_ui" / "app.py"


@pytest.mark.parametrize("page,title", [
    ("today_workbench", "今日工作台"),
    ("daily_decision", "日度信号与人工决策"),
    ("ops_cockpit", "生产运维"),
    ("run_center", "运行中心"),
])
def test_candidate_page_empty_state_executes_without_writes_or_jobs(
    page: str, title: str, tmp_path: Path,
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    calls: dict[str, int] = {}

    def forbidden(name: str):
        calls[name] = 0

        def fail(*args, **kwargs):
            calls[name] += 1
            raise AssertionError(f"empty-state smoke called operational API: {name}")

        return fail

    with pytest.MonkeyPatch.context() as patch:
        # Point every deployment path at an absent test-local leaf. No model,
        # data, journal, status, ledger, or scheduler log is read from production.
        for key, leaf in {
            "QUANT_MODEL_PATH": "missing-model.pkl",
            "QUANT_TRADING_CALENDAR": "missing-calendar.json",
            "QUANT_DELISTED_REGISTRY": "missing-registry.parquet",
            "QUANT_NAME_SOURCE": "missing-stock-basic.parquet",
            "QUANT_NAMECHANGE_PATH": "missing-namechange.parquet",
            "QUANT_DECISION_JOURNAL_DIR": "missing-journal",
        }.items():
            patch.setenv(key, str(tmp_path / leaf))
        patch.setenv("QUANT_ENSEMBLE_MANIFEST", "none")
        patch.delenv("QV2_SHOW_DESIGN_SYSTEM", raising=False)

        from web.operator_ui import (
            anchor_health,
            bundle_health,
            decision_journal,
            incumbent,
            job_io,
            job_manager,
            recert_health,
            recommend_runner,
            theme,
            update_runner,
        )
        from web.operator_ui.pages import _daily_decision_helpers as decision
        from web.operator_ui.pages import _ops_cockpit_helpers as ops

        patch.setattr(
            bundle_health, "resolve_default_provider_uri",
            lambda: str(tmp_path / "missing-provider"),
        )
        single = incumbent.IncumbentIdentity(kind="single")
        patch.setattr(incumbent, "resolve_incumbent", lambda: single)
        # The daily helper re-exports this callable; cover that bound alias too.
        patch.setattr(decision, "resolve_incumbent", lambda: single)
        patch.setattr(decision, "list_recommendation_artifacts", lambda: [])
        patch.setattr(job_manager.JobManager, "list_jobs", staticmethod(lambda: []))
        patch.setattr(job_io, "load_all_jobs_read_only", lambda: [])
        for name in (
            "count_malformed_cli_entries", "count_malformed_ui_job_entries",
            "count_cli_rows_outside_output_tree",
        ):
            patch.setattr(job_io, name, lambda: 0)
        patch.setattr(ops, "read_gate_cards", lambda: ([], "synthetic empty-state smoke"))
        patch.setattr(
            recert_health, "probe_recert_health",
            lambda: recert_health.RecertHealth(
                known=False, reason="synthetic empty-state smoke"),
        )
        patch.setattr(
            anchor_health, "baseline_identity",
            lambda: anchor_health.AnchorIdentity(None, None, None, False),
        )
        patch.setattr(
            anchor_health, "ci_leg_status",
            lambda: anchor_health.CiLegStatus(
                "unknown", None, "synthetic empty-state smoke"),
        )
        patch.setattr(theme, "load_preferences", lambda: theme.UserPreferences())
        patch.setattr(
            update_runner, "default_log_path",
            lambda provider: tmp_path / "missing-daily-update.log",
        )

        # Guard project operations, not Streamlit's internal script thread.
        # Count calls as well as raising, so a page cannot swallow the sentinel.
        for module, name in (
            (update_runner, "launch_daily_update"),
            (update_runner, "cancel_update"),
            (update_runner, "settle_late_cancel"),
            (recommend_runner, "run_daily_recommend"),
            (decision_journal, "append_decision"),
            (theme, "save_preferences"),
            (job_io, "write_job_json"),
            (job_manager, "_reconcile_zombie"),
        ):
            patch.setattr(module, name, forbidden(f"{module.__name__}.{name}"))
        for name in ("start", "stop", "delete"):
            patch.setattr(
                job_manager.JobManager, name,
                staticmethod(forbidden(f"JobManager.{name}")),
            )

        assert list(tmp_path.iterdir()) == []
        app = testing.AppTest.from_file(str(_APP), default_timeout=30)
        app.run(timeout=30)
        assert not app.exception, app.exception
        # This uses the registered real page, not an import or extracted AST.
        app.switch_page(f"pages/{page}.py").run(timeout=30)
        assert not app.exception, app.exception
        header = f'<h1 class="qv2-text-page-title">{title}</h1>'
        assert any(header in element.proto.body for element in app.get("html"))
        assert all(count == 0 for count in calls.values()), calls
        assert list(tmp_path.iterdir()) == []
