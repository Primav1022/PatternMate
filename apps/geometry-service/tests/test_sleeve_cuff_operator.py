from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from batch_executor import execute_batch_preview
from batch_planner import build_composition_plan


def line(entity_id: str, piece_id: str, points: list[list[float]], line_role: str = "pattern_boundary") -> dict:
    return {"entity_id": entity_id, "piece_id": piece_id, "line_role": line_role, "geometry": {"points": points}}


def chain(edge_chain_id: str, piece_id: str, role: str, ids: list[str]) -> dict:
    return {"edge_chain_id": edge_chain_id, "piece_id": piece_id, "edge_role": role, "ordered_entity_ids": ids}


class SleeveCuffOperatorTests(unittest.TestCase):
    def sleeve_base(self) -> dict:
        return {
            "case_id": "SLEEVEBASE",
            "piece_instances": [
                {"piece_id": "front", "piece_role": "front_body"},
                {"piece_id": "back", "piece_role": "back_body"},
                {"piece_id": "sleeve", "piece_role": "sleeve"},
                {"piece_id": "cuff", "piece_role": "cuff"},
            ],
            "edge_chains": [
                chain("af", "front", "armhole_front", ["front-armhole"]),
                chain("ab", "back", "armhole_back", ["back-armhole"]),
                chain("sc", "sleeve", "sleeve_cap", ["sleeve-cap"]),
                chain("su", "sleeve", "underarm", ["sleeve-underarm"]),
                chain("sh", "sleeve", "sleeve_hem", ["sleeve-hem"]),
                chain("ca", "cuff", "cuff_attach_line", ["cuff-attach"]),
                chain("co", "cuff", "cuff_edge", ["cuff-outer"]),
            ],
            "atomic_entities": [
                line("front-armhole", "front", [[0, 0], [40, 60]], "armhole_front"),
                line("back-armhole", "back", [[40, 60], [80, 0]], "armhole_back"),
                line("sleeve-cap", "sleeve", [[0, 0], [40, 42], [80, 0]], "sleeve_cap"),
                line("sleeve-underarm", "sleeve", [[0, 0], [0, 120], [80, 120], [80, 0]], "sleeve_underarm_seam"),
                line("sleeve-hem", "sleeve", [[0, 120], [80, 120]], "sleeve_hem"),
                line("cuff-attach", "cuff", [[0, 0], [80, 0]], "cuff_attach_line"),
                line("cuff-outer", "cuff", [[0, 20], [80, 20]], "cuff_edge"),
                line("grain", "sleeve", [[40, 10], [40, 110]], "grainline"),
            ],
        }

    def test_sleeve_operator_retains_open_single_sleeve_even_when_roles_exist(self) -> None:
        base = self.sleeve_base()
        recipe = {"execution_mode": "batch_preview", "selections": {"sleeve": "tshirt.sleeve.puff"}, "base_option_ids": {"sleeve": "tshirt.sleeve.set-in"}}
        plan = build_composition_plan(recipe, base)
        entities, results = execute_batch_preview(base, recipe, plan)
        result = results[0]
        self.assertEqual(base["atomic_entities"], entities)
        self.assertEqual("retained_current", result.status)
        self.assertEqual("paired_component_incomplete", result.validation_issues[0].code)

    def test_cuff_operator_retains_single_cuff_even_when_roles_exist(self) -> None:
        base = self.sleeve_base()
        recipe = {"execution_mode": "batch_preview", "selections": {"cuff": "shirt.cuff.gathered"}, "base_option_ids": {"cuff": "shirt.cuff.regular"}}
        plan = build_composition_plan(recipe, base)
        entities, results = execute_batch_preview(base, recipe, plan)
        result = results[0]
        self.assertEqual(base["atomic_entities"], entities)
        self.assertEqual("retained_current", result.status)
        self.assertEqual("paired_component_incomplete", result.validation_issues[0].code)
        self.assertEqual("cuff", result.group)
        self.assertIn("topology_gate", result.provenance)


if __name__ == "__main__":
    unittest.main()

class SleeveCuffValidationGateTests(unittest.TestCase):
    def test_single_sleeve_component_is_retained_current_after_validation(self) -> None:
        base = SleeveCuffOperatorTests().sleeve_base()
        recipe = {"execution_mode": "batch_preview", "selections": {"sleeve": "tshirt.sleeve.puff"}, "base_option_ids": {"sleeve": "tshirt.sleeve.set-in"}}
        plan = build_composition_plan(recipe, base)
        entities, results = execute_batch_preview(base, recipe, plan)
        self.assertEqual(base["atomic_entities"], entities)
        self.assertEqual("retained_current", results[0].status)
        self.assertEqual("paired_component_incomplete", results[0].validation_issues[0].code)

    def test_cuff_without_two_closed_sides_is_retained_current_after_validation(self) -> None:
        base = SleeveCuffOperatorTests().sleeve_base()
        recipe = {"execution_mode": "batch_preview", "selections": {"cuff": "shirt.cuff.gathered"}, "base_option_ids": {"cuff": "shirt.cuff.regular"}}
        plan = build_composition_plan(recipe, base)
        entities, results = execute_batch_preview(base, recipe, plan)
        self.assertEqual(base["atomic_entities"], entities)
        self.assertEqual("retained_current", results[0].status)
        self.assertEqual("paired_component_incomplete", results[0].validation_issues[0].code)
