from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from composition_engine import build_index, compose_recipe, pattern_catalog


class RealSleeveCuffIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = build_index(
            ROOT / "data" / "ir" / "v1_rule_ready",
            ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir",
            ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir",
        )
        cls.catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", cls.index)

    def test_real_shirt_entities_without_piece_id_are_not_dropped(self) -> None:
        from composition_engine import _scale_complete_base, grading_profile
        ir = self.index["C2431105"]
        entities = _scale_complete_base(ir, grading_profile({"family": "shirt", "sex": "female", "measurements_cm": {}}))
        roles = {entity.get("_piece_role") for entity in entities}
        self.assertTrue(entities)
        self.assertIn("sleeve", roles)
        self.assertIn("cuff", roles)
        self.assertTrue(any(entity.get("entity_id") == "line_6300" for entity in entities))

    def test_real_shirt_sleeve_bundle_is_applied_but_flagged_for_topology_review(self) -> None:
        recipe = {
            "family": "shirt",
            "sex": "female",
            "base_case_id": "C2431105",
            "measurements_cm": {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28},
            "selections": {"collar": "shirt.collar.pointed", "sleeve": "shirt.sleeve.puff", "garment_length": "shirt.garment-length.regular", "cuff": "shirt.cuff.gathered"},
            "base_option_ids": {"collar": "shirt.collar.pointed", "sleeve": "shirt.sleeve.regular", "garment_length": "shirt.garment-length.regular", "cuff": "shirt.cuff.regular"},
            "execution_mode": "batch_preview",
        }
        _, meta = compose_recipe(recipe, self.index, self.catalog)
        self.assertEqual("valid", meta["status"])
        by_group = {row["group"]: row for row in meta["component_results"]}
        self.assertEqual("applied", by_group["sleeve"]["status"])
        self.assertIn(by_group["cuff"]["status"], {"applied", "retained_current"})
        self.assertIn(
            by_group["sleeve"]["provenance"]["edge_transfer"]["mode"],
            {"donor_piece_bundle_preview", "experiment_remix_bodyA_sleeveB"},
        )
        self.assertGreaterEqual(by_group["sleeve"]["provenance"]["edge_transfer"].get("preview_outline_count", 0), 1)
        if by_group["cuff"]["status"] == "applied":
            self.assertTrue(by_group["cuff"]["modified_entity_ids"])
            self.assertIn("edge_transfer", by_group["cuff"]["provenance"])
        else:
            self.assertIn("topology_gate", by_group["cuff"]["provenance"])


if __name__ == "__main__":
    unittest.main()
