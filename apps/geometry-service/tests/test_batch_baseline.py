from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from composition_engine import build_index, compose_recipe, pattern_catalog


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

    def test_legacy_compose_remains_finite_during_migration(self) -> None:
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
        self.assertEqual({"base": "C2590529"}, meta["sources"])


if __name__ == "__main__":
    unittest.main()
