from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))

from simple_compose import (  # noqa: E402
    _annotate,
    _group_by_piece,
    _keep_largest_clusters,
    _role,
    bounds_of_entities,
)


def _ent(eid: str, pid: str, role: str, line_role: str, pts: list[list[float]]) -> dict:
    return {
        "entity_id": eid,
        "piece_id": pid,
        "_piece_role": role,
        "piece_role": role,
        "line_role": line_role,
        "geometry": {"points": pts},
    }


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


class PruneSleevePanelsTests(unittest.TestCase):
    def test_drops_unknown_slab_keeps_armhole_sleeve(self) -> None:
        # C2490481-like: huge unknown nest slab + a real short sleeve.
        body = [
            _ent("f", "front", "front_body", "pattern_boundary", _rect(0, 0, 800, 650)),
            _ent("b", "back", "back_body", "pattern_boundary", _rect(0, 0, 800, 650)),
        ]
        junk = [_ent("j", "sleeve:01", "sleeve", "unknown", _rect(0, 0, 840, 230))]
        hem_only = [
            _ent("h1", "sleeve:06", "sleeve", "unknown", _rect(0, 0, 380, 260)),
            _ent("h2", "sleeve:06", "sleeve", "sleeve_hem", [[0, 0], [380, 0]]),
        ]
        real = [
            _ent("s1", "sleeve:11", "sleeve", "armhole_seam", _rect(100, 100, 250, 295)),
            _ent("s2", "sleeve:11", "sleeve", "sleeve_hem", [[100, 100], [250, 100]]),
        ]
        spec = [_ent("t", "sleeve:18", "sleeve", "pattern_boundary", _rect(0, 0, 20, 80))]
        out = _keep_largest_clusters(body + junk + hem_only + real + spec)
        sleeves = [row for row in out if _role(row) == "sleeve"]
        pids = set(_group_by_piece(sleeves))
        self.assertEqual(pids, {"sleeve:11"})
        box = bounds_of_entities(sleeves)
        self.assertLess(box[2] - box[0], 400)

    def test_keeps_left_right_pair(self) -> None:
        body = [_ent("f", "front", "front_body", "pattern_boundary", _rect(0, 0, 400, 500))]
        left = [_ent("l", "sleeve_l", "sleeve", "armhole_seam", _rect(0, 0, 160, 200))]
        right = [_ent("r", "sleeve_r", "sleeve", "armhole_seam", _rect(400, 0, 560, 200))]
        out = _keep_largest_clusters(body + left + right)
        pids = set(_group_by_piece([row for row in out if _role(row) == "sleeve"]))
        self.assertEqual(pids, {"sleeve_l", "sleeve_r"})

    def test_grading_keeps_sleeve_panel(self) -> None:
        from composition_engine import filter_preview_entities, _normalize_physical_components
        from simple_compose import _clamp_insane_roles, _fit_relative_to_host

        body = [
            _ent("f", "front", "front_body", "pattern_boundary", _rect(0, 0, 800, 650)),
            _ent("b", "back", "back_body", "pattern_boundary", _rect(0, 0, 800, 650)),
        ]
        sleeve = [
            _ent("s1", "sleeve:11", "sleeve", "unknown", _rect(100, 100, 250, 295)),
            _ent("s2", "sleeve:11", "sleeve", "armhole_seam", [[100, 295], [140, 200], [250, 295]]),
            _ent("s3", "sleeve:11", "sleeve", "sleeve_hem", [[100, 100], [250, 100]]),
        ]
        entities = filter_preview_entities(body + sleeve)
        entities = _keep_largest_clusters(entities)
        entities = _clamp_insane_roles(entities)
        entities = _normalize_physical_components(entities)
        entities, _ = _fit_relative_to_host(
            entities,
            {"width": 1.02, "length": 1.04, "sleeve_width": 0.87, "sleeve_length": 0.87, "neck": 1.0},
            "regular",
            host_ref=body + sleeve,
        )
        sleeves = [row for row in entities if _role(row) == "sleeve"]
        box = bounds_of_entities(sleeves)
        self.assertIsNotNone(box)
        w, h = box[2] - box[0], box[3] - box[1]
        self.assertGreater(min(w, h), 80)
        self.assertLess(max(w, h) / min(w, h), 4.0)

    def test_original_c2490481_keeps_real_sleeve(self) -> None:
        from composition_engine import compose_recipe, build_index, pattern_catalog

        index = build_index(
            ROOT / "data" / "ir" / "v1_rule_ready",
            ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir",
            ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir",
        )
        catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", index)
        entities, meta = compose_recipe(
            {
                "family": "tshirt",
                "sex": "female",
                "base_case_id": "C2490481",
                "measurements_cm": {
                    "height": 160, "chest": 85, "waist": 60, "shoulder": 38,
                    "neck": 32, "sleeveLength": 50.5, "upperArm": 25,
                },
                "selections": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "special": None},
                "base_option_ids": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in"},
            },
            index,
            catalog,
        )
        sleeves = [row for row in entities if _role(row) == "sleeve"]
        pids = set(_group_by_piece(sleeves))
        self.assertGreaterEqual(len(pids), 1)
        self.assertLessEqual(len(pids), 2)
        box = bounds_of_entities(sleeves)
        w, h = box[2] - box[0], box[3] - box[1]
        self.assertGreater(min(w, h), 80)
        self.assertLess(max(w, h) / min(w, h), 3.5)
        fit = ((meta.get("sources") or {}).get("sizing") or {}).get("sleeve_armhole_fit") or {}
        self.assertEqual(fit.get("mode"), "knit_cap_to_armhole")

    def test_c2490478_recovers_back_and_real_sleeve(self) -> None:
        from composition_engine import compose_recipe, build_index, pattern_catalog, FRONT_ROLES, BACK_ROLES, PURE_SLEEVE_ROLES

        index = build_index(
            ROOT / "data" / "ir" / "v1_rule_ready",
            ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir",
            ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir",
        )
        catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", index)
        entities, _ = compose_recipe(
            {
                "family": "tshirt",
                "sex": "female",
                "base_case_id": "C2490478",
                "measurements_cm": {
                    "height": 160, "chest": 85, "waist": 60, "shoulder": 38,
                    "neck": 32, "sleeveLength": 50.5, "upperArm": 25,
                },
                "selections": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "special": None},
                "base_option_ids": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in"},
            },
            index,
            catalog,
        )
        roles = {_role(row) for row in entities}
        self.assertTrue(roles & FRONT_ROLES)
        self.assertTrue(roles & BACK_ROLES)
        self.assertTrue(roles & PURE_SLEEVE_ROLES)
        front = bounds_of_entities([row for row in entities if _role(row) in FRONT_ROLES])
        back = bounds_of_entities([row for row in entities if _role(row) in BACK_ROLES])
        sleeve = bounds_of_entities([row for row in entities if _role(row) in PURE_SLEEVE_ROLES])
        fh, fw = front[3] - front[1], front[2] - front[0]
        sh, sw = sleeve[3] - sleeve[1], sleeve[2] - sleeve[0]
        self.assertLess(sh / fh, 0.75)
        self.assertLess(abs((back[3] - back[1]) - fh) / fh, 0.2)
        self.assertLess(max(sw, sh) / min(sw, sh), 3.5)

    def test_c2490654_keeps_armhole_not_hull(self) -> None:
        ir = json.loads((ROOT / "data/ir/tshirt_v2/pattern_ir/C2490654.pattern-ir.json").read_text(encoding="utf-8"))
        entities = _annotate(ir)
        front = [row for row in entities if _role(row) == "front_body"]
        self.assertTrue(front)
        self.assertGreaterEqual(len(front), 8)
        self.assertFalse(any(row.get("_transfer_mode") == "closed_preview_outline" for row in front))
        line_roles = {str(row.get("line_role")) for row in front}
        self.assertTrue({"front_neckline", "neckline"} & line_roles)
        self.assertIn("hem_line", line_roles)


if __name__ == "__main__":
    unittest.main()
