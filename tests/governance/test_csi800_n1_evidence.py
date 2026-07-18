"""N1 baseline source-evidence integrity (codex #374 r8).

The 50% gross-collapse criterion of the cadence campaign reads its N1
baseline gross values ONLY from the committed source fold reports under
``docs/research/evidence/csi800_n1_folds/`` — never from an editable
document. This test closes the chain END-TO-END on a fresh checkout:
every committed source file must hash to the digest the committed #373
pair artifact (v2) pinned at pairing time. Both sides of the assertion
are committed files, so CI needs no ``output/`` run directories.

Byte fidelity: the evidence dir carries ``.gitattributes`` with
``*.json -text`` so line-ending normalization can never silently break
the hashes (any byte drift — including CRLF rewriting — turns this red).
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PAIR = _REPO / "docs/research/csi800_campaign_pair_report.json"
_EVIDENCE = _REPO / "docs/research/evidence/csi800_n1_folds"


class N1EvidenceIntegrityTests(unittest.TestCase):
    def test_gitattributes_pins_byte_fidelity(self) -> None:
        ga = _EVIDENCE / ".gitattributes"
        self.assertTrue(ga.is_file(), ".gitattributes missing")
        self.assertIn("*.json -text", ga.read_text(encoding="utf-8"))

    def test_every_committed_source_report_hashes_to_v2_pin(self) -> None:
        pair = json.loads(_PAIR.read_text(encoding="utf-8"))
        total = 0
        for side in ("base", "conservative"):
            pinned: dict[str, str] = pair[side]["fold_report_sha256"]
            self.assertEqual(
                len(pinned), pair[side]["num_folds"],
                f"{side}: pinned hash map does not cover every fold",
            )
            for idx_s, expected in sorted(pinned.items()):
                p = _EVIDENCE / side / f"fold_{int(idx_s):02d}_report.json"
                with self.subTest(side=side, fold=idx_s):
                    self.assertTrue(p.is_file(), f"missing source: {p}")
                    actual = hashlib.sha256(p.read_bytes()).hexdigest()
                    self.assertEqual(
                        actual, expected,
                        f"{p} bytes no longer hash to the v2-pinned "
                        "digest — the N1 baseline evidence was altered",
                    )
                total += 1
        self.assertEqual(total, 46)

    def test_gross_values_readable_from_verified_sources(self) -> None:
        # the exact read path the primary-criterion comparator uses:
        # gross annualized per fold from the hash-verified sources.
        for side in ("base", "conservative"):
            for p in sorted((_EVIDENCE / side).glob("fold_*_report.json")):
                payload = json.loads(p.read_text(encoding="utf-8"))
                gross = (payload["backtest"]["risk_analysis"]
                         ["excess_return_without_cost"]["annualized_return"])
                self.assertIsInstance(gross, float)


if __name__ == "__main__":
    unittest.main()
