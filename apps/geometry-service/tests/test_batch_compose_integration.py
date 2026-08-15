from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from composition_engine import build_index, compose_recipe, pattern_catalog


class BatchComposeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = build_index(
            ROOT / "data" / "ir" / "v1_rule_ready",
            ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir",
            ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir",
        )
        cls.catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", cls.index)

    def test_tshirt_compose_uses_simple_piece_swap(self) -> None:
        recipe = {
            "family": "tshirt",
            "sex": "female",
            "base_case_id": "C2590529",
            "measurements_cm": {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28},
            "selections": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "special": None},
            "base_option_ids": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in"},
            "execution_mode": "batch_preview",
        }
        entities, meta = compose_recipe(recipe, self.index, self.catalog)
        self.assertTrue(entities)
        self.assertEqual("simple_piece_swap", meta["execution_mode"])
        self.assertEqual("valid", meta["status"])
        self.assertTrue(meta["validation"]["trial_ready"])
        self.assertIn("component_results", meta)
        self.assertIn("base", meta["sources"])

    def test_garment_length_selection_does_not_fail_compose(self) -> None:
        recipe = {
            "family": "tshirt",
            "sex": "female",
            "base_case_id": "C2590529",
            "measurements_cm": {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28},
            "selections": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.long"},
            "base_option_ids": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.regular"},
            "execution_mode": "batch_preview",
        }
        _, meta = compose_recipe(recipe, self.index, self.catalog)
        self.assertEqual("valid", meta["status"])
        self.assertTrue(any(row["group"] == "garment_length" for row in meta["component_results"]))
        length_result = next(row for row in meta["component_results"] if row["group"] == "garment_length")
        self.assertIn(length_result["status"], {"applied", "retained_current"})


if __name__ == "__main__":
    unittest.main()
