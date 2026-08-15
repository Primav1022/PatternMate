from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from shirt_side_seam import morph_body_side_seams  # noqa: E402
from shirt_strategy import COLLAR_SWAP_ROLES, swap_plan  # noqa: E402


def _closed(entity_id: str, piece_id: str, role: str, points: list[list[float]]) -> dict:
    pts = [p[:] for p in points]
    if pts[0] != pts[-1]:
        pts.append(pts[0][:])
    return {
        "entity_id": entity_id,
        "piece_id": piece_id,
        "_piece_role": role,
        "line_role": "pattern_boundary",
        "geometry": {"points": pts},
    }


def _rect(x0: float, y0: float, x1: float, y1: float, n: int = 6) -> list[list[float]]:
    """Closed-ish rectangle, denser on vertical sides. Y-up, hem at y0."""
    def lerp(a, b, k, m):
        return a + (b - a) * k / m

    pts = []
    for i in range(n):
        pts.append([x0, lerp(y0, y1, i, n - 1)])
    for i in range(1, n):
        pts.append([lerp(x0, x1, i, n - 1), y1])
    for i in range(1, n):
        pts.append([x1, lerp(y1, y0, i, n - 1)])
    for i in range(1, n - 1):
        pts.append([lerp(x1, x0, i, n - 1), y0])
    pts.append([x0, y0])
    return pts


def _aline(x_top0: float, x_top1: float, x_hem0: float, x_hem1: float, y0: float, y1: float, n: int = 8) -> list[list[float]]:
    def lerp(a, b, k, m):
        return a + (b - a) * k / m

    pts = []
    for i in range(n):
        t = i / (n - 1)
        pts.append([lerp(x_hem0, x_top0, t, 1), lerp(y0, y1, t, 1)])
    for i in range(1, n):
        pts.append([lerp(x_top0, x_top1, i, n - 1), y1])
    for i in range(1, n):
        t = i / (n - 1)
        pts.append([lerp(x_top1, x_hem1, t, 1), lerp(y1, y0, t, 1)])
    for i in range(1, n - 1):
        pts.append([lerp(x_hem1, x_hem0, i, n - 1), y0])
    pts.append([x_hem0, y0])
    return pts


def _width_at(pts: list[list[float]], y: float, tol: float = 8.0) -> float:
    xs = [p[0] for p in pts if abs(p[1] - y) <= tol]
    if len(xs) < 2:
        return 0.0
    return max(xs) - min(xs)


class ShirtSideSeamTests(unittest.TestCase):
    def test_collar_and_placket_swap_whole_body(self) -> None:
        collar = swap_plan("collar", "shirt.collar.pointed")
        placket = swap_plan("placket", "shirt.placket.full")
        self.assertEqual(collar["mode"], "piece_swap")
        self.assertEqual(collar["roles"], placket["roles"])
        self.assertTrue({"front_body", "back_body"} <= COLLAR_SWAP_ROLES)
        self.assertIn("front_placket", COLLAR_SWAP_ROLES)
        self.assertIn("collar", COLLAR_SWAP_ROLES)

    def test_silhouette_plan_is_side_seam_morph(self) -> None:
        plan = swap_plan("silhouette", "shirt.silhouette.a-line")
        self.assertEqual(plan["mode"], "side_seam_morph")
        self.assertIn("front_body", plan["roles"])
        self.assertNotIn("back_yoke", plan["roles"])

    def test_h_host_picks_up_aline_flare(self) -> None:
        host = _closed("h", "front", "front_body", _rect(0, 0, 100, 200, n=10))
        donor = _closed("a", "front", "front_body", _aline(20, 80, 0, 100, 0, 200, n=10))
        extra = {
            "entity_id": "collar-keep",
            "piece_id": "collar",
            "_piece_role": "collar",
            "line_role": "cut_line",
            "geometry": {"points": [[0, 0], [10, 0]]},
        }
        out, meta = morph_body_side_seams([host, extra], [donor])
        self.assertTrue(meta["applied"])
        body = next(entity for entity in out if entity["entity_id"] == "h")
        pts = body["geometry"]["points"]
        hem_w = _width_at(pts, 0, tol=12)
        chest_w = _width_at(pts, 140, tol=20)
        self.assertGreater(hem_w, chest_w + 8)
        self.assertEqual(out[1]["geometry"], extra["geometry"])


class BodyStructureGradeTests(unittest.TestCase):
    def test_chest_lets_out_hem_not_shoulder(self) -> None:
        from shirt_side_seam import grade_body_structure

        armhole = {
            "entity_id": "ah",
            "piece_id": "front",
            "_piece_role": "front_body",
            "line_role": "armhole_front",
            "geometry": {"points": [[0, 140], [8, 170], [0, 185]]},
        }
        host = _closed("h", "front", "front_body", _rect(0, 0, 100, 200, n=10))
        out, meta = grade_body_structure([host, armhole], width_sx=1.2, length_sy=1.0, neck_s=1.0)
        self.assertTrue(meta["applied"])
        pts = next(e for e in out if e["entity_id"] == "h")["geometry"]["points"]
        self.assertAlmostEqual(_width_at(pts, 200, tol=6), 100, delta=2)
        self.assertGreater(_width_at(pts, 0, tol=6), 115)
        self.assertEqual(out[1]["geometry"], armhole["geometry"])

    def test_length_grows_below_chest_only(self) -> None:
        from shirt_side_seam import grade_body_structure

        host = _closed("h", "front", "front_body", _rect(0, 0, 100, 200, n=10))
        src = host["geometry"]["points"]
        out, _ = grade_body_structure([host], width_sx=1.0, length_sy=1.2, neck_s=1.0)
        pts = out[0]["geometry"]["points"]
        self.assertAlmostEqual(max(p[1] for p in pts), max(p[1] for p in src), delta=0.5)
        self.assertLess(min(p[1] for p in pts), min(p[1] for p in src) - 8)

    def test_neck_does_not_share_chest_width(self) -> None:
        from shirt_side_seam import grade_body_structure

        host = _closed("h", "front", "front_body", _rect(0, 0, 100, 200, n=10))
        src = host["geometry"]["points"]
        out, _ = grade_body_structure([host], width_sx=1.0, length_sy=1.0, neck_s=1.25)
        pts = out[0]["geometry"]["points"]
        self.assertAlmostEqual(_width_at(pts, 0, tol=6), 100, delta=2)
        src_inner = [p[0] for p in src if abs(p[1] - 200) <= 6 and abs(p[0] - 50) <= 32]
        out_inner = [p[0] for p in pts if abs(p[1] - 200) <= 6 and 2 < p[0] < 98]
        self.assertGreater(max(out_inner) - min(out_inner), max(src_inner) - min(src_inner) + 4)


if __name__ == "__main__":
    unittest.main()
