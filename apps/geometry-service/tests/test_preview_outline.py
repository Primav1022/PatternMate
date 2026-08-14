from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

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


if __name__ == "__main__":
    unittest.main()
