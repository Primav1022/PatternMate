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


if __name__ == "__main__":
    unittest.main()
