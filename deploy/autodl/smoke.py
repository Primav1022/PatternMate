from __future__ import annotations

import json
import os
import time
import urllib.request


BASE = "http://127.0.0.1:6006"
MEASUREMENTS = {
    "height": 160,
    "chest": 84,
    "waist": 68,
    "shoulder": 39,
    "neck": 34,
    "sleeveLength": 58,
    "upperArm": 28,
}


def request(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"content-type": "application/json"} if data else {}
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=headers), timeout=30) as response:
        return json.load(response)


def wait(job_id: str) -> dict:
    deadline = time.time() + 300
    while time.time() < deadline:
        job = request(f"/tryon/research/jobs/{job_id}")
        if job["status"] in {"completed", "failed", "cancelled"}:
            if job["status"] != "completed":
                raise RuntimeError(job)
            return job
        time.sleep(0.25)
    raise TimeoutError(job_id)


recipe = {
    "family": "tshirt",
    "sex": "female",
    "base_case_id": "C2590529",
    "measurements_cm": MEASUREMENTS,
    "ease_cm": 8,
    "material_id": "tshirt.fabric.cotton",
    "fabric_color": "#eee7dc",
    "selections": {
        "neckline": "tshirt.neckline.v-neck",
        "sleeve": "tshirt.sleeve.set-in",
        "garment_length": "tshirt.garment-length.regular",
    },
    "base_option_ids": {
        "neckline": "tshirt.neckline.crew",
        "sleeve": "tshirt.sleeve.set-in",
        "garment_length": "tshirt.garment-length.regular",
    },
    "execution_mode": "batch_preview",
}
composition = request("/geometry/compose", recipe)
descriptor = composition["tryon_descriptor"]
if composition["status"] != "valid" or not composition["validation"]["trial_ready"]:
    raise RuntimeError({"compose_status": composition["status"], "validation": composition["validation"]})

if not descriptor["validation"].get("tryon_ready"):
    print(json.dumps({
        "compose_status": composition["status"],
        "execution_mode": composition.get("execution_mode"),
        "recipe_hash": composition["recipe_hash"],
        "triangulated_panels": descriptor["validation"].get("triangulated_panel_count"),
        "dense_mesh_panels": descriptor["validation"].get("dense_mesh_panel_count"),
        "tryon_status": "skipped_not_ready",
        "tryon_errors": descriptor["validation"].get("errors", []),
    }, ensure_ascii=False))
    raise SystemExit(0)

avatar = request("/tryon/research/avatar/jobs", {"sex": "female", "measurements_cm": MEASUREMENTS})
avatar_done = wait(avatar["job_id"])
panel = request(
    "/tryon/research/tryon/jobs",
    {
        "avatar_id": avatar.get("avatar_hash", avatar["job_id"]),
        "recipe_hash": descriptor["recipe_hash"],
        "family": "tshirt",
        "sex": "female",
        "measurements_cm": MEASUREMENTS,
        "recipe": recipe,
        "composition_descriptor": descriptor,
        "material": {"id": recipe["material_id"], "color": recipe["fabric_color"]},
        "quality": os.getenv("QUALITY", "draft"),
    },
)
panel_done = wait(panel["job_id"])
with urllib.request.urlopen(BASE + "/tryon" + panel_done["result_url"], timeout=30) as response:
    glb = response.read()
if len(glb) < 1000 or glb[:4] != b"glTF":
    raise RuntimeError("Invalid panel-preview GLB")

print(json.dumps({
    "compose_status": composition["status"],
    "execution_mode": composition.get("execution_mode"),
    "recipe_hash": composition["recipe_hash"],
    "triangulated_panels": descriptor["validation"]["triangulated_panel_count"],
    "avatar_status": avatar_done["status"],
    "avatar_model_source": avatar_done["metadata"]["model_source"],
    "tryon_status": panel_done["status"],
    "tryon_solver": panel_done["metadata"]["solver"],
    "converged": panel_done["metadata"]["converged"],
    "seam_mean_gap_mm": panel_done["metadata"]["seam_mean_gap_mm"],
    "penetration_rate": panel_done["metadata"]["penetration_rate"],
    "frames": panel_done["metadata"]["frames"],
    "glb_bytes": len(glb),
}, ensure_ascii=False))
