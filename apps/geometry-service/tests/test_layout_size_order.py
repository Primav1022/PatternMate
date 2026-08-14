from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from composition_engine import _layout_complete  # noqa: E402
from geometry_ops import bounds_of_entities  # noqa: E402


def _box(role: str, piece_id: str, width: float, height: float) -> dict:
    return {
        "_source_case": "case",
        "_piece_role": role,
        "piece_id": piece_id,
        "geometry": {"points": [[0, 0], [width, 0], [width, height], [0, height]]},
    }


class LayoutSizeOrderTests(unittest.TestCase):
    def test_large_pieces_sit_above_small_after_svg_flip(self):
        laid = _layout_complete([
            _box("front_body", "body", 400, 500),
            _box("cuff", "cuff", 80, 60),
            _box("collar", "collar", 120, 50),
        ], gap=40)
        body = bounds_of_entities([row for row in laid if row["piece_id"] == "body"])
        cuff = bounds_of_entities([row for row in laid if row["piece_id"] == "cuff"])
        collar = bounds_of_entities([row for row in laid if row["piece_id"] == "collar"])
        assert body and cuff and collar
        # CAD y=0 is SVG bottom; larger CAD y is SVG top.
        self.assertGreater(body[1], cuff[3])
        self.assertGreater(body[1], collar[3])

    def test_tall_sleeve_stays_below_body(self):
        laid = _layout_complete([
            _box("front_body", "body", 400, 500),
            _box("sleeve", "sleeve", 220, 420),
        ], gap=40)
        body = bounds_of_entities([row for row in laid if row["piece_id"] == "body"])
        sleeve = bounds_of_entities([row for row in laid if row["piece_id"] == "sleeve"])
        assert body and sleeve
        self.assertGreater(body[1], sleeve[3])

    def test_front_and_back_share_a_row(self):
        laid = _layout_complete([
            _box("front_body", "front", 400, 500),
            _box("back_body", "back", 400, 500),
            _box("sleeve", "sleeve", 180, 220),
        ], gap=40)
        front = bounds_of_entities([row for row in laid if row["piece_id"] == "front"])
        back = bounds_of_entities([row for row in laid if row["piece_id"] == "back"])
        assert front and back
        self.assertLess(abs(front[1] - back[1]), 5)
        self.assertGreater(max(front[2], back[2]), 700)


if __name__ == "__main__":
    unittest.main()
