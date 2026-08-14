from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from component_index import build_component_index, extract_edge_chain_bundle


def atom(eid: str, pid: str, pts: list[list[float]], role: str = "neckline") -> dict:
    return {"entity_id": eid, "piece_id": pid, "line_role": role, "geometry": {"points": pts}}


class ComponentIndexTests(unittest.TestCase):
    def test_extracts_front_and_back_neckline_bundles_by_canonical_role(self) -> None:
        ir = {
            "piece_instances": [
                {"piece_id": "front", "piece_role": "front_body"},
                {"piece_id": "back", "piece_role": "back_body"},
            ],
            "edge_chains": [
                {"edge_chain_id": "fn", "piece_id": "front", "edge_role": "front_neckline", "ordered_entity_ids": ["f1", "f2"]},
                {"edge_chain_id": "bn", "piece_id": "back", "edge_role": "back_neckline", "ordered_entity_ids": ["b1"]},
            ],
            "atomic_entities": [
                atom("f1", "front", [[0, 0], [50, 20]]),
                atom("f2", "front", [[50, 20], [100, 0]]),
                atom("b1", "back", [[0, 0], [100, 5]]),
            ],
        }
        index = build_component_index(ir)
        front = extract_edge_chain_bundle(index, "front_neckline")
        back = extract_edge_chain_bundle(index, "back_neckline")
        self.assertEqual(["f1", "f2"], [e["entity_id"] for e in front.entities])
        self.assertEqual("front_body", front.piece_role)
        self.assertEqual(["b1"], [e["entity_id"] for e in back.entities])
        self.assertEqual("back_body", back.piece_role)


if __name__ == "__main__":
    unittest.main()
