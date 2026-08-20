from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))

from shirt_sleeve_fit import fit_knit_sleeves, fit_sleeves_to_armholes, infer_armholes  # noqa: E402


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

    def test_gathered_sleeve_keeps_fullness(self) -> None:
        body = _bodice()
        gather = [[200 + i, 150 + (4 if i % 2 else 0)] for i in range(80)]
        entities = [
            _closed("f", "front", "front_body", body),
            _closed("b", "back", "back_body", body),
            _closed("s", "sleeve", "sleeve", _rect(200, 0, 380, 160), line_role="cut_line"),
            {
                "entity_id": "g", "piece_id": "sleeve", "_piece_role": "sleeve", "piece_role": "sleeve",
                "line_role": "internal", "geometry": {"points": gather},
            },
        ]
        out, meta = fit_sleeves_to_armholes(entities)
        self.assertFalse(meta["applied"])
        self.assertEqual("gathered_sleeve", (meta.get("skipped") or [{}])[0].get("reason"))
        xs = [p[0] for row in out if row.get("piece_id") == "sleeve" for p in row["geometry"]["points"]]
        self.assertGreater(max(xs) - min(xs), 170)

    def test_sew_ring_does_not_shrink_inferred_armhole(self) -> None:
        body = _bodice()
        sew = [[p[0] * 0.55 + 22.5, p[1] * 0.55 + 22.5] for p in body]
        cut_only = infer_armholes([
            _closed("f", "front", "front_body", body, line_role="cut"),
            _closed("b", "back", "back_body", body, line_role="cut"),
        ])
        with_sew = infer_armholes([
            _closed("f", "front", "front_body", body, line_role="cut"),
            _closed("fs", "front", "front_body", sew, line_role="sew"),
            _closed("b", "back", "back_body", body, line_role="cut"),
            _closed("bs", "back", "back_body", sew, line_role="sew"),
        ])
        self.assertEqual(cut_only["front_arc"], with_sew["front_arc"])
        self.assertEqual(cut_only["back_arc"], with_sew["back_arc"])
        self.assertEqual(cut_only["target_width"], with_sew["target_width"])

    def test_uses_labeled_armhole_spans(self) -> None:
        body = _bodice()
        front_span = [[14.0, 100.0], [4.0, 85.0], [0.0, 70.0]]
        back_span = [[86.0, 100.0], [96.0, 85.0], [100.0, 70.0]]
        entities = [
            _closed("f", "front", "front_body", body, line_role="cut"),
            _closed("b", "back", "back_body", body, line_role="cut"),
            {
                "entity_id": "af",
                "piece_id": "front",
                "_piece_role": "front_body",
                "line_role": "armhole_front",
                "geometry": {"points": front_span},
            },
            {
                "entity_id": "ab",
                "piece_id": "back",
                "_piece_role": "back_body",
                "line_role": "armhole_back",
                "geometry": {"points": back_span},
            },
        ]
        ah = infer_armholes(entities)
        self.assertEqual(ah["source"], "labeled")
        expect = ((10.0 ** 2 + 15.0 ** 2) ** 0.5) + ((4.0 ** 2 + 15.0 ** 2) ** 0.5)
        self.assertAlmostEqual(ah["front_arc"], expect, delta=0.2)
        self.assertAlmostEqual(ah["back_arc"], ah["front_arc"], delta=0.2)

    def test_rejects_armhole_label_that_is_the_whole_cut(self) -> None:
        body = _bodice()
        entities = [
            _closed("f", "front", "front_body", body, line_role="cut"),
            _closed("b", "back", "back_body", body, line_role="cut"),
            {
                "entity_id": "af",
                "piece_id": "front",
                "_piece_role": "front_body",
                "line_role": "armhole_front",
                "geometry": {"points": body},
            },
            {
                "entity_id": "ab",
                "piece_id": "back",
                "_piece_role": "back_body",
                "line_role": "armhole_back",
                "geometry": {"points": body},
            },
        ]
        ah = infer_armholes(entities)
        self.assertEqual(ah["source"], "inferred")
        self.assertGreater(ah["front_arc"], 25.0)

    def test_knit_flattens_peaked_cap_and_keeps_length_below_bicep(self) -> None:
        body = _bodice()
        peaked = [
            [20.0, 0.0], [80.0, 0.0], [100.0, 40.0], [50.0, 130.0], [0.0, 40.0], [20.0, 0.0],
        ]
        entities = [
            _closed("f", "front", "front_body", body),
            _closed("b", "back", "back_body", body),
            _closed("s", "sleeve", "sleeve", peaked, line_role="cut_line"),
        ]
        out, meta = fit_knit_sleeves(entities, sleeve_sy=1.3, slug="regular")
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["mode"], "knit_cap_to_armhole")
        sleeve = next(row for row in out if row["piece_id"] == "sleeve")
        ys = [p[1] for p in sleeve["geometry"]["points"]]
        xs = [p[0] for p in sleeve["geometry"]["points"]]
        cap0, cap1 = meta["pieces"][0]["cap_h"]
        body0, body1 = meta["pieces"][0]["body_h"]
        self.assertLess(cap1, cap0 * 0.75)
        self.assertGreater(body1, body0)
        self.assertGreater(max(ys) - min(ys), 130.0 * 1.15)
        self.assertGreater(max(xs) - min(xs), 40.0)

    def test_knit_sleeve_sy_changes_total_length(self) -> None:
        body = _bodice()
        peaked = [
            [20.0, 0.0], [80.0, 0.0], [100.0, 40.0], [50.0, 130.0], [0.0, 40.0], [20.0, 0.0],
        ]
        short, _ = fit_knit_sleeves([
            _closed("f", "front", "front_body", body),
            _closed("b", "back", "back_body", body),
            _closed("s", "sleeve", "sleeve", peaked, line_role="cut_line"),
        ], sleeve_sy=0.8, slug="regular")
        long, _ = fit_knit_sleeves([
            _closed("f", "front", "front_body", body),
            _closed("b", "back", "back_body", body),
            _closed("s", "sleeve", "sleeve", peaked, line_role="cut_line"),
        ], sleeve_sy=1.3, slug="regular")
        def _h(rows):
            sleeve = next(row for row in rows if row["piece_id"] == "sleeve")
            ys = [p[1] for p in sleeve["geometry"]["points"]]
            return max(ys) - min(ys)
        self.assertGreater(_h(long), _h(short) * 1.2)

    def test_knit_skips_raglan(self) -> None:
        body = _bodice()
        entities = [
            _closed("f", "front", "front_body", body),
            _closed("s", "sleeve", "sleeve", _rect(0, 0, 80, 100), line_role="cut_line"),
        ]
        _, meta = fit_knit_sleeves(entities, slug="raglan")
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["reason"], "integrated_sleeve")

    def test_cuff_follows_sleeve_hem_width(self) -> None:
        from shirt_sleeve_fit import fit_cuffs_to_sleeves

        entities = [
            _closed("s", "sleeve", "sleeve", _rect(0, 0, 120, 200), line_role="cut"),
            _closed("c", "cuff", "cuff", _rect(200, 0, 260, 40), line_role="cut"),
        ]
        out, meta = fit_cuffs_to_sleeves(entities)
        self.assertTrue(meta["applied"])
        cuff = next(row for row in out if row["piece_id"] == "cuff")
        xs = [p[0] for p in cuff["geometry"]["points"]]
        self.assertGreater(max(xs) - min(xs), 90.0)


if __name__ == "__main__":
    unittest.main()
