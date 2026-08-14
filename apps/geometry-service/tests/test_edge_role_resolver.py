from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from edge_role_resolver import resolve_edge_chains


class EdgeRoleResolverTests(unittest.TestCase):
    def test_unified_neckline_uses_piece_context(self) -> None:
        ir = {
            "piece_instances": [
                {"piece_id": "front", "piece_role": "front_body"},
                {"piece_id": "back", "piece_role": "back_body"},
            ],
            "edge_chains": [
                {"edge_chain_id": "ef", "piece_id": "front", "edge_role": "neckline", "ordered_entity_ids": ["f1"], "direction": "forward", "review": "approved"},
                {"edge_chain_id": "eb", "piece_id": "back", "edge_role": "neckline", "ordered_entity_ids": ["b1"], "direction": "forward", "review": "approved"},
            ],
        }
        rows = resolve_edge_chains(ir)
        self.assertEqual(["front_neckline", "back_neckline"], [row.canonical_role for row in rows])
        self.assertTrue(all(row.status == "resolved" for row in rows))

    def test_unknown_role_is_preserved_not_guessed(self) -> None:
        ir = {
            "piece_instances": [{"piece_id": "front", "piece_role": "front_body"}],
            "edge_chains": [{"edge_chain_id": "e1", "piece_id": "front", "edge_role": "unknown", "ordered_entity_ids": ["x"], "direction": "forward"}],
        }
        row = resolve_edge_chains(ir)[0]
        self.assertEqual("ambiguous", row.status)
        self.assertIsNone(row.canonical_role)


    def test_polluted_garment_hem_is_not_resolved(self) -> None:
        ir = {
            "piece_instances": [{"piece_id": "front", "piece_role": "front_body"}],
            "edge_chains": [{"edge_chain_id": "e1", "piece_id": "front", "edge_role": "hem_line", "ordered_entity_ids": ["arm"]}],
            "atomic_entities": [{"entity_id": "arm", "piece_id": "front", "line_role": "armhole_front", "geometry": {"points": [[0, 0], [1, 1]]}}],
        }
        row = resolve_edge_chains(ir)[0]
        self.assertEqual("ambiguous", row.status)
        self.assertIsNone(row.canonical_role)
        self.assertIn("polluted", row.provenance["reason"])


if __name__ == "__main__":
    unittest.main()
