from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from batch_executor import entity_hash, execute_batch_preview
from batch_planner import build_composition_plan


def line(entity_id: str, piece_id: str, points: list[list[float]], line_role: str = "pattern_boundary") -> dict:
    return {"entity_id": entity_id, "piece_id": piece_id, "line_role": line_role, "geometry": {"points": points}}


def closed_line(entity_id: str, piece_id: str, points: list[list[float]], line_role: str = "pattern_boundary") -> dict:
    if points[0] != points[-1]:
        points = points + [points[0]]
    return line(entity_id, piece_id, points, line_role)


class BatchExecutorTests(unittest.TestCase):
    def test_neckline_changes_only_mutable_edge_entities_and_preserves_unknown_yoke(self) -> None:
        base_ir = {
            "case_id": "SYN001",
            "piece_instances": [
                {"piece_id": "front", "piece_role": "front_body"},
                {"piece_id": "back", "piece_role": "back_body"},
                {"piece_id": "yoke", "piece_role": "unknown"},
            ],
            "edge_chains": [
                {"edge_chain_id": "front-neck", "piece_id": "front", "edge_role": "neckline", "ordered_entity_ids": ["front-neck"]},
                {"edge_chain_id": "back-neck", "piece_id": "back", "edge_role": "neckline", "ordered_entity_ids": ["back-neck"]},
            ],
            "atomic_entities": [
                line("front-neck", "front", [[0, 0], [50, 0], [100, 0]], "neckline"),
                line("front-side", "front", [[100, 0], [100, 100]], "side_seam"),
                line("back-neck", "back", [[0, 0], [100, 0]], "neckline"),
                line("yoke-a", "yoke", [[0, 10], [100, 10]], "construction"),
            ],
        }
        recipe = {"execution_mode": "batch_preview", "selections": {"neckline": "tshirt.neckline.v-neck"}, "base_option_ids": {"neckline": "tshirt.neckline.crew"}}
        plan = build_composition_plan(recipe, base_ir)
        before_yoke = entity_hash(base_ir["atomic_entities"][-1])
        entities, results = execute_batch_preview(base_ir, recipe, plan)
        by_id = {entity["entity_id"]: entity for entity in entities}
        self.assertNotEqual([[0, 0], [50, 0], [100, 0]], by_id["front-neck"]["geometry"]["points"])
        self.assertEqual(before_yoke, entity_hash(by_id["yoke-a"]))
        self.assertEqual("applied", results[0].status)
        self.assertTrue(results[0].review_required)
        self.assertIn("edge_transfer", results[0].provenance)
        self.assertEqual({"front_neckline", "back_neckline"}, set(results[0].provenance["edge_transfer"]["host_roles"]))
        self.assertIn("closure", results[0].provenance)
        self.assertIn(results[0].provenance["closure"]["status"], {"closed", "open_review_required"})
        self.assertIn("yoke-a", results[0].protected_entity_hashes)

    def test_missing_sleeve_roles_retains_current_component_without_global_failure(self) -> None:
        base_ir = {"case_id": "SYN002", "piece_instances": [], "edge_chains": [], "atomic_entities": [line("body", "front", [[0, 0], [1, 1]])]}
        recipe = {"execution_mode": "batch_preview", "selections": {"sleeve": "shirt.sleeve.puff"}, "base_option_ids": {"sleeve": "shirt.sleeve.regular"}}
        plan = build_composition_plan(recipe, base_ir)
        entities, results = execute_batch_preview(base_ir, recipe, plan)
        self.assertEqual(base_ir["atomic_entities"], entities)
        self.assertEqual("retained_current", results[0].status)
        self.assertEqual("missing_required_edge_roles", results[0].validation_issues[0].code)

    def test_tshirt_puff_sleeve_applies_only_with_armhole_adaptation_recorded(self) -> None:
        base_ir = {
            "case_id": "SYN003",
            "piece_instances": [
                {"piece_id": "front", "piece_role": "front_body"},
                {"piece_id": "back", "piece_role": "back_body"},
                {"piece_id": "sleeve_l", "piece_role": "sleeve_left"},
                {"piece_id": "sleeve_r", "piece_role": "sleeve_right"},
            ],
            "edge_chains": [
                {"edge_chain_id": "front-arm", "piece_id": "front", "edge_role": "armhole_front", "ordered_entity_ids": ["front-arm"]},
                {"edge_chain_id": "back-arm", "piece_id": "back", "edge_role": "armhole_back", "ordered_entity_ids": ["back-arm"]},
                {"edge_chain_id": "sl-cap", "piece_id": "sleeve_l", "edge_role": "sleeve_cap", "ordered_entity_ids": ["sl"]},
                {"edge_chain_id": "sl-hem", "piece_id": "sleeve_l", "edge_role": "sleeve_hem", "ordered_entity_ids": ["sl"]},
            ],
            "atomic_entities": [
                closed_line("front-body", "front", [[0, 0], [120, 0], [120, 180], [0, 180]], "pattern_boundary"),
                line("front-arm", "front", [[120, 20], [95, 85], [120, 150]], "armhole_front"),
                line("front-shoulder", "front", [[55, 0], [120, 20]], "shoulder_line"),
                closed_line("back-body", "back", [[160, 0], [280, 0], [280, 180], [160, 180]], "pattern_boundary"),
                line("back-arm", "back", [[160, 20], [190, 90], [160, 150]], "armhole_back"),
                line("back-shoulder", "back", [[160, 20], [225, 0]], "shoulder_line"),
                closed_line("old-sleeve-l", "sleeve_l", [[0, 0], [60, -20], [120, 0], [100, 120], [20, 120]], "pattern_boundary"),
                closed_line("old-sleeve-r", "sleeve_r", [[0, 0], [60, -20], [120, 0], [100, 120], [20, 120]], "pattern_boundary"),
            ],
        }
        donor_ir = {
            "case_id": "DONOR_PUFF",
            "design_semantics_extra": {"part_labels": {"sleeve_style": {"slug": "puff"}}},
            "piece_instances": [{"piece_id": "d_sleeve", "piece_role": "sleeve"}],
            "atomic_entities": [closed_line("donor-sleeve", "d_sleeve", [[0, 0], [80, -15], [160, 0], [140, 130], [20, 130]], "pattern_boundary")],
        }
        recipe = {
            "execution_mode": "batch_preview",
            "family": "tshirt",
            "measurements_cm": {"sleeveLength": 58, "upperArm": 28},
            "selections": {"sleeve": "tshirt.sleeve.puff"},
            "base_option_ids": {"sleeve": "tshirt.sleeve.set-in"},
        }
        plan = build_composition_plan(recipe, base_ir)
        entities, results = execute_batch_preview(base_ir, recipe, plan, donor_index={"DONOR_PUFF": donor_ir})
        result = results[0]
        self.assertEqual("applied", result.status)
        self.assertEqual("R3", result.provenance["edge_transfer"]["rule_id"])
        self.assertEqual("applied", result.provenance["edge_transfer"]["armhole_adaptation"]["status"])
        self.assertEqual("experiment_remix_bodyA_sleeveB", result.provenance["edge_transfer"]["mode"])
        self.assertGreaterEqual(result.provenance["edge_transfer"].get("preview_outline_count", 0), 1)
        by_id = {entity["entity_id"]: entity for entity in entities}
        self.assertIn("front-shoulder", by_id)
        self.assertIn("back-shoulder", by_id)
        # Host body armholes stay; donor sleeve caps are morphed to match them.
        self.assertTrue(any(str(eid).startswith("DONOR_PUFF:") for eid in result.modified_entity_ids))


if __name__ == "__main__":
    unittest.main()
