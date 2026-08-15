from __future__ import annotations

import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

import app


class ExportReviewLedgerTests(unittest.TestCase):
    def test_batch_preview_export_contains_review_ledger_manifest(self) -> None:
        client = TestClient(app.app)
        recipe = {
            "family": "tshirt",
            "sex": "female",
            "base_case_id": "C2590529",
            "measurements_cm": {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28},
            "ease_cm": 8,
            "material_id": "tshirt.fabric.cotton",
            "fabric_color": "#eee7dc",
            "selections": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "special": None},
            "base_option_ids": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in"},
            "execution_mode": "batch_preview",
        }
        response = client.post("/export", json={"project_name": "ledger-test", "recipe": recipe, "design": {}})
        self.assertEqual(200, response.status_code, response.text)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn("review-ledger.json", archive.namelist())
            ledger = json.loads(archive.read("review-ledger.json"))
            self.assertEqual("chi27.review-ledger.simple-piece-swap.v1", ledger["schema"])
            self.assertIn("operations", ledger)


if __name__ == "__main__":
    unittest.main()
