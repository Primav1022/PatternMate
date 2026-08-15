from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from composition_engine import build_index, compose_recipe, grading_profile, pattern_catalog, resolve_execution_mode


class BatchBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = build_index(
            ROOT / "data" / "ir" / "v1_rule_ready",
            ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir",
            ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir",
        )
        cls.catalog = pattern_catalog(
            ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json",
            cls.index,
        )

    def test_family_lock_ignores_legacy_and_batch_flags(self) -> None:
        self.assertEqual("simple_piece_swap", resolve_execution_mode({"family": "tshirt", "execution_mode": "legacy"}))
        self.assertEqual("simple_piece_swap", resolve_execution_mode({"family": "tshirt", "execution_mode": "batch_preview"}))
        self.assertEqual("shirt_strategy", resolve_execution_mode({"family": "shirt", "execution_mode": "batch_preview"}))
        self.assertEqual(
            "batch_preview",
            resolve_execution_mode({"family": "shirt", "execution_mode": "batch_preview", "sandbox_compare": True}),
        )

    def test_tshirt_compose_remains_finite(self) -> None:
        recipe = {
            "family": "tshirt",
            "sex": "female",
            "base_case_id": "C2590529",
            "measurements_cm": {
                "height": 160, "chest": 84, "waist": 68, "shoulder": 39,
                "neck": 34, "sleeveLength": 58, "upperArm": 28,
            },
            "selections": {
                "neckline": "tshirt.neckline.crew",
                "sleeve": "tshirt.sleeve.set-in",
                "special": None,
            },
            "base_option_ids": {
                "neckline": "tshirt.neckline.crew",
                "sleeve": "tshirt.sleeve.set-in",
            },
            "execution_mode": "legacy",
        }
        entities, meta = compose_recipe(recipe, self.index, self.catalog)
        points = [point for entity in entities for point in (entity.get("geometry") or {}).get("points", [])]
        self.assertTrue(points)
        self.assertTrue(all(math.isfinite(float(value)) for point in points for value in point))
        self.assertEqual("C2590529", meta["sources"]["base"])
        self.assertEqual("simple_piece_swap", meta["execution_mode"])

    def test_male_grading_maps_female_prototype_first(self) -> None:
        female_base = {
            "sex": "female",
            "measurements_cm": {"height": 160, "chest": 84, "waist": 68, "shoulder": 38.88, "neck": 34, "sleeveLength": 58, "upperArm": 28},
        }
        male_proto = {
            "sex": "male_general",
            "measurements_cm": {"height": 175, "chest": 92, "waist": 78, "shoulder": 44, "neck": 39, "sleeveLength": 60, "upperArm": 32},
        }
        female = grading_profile(female_base)
        male = grading_profile(male_proto)
        self.assertFalse((female.get("prototype") or {}).get("applied"))
        self.assertTrue(male["prototype"]["applied"])
        self.assertEqual("female_to_male_prototype", male["prototype"]["mode"])
        self.assertGreater(float(male["width"]), float(female["width"]))
        self.assertGreater(float(male["length"]), float(female["length"]))
        self.assertAlmostEqual(float(male["grade_width"]), 1.0, delta=0.08)
        self.assertAlmostEqual(float(male["prototype"]["length"]), 175.0 / 160.0, places=4)

    def test_male_compose_is_larger_than_female_on_same_block(self) -> None:
        shared = {
            "family": "tshirt",
            "base_case_id": "C2590529",
            "selections": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "special": None},
            "base_option_ids": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in"},
        }
        female_recipe = {
            **shared,
            "sex": "female",
            "measurements_cm": {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28},
        }
        male_recipe = {
            **shared,
            "sex": "male_general",
            "measurements_cm": {"height": 175, "chest": 92, "waist": 78, "shoulder": 44, "neck": 39, "sleeveLength": 60, "upperArm": 32},
        }
        female_entities, female_meta = compose_recipe(female_recipe, self.index, self.catalog)
        male_entities, male_meta = compose_recipe(male_recipe, self.index, self.catalog)
        female_w = max(piece["width_mm"] for piece in female_meta["pieces"] if str(piece.get("role") or "").startswith("front"))
        male_w = max(piece["width_mm"] for piece in male_meta["pieces"] if str(piece.get("role") or "").startswith("front"))
        self.assertGreater(male_w, female_w)
        self.assertTrue(male_meta["sources"]["sizing"]["prototype"]["applied"])
        self.assertTrue(all(
            math.isfinite(float(value))
            for entity in male_entities
            for point in (entity.get("geometry") or {}).get("points", [])
            for value in point
        ))


if __name__ == "__main__":
    unittest.main()
