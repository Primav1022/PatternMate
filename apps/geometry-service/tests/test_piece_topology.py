from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from piece_topology import validate_closed_pieces, validate_garment_inventory, validate_paired_component


def ent(entity_id: str, piece_id: str, points: list[list[float]], line_role: str = "pattern_boundary", piece_role: str | None = None) -> dict:
    row = {"entity_id": entity_id, "piece_id": piece_id, "line_role": line_role, "geometry": {"points": points}}
    if piece_role:
        row["_piece_role"] = piece_role
    return row


def rect(piece_id: str, piece_role: str, x: float, y: float, w: float = 100, h: float = 80) -> list[dict]:
    return [
        ent(f"{piece_id}-a", piece_id, [[x, y], [x + w, y]], piece_role=piece_role),
        ent(f"{piece_id}-b", piece_id, [[x + w, y], [x + w, y + h]], piece_role=piece_role),
        ent(f"{piece_id}-c", piece_id, [[x + w, y + h], [x, y + h]], piece_role=piece_role),
        ent(f"{piece_id}-d", piece_id, [[x, y + h], [x, y]], piece_role=piece_role),
    ]


class PieceTopologyTests(unittest.TestCase):
    def test_open_boundary_fails_piece_closure(self) -> None:
        entities = [
            ent("a", "front", [[0, 0], [100, 0]], piece_role="front_body"),
            ent("b", "front", [[100, 0], [100, 100]], piece_role="front_body"),
        ]
        report = validate_closed_pieces(entities)
        self.assertFalse(report["valid"])
        self.assertEqual(2, report["pieces"]["front"]["open_endpoint_count"])

    def test_closed_ring_passes_even_when_split_into_edges(self) -> None:
        report = validate_closed_pieces(rect("front", "front_body", 0, 0))
        self.assertTrue(report["valid"])
        self.assertEqual(1, report["pieces"]["front"]["closed_loop_count"])

    def test_primary_cut_line_closure_is_not_broken_by_duplicate_semantic_edges(self) -> None:
        entities = [
            ent("cut-a", "front", [[0, 0], [100, 0]], "cut_line", "front_body"),
            ent("cut-b", "front", [[100, 0], [100, 100]], "cut_line", "front_body"),
            ent("cut-c", "front", [[100, 100], [0, 100]], "cut_line", "front_body"),
            ent("cut-d", "front", [[0, 100], [0, 0]], "cut_line", "front_body"),
            ent("neck", "front", [[20, 0], [50, 20], [80, 0]], "neckline", "front_body"),
            ent("arm", "front", [[100, 25], [85, 50], [100, 75]], "armhole_front", "front_body"),
            ent("shoulder", "front", [[80, 0], [100, 25]], "shoulder_line", "front_body"),
        ]
        report = validate_closed_pieces(entities)
        self.assertTrue(report["valid"])
        self.assertEqual(0, report["pieces"]["front"]["open_endpoint_count"])

    def test_tshirt_inventory_requires_front_back_and_two_closed_sleeves(self) -> None:
        one_sleeve = rect("front", "front_body", 0, 0) + rect("back", "back_body", 150, 0) + rect("sleeve_l", "sleeve", 300, 0)
        report = validate_garment_inventory(one_sleeve, "tshirt")
        self.assertFalse(report["valid"])
        self.assertEqual({"min": 2, "max": 2}, report["missing_or_invalid"]["sleeve"]["expected"])

    def test_tshirt_inventory_passes_with_two_closed_sleeves(self) -> None:
        entities = rect("front", "front_body", 0, 0) + rect("back", "back_body", 150, 0) + rect("sleeve_l", "sleeve", 300, 0) + rect("sleeve_r", "sleeve", 450, 0)
        report = validate_garment_inventory(entities, "tshirt")
        self.assertTrue(report["valid"])
        self.assertEqual(2, report["counts"]["sleeve"])

    def test_review_layer_sleeve_does_not_count_as_production_inventory(self) -> None:
        entities = rect("front", "front_body", 0, 0) + rect("back", "back_body", 150, 0) + rect("sleeve_l", "sleeve_left", 300, 0) + rect("sleeve_r", "sleeve_right", 450, 0)
        review = ent("audit", "donor_sleeve", [[0, 0], [10, 0], [10, 10], [0, 0]], "pattern_boundary", "sleeve")
        review["_review_layer"] = "AI4M_REVIEW_RETAINED"
        report = validate_garment_inventory(entities + [review], "tshirt")
        self.assertTrue(report["valid"])
        self.assertEqual(2, report["counts"]["sleeve"])

    def test_paired_component_requires_both_sides_closed(self) -> None:
        entities = rect("sleeve_l", "sleeve", 0, 0) + [ent("loose", "sleeve_r", [[0, 0], [50, 0]], "sleeve_hem", "sleeve")]
        report = validate_paired_component(entities, role="sleeve")
        self.assertFalse(report["valid"])
        self.assertEqual("paired_component_incomplete", report["code"])


if __name__ == "__main__":
    unittest.main()
