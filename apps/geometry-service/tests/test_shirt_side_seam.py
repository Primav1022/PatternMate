from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))

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
        self.assertIn("side_panel", COLLAR_SWAP_ROLES)

    def test_silhouette_plan_is_side_seam_morph(self) -> None:
        plan = swap_plan("silhouette", "shirt.silhouette.a-line")
        self.assertEqual(plan["mode"], "side_seam_morph")
        self.assertIn("front_body", plan["roles"])
        self.assertIn("side_panel", plan["roles"])
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

    def test_split_donor_flares_both_front_and_back_sides(self) -> None:
        host_f = _closed("hf", "front", "front_body", _rect(0, 0, 200, 200, n=10))
        host_b = _closed("hb", "back", "back_body", _rect(400, 0, 600, 200, n=10))
        left = _closed("dl", "fl", "front_left", _aline(0, 80, 0, 110, 0, 200, n=10))
        right = _closed("dr", "fr", "front_right", _aline(20, 100, -10, 100, 0, 200, n=10))
        back = _closed("db", "bk", "back_body", _rect(200, 0, 360, 200, n=10))
        out, meta = morph_body_side_seams([host_f, host_b], [left, right, back])
        self.assertTrue(meta["applied"])
        front = next(entity for entity in out if entity["entity_id"] == "hf")["geometry"]["points"]
        rear = next(entity for entity in out if entity["entity_id"] == "hb")["geometry"]["points"]
        front_hem = [p[0] for p in front if abs(p[1] - 0) <= 12]
        back_hem = [p[0] for p in rear if abs(p[1] - 0) <= 12]
        self.assertLess(min(front_hem), -4)
        self.assertGreater(max(front_hem), 204)
        self.assertLess(min(back_hem), 396)
        self.assertGreater(max(back_hem), 604)

    def test_side_seam_morph_moves_sew_ring(self) -> None:
        host = _closed("h", "front", "front_body", _rect(0, 0, 100, 200, n=10))
        host["line_role"] = "cut"
        sew_pts = _rect(10, 10, 90, 190, n=10)
        sew = _closed("s", "front", "front_body", sew_pts)
        sew["line_role"] = "sew"
        donor = _closed("a", "front", "front_body", _aline(20, 80, 0, 100, 0, 200, n=10))
        donor["line_role"] = "cut"
        out, meta = morph_body_side_seams([host, sew], [donor])
        self.assertTrue(meta["applied"])
        self.assertNotEqual(next(e for e in out if e["entity_id"] == "s")["geometry"]["points"], sew["geometry"]["points"])
        self.assertNotEqual(next(e for e in out if e["entity_id"] == "h")["geometry"]["points"], host["geometry"]["points"])

    def test_drops_stale_second_cut_after_morph(self) -> None:
        host = _closed("h", "front", "front_body", _rect(0, 0, 100, 200, n=10))
        host["line_role"] = "cut"
        ghost = _closed("g", "front", "front_body", _rect(5, 5, 95, 195, n=10))
        ghost["line_role"] = "cut_line"
        donor = _closed("a", "front", "front_body", _aline(20, 80, 0, 100, 0, 200, n=10))
        out, meta = morph_body_side_seams([host, ghost], [donor])
        self.assertTrue(meta["applied"])
        cuts = [entity for entity in out if entity.get("piece_id") == "front"]
        self.assertEqual([entity["entity_id"] for entity in cuts], ["h"])
        self.assertGreater(_width_at(cuts[0]["geometry"]["points"], 0, tol=12), 100)


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
        self.assertGreater(_width_at(pts, 140, tol=12), 112)
        self.assertGreater(_width_at(pts, 0, tol=6), 115)
        self.assertLess(out[1]["geometry"]["points"][0][0], armhole["geometry"]["points"][0][0])

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

    def test_shoulder_grade_widens_top(self) -> None:
        from shirt_side_seam import grade_body_structure

        host = _closed("h", "front", "front_body", _rect(0, 0, 100, 200, n=10))
        out, meta = grade_body_structure([host], width_sx=1.0, length_sy=1.0, shoulder_s=1.18)
        self.assertTrue(meta["applied"])
        pts = out[0]["geometry"]["points"]
        self.assertGreater(_width_at(pts, 200, tol=6), 112)
        self.assertAlmostEqual(_width_at(pts, 0, tol=6), 100, delta=2)

    def test_graded_shoulder_stays_straight(self) -> None:
        from shirt_side_seam import _dist_to_seg, grade_body_structure

        def lerp(a, b, k, m):
            return a + (b - a) * k / m

        pts = []
        for i in range(8):
            pts.append([0.0, lerp(0, 90, i, 7)])
        pts += [[2.0, 105.0], [12.0, 118.0]]
        for i in range(8):
            pts.append([lerp(12, 42, i, 7), lerp(118, 132, i, 7)])
        for i in range(1, 6):
            pts.append([lerp(42, 78, i, 5), 128.0])
        for i in range(1, 8):
            pts.append([lerp(78, 108, i, 7), lerp(132, 118, i, 7)])
        pts += [[118.0, 105.0], [120.0, 90.0]]
        for i in range(1, 8):
            pts.append([120.0, lerp(90, 0, i, 7)])
        for i in range(1, 8):
            pts.append([lerp(120, 0, i, 7), 0.0])
        host = _closed("h", "front", "front_body", pts)
        src = host["geometry"]["points"]
        left = src[10:18]
        out, meta = grade_body_structure([host], width_sx=1.22, length_sy=1.0, shoulder_s=1.0, neck_s=1.12)
        self.assertTrue(meta["applied"])
        graded = out[0]["geometry"]["points"][10:18]
        bow = max(_dist_to_seg(p, graded[0], graded[-1]) for p in graded[1:-1])
        self.assertLess(bow, 0.6)
        src_bow = max(_dist_to_seg(p, left[0], left[-1]) for p in left[1:-1])
        self.assertLess(src_bow, 0.4)

    def test_armhole_grade_does_not_tear_outline(self) -> None:
        from shirt_side_seam import _hypot, grade_body_structure

        host = _closed("h", "front", "front_body", _rect(0, 0, 100, 200, n=10))
        src = host["geometry"]["points"]
        src_gaps = [_hypot(a, b) for a, b in zip(src, src[1:])]
        out, _ = grade_body_structure([host], width_sx=1.2, length_sy=1.0, armhole_s=1.2)
        pts = out[0]["geometry"]["points"]
        gaps = [_hypot(a, b) for a, b in zip(pts, pts[1:])]
        self.assertLess(max(gaps), max(src_gaps) * 2.5)

    def test_armhole_label_moves_with_grade(self) -> None:
        from shirt_side_seam import grade_body_structure

        armhole = {
            "entity_id": "ah",
            "piece_id": "front",
            "_piece_role": "front_body",
            "line_role": "armhole_front",
            "geometry": {"points": [[2, 150], [6, 170], [2, 185]]},
        }
        host = _closed("h", "front", "front_body", _rect(0, 0, 100, 200, n=10))
        out, _ = grade_body_structure([host, armhole], width_sx=1.2, length_sy=1.0, armhole_s=1.2)
        self.assertNotEqual(out[1]["geometry"]["points"], armhole["geometry"]["points"])

    def test_c2431239_large_chest_widens_bust_without_tearing_armhole(self) -> None:
        from compose_ir import entities_from_compose, load_compose
        from shirt_side_seam import _hypot, _line_role, _open_loop, _piece_role, _points, grade_body_structure

        doc = load_compose("C2431239")
        if doc is None:
            self.skipTest("missing C2431239 compose IR")
        ents = entities_from_compose(doc)
        cut = next(e for e in ents if _piece_role(e) == "front_body" and _line_role(e) == "cut")
        src = _open_loop(_points(cut))
        src_gaps = [_hypot(a, b) for a, b in zip(src, src[1:] + src[:1])]
        out, meta = grade_body_structure(ents, width_sx=1.22, length_sy=1.0, neck_s=1.06, shoulder_s=0.98, armhole_s=1.2)
        self.assertTrue(meta["applied"])
        graded = next(e for e in out if e.get("entity_id") == cut["entity_id"])
        loop = _open_loop(_points(graded))
        gaps = [_hypot(a, b) for a, b in zip(loop, loop[1:] + loop[:1])]
        grown = [g - s for s, g in zip(src_gaps, gaps)]
        self.assertLess(max(grown), 20)
        src_ys = [p[1] for p in src]
        chest_y = min(src_ys) + 0.58 * (max(src_ys) - min(src_ys))
        self.assertGreater(_width_at(loop, chest_y, tol=20), _width_at(src, chest_y, tol=20) + 40)

    def test_split_back_and_chest_regression_warnings(self) -> None:
        from compose_ir import entities_from_compose, load_compose
        from shirt_side_seam import shirt_body_sanity_warnings

        doc = load_compose("C2431105")
        ents = entities_from_compose(doc)
        split = shirt_body_sanity_warnings(None, ents, width_sx=1.0)
        self.assertTrue(any("分片中后片" in msg for msg in split))
        grown = shirt_body_sanity_warnings(ents, ents, width_sx=1.05)
        self.assertFalse(any("应变宽" in msg for msg in grown))
        crushed = []
        for entity in ents:
            if entity.get("_piece_role") == "back_body":
                pts = [[p[0] * 0.5, p[1]] for p in (entity.get("geometry") or {}).get("points") or []]
                crushed.append({**entity, "geometry": {**(entity.get("geometry") or {}), "points": pts}})
            else:
                crushed.append(entity)
        bad = shirt_body_sanity_warnings(ents, crushed, width_sx=1.05)
        self.assertTrue(any("应变宽" in msg for msg in bad))


if __name__ == "__main__":
    unittest.main()
