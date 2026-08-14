from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from review_ledger import append_review_decision, read_review_history
import app


class ReviewLedgerTests(unittest.TestCase):
    def test_append_preserves_atomic_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = append_review_decision(root, recipe_hash="abc123", operation_id="op:sleeve", decision="human_accepted", reviewer="r1")
            second = append_review_decision(root, recipe_hash="abc123", operation_id="op:cuff", decision="human_rejected", reviewer="r2", note="接口需调整")
            history = read_review_history(root, "abc123")
        self.assertEqual([first, second], history)
        self.assertEqual("human_rejected", history[1]["decision"])
        self.assertEqual("接口需调整", history[1]["note"])

    def test_review_decision_endpoint_records_history(self) -> None:
        client = TestClient(app.app)
        payload = {
            "recipe_hash": "endpoint-test-hash",
            "operation_id": "op:neckline",
            "decision": "human_modified",
            "reviewer": "paper-reviewer",
            "note": "领口曲线已人工微调",
            "geometry_hash_before": "before",
            "geometry_hash_after": "after",
        }
        response = client.post("/review-decisions", json=payload)
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual("recorded", body["status"])
        self.assertGreaterEqual(body["history_count"], 1)
        self.assertEqual("human_modified", body["record"]["decision"])


if __name__ == "__main__":
    unittest.main()
