from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from batch_executor import execute_batch_preview
from batch_planner import build_composition_plan
from donor_similarity import rank_donors


def chain(edge_chain_id: str, piece_id: str, edge_role: str, ids: list[str]) -> dict:
    return {"edge_chain_id": edge_chain_id, "piece_id": piece_id, "edge_role": edge_role, "ordered_entity_ids": ids}


def line(entity_id: str, piece_id: str, points: list[list[float]], line_role: str = "neckline") -> dict:
    return {"entity_id": entity_id, "piece_id": piece_id, "line_role": line_role, "geometry": {"points": points}}


class DonorSimilarityTests(unittest.TestCase):
    def base_ir(self) -> dict:
        return {
            "case_id": "BASE",
            "piece_instances": [
                {"piece_id": "front", "piece_role": "front_body"},
                {"piece_id": "back", "piece_role": "back_body"},
            ],
            "edge_chains": [
                chain("front-neck", "front", "neckline", ["front-neck"]),
                chain("back-neck", "back", "neckline", ["back-neck"]),
            ],
            "atomic_entities": [
                line("front-neck", "front", [[0, 0], [50, 0], [100, 0]]),
                line("back-neck", "back", [[0, 0], [100, 0]]),
            ],
        }

    def donor_ir(self, case_id: str, width: float, review: str = "approved", neckline_slug: str = "crew") -> dict:
        return {
            "case_id": case_id,
            "design_semantics_extra": {"part_labels": {"neckline": {"slug": neckline_slug}}},
            "piece_instances": [
                {"piece_id": "front", "piece_role": "front_body"},
                {"piece_id": "back", "piece_role": "back_body"},
            ],
            "edge_chains": [
                {**chain("front-neck", "front", "neckline", ["front-neck"]), "review": review},
                {**chain("back-neck", "back", "neckline", ["back-neck"]), "review": review},
            ],
            "atomic_entities": [
                line("front-neck", "front", [[0, 0], [width / 2, 0], [width, 0]]),
                line("back-neck", "back", [[0, 0], [width, 0]]),
            ],
        }

    def test_rank_donors_is_limited_and_explains_weighted_scores(self) -> None:
        donors = {
            "D1": self.donor_ir("D1", 102),
            "D2": self.donor_ir("D2", 140),
            "D3": self.donor_ir("D3", 92, review="unknown"),
            "D4": self.donor_ir("D4", 210),
        }
        rows = rank_donors("neckline", self.base_ir(), donors, max_donors=3)
        self.assertEqual(3, len(rows))
        self.assertEqual("D1", rows[0].case_id)
        self.assertGreater(rows[0].score, rows[1].score)
        for row in rows:
            self.assertEqual({"interface", "topology", "proportion", "quality", "label_match"}, set(row.breakdown))
            self.assertGreaterEqual(row.score, 0)
            self.assertLessEqual(row.score, 1.25)

    def test_rank_donors_prefers_matching_part_label_slug(self) -> None:
        donors = {
            "CREW_CLOSE": self.donor_ir("CREW_CLOSE", 102, neckline_slug="crew"),
            "VNECK_FAR": self.donor_ir("VNECK_FAR", 180, neckline_slug="v-neck"),
        }
        rows = rank_donors("neckline", self.base_ir(), donors, max_donors=2, target_option_id="tshirt.neckline.v-neck")
        self.assertEqual(["VNECK_FAR"], [row.case_id for row in rows])
        self.assertEqual(1.0, rows[0].breakdown["label_match"])

    def test_executor_records_donor_ranking_in_component_provenance(self) -> None:
        base = self.base_ir()
        recipe = {
            "execution_mode": "batch_preview",
            "selections": {"neckline": "tshirt.neckline.v-neck"},
            "base_option_ids": {"neckline": "tshirt.neckline.crew"},
        }
        plan = build_composition_plan(recipe, base)
        _, results = execute_batch_preview(base, recipe, plan, donor_index={"D1": self.donor_ir("D1", 102)})
        self.assertEqual("applied", results[0].status)
        self.assertEqual("D1", results[0].donor_case_id)
        self.assertIn("donor_candidates", results[0].provenance)
        self.assertEqual("D1", results[0].provenance["donor_candidates"][0]["case_id"])


if __name__ == "__main__":
    unittest.main()
