from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))

from preview_outline import build_closed_preview_outline


class PreviewOutlineTests(unittest.TestCase):
    def test_builds_closed_outline_from_fragmented_sleeve_entities(self) -> None:
        entities = [
            {"entity_id": "a", "piece_id": "sleeve", "_piece_role": "sleeve", "line_role": "sleeve_cap", "geometry": {"points": [[0, 0], [50, -20], [100, 0]]}},
            {"entity_id": "b", "piece_id": "sleeve", "_piece_role": "sleeve", "line_role": "sleeve_underarm", "geometry": {"points": [[0, 0], [0, 120], [100, 120], [100, 0]]}},
        ]
        outline = build_closed_preview_outline(entities, piece_role="sleeve", entity_id="preview")
        points = outline["geometry"]["points"]
        self.assertEqual(points[0], points[-1])
        self.assertEqual("pattern_boundary", outline["line_role"])
        self.assertEqual("sleeve", outline["_piece_role"])
        self.assertEqual("closed_preview_outline", outline["_transfer_mode"])
        self.assertGreaterEqual(len(points), 4)

    def test_preview_keeps_one_grainline(self) -> None:
        from composition_engine import filter_preview_entities

        cut = {
            "entity_id": "c", "piece_id": "tie", "_piece_role": "neck_binding", "piece_role": "neck_binding",
            "line_role": "cut", "geometry": {"points": [[0, 0], [100, 0], [100, 40], [0, 40], [0, 0]]},
        }
        grains = [
            {
                "entity_id": f"g{i}", "piece_id": "tie", "_piece_role": "neck_binding", "piece_role": "neck_binding",
                "line_role": "grainline", "geometry": {"points": [[5, i * 2], [95, i * 2]]},
            }
            for i in range(20)
        ]
        out = filter_preview_entities([cut, *grains])
        self.assertEqual(sum(1 for row in out if row["line_role"] == "grainline"), 1)
        self.assertTrue(any(row["line_role"] == "cut" for row in out))

    def test_preview_drops_internal_construction(self) -> None:
        from composition_engine import filter_preview_entities

        cut = {
            "entity_id": "c", "piece_id": "front", "_piece_role": "front_body", "piece_role": "front_body",
            "line_role": "cut", "geometry": {"points": [[0, 0], [100, 0], [100, 200], [0, 200], [0, 0]]},
        }
        junk = {
            "entity_id": "i", "piece_id": "front", "_piece_role": "front_body", "piece_role": "front_body",
            "line_role": "internal", "geometry": {"points": [[10, 180], [40, 160], [20, 140]]},
        }
        out = filter_preview_entities([cut, junk])
        self.assertTrue(any(row["line_role"] == "cut" for row in out))
        self.assertFalse(any(row["line_role"] == "internal" for row in out))

    def test_preview_keeps_center_front_slash(self) -> None:
        from composition_engine import filter_preview_entities
        from dxf_closed_cuts import opening_line_role

        cut_pts = [[0, 0], [100, 0], [100, 200], [0, 200], [0, 0]]
        slash = [[50, 190], [50, 40]]
        self.assertEqual("center_front", opening_line_role(slash, cut_pts))
        cut = {
            "entity_id": "c", "piece_id": "front", "_piece_role": "front_body", "piece_role": "front_body",
            "line_role": "cut", "geometry": {"points": cut_pts},
        }
        opening = {
            "entity_id": "cf", "piece_id": "front", "_piece_role": "front_body", "piece_role": "front_body",
            "line_role": "center_front", "geometry": {"points": slash},
        }
        out = filter_preview_entities([cut, opening])
        self.assertTrue(any(row["line_role"] == "center_front" for row in out))


if __name__ == "__main__":
    unittest.main()
