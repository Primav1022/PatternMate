from __future__ import annotations

import hashlib
import inspect
import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from body_fit import fit_smpl_avatar
from cloth_pipeline import simulate_cloth
from fabric_physics import fabric_physics, physics_cache_key
from glb import avatar_mesh, descriptor_garment_surface_mesh, make_glb


ENABLED = os.getenv("ENABLE_RESEARCH_3D", "false").lower() == "true"
SERVICE_BUILD = "newton-dxf-cloth-v2"
DEVICE_REQUEST = os.getenv("TRYON_DEVICE", "cuda:0")
RESULT_DIR = Path(os.getenv("TRYON_RESULT_DIR", Path(__file__).resolve().parent / ".results"))
RESULT_DIR.mkdir(parents=True, exist_ok=True)
SMPL_MODEL_DIR = os.getenv("SMPL_MODEL_DIR", "")
os.environ.setdefault("WARP_CACHE_PATH", str(Path(__file__).resolve().parents[2] / ".cache" / "warp"))
CLOTH_PYTHON = Path(os.getenv("TRYON_CLOTH_PYTHON", Path(__file__).resolve().parents[2] / ".venv-cloth" / "bin" / "python"))

try:
    import torch  # type: ignore
except ImportError:
    torch = None
try:
    import warp as wp  # type: ignore
except ImportError:
    wp = None
try:
    import newton  # type: ignore
except ImportError:
    newton = None
for _legacy_name, _legacy_type in {
    "bool": bool,
    "int": int,
    "float": float,
    "complex": complex,
    "object": object,
    "unicode": str,
    "str": str,
}.items():
    if _legacy_name not in np.__dict__:
        setattr(np, _legacy_name, _legacy_type)
if not hasattr(inspect, "getargspec"):
    inspect.getargspec = inspect.getfullargspec  # type: ignore[attr-defined]
try:
    import smplx  # type: ignore
except ImportError:
    smplx = None


def runtime_info() -> dict[str, Any]:
    cuda = bool(torch and torch.cuda.is_available())
    gpu_name = torch.cuda.get_device_name(0) if cuda else None
    memory_mb = round(torch.cuda.get_device_properties(0).total_memory / 1024 / 1024) if cuda else 0
    return {
        "service_build": SERVICE_BUILD,
        "enabled": ENABLED,
        "device_requested": DEVICE_REQUEST,
        "device": DEVICE_REQUEST if cuda and DEVICE_REQUEST.startswith("cuda") else "cpu",
        "cuda_available": cuda,
        "gpu_name": gpu_name,
        "gpu_memory_mb": memory_mb,
        "torch_available": torch is not None,
        "warp_available": wp is not None,
        "cloth_solver_available": bool(cuda and wp and newton),
        "panel_descriptor_supported": True,
        "cloth_solver": "newton_style3d_1.4" if newton else None,
        "cloth_blockers": [] if cuda and wp and newton else ["Newton 1.4, Warp and CUDA are required"],
        "cloth_runtime_installed": bool(newton),
        "smpl_available": smplx is not None,
        "smpl_model_mounted": bool(SMPL_MODEL_DIR and Path(SMPL_MODEL_DIR).exists()),
    }


class AvatarJobRequest(BaseModel):
    sex: Literal["female", "male_general"] = "female"
    measurements_cm: dict[str, float] = Field(default_factory=dict)


class TryonJobRequest(BaseModel):
    avatar_id: str
    recipe_hash: str
    family: Literal["tshirt", "shirt"]
    sex: Literal["female", "male_general"] = "female"
    measurements_cm: dict[str, float] = Field(default_factory=dict)
    recipe: dict[str, Any] = Field(default_factory=dict)
    composition_descriptor: dict[str, Any] = Field(default_factory=dict)
    material: dict[str, Any] = Field(default_factory=dict)
    quality: Literal["draft", "refined"] = "draft"


@dataclass
class Job:
    id: str
    kind: str
    priority: int
    payload: dict[str, Any]
    status: str = "queued"
    progress: int = 0
    stage: str = "queued"
    result_url: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cancelled: threading.Event = field(default_factory=threading.Event)
    created_at: float = field(default_factory=time.time)


JOBS: dict[str, Job] = {}
JOB_QUEUE: queue.PriorityQueue[tuple[int, float, str]] = queue.PriorityQueue()
LOCK = threading.Lock()


def _require_enabled() -> None:
    if not ENABLED:
        raise HTTPException(status_code=503, detail="Research 3D is disabled. Set ENABLE_RESEARCH_3D=true for a licensed research environment.")


def _save(job: Job, meshes) -> None:
    path = RESULT_DIR / f"{job.id}.glb"
    path.write_bytes(make_glb(meshes))
    job.result_url = f"/research/results/{job.id}.glb"


def _smpl_avatar(measurements: dict[str, float], sex: str):
    if not (torch and smplx and SMPL_MODEL_DIR and Path(SMPL_MODEL_DIR).exists()):
        return None
    device = runtime_info()["device"]
    gender = "male" if sex == "male_general" else "female"
    model_root = Path(SMPL_MODEL_DIR)
    supplied = model_root / ("basicmodel_m_lbs_10_207_0_v1.1.0.pkl" if gender == "male" else "basicmodel_f_lbs_10_207_0_v1.1.0.pkl")
    model = (smplx.SMPL(str(supplied), gender=gender, batch_size=1) if supplied.exists() else smplx.create(str(model_root), model_type="smpl", gender=gender, ext="pkl", batch_size=1)).to(device)
    vertices, faces, metrics, betas = fit_smpl_avatar(torch, model, measurements, gender, device)
    mesh = ([tuple(map(float, row)) for row in vertices.detach().cpu().tolist()], [tuple(map(int, row)) for row in faces.tolist()])
    return mesh, metrics, betas


def _safe_avatar(measurements: dict[str, float], sex: str):
    try:
        fitted = _smpl_avatar(measurements, sex)
        if fitted:
            mesh, metrics, betas = fitted
            return mesh, "smpl_anthropometric_fit", None, metrics, betas
        return avatar_mesh(measurements), "parametric_fallback", "SMPL model is not configured; measurements are approximated", {}, []
    except Exception as exc:
        return avatar_mesh(measurements), "parametric_fallback", f"SMPL fitting unavailable: {exc}", {}, []


def _display_avatar(vertices, faces, source: str):
    """Return a smoother render mesh while leaving the fitted collision mesh unchanged."""
    if source != "smpl_anthropometric_fit":
        return vertices, faces
    try:
        from trimesh.remesh import subdivide_loop

        smooth_vertices, smooth_faces = subdivide_loop(
            np.asarray(vertices, dtype=np.float64),
            np.asarray(faces, dtype=np.int64),
            iterations=1,
        )
        return smooth_vertices.astype(np.float32).tolist(), smooth_faces.astype(np.int32).tolist()
    except Exception:
        return vertices, faces


def _run_avatar(job: Job) -> None:
    measurements = job.payload["measurements_cm"]
    (vertices, faces), source, warning, metrics, betas = _safe_avatar(measurements, job.payload.get("sex", "female"))
    display_vertices, display_faces = _display_avatar(vertices, faces, source)
    job.metadata = {
        "model_source": source,
        "fit_method": "smpl_cross_section_optimization" if source == "smpl_anthropometric_fit" else "fallback_preview",
        "fit_metrics_cm": metrics,
        "shape_coefficients": betas,
        "collision_vertex_count": len(vertices),
        "display_vertex_count": len(display_vertices),
        "smooth_normals": True,
        "approximate": True,
        "warning": warning or "Body dimensions are optimized approximations and are not a substitute for a physical fitting",
    }
    job.progress = 70
    if job.cancelled.is_set():
        return
    _save(job, [(display_vertices, display_faces, (0.76, 0.60, 0.50, 1.0))])


def _run_tryon(job: Job) -> None:
    payload = job.payload
    if payload["family"] != "tshirt":
        raise ValueError("3D try-on currently supports T-shirts and Polo only")
    measurements = payload["measurements_cm"]
    avatar, avatar_source, warning, fit_metrics, _ = _safe_avatar(measurements, payload.get("sex", "female"))
    material = fabric_physics(payload.get("material"))

    def progress(stage: str, value: int) -> None:
        job.stage = stage
        job.progress = value

    garment_vertices, garment_faces, simulation = simulate_cloth(
        avatar,
        payload.get("composition_descriptor") or {},
        measurements,
        material,
        payload.get("quality") or "draft",
        progress,
        job.cancelled.is_set,
    )
    display_avatar = _display_avatar(avatar[0], avatar[1], avatar_source)
    job.metadata = {
        "cache_key": job.metadata.get("cache_key"),
        "avatar_source": avatar_source,
        "avatar_fit_metrics_cm": fit_metrics,
        "collision_vertex_count": len(avatar[0]),
        "display_vertex_count": len(display_avatar[0]),
        "smooth_normals": True,
        "warning": warning,
        "composition_recipe_hash": payload.get("recipe_hash"),
        **simulation,
        "simulation_ready": True,
        "production_valid": False,
    }
    color = str((payload.get("material") or {}).get("color") or "#f7f2e8").lstrip("#")
    try:
        rgb = tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        rgb = (0.97, 0.95, 0.91)
    _save(job, [(display_avatar[0], display_avatar[1], (0.76, 0.60, 0.50, 1.0)), (garment_vertices, garment_faces, (*rgb, 0.94))])


def _run_panel_preview(job: Job) -> None:
    payload = job.payload
    measurements = payload["measurements_cm"]
    avatar, avatar_source, warning, fit_metrics, _ = _safe_avatar(measurements, payload.get("sex", "female"))
    descriptor = payload.get("composition_descriptor") or {}
    garment = descriptor_garment_surface_mesh(descriptor, measurements)
    color = str((payload.get("material") or {}).get("color") or "#f7f2e8").lstrip("#")
    try:
        rgb = tuple(int(color[index:index + 2], 16) / 255 for index in (0, 2, 4))
    except (ValueError, IndexError):
        rgb = (0.97, 0.95, 0.91)
    panels = descriptor.get("panels") or []
    triangulated = sum(1 for panel in panels if panel.get("triangles"))
    job.metadata = {
        "avatar_source": avatar_source,
        "avatar_fit_metrics_cm": fit_metrics,
        "warning": warning or "Open single-layer garment surface derived from DXF panel roles and dimensions; sewing constraints, body collision and cloth drape simulation have not run",
        "composition_recipe_hash": payload.get("recipe_hash"),
        "solver": "single_layer_dxf_surface_preview",
        "simulation_ready": False,
        "production_valid": False,
        "panel_count": len(panels),
        "triangulated_panel_count": triangulated,
    }
    job.progress = 80
    if job.cancelled.is_set():
        return
    _save(job, [(avatar[0], avatar[1], (0.76, 0.60, 0.50, 1.0)), (garment[0], garment[1], (*rgb, 0.92))])


def _worker() -> None:
    while True:
        _, _, job_id = JOB_QUEUE.get()
        job = JOBS.get(job_id)
        if not job or job.cancelled.is_set():
            if job:
                job.status = "cancelled"
            JOB_QUEUE.task_done()
            continue
        job.status = "running"
        job.stage = "mesh" if job.kind == "tryon" else "running"
        try:
            {"avatar": _run_avatar, "panel_preview": _run_panel_preview, "tryon": _run_tryon}[job.kind](job)
            job.status = "cancelled" if job.cancelled.is_set() else "completed"
            job.stage = job.status
            job.progress = 100 if job.status == "completed" else job.progress
        except Exception as exc:
            job.status = "failed"
            job.stage = "failed"
            job.error = str(exc)
        finally:
            if torch and torch.cuda.is_available():
                torch.cuda.empty_cache()
            JOB_QUEUE.task_done()


threading.Thread(target=_worker, daemon=True, name="patternmate-gpu-worker").start()

app = FastAPI(title="PatternMate Research Try-on Service", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/research/health")
def health() -> dict[str, Any]:
    return {**runtime_info(), "queue_length": JOB_QUEUE.qsize(), "jobs": len(JOBS)}


def _enqueue(kind: str, payload: dict[str, Any], priority: int) -> dict[str, Any]:
    _require_enabled()
    if kind == "tryon" and payload.get("quality") == "draft":
        with LOCK:
            for existing in JOBS.values():
                if existing.kind == "tryon" and existing.payload.get("quality") == "refined" and existing.status in {"queued", "running"}:
                    existing.cancelled.set()
    if kind == "tryon":
        material_key = physics_cache_key(fabric_physics(payload.get("material")))
        cache_key = "|".join((str(payload.get("avatar_id")), str(payload.get("recipe_hash")), material_key, str(payload.get("quality"))))
        with LOCK:
            for existing in JOBS.values():
                if existing.kind == "tryon" and existing.metadata.get("cache_key") == cache_key and existing.status in {"queued", "running", "completed"}:
                    return _serialize(existing)
    else:
        cache_key = ""
    job = Job(uuid.uuid4().hex, kind, priority, payload)
    if cache_key:
        job.metadata["cache_key"] = cache_key
    with LOCK:
        JOBS[job.id] = job
    JOB_QUEUE.put((priority, job.created_at, job.id))
    return _serialize(job)


@app.post("/research/avatar/jobs")
def avatar_job(request: AvatarJobRequest) -> dict[str, Any]:
    payload = request.model_dump()
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    result = _enqueue("avatar", payload, 0)
    result["avatar_hash"] = digest
    return result


@app.post("/research/tryon/jobs")
def tryon_job(request: TryonJobRequest) -> dict[str, Any]:
    if request.family != "tshirt":
        raise HTTPException(status_code=422, detail="3D try-on currently supports T-shirts and Polo only")
    descriptor = request.composition_descriptor
    if descriptor.get("version") != "patternmate.tryon.v2":
        raise HTTPException(status_code=422, detail="A patternmate.tryon.v2 dense panel descriptor is required")
    if descriptor.get("recipe_hash") != request.recipe_hash:
        raise HTTPException(status_code=409, detail="The panel descriptor does not match the current recipe hash")
    descriptor_validation = descriptor.get("validation") or {}
    if not descriptor_validation.get("tryon_ready"):
        raise HTTPException(status_code=422, detail={
            "message": "The composed DXF panels or seam graph are incomplete",
            "errors": descriptor_validation.get("errors") or [],
        })
    if not runtime_info()["cloth_solver_available"]:
        raise HTTPException(
            status_code=503,
            detail="Real DXF cloth try-on is not ready: panel sewing and GPU collision solving have not been connected.",
        )
    return _enqueue("tryon", request.model_dump(), 0 if request.quality == "draft" else 10)


@app.post("/research/panel-preview/jobs")
def panel_preview_job(request: TryonJobRequest) -> dict[str, Any]:
    descriptor = request.composition_descriptor
    if descriptor.get("version") not in {"patternmate.tryon.v1", "patternmate.tryon.v2"}:
        raise HTTPException(status_code=422, detail="A PatternMate panel descriptor is required")
    if descriptor.get("recipe_hash") != request.recipe_hash:
        raise HTTPException(status_code=409, detail="The panel descriptor does not match the current recipe hash")
    if not any(panel.get("triangles") for panel in descriptor.get("panels") or []):
        raise HTTPException(status_code=422, detail="The composed DXF has no triangulated panel available for 3D preview")
    return _enqueue("panel_preview", request.model_dump(), 0)


def _serialize(job: Job) -> dict[str, Any]:
    return {"job_id": job.id, "kind": job.kind, "status": job.status, "stage": job.stage, "progress": job.progress, "result_url": job.result_url, "error": job.error, "metadata": job.metadata}


@app.get("/research/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize(job)


@app.delete("/research/jobs/{job_id}")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.cancelled.set()
    if job.status == "queued":
        job.status = "cancelled"
    return _serialize(job)


@app.get("/research/results/{name}")
def result(name: str):
    safe_name = Path(name).name
    path = RESULT_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Result not found")
    return FileResponse(path, media_type="model/gltf-binary")
