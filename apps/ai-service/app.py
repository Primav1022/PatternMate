from __future__ import annotations

import json
import io
import base64
import os
import queue
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from PIL import Image


app = FastAPI(title="PatternMate Creative Service", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).resolve().parent / ".env")

COMFY_BASE_URL = os.getenv("COMFY_BASE_URL", "http://127.0.0.1:8188").rstrip("/")
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8801/v1").rstrip("/")
RESULT_DIR = Path(os.getenv("AI_RESULT_DIR", Path(__file__).resolve().parent / ".results"))
RESULT_DIR.mkdir(parents=True, exist_ok=True)
GPU_LOCK_PATH = Path(os.getenv("PATTERNMATE_GPU_LOCK", "/tmp/patternmate-gpu.lock"))
COMFY_MODEL_DIR = Path(os.getenv("COMFY_MODEL_DIR", "/root/autodl-tmp/ComfyUI/models"))
COMFY_INPUT_DIR = Path(os.getenv("COMFY_INPUT_DIR", "/root/autodl-tmp/ComfyUI/input"))
PROJECT_ROOT = Path(os.getenv("CHI27_ROOT", Path(__file__).resolve().parents[2]))


class PrintJobRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=1200)
    history: list[str] = Field(default_factory=list, max_length=6)
    negative_prompt: str = Field(default="text, letters, watermark, logo, mockup, garment, frame", max_length=600)
    mode: Literal["motif", "seamless"] = "motif"
    width: int = Field(default=1024, ge=512, le=1536)
    height: int = Field(default=1024, ge=512, le=1536)
    candidate_count: int = Field(default=2, ge=2, le=4)
    inspiration_image_data_url: str = ""
    seed: Optional[int] = None


class DesignPreviewJobRequest(BaseModel):
    case_id: str = Field(pattern=r"^C\d+$")
    family: Literal["tshirt", "shirt"]
    sex: Literal["female", "male_general"]
    intent: dict[str, Any] = Field(default_factory=dict)
    measurements_cm: dict[str, Any] = Field(default_factory=dict)
    selections: dict[str, Any] = Field(default_factory=dict)
    fabric_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    material_id: str = ""
    material_label: str = ""
    material_description: str = ""
    process_id: str = ""
    process_label: str = ""
    pattern_context: dict[str, Any] = Field(default_factory=dict)
    style_key: str = Field(default="", max_length=256)
    user_instruction: str = Field(default="", max_length=2000)
    prompt: Optional[str] = Field(default=None, max_length=4000)
    seed: Optional[int] = None


class GarmentPrintJobRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=1200)
    history: list[str] = Field(default_factory=list, max_length=8)
    source_preview_url: str
    selected_print_url: str
    selected_print_mode: Literal["motif", "seamless"] = "motif"
    inspiration_image_data_url: str = ""
    design_context: dict[str, Any] = Field(default_factory=dict)
    seed: Optional[int] = None


@dataclass
class PrintJob:
    id: str
    request: PrintJobRequest
    status: str = "queued"
    progress: int = 0
    stage: str = "queued"
    result_urls: list[str] = field(default_factory=list)
    error: Optional[str] = None
    cancelled: threading.Event = field(default_factory=threading.Event)


@dataclass
class ImageEditJob:
    id: str
    request: DesignPreviewJobRequest | GarmentPrintJobRequest
    kind: Literal["design_preview", "garment_print"]
    status: str = "queued"
    progress: int = 0
    stage: str = "queued"
    result_urls: list[str] = field(default_factory=list)
    production_asset: dict[str, Any] | None = None
    prompt_used: Optional[str] = None
    visual_validation: dict[str, Any] | None = None
    attempt_count: int = 0
    error: Optional[str] = None
    cancelled: threading.Event = field(default_factory=threading.Event)


JOBS: dict[str, PrintJob | ImageEditJob] = {}
JOB_QUEUE: queue.Queue[str] = queue.Queue()
JOBS_LOCK = threading.Lock()


class gpu_lock:
    def __enter__(self):
        GPU_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.handle = GPU_LOCK_PATH.open("a+")
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        return self

    def __exit__(self, *_args):
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        self.handle.close()


def _json_request(url: str, payload: dict[str, Any] | None = None, timeout: float = 10) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _release_comfy_memory() -> None:
    payload = json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8")
    request = urllib.request.Request(f"{COMFY_BASE_URL}/free", data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        pass


def _available(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=1).close()
        return True
    except OSError:
        return False


def _image_api_config() -> dict[str, Any] | None:
    name = (os.getenv("IMAGE_MODEL_NAME") or "").strip()
    base_url = (os.getenv("IMAGE_MODEL_BASE_URL") or os.getenv("MODEL_BASE_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("IMAGE_MODEL_API_KEY") or os.getenv("MODEL_API_KEY") or "").strip()
    if not name or not base_url or not api_key or api_key.startswith("fill-your-"):
        return None
    timeout = float(os.getenv("IMAGE_MODEL_TIMEOUT_SECONDS") or os.getenv("MODEL_TIMEOUT_SECONDS") or "180")
    verify = (os.getenv("IMAGE_MODEL_SSL_VERIFY") or os.getenv("MODEL_SSL_VERIFY") or "true").strip().lower() != "false"
    return {
        "base_url": base_url,
        "name": name,
        "key": api_key,
        "timeout": timeout,
        "verify": verify,
        "size": (os.getenv("IMAGE_MODEL_SIZE") or "1024x1024").strip(),
        "quality": (os.getenv("IMAGE_MODEL_QUALITY") or "medium").strip(),
    }


def _image_backend() -> str:
    value = (os.getenv("IMAGE_BACKEND") or "auto").strip().lower()
    return value if value in {"api", "comfy", "auto"} else "auto"


def _image_api_ready() -> bool:
    return _image_api_config() is not None


def _use_image_api() -> bool:
    backend = _image_backend()
    if backend == "comfy":
        return False
    if backend == "api":
        return _image_api_ready()
    return _image_api_ready()


def _image_ready() -> bool:
    return _use_image_api() or _print_ready()


def _ssl_context(verify: bool):
    if verify:
        return None
    import ssl
    return ssl._create_unverified_context()


def _multipart(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----PatternMate" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    for name, filename, data, content_type in files:
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode()
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _decode_image_api_body(body: dict[str, Any], timeout: float, ssl_context) -> bytes:
    item = (body.get("data") or [None])[0]
    if not isinstance(item, dict):
        raise RuntimeError("image api returned no image")
    b64 = str(item.get("b64_json") or "").strip()
    if b64:
        if "," in b64 and b64.lower().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)
    url = str(item.get("url") or "").strip()
    if not url:
        raise RuntimeError("image api returned no image")
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
        return response.read()


def _image_api_call(kind: Literal["generate", "edit"], prompt: str, image_path: Path | None = None) -> bytes:
    config = _image_api_config()
    if not config:
        raise RuntimeError("image api is not configured")
    ssl_context = _ssl_context(config["verify"])
    headers = {"Authorization": f"Bearer {config['key']}"}
    fields = {
        "model": config["name"],
        "prompt": prompt,
        "n": "1",
        "size": config["size"],
        "quality": config["quality"],
        "response_format": "b64_json",
    }
    if kind == "generate":
        payload = json.dumps({key: (int(value) if key == "n" else value) for key, value in fields.items()}).encode("utf-8")
        request = urllib.request.Request(
            f"{config['base_url']}/images/generations",
            data=payload,
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
    else:
        if image_path is None or not image_path.is_file():
            raise RuntimeError("reference image is unavailable")
        body, content_type = _multipart(fields, [("image", image_path.name, image_path.read_bytes(), "image/png")])
        request = urllib.request.Request(
            f"{config['base_url']}/images/edits",
            data=body,
            headers={**headers, "Content-Type": content_type},
            method="POST",
        )
    try:
        with urllib.request.urlopen(request, timeout=config["timeout"], context=ssl_context) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:800]
        raise RuntimeError(f"image api {exc.code}: {detail}") from exc
    return _decode_image_api_body(payload, config["timeout"], ssl_context)


def _save_result_bytes(data: bytes, job_id: str, index: int, transparent: bool) -> str:
    if transparent:
        data = _transparent_motif(data)
    else:
        source = Image.open(io.BytesIO(data)).convert("RGB")
        normalized = io.BytesIO()
        source.save(normalized, format="PNG", dpi=(300, 300), optimize=True)
        data = normalized.getvalue()
    target = RESULT_DIR / f"{job_id}-{index}.png"
    target.write_bytes(data)
    return f"/results/{target.name}"


def _work_image_dir() -> Path:
    if _use_image_api():
        path = RESULT_DIR / "_inputs"
        path.mkdir(parents=True, exist_ok=True)
        return path
    COMFY_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    return COMFY_INPUT_DIR


def _chat_ready() -> bool:
    try:
        return bool(_json_request(f"{VLLM_BASE_URL}/models", timeout=2).get("data"))
    except OSError:
        return False


def _print_ready() -> bool:
    required = (
        (COMFY_MODEL_DIR / "diffusion_models" / os.getenv("QWEN_IMAGE_UNET", "qwen_image_2512_fp8_e4m3fn.safetensors"), 18_000_000_000),
        (COMFY_MODEL_DIR / "text_encoders" / os.getenv("QWEN_IMAGE_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors"), 8_000_000_000),
        (COMFY_MODEL_DIR / "vae" / os.getenv("QWEN_IMAGE_VAE", "qwen_image_vae.safetensors"), 200_000_000),
    )
    return _available(f"{COMFY_BASE_URL}/system_stats") and all(path.is_file() and path.stat().st_size >= minimum for path, minimum in required)


def _print_positive(request: PrintJobRequest) -> str:
    previous = "; ".join(item.strip() for item in request.history[-6:] if item.strip())
    positive = f"Previous design direction: {previous}. Latest revision: {request.prompt.strip()}" if previous else request.prompt.strip()
    if request.mode == "motif":
        return positive + ", isolated single textile motif, centered, high contrast against a clean plain background, no typography"
    return positive + ", seamless repeating textile pattern, tileable edges, flat print artwork, no typography"


def _workflow(request: PrintJobRequest, seed: int) -> dict[str, Any]:
    return {
        "6": {"class_type": "UNETLoader", "inputs": {"unet_name": os.getenv("QWEN_IMAGE_UNET", "qwen_image_2512_fp8_e4m3fn.safetensors"), "weight_dtype": "default"}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": os.getenv("QWEN_IMAGE_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors"), "type": "qwen_image", "device": "default"}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": os.getenv("QWEN_IMAGE_VAE", "qwen_image_vae.safetensors")}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": _print_positive(request), "clip": ["7", 0]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": request.negative_prompt, "clip": ["7", 0]}},
        "11": {"class_type": "EmptySD3LatentImage", "inputs": {"width": request.width, "height": request.height, "batch_size": 1}},
        "12": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3.1, "model": ["6", 0]}},
        "13": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 28, "cfg": 4.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["12", 0], "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["11", 0]}},
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["8", 0]}},
        "15": {"class_type": "SaveImage", "inputs": {"filename_prefix": "PatternMate", "images": ["14", 0]}},
    }


def _case_reference_path(case_id: str) -> Path | None:
    return next((path for version in ("v2", "v1") for name in ("cover.png", "cover.jpg", "cover.jpeg", "cover.webp", "thumb.jpg")
                 if (path := PROJECT_ROOT / "apps" / "web" / "public" / "reference-images" / version / case_id / name).is_file()), None)


VISUAL_SEMANTICS: dict[str, tuple[str, str]] = {
    "crew": ("a close round crew neckline with a visible circular neck band", "V neckline, polo collar, raised mock neck, wide open neckline"),
    "v-neck": ("a clearly pointed V-shaped front neckline", "round crew neckline, polo collar, raised mock neck"),
    "polo": ("a folded polo collar with a short center-front placket", "plain crew neckline, collarless neckline, raised mock neck"),
    "high-mock": ("a high or mock neckline rising from the neck base and visibly wrapping the lower neck", "low round crew neckline, ordinary rib crew band, open neckline, V neckline"),
    "cowl": ("a soft draped cowl neckline with visible folded fabric volume", "flat crew neckline, rigid shirt collar, raised fitted mock neck"),
    "scrunch": ("a gathered scrunch neckline with dense soft folds around the neck", "plain crew neckline, smooth flat neck band"),
    "boat": ("a broad shallow boat neckline extending toward both shoulders", "deep round neckline, V neckline, narrow crew neckline"),
    "asymmetric": ("a visibly asymmetric diagonal neckline", "symmetrical crew, V or boat neckline"),
    "bow-tie": ("a neck tie collar forming a visible soft bow at center front", "plain pointed collar, collarless neckline"),
    "pointed": ("a structured shirt collar with two clear pointed collar tips", "rounded collar, wide lapel, collarless neckline"),
    "peter-pan": ("a small flat collar with rounded Peter Pan collar edges", "sharp pointed collar, wide lapel"),
    "casual-wide-lapel": ("a relaxed open collar with broad lapel-like revers", "closed pointed collar, collarless neckline"),
    "open-v-pointed": ("an open V-shaped shirt neckline ending in pointed collar tips", "closed crew neckline, rounded collar"),
    "raglan": ("raglan sleeves with diagonal seams running from neckline to underarm", "set-in shoulder armhole seams"),
    "set-in": ("regular set-in sleeves with armhole seams at the natural shoulder", "raglan seams, batwing sleeves, puff sleeves"),
    "puff": ("puff sleeves with clearly gathered volume at sleeve head", "flat regular sleeves, raglan sleeves"),
    "bell": ("bell sleeves widening visibly toward the cuffs", "straight regular sleeves, tight cuffs"),
    "flutter": ("short flutter sleeves with loose wing-like flared edges", "straight fitted sleeves, long regular sleeves"),
    "batwing": ("batwing sleeves with a deep low armhole joining the bodice", "standard fitted armholes, puff sleeve heads"),
    "relaxed-h": ("a relaxed straight H-shaped silhouette with generous ease", "fitted waist, flared A-line hem"),
    "fitted-x": ("a fitted X-shaped silhouette with a clearly defined waist", "boxy straight silhouette, oversized fit"),
    "oversized": ("an intentionally oversized silhouette with dropped ease and broad volume", "close fitted silhouette"),
    "regular-fit": ("a balanced regular-fit silhouette following the body without clinging", "extreme oversized or strongly fitted silhouette"),
    "a-line": ("an A-line silhouette widening progressively toward the hem", "straight H silhouette, fitted pencil shape"),
    "full": ("a full-length center-front placket running from collar to hem", "half placket, diagonal closure"),
    "half": ("a half placket ending around the upper torso", "full-length placket to hem"),
    "concealed": ("a concealed center-front placket with hidden buttons", "fully exposed button row"),
    "placket:ruffled": ("a center-front placket edged with visible controlled ruffles", "plain flat placket"),
    "diagonal": ("a clearly diagonal front placket or closure", "vertical center-front placket"),
    "gathered": ("a cuff with visible gathering where the sleeve joins the cuff", "plain ungathered cuff"),
    "cuff:ruffled": ("a sleeve cuff finished with a visible ruffled edge", "plain straight cuff, ruffled center-front placket"),
    "sleeve:regular": ("regular sleeves with a conventional straight sleeve shape", "puff, bell, flutter or batwing sleeves"),
    "cuff:regular": ("a plain conventional cuff without gathers or ruffles", "gathered or ruffled cuff"),
    "side-waist-pleats": ("visible controlled pleats shaping both side waists", "plain unshaped side seams"),
    "waist-gathers": ("visible gathering concentrated around the waist", "completely flat waist area"),
    "wrap-v": ("a crossing wrap-front construction forming a V neckline", "single flat front panel"),
    "shoulder-pleats": ("visible pleats placed along the shoulder line", "plain shoulder without pleats"),
    "short": ("a short cropped garment length ending above the natural hip", "regular or long garment length"),
    "garment_length:regular": ("a regular garment length ending around the natural hip", "cropped above the hip or extended long below the hip"),
    "long": ("an extended long garment length below the hip", "cropped or short garment length"),
}


def _option_catalog() -> dict[str, dict[str, Any]]:
    path = PROJECT_ROOT / "apps" / "web" / "src" / "catalog-data" / "pattern-options.v1.json"
    try:
        return {str(item["id"]): item for item in json.loads(path.read_text(encoding="utf-8")).get("options", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _selected_components(request: DesignPreviewJobRequest) -> list[dict[str, str]]:
    catalog = _option_catalog()
    components = []
    for group, option_id in request.selections.items():
        if not option_id:
            continue
        item = catalog.get(str(option_id), {})
        slug = str(item.get("slug") or str(option_id).split(".")[-1])
        visual_cues, forbidden = VISUAL_SEMANTICS.get(f"{group}:{slug}", VISUAL_SEMANTICS.get(slug, (f"the clearly recognizable {slug.replace('-', ' ')} construction", f"any construction conflicting with {slug.replace('-', ' ')}")))
        components.append({"group": str(group), "option_id": str(option_id), "slug": slug, "label": str(item.get("label_zh") or slug), "visual_cues": visual_cues, "forbidden_confusions": forbidden})
    return components


def _reference_panels(request: DesignPreviewJobRequest) -> list[dict[str, Any]]:
    panels: list[dict[str, Any]] = [{"panel": 1, "case_id": request.case_id, "groups": ["base garment family"]}]
    by_case: dict[str, list[str]] = {}
    for group, source in (request.pattern_context.get("sources") or {}).items():
        if not isinstance(source, dict):
            continue
        case_id = str(source.get("case_id") or "")
        option_id = str(source.get("option_id") or "")
        if case_id and case_id != request.case_id and option_id == str(request.selections.get(group) or ""):
            by_case.setdefault(case_id, []).append(str(group))
    for case_id, groups in list(by_case.items())[:5]:
        if _case_reference_path(case_id):
            panels.append({"panel": len(panels) + 1, "case_id": case_id, "groups": groups})
    return panels


def _reference_image(request: DesignPreviewJobRequest, job_id: str) -> str:
    panels = _reference_panels(request)
    images: list[Image.Image] = []
    for panel in panels:
        source = _case_reference_path(str(panel["case_id"]))
        if source:
            images.append(Image.open(source).convert("RGB"))
    if not images:
        raise RuntimeError("reference image is unavailable")
    target_name = f"patternmate-design-{job_id}.png"
    target = _work_image_dir() / target_name
    if len(images) == 1:
        images[0].save(target, format="PNG")
        return target_name
    cell = 768; columns = 2; rows = (len(images) + columns - 1) // columns
    canvas = Image.new("RGB", (cell * columns, cell * rows), "white")
    for index, image in enumerate(images):
        image.thumbnail((cell - 24, cell - 24), Image.Resampling.LANCZOS)
        x = (index % columns) * cell + (cell - image.width) // 2
        y = (index // columns) * cell + (cell - image.height) // 2
        canvas.paste(image, (x, y))
    canvas.save(target, format="PNG")
    return target_name


def _design_prompt(request: DesignPreviewJobRequest) -> str:
    measurements = request.measurements_cm or {}
    color_rgb = tuple(int(request.fabric_color[index:index + 2], 16) for index in (1, 3, 5))
    body_fields = (("Gender", request.sex, ""), ("Height", measurements.get("height"), " cm"), ("Weight", measurements.get("weight"), " kg"), ("Chest", measurements.get("chest"), " cm"), ("Waist", measurements.get("waist"), " cm"), ("Shoulder width", measurements.get("shoulder"), " cm"), ("Neck circumference", measurements.get("neck"), " cm"), ("Target sleeve length", measurements.get("sleeveLength"), " cm"), ("Upper-arm circumference", measurements.get("upperArm"), " cm"))
    body = "\n".join(f"- {label}: {value}{unit}" for label, value, unit in body_fields if value not in (None, ""))
    components = _selected_components(request)
    component_text = "\n".join(f"- {item['group']}: {item['label']} ({item['option_id']}). REQUIRED VISUAL CUES: {item['visual_cues']}. FORBIDDEN CONFUSIONS: {item['forbidden_confusions']}." for item in components) or "- Preserve the validated base construction."
    panels = "\n".join(f"- Panel {item['panel']}: case {item['case_id']}, controls only {', '.join(item['groups'])}." for item in _reference_panels(request))
    context = request.pattern_context or {}
    intent = {key: value for key, value in (request.intent or {}).items() if value not in (None, "", [], {})}
    pieces = [{key: item.get(key) for key in ("role", "width_mm", "height_mm") if item.get(key) is not None} for item in context.get("pieces", []) if isinstance(item, dict)]
    sources = {group: {key: value.get(key) for key in ("case_id", "option_id", "mapping_mode") if value.get(key) not in (None, "")} for group, value in (context.get("sources") or {}).items() if isinstance(value, dict)}
    user_instruction = request.user_instruction.strip() or (request.prompt or "").strip() or "No additional user instruction."
    return f"""[TASK]
Generate exactly one realistic front-facing 2D fashion preview from the validated garment design and supplied visual references.

[REFERENCE PANELS]
{panels}
- Panel 1 controls only the overall garment family. Following panels are component references selected by the user; copy only their named components.
- Validated selections and DXF context override conflicting details in Panel 1. Never inherit garment color, print, logo, person identity or background.

[WEARER]
{body}
- Represent these values through plausible relative body proportions and garment fit. Never display measurements or annotations.

[GARMENT]
- Category: {request.family}
- Base reference: {request.case_id}
- Validated components:
{component_text}
- User design intent: {json.dumps(intent, ensure_ascii=False)}

[FABRIC]
- Material: {request.material_label or request.material_id}
- Characteristics: {request.material_description}
- REQUIRED BASE COLOR: {request.fabric_color}, RGB{color_rgb}. This is a hard constraint overriding every reference image.
- Process: {request.process_label or request.process_id}
- Render only material properties supported by the description. Keep collars, sleeves, trims and garment panels in the required base color unless an explicit contrasting construction detail was selected.
- HARD SURFACE RULE: the entire garment must remain plain and unbranded, with no logo, brand mark, printed motif, graphic, lettering, embroidery, applique, badge, illustration or decorative surface pattern. Show only the selected solid base color, natural fabric texture and construction seams.

[VALIDATED PATTERN]
- Recipe hash: {context.get('recipe_hash', '')}
- Sizing profile: {json.dumps(context.get('sizing_profile', {}), ensure_ascii=False)}
- Pattern pieces: {json.dumps(pieces, ensure_ascii=False)}
- Component sources: {json.dumps(sources, ensure_ascii=False)}
- Preserve the construction and proportions implied by this validated pattern. Never show DXF pieces or technical annotations.

[OUTPUT]
- One wearer only, strictly front-facing, centered, shown from head to hips.
- Natural standing pose with garment components unobstructed.
- Neutral studio background unless the user explicitly requested a scene. Scene cues must remain restrained and cannot alter garment construction, color or body proportions.
- Clean commercial fashion visualization with realistic seams, folds, texture, lighting and material drape.

[NEGATIVE]
No conflicting neckline, collar, sleeve, cuff, placket, silhouette or length. No side or back view, full-body crop, extra person, extra garment, mannequin, flat garment, technical drawing, DXF diagram, measurement labels, captions, logos, brand marks, printed motifs, graphics, lettering, embroidery, applique, badges, decorative illustrations, ornamental surface patterns, watermarks, interface elements or visible text.

[USER ADDENDUM]
{user_instruction}"""


VALIDATED_COMPONENT_GROUPS = {"neckline", "collar", "sleeve", "cuff", "placket", "silhouette", "garment_length"}


def _visual_expectations(request: DesignPreviewJobRequest) -> list[dict[str, str]]:
    components = [item for item in _selected_components(request) if item["group"] in VALIDATED_COMPONENT_GROUPS]
    components.append({
        "group": "surface_decoration",
        "option_id": "internal.surface.plain-unprinted",
        "slug": "plain-unprinted",
        "label": "plain unbranded garment surface",
        "visual_cues": "a completely plain solid-color garment surface with only construction seams and natural fabric texture",
        "forbidden_confusions": "any logo, brand mark, printed motif, graphic, lettering, embroidery, applique, badge, decorative illustration or ornamental surface pattern",
    })
    return components


def _json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if "```" in cleaned:
        cleaned = re.sub(r"^.*?```(?:json)?\s*", "", cleaned, flags=re.S | re.I)
        cleaned = re.sub(r"\s*```.*$", "", cleaned, flags=re.S)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("visual validation returned invalid JSON")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise RuntimeError("visual validation returned invalid JSON")
    return value


def _validate_design_image(image_path: Path, request: DesignPreviewJobRequest) -> dict[str, Any]:
    expectations = _visual_expectations(request)
    if not expectations:
        return {"matches": True, "components": []}
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    image_data = f"data:{mime};base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"
    expected = [{key: item[key] for key in ("group", "slug", "visual_cues", "forbidden_confusions")} for item in expectations]
    instruction = "Inspect only the garment in this image. Compare each expected component with what is visibly present. Return strict JSON only with this schema: {\"matches\":boolean,\"components\":[{\"group\":string,\"expected\":string,\"observed\":string,\"match\":boolean,\"confidence\":number,\"reason\":string}]}. Confidence is 0 to 1. Mark mismatch only when the visible construction clearly conflicts with the expected visual cues. Expected components: " + json.dumps(expected, ensure_ascii=False)
    payload = {
        "model": os.getenv("QWEN_VL_SERVED_NAME", "qwen3-vl"),
        "temperature": 0,
        "max_tokens": 700,
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_data}}, {"type": "text", "text": instruction}]}],
    }
    try:
        with gpu_lock():
            _release_comfy_memory()
            try:
                response = _json_request(f"{VLLM_BASE_URL}/chat/completions", payload, timeout=180)
            finally:
                try:
                    _json_request(f"{VLLM_BASE_URL}/unload", {}, timeout=30)
                except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                    pass
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("本地视觉校验服务暂时不可用，已保留上一张有效2D图，请稍后重新生成。 Local visual validation service is unavailable.") from exc
    content = str((((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""))
    value = _json_object(content)
    rows = value.get("components")
    if not isinstance(rows, list):
        raise RuntimeError("visual validation returned no component results")
    normalized = []
    expected_by_group = {item["group"]: item for item in expectations}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("group")) not in expected_by_group:
            continue
        confidence = max(0.0, min(1.0, float(row.get("confidence", 0))))
        normalized.append({"group": str(row.get("group")), "expected": expected_by_group[str(row.get("group"))]["slug"], "observed": str(row.get("observed") or "unknown"), "match": bool(row.get("match")), "confidence": confidence, "reason": str(row.get("reason") or "")[:300]})
    if len({row["group"] for row in normalized}) != len(expected_by_group):
        raise RuntimeError("visual validation omitted expected components")
    clear_mismatches = [row for row in normalized if not row["match"] and row["confidence"] >= 0.75]
    return {"matches": not clear_mismatches, "components": normalized, "clear_mismatches": clear_mismatches}


def _correction_prompt(prompt: str, validation: dict[str, Any]) -> str:
    corrections = "; ".join(f"{item['group']} must be {item['expected']}, but the rejected image appeared as {item['observed']}: {item['reason']}" for item in validation.get("clear_mismatches", []))
    return prompt + "\n\n[MANDATORY CORRECTION]\nThe previous attempt was rejected. Correct these visible construction errors without changing any already correct feature: " + corrections


def _garment_print_prompt(request: GarmentPrintJobRequest) -> str:
    history = "; ".join(request.history)
    return "The supplied reference is a two-panel multimodal design input: the LEFT panel is the approved garment-and-wearer preview from the previous design step, and the RIGHT panel is the exact print artwork selected by the user. Output one image only: the same front-facing, head-to-hips garment preview from the LEFT panel with the selected RIGHT-panel artwork realistically printed on its garment surface. Do not output the two-panel reference, a comparison, a motif sheet or a separate artwork image. Preserve the selected artwork's recognizable shapes, colors and composition; do not replace it with a newly invented similar motif. Perform a minimal localized print-only edit. Treat every source-image pixel outside the requested print area as locked. Do not redesign, regenerate or alter the garment silhouette, neckline, collar, sleeves, hem, seams, fit, proportions, fabric texture, fabric drape or base color. Do not change the person, body, face, hair, pose, front-facing head-to-hips composition, camera, lighting, background or scene. The garment base color must remain pixel-consistent with the LEFT panel; never recolor it. Outside the actual print artwork on the shirt, ABSOLUTELY NO VISIBLE TEXT OR LETTERS: no signs, captions, labels, interface text, brand marks, logos or watermarks. Typography is allowed only inside the selected shirt print itself and only when the user explicitly requests typographic print artwork; otherwise the print must also contain no letters or words. " + f"Previous requests: {history}. Latest request: {request.prompt}. Design context: {json.dumps(request.design_context, ensure_ascii=False)}"


def _requests_typography(request: GarmentPrintJobRequest) -> bool:
    text = " ".join([*request.history, request.prompt]).lower()
    return bool(re.search(r"文字|字母|字体|标语|口号|文案|英文|中文|typograph|lettering|slogan|wordmark|\btext\b|\bwords?\b", text))


def _print_asset_mode(request: GarmentPrintJobRequest) -> Literal["motif", "seamless"]:
    text = " ".join([*request.history, request.prompt]).lower()
    return "seamless" if re.search(r"满印|满版|连续|无缝|平铺|重复|循环|四方连续|二方连续|all[- ]?over|seamless|repeat|tileable|tiled", text) else "motif"


def _production_print_request(request: GarmentPrintJobRequest, seed: int) -> PrintJobRequest:
    asset_mode = _print_asset_mode(request)
    instruction = (
        "Create the independent production artwork used in the approved garment preview. "
        "Output artwork only: no person, garment, mockup, scene, frame, label, brand mark, logo or watermark. Do not add any words or letters unless typography is explicitly requested by the user as part of the print artwork. "
        + ("Use a transparent plain background around one centered placement motif. " if asset_mode == "motif" else "Create a perfectly tileable edge-to-edge seamless textile repeat. ")
        + f"User direction: {'; '.join(request.history)}; {request.prompt}."
    )
    negative = "watermark, logo, brand mark, caption, label, mockup, garment, person, frame"
    if not _requests_typography(request):
        negative += ", text, letters, words, typography"
    return PrintJobRequest(prompt=instruction, negative_prompt=negative, mode=asset_mode, width=1536, height=1536, seed=seed)


def _image_edit_workflow(prompt: str, seed: int, image_name: str, allow_print_text: bool = False, denoise: float = 0.7) -> dict[str, Any]:
    return {
        "6": {"class_type": "UNETLoader", "inputs": {"unet_name": os.getenv("QWEN_IMAGE_UNET", "qwen_image_2512_fp8_e4m3fn.safetensors"), "weight_dtype": "default"}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": os.getenv("QWEN_IMAGE_CLIP", "qwen_2.5_vl_7b_fp8_scaled.safetensors"), "type": "qwen_image", "device": "default"}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": os.getenv("QWEN_IMAGE_VAE", "qwen_image_vae.safetensors")}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["7", 0]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": ("watermark, logo, brand mark, caption, label, interface text, multiple people, extra garment, side view, full body, flat pattern pieces" if allow_print_text else "text, letters, words, typography, watermark, logo, brand mark, caption, label, interface text, multiple people, extra garment, side view, full body, flat pattern pieces"), "clip": ["7", 0]}},
        "11": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "12": {"class_type": "VAEEncode", "inputs": {"pixels": ["11", 0], "vae": ["8", 0]}},
        "13": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3.1, "model": ["6", 0]}},
        "14": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 32, "cfg": 4.2, "sampler_name": "euler", "scheduler": "simple", "denoise": denoise, "model": ["13", 0], "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["12", 0]}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["8", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"filename_prefix": "PatternMateGarment", "images": ["15", 0]}},
    }


def _print_reference_source(request: PrintJobRequest, job_id: str) -> str | None:
    if not request.inspiration_image_data_url:
        return None
    try:
        encoded = request.inspiration_image_data_url.split(",", 1)[1]
        image = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGB")
        target_name = f"patternmate-print-reference-{job_id}.png"
        image.save(_work_image_dir() / target_name, format="PNG")
        return target_name
    except (ValueError, OSError, IndexError):
        return None


def _garment_source(request: GarmentPrintJobRequest, job_id: str) -> str:
    preview_name = Path(urllib.parse.urlparse(request.source_preview_url).path).name
    preview_path = RESULT_DIR / preview_name
    if not preview_name or not preview_path.is_file():
        raise RuntimeError("source garment preview is unavailable")
    print_name = Path(urllib.parse.urlparse(request.selected_print_url).path).name
    print_path = RESULT_DIR / print_name
    if not print_name or not print_path.is_file():
        raise RuntimeError("selected print artwork is unavailable")
    target_name = f"patternmate-print-{job_id}.png"
    target = _work_image_dir() / target_name
    garment = Image.open(preview_path).convert("RGB")
    artwork = Image.open(print_path).convert("RGBA")
    artwork_background = Image.new("RGBA", artwork.size, "white")
    artwork_background.alpha_composite(artwork)
    artwork_rgb = artwork_background.convert("RGB")
    height = min(1536, max(garment.height, artwork_rgb.height))
    garment = garment.resize((max(1, round(garment.width * height / garment.height)), height), Image.Resampling.LANCZOS)
    artwork_rgb = artwork_rgb.resize((max(1, round(artwork_rgb.width * height / artwork_rgb.height)), height), Image.Resampling.LANCZOS)
    divider = 16
    canvas = Image.new("RGB", (garment.width + divider + artwork_rgb.width, height), "white")
    canvas.paste(garment, (0, 0))
    canvas.paste(artwork_rgb, (garment.width + divider, 0))
    canvas.save(target, format="PNG")
    return target_name


def _transparent_motif(data: bytes) -> bytes:
    source = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"), dtype=np.uint8).copy()
    corners = np.stack((source[0, 0, :3], source[0, -1, :3], source[-1, 0, :3], source[-1, -1, :3])).astype(np.float32)
    background = corners.mean(axis=0)
    distance = np.linalg.norm(source[:, :, :3].astype(np.float32) - background, axis=2)
    alpha = np.clip((distance - 24.0) / 58.0 * 255.0, 0.0, 255.0).astype(np.uint8)
    source[:, :, 3] = np.minimum(source[:, :, 3], alpha)
    output = io.BytesIO()
    Image.fromarray(source).save(output, format="PNG", dpi=(300, 300), optimize=True)
    return output.getvalue()


def _copy_output(image: dict[str, Any], job_id: str, index: int, transparent: bool) -> str:
    query = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")})
    suffix = ".png" if transparent else (Path(str(image["filename"])).suffix or ".png")
    target = RESULT_DIR / f"{job_id}-{index}{suffix}"
    with urllib.request.urlopen(f"{COMFY_BASE_URL}/view?{query}", timeout=30) as response:
        data = response.read()
    if transparent:
        target.write_bytes(_transparent_motif(data))
    else:
        source = Image.open(io.BytesIO(data)).convert("RGB")
        normalized = io.BytesIO(); source.save(normalized, format="PNG", dpi=(300, 300), optimize=True)
        target = target.with_suffix(".png"); target.write_bytes(normalized.getvalue())
    return f"/results/{target.name}"


def _run_print_job(job: PrintJob) -> None:
    seed = job.request.seed if job.request.seed is not None else int.from_bytes(os.urandom(6), "big")
    image_name = _print_reference_source(job.request, job.id)
    try:
        if _use_image_api():
            job.status, job.stage, job.progress = "running", "generating", 12
            prompt = _print_positive(job.request)
            image_path = (_work_image_dir() / image_name) if image_name else None
            for candidate in range(job.request.candidate_count):
                data = _image_api_call("edit" if image_path else "generate", prompt, image_path)
                job.result_urls.append(_save_result_bytes(data, job.id, candidate, job.request.mode == "motif"))
                job.stage = f"generating_{candidate + 1}_of_{job.request.candidate_count}"
                job.progress = min(92, 18 + round((candidate + 1) * 72 / job.request.candidate_count))
            job.status, job.stage, job.progress = "succeeded", "completed", 100
            return
        with gpu_lock():
            job.status, job.stage, job.progress = "running", "model_loading", 8
            for candidate in range(job.request.candidate_count):
                workflow = (_image_edit_workflow(_print_positive(job.request), seed + candidate, image_name, False, 0.9)
                            if image_name else _workflow(job.request, seed + candidate))
                submitted = _json_request(f"{COMFY_BASE_URL}/prompt", {"prompt": workflow}, timeout=30)
                prompt_id = str(submitted["prompt_id"])
                started = time.time()
                while not job.cancelled.is_set():
                    history = _json_request(f"{COMFY_BASE_URL}/history/{prompt_id}", timeout=10)
                    record = history.get(prompt_id)
                    if record:
                        images = [image for node in record.get("outputs", {}).values() for image in node.get("images", [])]
                        if not images:
                            raise RuntimeError("generation completed without an image output")
                        job.result_urls.append(_copy_output(images[0], job.id, candidate, job.request.mode == "motif"))
                        break
                    job.stage = f"generating_{candidate + 1}_of_{job.request.candidate_count}"
                    job.progress = min(92, 10 + round(candidate * 72 / job.request.candidate_count) + int((time.time() - started) / 4))
                    time.sleep(2)
            if not job.cancelled.is_set():
                job.status, job.stage, job.progress = "succeeded", "completed", 100
                return
            job.status, job.stage = "cancelled", "cancelled"
    finally:
        if image_name:
            (_work_image_dir() / image_name).unlink(missing_ok=True)


def _run_comfy_edit_once(job: ImageEditJob, prompt: str, seed: int, image_name: str, output_index: int) -> str:
    with gpu_lock():
        job.status, job.stage, job.progress = "running", "understanding_design", 12
        allow_print_text = job.kind == "garment_print" and isinstance(job.request, GarmentPrintJobRequest) and _requests_typography(job.request)
        edit_denoise = 0.38 if job.kind == "garment_print" else 0.7
        submitted = _json_request(f"{COMFY_BASE_URL}/prompt", {"prompt": _image_edit_workflow(prompt, seed, image_name, allow_print_text, edit_denoise)}, timeout=30)
        prompt_id = str(submitted["prompt_id"]); started = time.time()
        while not job.cancelled.is_set():
            record = _json_request(f"{COMFY_BASE_URL}/history/{prompt_id}", timeout=10).get(prompt_id)
            if record:
                images = [image for node in record.get("outputs", {}).values() for image in node.get("images", [])]
                if not images:
                    raise RuntimeError("generation completed without an image output")
                return _copy_output(images[0], job.id, output_index, False)
            job.stage = "generating_preview"; job.progress = min(88, 18 + int((time.time() - started) / 3)); time.sleep(2)
    raise RuntimeError("generation cancelled")


def _run_image_edit_job(job: ImageEditJob) -> None:
    seed = job.request.seed if job.request.seed is not None else int.from_bytes(os.urandom(6), "big")
    if job.kind == "design_preview":
        request = job.request; assert isinstance(request, DesignPreviewJobRequest)
        prompt = _design_prompt(request)
        image_name = _reference_image(request, job.id)
        job.prompt_used = prompt
    else:
        request = job.request; assert isinstance(request, GarmentPrintJobRequest)
        image_name, prompt = _garment_source(request, job.id), _garment_print_prompt(request)
        asset_path = RESULT_DIR / Path(urllib.parse.urlparse(request.selected_print_url).path).name
        with Image.open(asset_path) as asset_image:
            width, height = asset_image.size
        job.production_asset = {"url": f"/results/{asset_path.name}", "mode": request.selected_print_mode, "format": "PNG", "width_px": width, "height_px": height, "dpi": 300, "color_space": "sRGB", "transparent": request.selected_print_mode == "motif"}
        job.prompt_used = prompt
    try:
        if _use_image_api():
            image_path = _work_image_dir() / image_name
            job.status, job.stage, job.progress = "running", "generating_preview", 18
            if job.kind == "design_preview":
                request = job.request; assert isinstance(request, DesignPreviewJobRequest)
                attempt_prompt = prompt
                for attempt in range(2):
                    job.attempt_count = attempt + 1
                    data = _image_api_call("edit", attempt_prompt, image_path)
                    candidate_path = _work_image_dir() / f"patternmate-validation-{job.id}-{attempt}.png"
                    candidate_path.write_bytes(data)
                    try:
                        job.stage, job.progress = "validating_components", 72 + attempt * 10
                        validation = _validate_design_image(candidate_path, request)
                    finally:
                        candidate_path.unlink(missing_ok=True)
                    job.visual_validation = validation
                    if validation.get("matches"):
                        job.result_urls.append(_save_result_bytes(data, job.id, attempt, False))
                        break
                    if attempt == 0:
                        job.stage, job.progress = "correcting_components", 82
                        attempt_prompt = _correction_prompt(prompt, validation)
                if not job.result_urls:
                    raise RuntimeError("2D preview did not match the selected garment components after correction")
            else:
                job.attempt_count = 1
                data = _image_api_call("edit", prompt, image_path)
                job.result_urls.append(_save_result_bytes(data, job.id, 0, False))
            job.status, job.stage, job.progress = "succeeded", "completed", 100
            return
        if job.kind == "design_preview":
            request = job.request; assert isinstance(request, DesignPreviewJobRequest)
            attempt_prompt = prompt
            for attempt in range(2):
                job.attempt_count = attempt + 1
                result_url = _run_comfy_edit_once(job, attempt_prompt, seed + attempt, image_name, attempt)
                result_path = RESULT_DIR / Path(result_url).name
                job.stage, job.progress = "validating_components", 72 + attempt * 10
                validation = _validate_design_image(result_path, request)
                job.visual_validation = validation
                if validation.get("matches"):
                    job.result_urls.append(result_url)
                    break
                result_path.unlink(missing_ok=True)
                if attempt == 0:
                    job.stage, job.progress = "correcting_components", 82
                    attempt_prompt = _correction_prompt(prompt, validation)
            if not job.result_urls:
                raise RuntimeError("2D preview did not match the selected garment components after correction")
        else:
            job.attempt_count = 1
            job.result_urls.append(_run_comfy_edit_once(job, prompt, seed, image_name, 0))
        job.status, job.stage, job.progress = "succeeded", "completed", 100
    finally:
        (_work_image_dir() / image_name).unlink(missing_ok=True)


def _worker() -> None:
    while True:
        job_id = JOB_QUEUE.get()
        job = JOBS.get(job_id)
        if not job:
            continue
        try:
            _run_image_edit_job(job) if isinstance(job, ImageEditJob) else _run_print_job(job)
        except Exception as exc:
            print(f"print job {job.id} failed: {exc}", flush=True)
            message = str(exc)[:300] if isinstance(job, ImageEditJob) else "Pattern generation failed"
            job.status, job.stage, job.error = "failed", "failed", message
        finally:
            JOB_QUEUE.task_done()


threading.Thread(target=_worker, daemon=True, name="patternmate-qwen-image").start()


def _job_dict(job: PrintJob | ImageEditJob) -> dict[str, Any]:
    result = {"job_id": job.id, "status": job.status, "progress": job.progress, "stage": job.stage, "result_urls": job.result_urls, "error": job.error}
    if isinstance(job, ImageEditJob):
        if job.prompt_used:
            result["prompt"] = job.prompt_used
        if job.kind == "garment_print":
            result["preview_url"] = job.result_urls[0] if job.result_urls else None
            result["production_asset"] = job.production_asset
        if job.kind == "design_preview":
            request = job.request; assert isinstance(request, DesignPreviewJobRequest)
            result["style_key"] = request.style_key
            result["visual_validation"] = job.visual_validation
            result["attempt_count"] = job.attempt_count
    return result


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "chat_ready": _chat_ready(),
        "print_ready": _image_ready(),
        "image_backend": "api" if _use_image_api() else "comfy",
        "image_api_ready": _image_api_ready(),
        "image_model": (_image_api_config() or {}).get("name"),
    }


@app.post("/print/jobs", status_code=202)
def create_print_job(request: PrintJobRequest) -> dict[str, Any]:
    if not _image_ready():
        raise HTTPException(status_code=503, detail="Pattern generation is temporarily unavailable")
    job = PrintJob(id=uuid.uuid4().hex, request=request)
    with JOBS_LOCK:
        JOBS[job.id] = job
    JOB_QUEUE.put(job.id)
    return _job_dict(job)


@app.get("/print/jobs/{job_id}")
def get_print_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Print job not found")
    return _job_dict(job)


@app.delete("/print/jobs/{job_id}")
def cancel_print_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Print job not found")
    job.cancelled.set()
    return _job_dict(job)


def _enqueue_image_edit(request: DesignPreviewJobRequest | GarmentPrintJobRequest, kind: Literal["design_preview", "garment_print"]) -> dict[str, Any]:
    if not _image_ready(): raise HTTPException(status_code=503, detail="Image generation is temporarily unavailable")
    job = ImageEditJob(id=uuid.uuid4().hex, request=request, kind=kind)
    with JOBS_LOCK: JOBS[job.id] = job
    JOB_QUEUE.put(job.id); return _job_dict(job)


@app.post("/design-preview/jobs", status_code=202)
def create_design_preview_job(request: DesignPreviewJobRequest) -> dict[str, Any]: return _enqueue_image_edit(request, "design_preview")


@app.post("/design-preview/prompt")
def preview_design_prompt(request: DesignPreviewJobRequest) -> dict[str, Any]:
    return {"prompt": _design_prompt(request), "user_instruction": request.user_instruction or (request.prompt or "")}


@app.get("/design-preview/jobs/{job_id}")
def get_design_preview_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not isinstance(job, ImageEditJob) or job.kind != "design_preview": raise HTTPException(status_code=404, detail="Design preview job not found")
    return _job_dict(job)


@app.post("/garment-print/jobs", status_code=202)
def create_garment_print_job(request: GarmentPrintJobRequest) -> dict[str, Any]: return _enqueue_image_edit(request, "garment_print")


@app.get("/garment-print/jobs/{job_id}")
def get_garment_print_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not isinstance(job, ImageEditJob) or job.kind != "garment_print": raise HTTPException(status_code=404, detail="Garment print job not found")
    return _job_dict(job)


@app.get("/results/{filename}")
def result(filename: str):
    path = RESULT_DIR / Path(filename).name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Result not found")
    return FileResponse(path)


@app.post("/v1/chat/completions")
async def chat_proxy(request: Request):
    payload = await request.body()
    headers = {"Content-Type": "application/json", "Authorization": request.headers.get("authorization", "Bearer local")}
    try:
        with gpu_lock():
            upstream = urllib.request.Request(f"{VLLM_BASE_URL}/chat/completions", data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(upstream, timeout=600) as response:
                return JSONResponse(json.loads(response.read().decode("utf-8")), status_code=response.status)
    except urllib.error.HTTPError as exc:
        return JSONResponse(json.loads(exc.read().decode("utf-8")), status_code=exc.code)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Design assistance is temporarily unavailable") from exc
