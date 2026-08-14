from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from composition_engine import build_index, compose_recipe, pattern_catalog

FIXTURE_PATH = ROOT / "apps" / "geometry-service" / "tests" / "fixtures" / "batch_recipes.json"


def all_coordinates(entities: list[dict]) -> list[float]:
    values: list[float] = []
    for entity in entities:
        for point in (entity.get("geometry") or {}).get("points") or []:
            values.extend(float(value) for value in point)
    return values


class BatchEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = build_index(
            ROOT / "data" / "ir" / "v1_rule_ready",
            ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir",
            ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir",
        )
        cls.catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", cls.index)
        cls.fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixed_batch_recipes_are_reviewable_and_finite(self) -> None:
        self.assertGreaterEqual(len(self.fixtures), 4)
        for fixture in self.fixtures:
            with self.subTest(fixture=fixture["fixture_id"]):
                recipe = {
                    "family": fixture["family"],
                    "sex": "female",
                    "base_case_id": fixture["base_case_id"],
                    "measurements_cm": fixture["measurements_cm"],
                    "selections": fixture["selections"],
                    "base_option_ids": fixture["base_option_ids"],
                    "execution_mode": "batch_preview",
                }
                entities, meta = compose_recipe(recipe, self.index, self.catalog)
                coords = all_coordinates(entities)
                self.assertTrue(coords)
                self.assertTrue(all(math.isfinite(value) for value in coords))
                self.assertIn(meta["status"], fixture["allowed_statuses"])
                self.assertEqual("batch_preview", meta["execution_mode"])
                self.assertIn("batch_plan", meta)
                self.assertIn("component_results", meta)
                self.assertTrue(meta["review_required"])
                self.assertEqual("chi27.review-ledger.edge-role-batch.v1", meta["review_ledger"]["schema"])

                operations = meta["batch_plan"]["operations"]
                self.assertLessEqual(max((op["max_donors"] for op in operations), default=0), 3)
                self.assertLessEqual(max((op["max_repair_rounds"] for op in operations), default=0), 3)
                self.assertEqual(fixture["expected_requested_groups"], [op["group"] for op in operations])

                by_group = {row["group"]: row for row in meta["component_results"]}
                for group, allowed_statuses in fixture.get("expected_component_statuses", {}).items():
                    self.assertIn(group, by_group)
                    self.assertIn(by_group[group]["status"], set(allowed_statuses))
                    self.assertLessEqual(len(by_group[group]["provenance"].get("donor_candidates", [])), 3)
                    if by_group[group]["status"] == "applied":
                        self.assertTrue(by_group[group]["modified_entity_ids"])
                        self.assertIn("edge_transfer", by_group[group]["provenance"])

                warning_text = "\n".join(meta["validation"].get("warnings") or [])
                for expected in fixture.get("expected_warning_substrings", []):
                    self.assertIn(expected, warning_text)


if __name__ == "__main__":
    unittest.main()
