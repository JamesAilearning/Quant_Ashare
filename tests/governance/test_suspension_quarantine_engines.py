"""Today's incident policy must not silently redefine historical certification."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("engine", ["pipeline", "walk_forward"])
@pytest.mark.parametrize("padding", ["", "  "])
def test_quarantined_provider_refuses_before_historical_output_or_resume(tmp_path, engine, padding):
    from src.data.pit.bundle_integrity import write_bundle_integrity
    from tests.data_pipeline.test_qlib_bin_builder import _write_active
    from tests.data_pipeline.test_suspension_quarantine_update import _manifest

    raw = tmp_path / "raw"
    _write_active(raw / "active_stocks.parquet", ["600000.SH"])
    hole = _manifest(raw)
    provider = tmp_path / "provider"
    write_bundle_integrity(provider, built_from_holey_fetch=True, holes=(hole,))
    output = tmp_path / "must_not_exist"
    if engine == "pipeline":
        from src.core.pipeline import Pipeline, PipelineError

        cfg = SimpleNamespace(provider_uri=f"{padding}{provider}{padding}", output_dir=str(output))
        with patch("src.core.pipeline.provider_uri_guard_message", return_value=None):
            with pytest.raises(PipelineError, match="quarantin"):
                Pipeline.run(cfg)
    else:
        from src.core.walk_forward import WalkForwardConfig, WalkForwardEngine, WalkForwardError

        cfg = WalkForwardConfig(output_dir=str(output))
        with patch("src.core.walk_forward.engine.is_canonical_qlib_initialized", return_value=True), \
                patch("src.core.walk_forward.engine.get_canonical_qlib_config",
                      return_value=SimpleNamespace(provider_uri=str(provider))):
            with pytest.raises(WalkForwardError, match="quarantin"):
                WalkForwardEngine.run(cfg)
    assert not output.exists()
