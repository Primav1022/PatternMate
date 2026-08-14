from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "tryon-service"))
os.environ["ENABLE_RESEARCH_3D"] = "true"

import app as tryon_app  # noqa: E402
from glb import descriptor_garment_surface_mesh, descriptor_panel_mesh, make_glb  # noqa: E402


class TryonJobTests(unittest.TestCase):
    def wait(self, job_id: str) -> dict:
        deadline = time.time() + 8
        while time.time() < deadline:
            result = tryon_app.get_job(job_id)
            if result["status"] in {"completed", "failed", "cancelled"}:
                return result
            time.sleep(.03)
        self.fail("job timed out")

    def test_avatar_generates_glb_and_physical_tryon_stays_gated(self) -> None:
        measurements = {"height": 160, "chest": 85, "waist": 60, "shoulder": 38, "upperArm": 25}
        avatar = tryon_app.avatar_job(tryon_app.AvatarJobRequest(measurements_cm=measurements))
        avatar_done = self.wait(avatar["job_id"])
        self.assertEqual("completed", avatar_done["status"])
        self.assertTrue(avatar_done["result_url"].endswith(".glb"))
        self.assertNotEqual({"height": 0.0}, avatar_done["metadata"].get("fit_metrics_cm"))
        descriptor = {
            "version": "patternmate.tryon.v1", "unit": "mm", "recipe_hash": "abc",
            "validation": {"tryon_ready": True, "errors": []},
            "panels": [{"panel_id": "front", "role": "front_body", "vertices_2d_mm": [[0, 0], [300, 0], [260, 500], [40, 500]], "triangles": [[0, 1, 2], [0, 2, 3]]}],
        }
        with self.assertRaises(tryon_app.HTTPException) as caught:
            tryon_app.tryon_job(tryon_app.TryonJobRequest(
                avatar_id=avatar["avatar_hash"], recipe_hash="abc", family="tshirt",
                measurements_cm=measurements, recipe={"selections": {"sleeve": "short"}},
                composition_descriptor=descriptor, quality="draft",
            ))
        self.assertEqual(503, caught.exception.status_code)
        preview = tryon_app.panel_preview_job(tryon_app.TryonJobRequest(
            avatar_id=avatar["avatar_hash"], recipe_hash="abc", family="tshirt",
            measurements_cm=measurements, recipe={"selections": {"sleeve": "short"}},
            composition_descriptor=descriptor, quality="draft",
        ))
        preview_done = self.wait(preview["job_id"])
        self.assertEqual("completed", preview_done["status"])
        self.assertEqual("single_layer_dxf_surface_preview", preview_done["metadata"]["solver"])
        self.assertFalse(preview_done["metadata"]["simulation_ready"])
        self.assertGreater((tryon_app.RESULT_DIR / f"{preview['job_id']}.glb").stat().st_size, 1000)

    def test_descriptor_makes_debug_and_open_garment_meshes(self) -> None:
        descriptor = {
            "version": "patternmate.tryon.v1", "unit": "mm",
            "panels": [{"panel_id": "front", "role": "front_body", "vertices_2d_mm": [[0, 0], [300, 0], [260, 500], [40, 500]], "triangles": [[0, 1, 2], [0, 2, 3]]}],
        }
        mesh = descriptor_panel_mesh(descriptor, {"height": 160, "chest": 85})
        self.assertEqual(4, len(mesh[0]))
        self.assertEqual(2, len(mesh[1]))
        self.assertGreater(len(make_glb([(mesh[0], mesh[1], (1, 1, 1, 1))])), 500)

        garment = descriptor_garment_surface_mesh(descriptor, {"height": 160, "chest": 85, "shoulder": 38})
        self.assertEqual(1004, len(garment[0]))
        self.assertEqual(1760, len(garment[1]))

    @classmethod
    def tearDownClass(cls) -> None:
        for path in tryon_app.RESULT_DIR.glob("*.glb"):
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
