from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))

from composition_engine import remix_readiness  # noqa: E402
from relabel_queue import QUEUE, apply_labels, piece_outlines, svg_payload  # noqa: E402


class RelabelQueueTests(unittest.TestCase):
    def test_queue_has_the_ten_cases(self) -> None:
        ids = [row["case_id"] for row in QUEUE]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)
        for cid in ("C2490682", "C2590045", "C2590079", "C2590205", "C2590551", "C2590734", "C2490320", "C2490335", "C2490340", "C2590428"):
            self.assertIn(cid, ids)

    def test_outlines_and_flutter_readiness(self) -> None:
        path = ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir" / "C2490682.pattern-ir.json"
        ir = json.loads(path.read_text(encoding="utf-8"))
        pieces = piece_outlines(ir)
        self.assertGreaterEqual(len(pieces), 2)
        payload = svg_payload(pieces)
        self.assertTrue(payload["viewBox"])
        apply_labels(
            ir,
            piece_roles={row["piece_id"]: row["role"] for row in pieces if row["role"] != "unlabeled"},
            sleeve_style="flutter",
            notes="",
            reviewer="test",
        )
        ready, reasons = remix_readiness(ir)
        self.assertNotIn("missing_sleeve", reasons)
        self.assertTrue(ready or "missing_host_neckline" in reasons)

    def test_c2590045_uses_original_dxf_closed_cuts(self) -> None:
        ir = json.loads((ROOT / "data/ir/tshirt_v2/pattern_ir/C2590045.pattern-ir.json").read_text(encoding="utf-8"))
        pieces = piece_outlines(ir)
        self.assertTrue(all(row["piece_id"].startswith("dxf:") for row in pieces))
        self.assertTrue(all(row.get("closed") for row in pieces))
        names = {row.get("cad_name") for row in pieces}
        self.assertTrue(any("前片" in (name or "") for name in names))
        self.assertTrue(any("后片" in (name or "") for name in names))
        front = next(row for row in pieces if "前片" in (row.get("cad_name") or ""))
        self.assertGreaterEqual(front["width_mm"], 900)
        self.assertGreaterEqual(front["height_mm"], 700)
        apply_labels(
            ir,
            piece_roles={row["piece_id"]: row["role"] for row in pieces if row["role"] != "unlabeled"},
            sleeve_style="raglan",
            notes="",
            reviewer="test",
        )
        roles = {str(p.get("piece_role")) for p in ir.get("piece_instances") or [] if p.get("source") == "relabel_dxf"}
        self.assertIn("front_body", roles)
        self.assertIn("back_body", roles)
        self.assertIn("side_panel", roles)
        closed = [e for e in ir.get("atomic_entities") or [] if (e.get("geometry") or {}).get("closed")]
        self.assertGreaterEqual(len(closed), 4)

    def test_c2590734_recovers_back_piece_from_cleaned_dxf(self) -> None:
        ir = json.loads((ROOT / "data/ir/tshirt_v2/pattern_ir/C2590734.pattern-ir.json").read_text(encoding="utf-8"))
        pieces = piece_outlines(ir)
        names = {row.get("cad_name") or "" for row in pieces}
        self.assertTrue(any("前片" in name for name in names))
        self.assertTrue(any("后片" in name for name in names))
        back = next(row for row in pieces if "后片" in (row.get("cad_name") or ""))
        self.assertGreaterEqual(back["width_mm"], 650)
        self.assertGreaterEqual(back["height_mm"], 750)
        self.assertEqual(back["role"], "back_body")


if __name__ == "__main__":
    unittest.main()
