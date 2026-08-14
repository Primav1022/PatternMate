from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))

from shirt_sleeve_fit import fit_sleeves_to_armholes, infer_armholes  # noqa: E402


def _closed(entity_id: str, piece_id: str, role: str, points: list[list[float]], line_role: str = "pattern_boundary") -> dict:
    pts = [p[:] for p in points]
    if pts[0] != pts[-1]:
        pts.append(pts[0][:])
    return {
        "entity_id": entity_id,
        "piece_id": piece_id,
        "_piece_role": role,
        "piece_role": role,
        "line_role": line_role,
        "geometry": {"points": pts},
    }


def _lerp(a: float, b: float, k: int, m: int) -> float:
    return a + (b - a) * k / m


def _bodice() -> list[list[float]]:
    """Y-up bodice with inset shoulders so armholes have width + depth."""
    corners = [
        (8.0, 0.0),
        (92.0, 0.0),
        (100.0, 70.0),
        (86.0, 100.0),
        (62.0, 100.0),
        (38.0, 100.0),
        (14.0, 100.0),
        (0.0, 70.0),
    ]
    pts: list[list[float]] = []
    n = 6
    for i, (x0, y0) in enumerate(corners):
        x1, y1 = corners[(i + 1) % len(corners)]
        for k in range(n):
            pts.append([_lerp(x0, x1, k, n), _lerp(y0, y1, k, n)])
    pts.append(list(corners[0]))
    return pts


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


class ShirtSleeveArmholeFitTests(unittest.TestCase):
    def test_infers_front_and_back_armholes(self) -> None:
        body = _bodice()
        entities = [
            _closed("f", "front", "front_body", body),
            _closed("b", "back", "back_body", body),
        ]
        ah = infer_armholes(entities)
        self.assertGreater(ah["front_arc"], 25.0)
        self.assertGreater(ah["back_arc"], 25.0)
        self.assertGreater(ah["target_width"], 40.0)
        self.assertGreater(ah["target_cap_h"], 15.0)

    def test_scales_wide_sleeve_toward_armhole_width(self) -> None:
        body = _bodice()
        entities = [
            _closed("f", "front", "front_body", body),
            _closed("b", "back", "back_body", body),
            _closed("s", "sleeve", "sleeve", _rect(200, 0, 380, 160), line_role="cut_line"),
        ]
        before = 180.0
        out, meta = fit_sleeves_to_armholes(entities)
        self.assertTrue(meta["applied"])
        sleeve = [row for row in out if row.get("piece_id") == "sleeve"]
        xs = [p[0] for row in sleeve for p in row["geometry"]["points"]]
        width = max(xs) - min(xs)
        self.assertLess(width, before * 0.85)
        self.assertGreater(width, meta["target_width"] * 0.7)
        self.assertLess(abs(width - meta["target_width"]), 40.0)


if __name__ == "__main__":
    unittest.main()
