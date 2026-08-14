from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from batch_planner import build_composition_plan


class BatchPlannerTests(unittest.TestCase):
    def test_sleeve_plan_uses_armhole_as_host_context_not_mutation_target(self) -> None:
        recipe = {
            "execution_mode": "batch_preview",
            "selections": {"sleeve": "shirt.sleeve.puff"},
            "base_option_ids": {"sleeve": "shirt.sleeve.regular"},
        }
        plan = build_composition_plan(recipe, {"atomic_entities": []})
        sleeve = next(row for row in plan.operations if row.group == "sleeve")
        self.assertIn("armhole_front", sleeve.host_required_roles)
        self.assertIn("armhole_back", sleeve.host_required_roles)
        self.assertNotIn("armhole_front", sleeve.mutable_roles)
        self.assertIn("sleeve_cap", sleeve.mutable_roles)
        self.assertEqual(3, sleeve.max_donors)

    def test_cuff_depends_on_sleeve_when_both_are_selected(self) -> None:
        recipe = {
            "execution_mode": "batch_preview",
            "selections": {"sleeve": "shirt.sleeve.puff", "cuff": "shirt.cuff.gathered"},
            "base_option_ids": {"sleeve": "shirt.sleeve.regular", "cuff": "shirt.cuff.regular"},
        }
        plan = build_composition_plan(recipe, {"atomic_entities": []})
        cuff = next(row for row in plan.operations if row.group == "cuff")
        self.assertEqual(("op:sleeve",), cuff.depends_on)
        self.assertEqual(("cuff_attach", "cuff_outer"), cuff.mutable_roles)


if __name__ == "__main__":
    unittest.main()
