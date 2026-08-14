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

    def test_batch_preview_returns_reviewable_component_results(self) -> None:
        recipe = {
            "family": "tshirt",
            "sex": "female",
            "base_case_id": "C2590529",
            "measurements_cm": {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28},
            "selections": {"neckline": "tshirt.neckline.v-neck", "sleeve": "tshirt.sleeve.puff", "special": None},
            "base_option_ids": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in"},
            "execution_mode": "batch_preview",
        }
        entities, meta = compose_recipe(recipe, self.index, self.catalog)
        self.assertTrue(entities)
        self.assertEqual("batch_preview", meta["execution_mode"])
        self.assertEqual("valid", meta["status"])
        self.assertTrue(meta["validation"]["trial_ready"])
        self.assertIn("batch_plan", meta)
        self.assertIn("component_results", meta)
        self.assertTrue(meta["review_required"])
        self.assertIn("base", meta["sources"])
        sleeve = next(row for row in meta["component_results"] if row["group"] == "sleeve")
        self.assertEqual("applied", sleeve["status"])
        self.assertIn(
            sleeve["provenance"]["edge_transfer"]["mode"],
            {"donor_piece_bundle_preview", "parametric_tshirt_sleeve_pair_preview", "experiment_remix_bodyA_sleeveB"},
        )
        self.assertEqual("R3", sleeve["provenance"]["edge_transfer"]["rule_id"])
        self.assertEqual("applied", sleeve["provenance"]["edge_transfer"]["armhole_adaptation"]["status"])
        # target_bounds is optional in experiment remix path; prefer scale metadata
        et = sleeve["provenance"]["edge_transfer"]
        self.assertTrue(et.get("target_bounds") is not None or et.get("length_scale") is not None)
        neckline = next(row for row in meta["component_results"] if row["group"] == "neckline")
        self.assertLessEqual(len(neckline["provenance"].get("donor_candidates", [])), 3)
        self.assertTrue(neckline["provenance"].get("donor_candidates"))
        self.assertEqual({"interface", "topology", "proportion", "quality", "label_match"}, set(neckline["provenance"]["donor_candidates"][0]["breakdown"]))

    def test_garment_length_selection_is_planned_without_global_failure(self) -> None:
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
