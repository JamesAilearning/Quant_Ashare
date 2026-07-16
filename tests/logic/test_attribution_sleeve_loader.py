"""Unit tests for the CSI800 sleeve-grouping loader ((b) Step 3).

Coverage matrix (>=1 case per dimension):
  as-of resolution — span containing / preceding / following the date;
                     re-entry (multiple spans per instrument); open end.
  disjointness     — an instrument in both sleeves fails loud.
  fail-loud        — missing file, malformed row, reversed span,
                     bad as_of, empty sleeve (as-of outside coverage).
  engine handshake — the resolution feeds AttributionConfig's
                     industry_map_override/industry_taxonomy_id verbatim.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.core.attribution_sleeve_loader import (  # noqa: E402
    SLEEVE_CSI300,
    SLEEVE_CSI500,
    SLEEVE_TAXONOMY_ID,
    SleeveResolutionError,
    resolve_sleeve_map,
)


def _bundle(tmp: Path, csi300: str, csi500: str) -> Path:
    inst = tmp / "instruments"
    inst.mkdir(parents=True)
    (inst / "csi300.txt").write_text(csi300, encoding="utf-8")
    (inst / "csi500.txt").write_text(csi500, encoding="utf-8")
    return tmp


_CSI300 = (
    "SH600000\t2016-01-29\t2099-12-31\n"       # open-ended span
    "SH600004\t2018-12-28\t2021-06-01\n"       # closed span
    "SH600011\t2016-01-29\t2017-01-26\n"       # re-entry: two spans
    "SH600011\t2020-06-15\t2099-12-31\n"
)
_CSI500 = (
    "SZ000006\t2016-01-29\t2099-12-31\n"
    "SZ000008\t2019-01-02\t2020-12-31\n"
)


def test_as_of_resolution_spans_and_reentry():
    with tempfile.TemporaryDirectory() as t:
        root = _bundle(Path(t), _CSI300, _CSI500)
        r = resolve_sleeve_map(root, "2019-06-03")
        assert r.taxonomy_id == SLEEVE_TAXONOMY_ID
        assert r.as_of == "2019-06-03"
        # open-ended + in-window closed span in; re-entry gap out.
        assert r.sleeve_map["SH600000"] == SLEEVE_CSI300
        assert r.sleeve_map["SH600004"] == SLEEVE_CSI300
        assert "SH600011" not in r.sleeve_map     # between its two spans
        assert r.sleeve_map["SZ000006"] == SLEEVE_CSI500
        assert r.sleeve_map["SZ000008"] == SLEEVE_CSI500
        assert (r.n_csi300, r.n_csi500) == (2, 2)
        # after re-entry the second span picks the instrument back up.
        r2 = resolve_sleeve_map(root, "2020-06-15")
        assert r2.sleeve_map["SH600011"] == SLEEVE_CSI300


def test_as_of_beyond_coverage_fails_loud_despite_open_ended_rows():
    # codex #366 r1 P1: 2099-12-31 is the resolver's synthetic "active at
    # the last snapshot" marker — an as_of past the last REAL membership
    # change must be refused even though the open-ended rows would
    # happily "match", not silently resolved from stale composition.
    # codex #366 r2 P1: the bound is PER SLEEVE (files can be re-resolved
    # separately via --indices) — csi300's last change here is
    # 2021-06-01 but csi500's is 2020-12-31, so the STALER sleeve binds.
    with tempfile.TemporaryDirectory() as t:
        root = _bundle(Path(t), _CSI300, _CSI500)
        # past every sleeve's bound:
        with pytest.raises(SleeveResolutionError, match="beyond"):
            resolve_sleeve_map(root, "2021-06-02")
        # within csi300's bound but past csi500's -> refused, NAMING the
        # stale sleeve and both per-sleeve bounds.
        with pytest.raises(SleeveResolutionError,
                           match="csi500_sleeve.*2020-12-31"):
            resolve_sleeve_map(root, "2021-05-01")
        # the binding (min) bound itself still resolves, stamped as
        # provenance.
        r = resolve_sleeve_map(root, "2020-12-31")
        assert r.coverage_end == "2020-12-31"
        assert r.sleeve_map["SH600000"] == SLEEVE_CSI300
        assert r.sleeve_map["SH600011"] == SLEEVE_CSI300  # open-ended span


def test_both_sleeves_membership_fails_loud():
    overlap = _CSI500 + "SH600000\t2018-01-02\t2099-12-31\n"
    with tempfile.TemporaryDirectory() as t:
        root = _bundle(Path(t), _CSI300, overlap)
        with pytest.raises(SleeveResolutionError, match="BOTH"):
            resolve_sleeve_map(root, "2019-06-03")


def test_missing_file_fails_loud():
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        (root / "instruments").mkdir()
        (root / "instruments" / "csi300.txt").write_text(
            _CSI300, encoding="utf-8")
        with pytest.raises(SleeveResolutionError, match="missing"):
            resolve_sleeve_map(root, "2019-06-03")


def test_malformed_row_and_reversed_span_fail_loud():
    with tempfile.TemporaryDirectory() as t:
        root = _bundle(Path(t), "SH600000 2016-01-29 2099-12-31\n", _CSI500)
        with pytest.raises(SleeveResolutionError, match="INSTRUMENT"):
            resolve_sleeve_map(root, "2019-06-03")
    with tempfile.TemporaryDirectory() as t:
        root = _bundle(
            Path(t), "SH600000\t2020-01-02\t2019-01-02\n", _CSI500)
        with pytest.raises(SleeveResolutionError, match="start.*> end"):
            resolve_sleeve_map(root, "2019-06-03")


def test_bad_as_of_and_empty_sleeve_fail_loud():
    with tempfile.TemporaryDirectory() as t:
        root = _bundle(Path(t), _CSI300, _CSI500)
        with pytest.raises(SleeveResolutionError, match="ISO date"):
            resolve_sleeve_map(root, "20190603")
        # 2015: before every span start -> empty csi300 sleeve.
        with pytest.raises(SleeveResolutionError, match="no csi300_sleeve"):
            resolve_sleeve_map(root, "2015-01-05")


def test_resolution_feeds_attribution_config_verbatim():
    # The whole point of the loader: the engine's existing override
    # interface accepts the sleeve grouping with no engine change.
    from src.core.performance_attribution import AttributionConfig
    with tempfile.TemporaryDirectory() as t:
        root = _bundle(Path(t), _CSI300, _CSI500)
        r = resolve_sleeve_map(root, "2019-06-03")
        cfg = AttributionConfig(
            industry_map_override=r.sleeve_map,
            industry_taxonomy_id=r.taxonomy_id,
        )
        assert cfg.industry_map_override is r.sleeve_map
        assert cfg.industry_taxonomy_id == SLEEVE_TAXONOMY_ID
