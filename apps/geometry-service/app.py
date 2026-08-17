from __future__ import annotations

import json
import asyncio
import base64
import os
import re
import io
import ssl
import subprocess
import tempfile
import threading
import zipfile
import sys
import urllib.request
from collections import OrderedDict
from urllib.parse import quote
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="CHI27 Geometry Service", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
ROOT = Path(os.getenv("CHI27_ROOT", Path(__file__).resolve().parents[2]))


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader — no extra dependency. Does not override existing env."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT / ".env")
_load_dotenv(Path(__file__).resolve().parent / ".env")
READY = ROOT / "data" / "ir" / "v1_rule_ready"
if not READY.exists():
    READY = ROOT / "_handoff_pack" / "v1_rule_ready"
HANDOFF_SCRIPTS = ROOT / "_handoff_pack" / "scripts"
if HANDOFF_SCRIPTS.exists():
    sys.path.insert(0, str(HANDOFF_SCRIPTS))
PUBLIC_ROOT = ROOT / "public"
if not PUBLIC_ROOT.exists():
    PUBLIC_ROOT = ROOT / "apps" / "web" / "public"
WWW_ROOT = Path(os.getenv("PATTERNMATE_WWW_ROOT", "/var/www/chi27"))

from composition_engine import build_index, compose_recipe, pattern_catalog, remix_readiness
from data_registry import BLOCKED_DONORS, build_dxf_index, data_status
from relabel_queue import QUEUE as RELABEL_QUEUE, apply_labels, piece_outlines, summarize, svg_payload
from review_ledger import append_review_decision, read_review_history

TSHIRT_V2 = ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir"
SHIRT_V2 = ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir"
PATTERN_CATALOG_PATH = ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json"
DXF_INDEX = build_dxf_index((ROOT / "data" / "seed" / "dxf", ROOT / "tmp", ROOT / "_handoff_pack"))
IR_INDEX = build_index(READY, TSHIRT_V2, SHIRT_V2)
for _case_id, _ir in IR_INDEX.items():
    _match = DXF_INDEX.get(_case_id.upper())
    _ir["_dxf_available"] = bool(_match and _match.selected)
    # Geometry source of truth lives in the DXF; IR stamps piece/line semantics.
    _ir["_dxf_path"] = str(_match.selected) if (_match and _match.selected) else None
PATTERN_CATALOG = pattern_catalog(PATTERN_CATALOG_PATH, IR_INDEX)
ALLOWED_STYLE_TAGS = sorted({
    str(tag)
    for ir in IR_INDEX.values()
    if not ir.get("_donor_only")
    for tag in ((ir.get("design_semantics") or {}).get("style_tags") or [])
    if tag
})
ALLOWED_FIT_VALUES = sorted({
    str(fit)
    for ir in IR_INDEX.values()
    if not ir.get("_donor_only")
    for fit in [((ir.get("design_semantics") or {}).get("fit"))]
    if fit not in (None, "", "unknown")
})
ALLOWED_USAGE_VALUES = sorted({
    str(use)
    for ir in IR_INDEX.values()
    if not ir.get("_donor_only")
    for use in ((ir.get("design_semantics") or {}).get("usage") or [])
    if use not in (None, "", "unknown")
})
CHIP_LABELS_ZH = {
    "tshirt": "T恤", "shirt": "衬衫",
    "sleeveless": "无袖", "short": "短袖", "long": "长袖",
    "v-neck": "V领", "crew": "圆领", "polo": "Polo领",
    "relaxed": "宽松", "regular": "合体", "fitted": "修身", "oversized": "超宽松", "tight": "紧身",
    "basic": "基础", "casual": "休闲", "unisex": "中性", "streetwear": "街头", "vintage_washed": "复古水洗",
    "artistic": "艺术", "athleisure": "运动休闲", "commuter": "通勤", "preppy": "学院", "elegant": "优雅",
    "sweet": "甜美", "avant_garde": "先锋", "niche": "小众", "retro": "复古", "minimal": "简约",
    "conservative": "保守", "quiet_luxury": "静奢", "romantic": "浪漫", "sporty": "运动", "hot_girl": "辣妹",
    "business": "商务", "outdoor": "户外", "workwear": "工装", "oriental": "东方", "punk": "朋克", "y2k": "Y2K",
    "workplace": "职场", "fashion": "时尚", "sports": "运动",
    "low": "低活动", "medium": "中等活动", "high": "高活动",
    "slim": "偏瘦", "average": "匀称", "full": "丰满",
}
ALLOWED_SLEEVE_VALUES = ("sleeveless", "short", "long")
ALLOWED_NECKLINE_VALUES = ("v-neck", "crew", "polo")
ALLOWED_FAMILY_VALUES = ("tshirt", "shirt")
COMMON_STYLE_TAGS = ("casual", "minimal", "streetwear", "commuter", "elegant")
SKIP_OPTION = {"value": "_skip", "label_zh": "先跳过", "label_en": "Skip for now"}
SKIP_TEXTS = {"先跳过", "都可以", "都行", "无所谓", "skip", "either is fine"}
USAGE_PROMPT_ZH = {"casual": "日常休闲", "workplace": "通勤职场", "sports": "运动场景", "fashion": "时尚场合"}


def _chip_option(value: str) -> dict[str, str]:
    return {"value": value, "label_zh": CHIP_LABELS_ZH.get(value, value.replace("_", " ")), "label_en": value.replace("_", " ")}


def suggestion_chips() -> list[dict[str, Any]]:
    families = sorted({
        "tshirt" if str((ir.get("design_semantics") or {}).get("category")) in {"tshirt", "polo"} else "shirt"
        for ir in IR_INDEX.values()
        if not ir.get("_donor_only") and str((ir.get("design_semantics") or {}).get("category")) in {"tshirt", "polo", "shirt", "blouse"}
    })
    return [group for group in (
        {"field": "family", "multi": False, "title_zh": "品类", "title_en": "Family", "options": [_chip_option(value) for value in families]},
        {"field": "fit", "multi": False, "title_zh": "松量", "title_en": "Fit", "options": [_chip_option(value) for value in ALLOWED_FIT_VALUES]},
        {"field": "sleeve", "multi": False, "title_zh": "袖型", "title_en": "Sleeve", "options": [_chip_option(value) for value in ALLOWED_SLEEVE_VALUES]},
        {"field": "neckline", "multi": False, "title_zh": "领型", "title_en": "Neckline", "options": [_chip_option(value) for value in ALLOWED_NECKLINE_VALUES]},
        {"field": "styles", "multi": True, "title_zh": "风格", "title_en": "Style", "options": [_chip_option(value) for value in ALLOWED_STYLE_TAGS]},
    ) if group["options"]]


SUGGESTION_CHIPS = suggestion_chips()
CHIP_ALLOWED = {
    "family": set(ALLOWED_FAMILY_VALUES),
    "fit": set(ALLOWED_FIT_VALUES) | {"relaxed", "fitted", "regular"},
    "sleeve": set(ALLOWED_SLEEVE_VALUES),
    "neckline": set(ALLOWED_NECKLINE_VALUES),
    "styles": set(ALLOWED_STYLE_TAGS),
    "category": {"tshirt", "shirt", "polo"},
    "activity": {"low", "medium", "high"},
    "usage": set(ALLOWED_USAGE_VALUES),
    "general_shape": {"slim", "average", "full"},
}


class Job(BaseModel):
    jobId: str
    userId: str
    projectId: str
    revision: int
    kind: str


class PreviewRequest(BaseModel):
    case_id: str
    measurements: dict[str, str] = Field(default_factory=dict)


class CompositionRecipe(BaseModel):
    family: str
    sex: str = "female"
    base_case_id: str
    measurements_cm: dict[str, Any] = Field(default_factory=dict)
    fit: str = "regular"
    ease_cm: float = 8.0
    material_id: str = ""
    fabric_color: str = "#ffffff"
    compact_layout: bool = False
    skip_grading: bool = False
    selections: dict[str, str | None] = Field(default_factory=dict)
    base_option_ids: dict[str, str | None] = Field(default_factory=dict)
    intent_constraints: dict[str, Any] = Field(default_factory=dict)
    execution_mode: str = "simple_piece_swap"  # tshirt frozen; shirt remapped server-side to shirt_strategy
    compose_version: str | None = None


class ProductionRequest(BaseModel):
    project_name: str = "Smart Pattern Design"
    recipe: CompositionRecipe
    design: dict[str, Any] = Field(default_factory=dict)


class ReviewDecisionRequest(BaseModel):
    recipe_hash: str
    operation_id: str
    decision: str
    reviewer: str = "anonymous"
    note: str = ""
    geometry_hash_before: str | None = None
    geometry_hash_after: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class RelabelSaveRequest(BaseModel):
    piece_roles: dict[str, str] = Field(default_factory=dict)
    sleeve_style: str
    notes: str = ""
    reviewer: str = "expert"


class SleeveVlmSandboxRequest(BaseModel):
    """Minimal VLM sleeve critic sandbox. Recipe + optional model overrides."""
    recipe: CompositionRecipe
    scales: list[float] = Field(default_factory=lambda: [0.9, 1.0, 1.15])
    call_vlm: bool = True
    png_width: int = 640
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: str | None = None


class StrategyComposeSandboxRequest(BaseModel):
    """LLM judges edit scope, then compose once for sandbox preview."""
    recipe: CompositionRecipe
    group: str = "sleeve"  # sleeve | neckline | cuff
    use_llm: bool = True
    png_width: int = 720
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: str | None = None


class ShirtComposeSandboxRequest(BaseModel):
    """Shirt strategy compose + optional A/B vs legacy batch_preview."""
    recipe: CompositionRecipe
    compare_legacy: bool = True
    png_width: int = 720


class AnalyzeRequest(BaseModel):
    text: str = ""
    tags: list[str] = Field(default_factory=list)
    category: str | None = None
    language: str = "zh"


class TranslateRequest(BaseModel):
    text: str = ""
    target_language: str = "en"


class ConversationMessage(BaseModel):
    role: str
    content: str


class DesignConversationRequest(BaseModel):
    messages: list[ConversationMessage] = Field(default_factory=list)
    image_data_urls: list[str] = Field(default_factory=list, max_length=2)
    language: str = "zh"
    intent_version: int = 0
    current_intent: dict[str, Any] = Field(default_factory=dict)
    confirmed: dict[str, Any] = Field(default_factory=dict)
    skip_model: bool = False


class ConversationCorrectionRequest(DesignConversationRequest):
    card_id: str
    field: str
    values: list[str] = Field(default_factory=list)
    custom_text: str = ""


def load_ir(case_id: str) -> dict[str, Any]:
    ir = IR_INDEX.get(case_id)
    if not ir:
        raise HTTPException(status_code=404, detail=f"IR not found: {case_id}")
    return ir


def find_dxf(case_id: str) -> Path:
    match = DXF_INDEX.get(case_id.upper())
    if match and match.selected:
        return match.selected
    raise HTTPException(status_code=404, detail=f"Annotated DXF not found: {case_id}")


def reference_base_option_ids(ir: dict[str, Any], family: str | None) -> dict[str, str]:
    if family not in {"tshirt", "shirt"}:
        return {}
    labels = ((ir.get("design_semantics_extra") or {}).get("part_labels") or {})
    semantics = ir.get("design_semantics") or {}
    roles = {str(piece.get("piece_role") or "") for piece in ir.get("piece_instances") or []}

    def label_slug(*keys: str) -> str | None:
        for key in keys:
            value = labels.get(key)
            if isinstance(value, dict) and value.get("slug"):
                return str(value["slug"])
        return None

    slugs: dict[str, str | None]
    if family == "tshirt":
        slugs = {
            "neckline": label_slug("neckline") or ("crew" if roles & {"neck_binding", "neck_rib"} else None),
            "sleeve": label_slug("sleeve_style", "sleeve") or ("set-in" if roles & {"sleeve", "sleeve_left", "sleeve_right"} else None),
            "garment_length": "regular",
        }
    else:
        fit = str(semantics.get("fit") or "regular")
        silhouette = label_slug("silhouette") or {
            "relaxed": "relaxed-h", "fitted": "fitted-x", "oversized": "oversized",
        }.get(fit, "regular-fit")
        slugs = {
            "silhouette": silhouette,
            "collar": label_slug("collar") or ("pointed" if roles & {"collar", "collar_stand"} else None),
            "placket": label_slug("placket") or ("full" if "front_placket" in roles else None),
            "cuff": label_slug("cuff") or ("regular" if roles & {"cuff", "rib_cuff"} else None),
            "sleeve": label_slug("sleeve_style", "sleeve") or ("regular" if roles & {"sleeve", "sleeve_left", "sleeve_right"} else None),
            "garment_length": "regular",
        }
    result: dict[str, str] = {}
    for group, slug in slugs.items():
        if not slug:
            continue
        option = next((row for row in PATTERN_CATALOG["options"] if row["group"] == group and row["slug"] == slug and (row["family"] == family or family in set(row.get("compatible_families") or []))), None)
        if option:
            result[group] = option["id"]
    return result


def run_composition(recipe: CompositionRecipe) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if recipe.family not in {"tshirt", "shirt"}:
        raise HTTPException(status_code=422, detail="family 必须是 tshirt 或 shirt")
    if recipe.sex not in {"female", "male_general"}:
        raise HTTPException(status_code=422, detail="sex 必须是 female 或 male_general")
    try:
        return compose_recipe(recipe.model_dump(), IR_INDEX, PATTERN_CATALOG)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"组合几何生成失败: {exc}") from exc


_COMPOSE_CACHE: OrderedDict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = OrderedDict()
_COMPOSE_INFLIGHT: dict[str, threading.Event] = {}
_COMPOSE_GATE = threading.Lock()
_COMPOSE_INFLIGHT_COUNT = 0
_COMPOSE_CACHE_SIZE = 32


def run_composition_cached(recipe: CompositionRecipe) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    global _COMPOSE_INFLIGHT_COUNT
    key = recipe.model_dump_json()
    with _COMPOSE_GATE:
        cached = _COMPOSE_CACHE.get(key)
        if cached is not None:
            _COMPOSE_CACHE.move_to_end(key)
            return cached
        waiter = _COMPOSE_INFLIGHT.get(key)
        owner = waiter is None
        if owner:
            waiter = threading.Event()
            _COMPOSE_INFLIGHT[key] = waiter
    if not owner:
        waiter.wait(timeout=90)
        with _COMPOSE_GATE:
            cached = _COMPOSE_CACHE.get(key)
        if cached is not None:
            return cached
        return run_composition(recipe)
    with _COMPOSE_GATE:
        _COMPOSE_INFLIGHT_COUNT += 1
    try:
        result = run_composition(recipe)
        with _COMPOSE_GATE:
            _COMPOSE_CACHE[key] = result
            _COMPOSE_CACHE.move_to_end(key)
            while len(_COMPOSE_CACHE) > _COMPOSE_CACHE_SIZE:
                _COMPOSE_CACHE.popitem(last=False)
        return result
    finally:
        with _COMPOSE_GATE:
            _COMPOSE_INFLIGHT_COUNT = max(0, _COMPOSE_INFLIGHT_COUNT - 1)
            _COMPOSE_INFLIGHT.pop(key, None)
            waiter.set()


def parse_design_intent(text: str, requested_category: str | None = None) -> dict[str, Any]:
    normalized = text.strip().lower()
    def latest(options: dict[str, tuple[str, ...]], default: str | None = None) -> str | None:
        matches = [
            (normalized.rfind(token), value)
            for value, tokens in options.items()
            for token in tokens
            if normalized.rfind(token) >= 0
        ]
        return max(matches)[1] if matches else default

    def latest_sleeve() -> str | None:
        events: list[tuple[int, int, str | None]] = []
        for value, tokens in {
            "sleeveless": ("无袖", "sleeveless"),
            "short": ("短袖", "short sleeve"),
            "long": ("长袖", "long sleeve"),
        }.items():
            for token in tokens:
                events.extend((match.start(), 0, value) for match in re.finditer(re.escape(token), normalized))
        cancel_patterns = (
            r"(?:不要|取消|去掉|删除|不做|不采用|不需要|改掉)\s*(?:无袖|sleeveless)",
            r"(?:无袖|sleeveless)\s*(?:不要|取消|去掉|删除)",
        )
        for pattern in cancel_patterns:
            events.extend((match.end(), 1, None) for match in re.finditer(pattern, normalized))
        return max(events, default=(-1, -1, None))[-1]

    category = latest({"tshirt": ("t恤", "t-shirt", "tee"), "shirt": ("衬衫", "shirt", "blouse"), "polo": ("polo",)}, requested_category)
    sleeve = latest_sleeve()
    fit = latest({"relaxed": ("宽松", "oversize", "relaxed"), "regular": ("合体", "regular"), "fitted": ("修身", "贴身", "fitted")})
    neckline = latest({"v-neck": ("v领", "v 领", "v-neck"), "crew": ("圆领", "crew neck"), "polo": ("polo领", "polo collar")})
    length_matches = list(re.finditer(r"(?:衣长|服装长度|长度)\s*(?:约|大约)?\s*(\d+(?:\.\d+)?)\s*(?:cm|厘米)?", normalized))
    length_match = length_matches[-1] if length_matches else None
    style_map = {"休闲": "casual", "通勤": "commuter", "运动": "sporty", "复古": "retro", "街头": "streetwear", "简约": "minimal", "优雅": "elegant", "甜美": "sweet", "户外": "outdoor", "商务": "business"}
    styles = [english for chinese, english in style_map.items() if chinese in normalized]
    travel_scene = any(token in normalized for token in ("旅游", "旅行", "度假", "travel", "vacation"))
    if travel_scene:
        styles.extend(style for style in ("casual", "outdoor") if style not in styles)
    usage = latest({
        "casual": ("日常休闲", "日常", "旅游", "旅行", "度假", "出门"),
        "workplace": ("通勤职场", "职场", "上班"),
        "sports": ("运动场景", "跑步", "健身"),
        "fashion": ("时尚场合", "时尚"),
    }, "casual" if travel_scene else None)
    activity = latest({
        "high": ("高活动", "活动量大", "运动量大", "high activity"),
        "medium": ("中等活动", "适度活动", "medium activity"),
        "low": ("低活动", "活动量小", "low activity"),
    }, "high" if travel_scene else None)
    general_shape = latest({"slim": ("偏瘦",), "average": ("匀称",), "full": ("丰满",)})
    labels = []
    category_labels = {"tshirt": "T恤", "shirt": "衬衫", "polo": "Polo"}
    sleeve_labels = {"sleeveless": "无袖", "short": "短袖", "long": "长袖"}
    if category:
        labels.append(category_labels.get(category, category))
    if length_match:
        labels.append(f"衣长 {length_match.group(1)} cm")
    if sleeve:
        labels.append(sleeve_labels[sleeve])
    if fit:
        labels.append({"relaxed": "宽松", "regular": "合体", "fitted": "修身"}.get(fit, fit))
    if neckline:
        labels.append({"v-neck": "V领", "crew": "圆领", "polo": "Polo领"}[neckline])
    labels.extend([key for key, value in style_map.items() if value in styles])
    if usage:
        labels.append(USAGE_PROMPT_ZH.get(usage, usage))
    if activity:
        labels.append({"high": "高活动性", "medium": "中等活动性", "low": "低活动性"}[activity])
    if general_shape:
        labels.append({"slim": "偏瘦", "average": "匀称", "full": "丰满"}[general_shape])
    return {"family": "tshirt" if category in {"tshirt", "polo"} else category, "category": category, "sleeve": sleeve, "target_length_cm": float(length_match.group(1)) if length_match else None, "fit": fit, "neckline": neckline, "activity": activity, "usage": usage, "styles": styles, "general_shape": general_shape, "labels": labels, "source_text": text}


def design_model_config() -> dict[str, Any] | None:
    """Gemini (or OpenAI-compatible) config for design conversation only."""
    enabled = (os.getenv("DESIGN_MODEL_ENABLED") or "true").strip().lower()
    if enabled in {"0", "false", "no"}:
        return None
    base_url = (os.getenv("DESIGN_MODEL_BASE_URL") or os.getenv("MODEL_BASE_URL") or "").strip().rstrip("/")
    model_name = (os.getenv("DESIGN_MODEL_NAME") or os.getenv("MODEL_NAME") or "").strip()
    api_key = (os.getenv("DESIGN_MODEL_API_KEY") or os.getenv("MODEL_API_KEY") or "").strip()
    if not base_url or not model_name or not api_key or api_key.startswith("fill-your-"):
        return None
    timeout = float(os.getenv("DESIGN_MODEL_TIMEOUT_SECONDS") or os.getenv("MODEL_TIMEOUT_SECONDS") or "12")
    verify = (os.getenv("DESIGN_MODEL_SSL_VERIFY") or os.getenv("MODEL_SSL_VERIFY") or "true").strip().lower() != "false"
    return {"base_url": base_url, "name": model_name, "key": api_key, "timeout": timeout, "verify": verify}


def _catalog_dimension_guide() -> dict[str, Any]:
    return {
        "品类": ["T恤", "Polo", "衬衫"],
        "场景": [USAGE_PROMPT_ZH.get(value, value) for value in ("casual", "workplace", "sports", "fashion") if value in ALLOWED_USAGE_VALUES],
        "风格": [CHIP_LABELS_ZH.get(value, value) for value in COMMON_STYLE_TAGS if value in ALLOWED_STYLE_TAGS],
        "合体度": ["宽松", "合体", "修身"],
        "领型": ["V领", "圆领", "Polo领"],
        "袖长": ["无袖", "短袖", "长袖"],
        "活动量": ["低活动", "中等活动", "高活动"],
        "身材": ["偏瘦", "匀称", "丰满"],
        "廓形": "H / X / A 等",
        "覆盖度": "覆盖多少身体",
        "视觉重点": "领口 / 上身等",
    }


DESIGN_ASSISTANT_SYSTEM_PROMPT = """你是 PatternMate 设计助手。只返回 JSON。

用户在描述「什么样的人、什么场合、什么感觉」时，先根据他说的身份和需求追问，再落到我们的数据库维度。不要只对老人这样问，谁来都要对应着挖一句。

数据库维度互不相同，每轮只问 next_label_field，不要连着把风格、款式、场景问成同一件事：
- family 品类：T恤 / Polo / 衬衫
- styles 风格：甜美、街头、简约、通勤、优雅等观感
- usage 场景：日常休闲、通勤职场、运动场景、时尚场合
- fit 合体度、neckline 领型、sleeve 袖长、activity 活动量、general_shape 身材

对应追问（每轮只问一件，不超过100字，问完后界面会推出该维度的选项）：
- 老人、长辈、年纪大：先问身材，不要急着锁 T恤还是衬衫
- 学生、年轻人：先问风格（甜美/街头/简约等），不要问成「穿什么款式」
- 上班、通勤、职场：问穿着场景（日常/职场/运动/时尚），不要再问风格标签
- 运动、跑步、健身：问活动量
- 想要漂亮、时尚、气质：问更偏向哪种风格
- 只说舒适、舒服：问身材
- 旅游、度假：问穿着场景
- 提到胖、瘦、身材：问清楚偏瘦/匀称/丰满
- 用户已经点名 T恤/Polo/衬衫：不要再问品类

不要只凭身份或形容词就填写 family、category、styles；没说清就留 null，用问题去挖。
assistant_message 用用户的语言，一句短问，先复述他刚说的，再只问 next_label_field。
问法必须和维度一致：styles=更想要哪种风格，usage=主要在什么场合穿，family=T恤、Polo还是衬衫。
"""


def _limit_assistant(text: str, limit: int = 100) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    clipped = text[:limit].rstrip("，,。.!！?？、；; ")
    return clipped or text[:limit]


def enrich_intent_with_model(text: str, baseline: dict[str, Any], messages: list[dict[str, str]] | None = None, language: str = "zh", image_data_urls: list[str] | None = None, next_label_field: str | None = None) -> tuple[dict[str, Any], str | None, bool]:
    """Optionally enrich rule parsing through a server-side OpenAI-compatible model API."""
    config = design_model_config()
    if not config or not (text or "").strip():
        return baseline, None, False
    base_url = config["base_url"]
    model_name = config["name"]
    api_key = config["key"]
    is_english = language.lower().startswith("en")
    schema_prompt = {
        "task": "Interpret the cumulative apparel conversation. Later corrections override earlier requirements. Return JSON only.",
        "catalog_dimensions": _catalog_dimension_guide(),
        "allowed": {
            "family": ["tshirt", "shirt", None],
            "category": ["tshirt", "shirt", "polo", None],
            "sleeve": ["sleeveless", "short", "long", None],
            "fit": ["relaxed", "regular", "fitted", None],
            "neckline": ["v-neck", "crew", "polo", None],
            "activity": ["low", "medium", "high", None],
            "usage": list(ALLOWED_USAGE_VALUES) + [None],
            "styles": ALLOWED_STYLE_TAGS,
            "general_shape": ["slim", "average", "full", None],
        },
        "fields": ["family", "category", "sleeve", "target_length_cm", "fit", "neckline", "activity", "usage", "styles", "general_shape", "labels", "search_query", "assistant_message"],
        "baseline": baseline,
        "conversation": messages or [{"role": "user", "content": text}],
        "assistant_reply_language": "English" if is_english else "Simplified Chinese",
        "search_query_rule": "search_query is one Chinese sentence for CLIP retrieval, using category, fit, sleeve, neckline, style and scene words from the brief.",
        "ask_rule": "Follow the system prompt. assistant_message max 100 characters. Recap what they said, ask only next_label_field. styles=which look/style, usage=which wearing occasion, family=T-shirt/Polo/shirt. Do not ask style three times. Do not invent values outside allowed.",
        "next_label_field": next_label_field,
        "next_label_ask": {
            "family": "T恤、Polo 还是衬衫？",
            "styles": "更想要哪种风格？",
            "usage": "主要在什么场合穿？",
            "fit": "松量想要怎样？",
            "neckline": "领型呢？",
            "sleeve": "袖长呢？",
            "activity": "活动量大概怎样？",
            "general_shape": "身材偏瘦、匀称还是丰满？",
        }.get(next_label_field or "", ""),
    }
    images = [value for value in (image_data_urls or []) if re.match(r"^data:image/(?:png|jpeg|webp);base64,", value) and len(value) <= 6_000_000]
    user_content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(schema_prompt, ensure_ascii=False)}]
    user_content.extend({"type": "image_url", "image_url": {"url": value}} for value in images)
    payload = json.dumps({
        "model": model_name,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": DESIGN_ASSISTANT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        context = None if config["verify"] else ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=config["timeout"], context=context) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        parsed = json.loads(content)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return baseline, None, False
    enriched = dict(baseline)
    allowed_values = {
        "family": {"tshirt", "shirt", None},
        "category": {"tshirt", "shirt", "polo", None},
        "sleeve": {"sleeveless", "short", "long", None},
        "fit": {"relaxed", "regular", "fitted", None},
        "neckline": {"v-neck", "crew", "polo", None},
        "activity": {"low", "medium", "high", None},
        "usage": set(ALLOWED_USAGE_VALUES) | {None},
        "general_shape": {"slim", "average", "full", None},
    }
    for key, values in allowed_values.items():
        if parsed.get(key) in values:
            enriched[key] = parsed.get(key)
    if isinstance(parsed.get("usage"), list):
        picked = [str(value) for value in parsed["usage"] if str(value) in ALLOWED_USAGE_VALUES]
        if picked:
            enriched["usage"] = picked[0]
    if isinstance(parsed.get("target_length_cm"), (int, float)) and 20 <= float(parsed["target_length_cm"]) <= 200:
        enriched["target_length_cm"] = float(parsed["target_length_cm"])
    if isinstance(parsed.get("styles"), list):
        enriched["styles"] = [str(value) for value in parsed["styles"] if str(value) in ALLOWED_STYLE_TAGS]
    if isinstance(parsed.get("labels"), list):
        enriched["labels"] = [str(value) for value in parsed["labels"] if value][:12]
    if isinstance(parsed.get("search_query"), str) and parsed["search_query"].strip():
        enriched["search_query"] = parsed["search_query"].strip()[:160]
    assistant = _limit_assistant(str(parsed.get("assistant_message") or "").strip()) or None
    return enriched, assistant, True


def _ir_category(semantics: dict[str, Any]) -> str:
    return str(semantics.get("category") or "").lower()


def _ir_family(semantics: dict[str, Any]) -> str:
    category = _ir_category(semantics)
    if category in {"tshirt", "polo"}:
        return "tshirt"
    if category in {"shirt", "blouse"}:
        return "shirt"
    return category


def _wanted_usage(intent: dict[str, Any]) -> set[str]:
    value = intent.get("usage")
    if isinstance(value, str) and value not in (None, "", "unknown"):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value if item not in (None, "", "unknown")}
    return set()


def _matches_hard_intent(semantics: dict[str, Any], intent: dict[str, Any]) -> bool:
    family = _ir_family(semantics)
    category = _ir_category(semantics)
    wanted_family = intent.get("family")
    wanted_category = intent.get("category")
    if wanted_family and family and family != wanted_family:
        return False
    if wanted_category == "polo":
        return category == "polo"
    if wanted_category == "tshirt":
        return category == "tshirt"
    if wanted_category in {"shirt", "blouse"}:
        return category in {"shirt", "blouse"}
    return True


def _matches_selected_fields(semantics: dict[str, Any], intent: dict[str, Any], tags: list[str]) -> bool:
    if not _matches_hard_intent(semantics, intent):
        return False
    fit = intent.get("fit")
    ir_fit = semantics.get("fit")
    if fit and ir_fit not in (None, "", "unknown"):
        if ir_fit != fit and not (fit == "relaxed" and ir_fit == "oversized"):
            return False
    usage = _wanted_usage(intent)
    ir_usage = {str(item) for item in (semantics.get("usage") or []) if item not in (None, "", "unknown")}
    if usage and ir_usage and not (usage & ir_usage):
        return False
    activity = intent.get("activity")
    ir_activity = semantics.get("activity")
    if activity and ir_activity not in (None, "", "unknown") and ir_activity != activity:
        return False
    styles = {str(item) for item in (intent.get("styles") or []) if item} | {str(item) for item in tags if item in ALLOWED_STYLE_TAGS}
    ir_styles = {str(item) for item in (semantics.get("style_tags") or []) if item not in (None, "", "unknown")}
    if styles and ir_styles and not (styles & ir_styles):
        return False
    return True


def score_semantics(semantics: dict[str, Any], query: str, tags: list[str], intent: dict[str, Any]) -> tuple[float, list[str]]:
    haystack = json.dumps(semantics, ensure_ascii=False).lower()
    terms = [term.strip().lower() for term in (query + " " + " ".join(tags)).split() if term.strip()]
    lexical = sum(1 for term in terms if term in haystack) / max(len(terms), 1)
    semantic_category = _ir_category(semantics)
    semantic_family = _ir_family(semantics)
    wanted_category = intent.get("category")
    if wanted_category == "polo":
        category_score = 1.0 if semantic_category == "polo" else 0.0
    elif wanted_category == "tshirt":
        category_score = 1.0 if semantic_category == "tshirt" else 0.0
    elif wanted_category in {"shirt", "blouse"}:
        category_score = 1.0 if semantic_category in {"shirt", "blouse"} else 0.0
    else:
        category_score = 1.0 if intent.get("family") and semantic_family == intent["family"] else 0.0
    fit_score = 1.0 if intent.get("fit") and semantics.get("fit") == intent["fit"] else 0.0
    if intent.get("fit") == "relaxed" and semantics.get("fit") == "oversized":
        fit_score = 1.0
    styles = set(intent.get("styles") or []) | set(tags)
    semantic_styles = set(semantics.get("style_tags") or [])
    style_score = len(styles & semantic_styles) / max(len(styles), 1) if styles else 0.0
    usage = _wanted_usage(intent)
    ir_usage = {str(item) for item in (semantics.get("usage") or []) if item not in (None, "", "unknown")}
    usage_score = 1.0 if usage and ir_usage and usage & ir_usage else 0.0
    activity_score = 1.0 if intent.get("activity") and semantics.get("activity") == intent["activity"] else 0.0
    score = min(0.99, 0.46 * category_score + 0.14 * fit_score + 0.14 * style_score + 0.12 * usage_score + 0.08 * activity_score + 0.06 * lexical)
    reasons = []
    if category_score:
        reasons.append("品类匹配")
    if fit_score:
        reasons.append("版型松量匹配")
    if style_score:
        reasons.append("风格匹配")
    if usage_score:
        reasons.append("场景匹配")
    if activity_score:
        reasons.append("活动性匹配")
    return round(score, 4), reasons


FACET_LABELS = {
    "style_tags": "风格", "usage": "场景", "fit": "合体度", "silhouette": "廓形",
    "coverage": "覆盖度", "activity": "活动性", "visual_focus": "视觉重点",
    "target_gender": "目标性别", "general_shape": "体型倾向",
}


def semantic_facets(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facets = []
    for key, label in FACET_LABELS.items():
        scores: dict[str, float] = {}
        for row in results:
            raw = (row.get("semantics") or {}).get(key)
            values = raw if isinstance(raw, list) else [raw]
            for value in values:
                if value in (None, "", "unknown"):
                    continue
                scores[str(value)] = max(scores.get(str(value), 0.0), float(row["score"]))
        facets.append({
            "key": key,
            "label": label,
            "values": [{"value": value, "score": round(score, 4)} for value, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))],
        })
    return facets


def _clip_ranking_available() -> bool:
    from clip_rank import ranking_available

    return ranking_available(IR_INDEX)


def ranked_references(text: str, tags: list[str], intent: dict[str, Any]) -> list[dict[str, Any]]:
    from clip_rank import clip_query_text, score_clip

    query = clip_query_text(text, intent)
    clip_scores = score_clip(IR_INDEX, query)
    results = []
    for ir in IR_INDEX.values():
        if ir.get("_donor_only"):
            continue
        semantics = ir.get("design_semantics", {})
        score, reasons = score_semantics(semantics, text, tags, intent)
        if clip_scores is not None:
            clip = float(clip_scores.get(str(ir.get("case_id")), 0.0))
            score = round(min(0.99, 0.55 * clip + 0.45 * score), 4)
            reasons = ["CLIP图文相似"] + reasons
        results.append({"case_id": ir.get("case_id"), "score": score, "match_reasons": reasons, "semantics": semantics})
    results.sort(key=lambda item: (-item["score"], str(item["case_id"])))
    return results


def _is_skip_text(text: str) -> bool:
    return text.strip().lower() in {item.lower() for item in SKIP_TEXTS}


_NAMED_CATEGORY = re.compile(r"t恤|t-shirt|\btee\b|衬衫|polo|上衣", re.I)
_PROBE_LEADS = (
    (re.compile(r"老人|长辈|年纪|年龄大"), "general_shape"),
    (re.compile(r"学生|年轻|少年|少女"), "styles"),
    (re.compile(r"上班|通勤|职场|开会|办公"), "usage"),
    (re.compile(r"运动|跑步|健身|打球"), "activity"),
    (re.compile(r"漂亮|时尚|好看|气质|优雅"), "styles"),
    (re.compile(r"旅游|旅行|度假"), "usage"),
    (re.compile(r"胖|丰满|偏瘦|匀称|身材"), "general_shape"),
    (re.compile(r"舒适|舒服"), "general_shape"),
    (re.compile(r"我是|我希望|我想要|帮我"), "styles"),
)


def _named_category(text: str) -> bool:
    return bool(_NAMED_CATEGORY.search(text or ""))


def _probe_lead_field(text: str) -> str | None:
    if not (text or "").strip() or _named_category(text):
        return None
    for pattern, field in _PROBE_LEADS:
        if pattern.search(text):
            return field
    return None


def _soften_vague_intent(intent: dict[str, Any], text: str, confirmed: dict[str, Any]) -> dict[str, Any]:
    if not _probe_lead_field(text):
        return intent
    out = dict(intent)
    if "family" not in confirmed and "category" not in confirmed:
        out["family"] = None
        out["category"] = None
    if "styles" not in confirmed:
        out["styles"] = []
    return out


def _slot_done(intent: dict[str, Any], field: str, skipped: set[str]) -> bool:
    if field in skipped:
        return True
    if field == "family":
        return bool(intent.get("family") or intent.get("category"))
    if field == "neckline":
        if intent.get("family") == "shirt" or intent.get("category") in {"shirt", "blouse"} or "family" in skipped:
            return True
        return intent.get("neckline") not in (None, "", "unknown")
    if field == "styles":
        return bool(intent.get("styles"))
    if field == "usage":
        value = intent.get("usage")
        return bool(value) if isinstance(value, list) else value not in (None, "", "unknown")
    return intent.get(field) not in (None, "", [], "unknown")


def _style_options() -> list[dict[str, str]]:
    tags = [tag for tag in COMMON_STYLE_TAGS if tag in ALLOWED_STYLE_TAGS]
    for tag in ALLOWED_STYLE_TAGS:
        if len(tags) >= 4:
            break
        if tag not in tags:
            tags.append(tag)
    return [_chip_option(tag) for tag in tags[:4]]


def _usage_options() -> list[dict[str, str]]:
    ordered = [value for value in ("casual", "workplace", "sports", "fashion") if value in ALLOWED_USAGE_VALUES]
    for value in ALLOWED_USAGE_VALUES:
        if value not in ordered:
            ordered.append(value)
    return [{"value": value, "label_zh": USAGE_PROMPT_ZH.get(value, CHIP_LABELS_ZH.get(value, value)), "label_en": value} for value in ordered[:4]]


def clarification_cards(intent: dict[str, Any], version: int, skipped: list[str] | None = None, lead_field: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    skipped_set = {str(item) for item in (skipped or [])}
    family = ("family", "你更想做哪一类？", "Which garment family?", [
        {"value": "tshirt", "label_zh": "T恤", "label_en": "T-shirt"},
        {"value": "polo", "label_zh": "Polo", "label_en": "Polo"},
        {"value": "shirt", "label_zh": "衬衫", "label_en": "Shirt"},
    ], True)
    by_field = {
        "family": family,
        "general_shape": ("general_shape", "方便说一下身材吗？偏瘦、匀称还是丰满一些？", "What is your build — slim, average, or fuller?", [_chip_option(value) for value in ("slim", "average", "full")], False),
        "styles": ("styles", "更想要哪种风格？", "Which style look do you want?", _style_options(), False),
        "usage": ("usage", "主要在什么场合穿？", "Where will you wear it?", _usage_options(), False),
        "fit": ("fit", "松量想要怎样？", "Which fit do you prefer?", [_chip_option(value) for value in ("relaxed", "regular", "fitted")], False),
        "neckline": ("neckline", "领型呢？", "Which neckline?", [_chip_option(value) for value in ALLOWED_NECKLINE_VALUES], False),
        "sleeve": ("sleeve", "袖长呢？", "Which sleeve length?", [_chip_option(value) for value in ALLOWED_SLEEVE_VALUES], False),
        "activity": ("activity", "活动量大概怎样？", "How much movement?", [_chip_option(value) for value in ("low", "medium", "high")], False),
    }
    default = ("family", "usage", "styles", "fit", "neckline", "sleeve", "activity", "general_shape")
    order = (lead_field, *(name for name in default if name != lead_field)) if lead_field in by_field else default
    slots = tuple(by_field[name] for name in order)
    for field, title_zh, title_en, options, required in slots:
        if _slot_done(intent, field, skipped_set) or not options:
            continue
        return [{
            "id": f"{field}-{version}", "field": field, "type": "single_select", "required": required,
            "title_zh": title_zh, "title_en": title_en,
            "options": list(options) + [SKIP_OPTION],
            "allow_custom_text": True,
        }], [field]
    return [], []


def _fallback_assistant(intent: dict[str, Any], cards: list[dict[str, Any]], is_english: bool, has_user: bool) -> str:
    recap = "、".join(intent.get("labels") or [])
    if not has_user:
        return "Describe the garment you want — pick a category, or just type. References on the right update as we talk." if is_english else "直接说说你想做的衣服，右侧参考款会跟着对话更新。先选一个品类，或直接打字。"
    if cards:
        title = cards[0]["title_en"] if is_english else cards[0]["title_zh"]
        if recap:
            return f"Noted: {recap}. {title}" if is_english else f"已记下{recap}。{title}"
        return title
    if recap:
        return f"Noted: {recap}. Click a reference on the right to confirm." if is_english else f"已记下{recap}。右侧已按当前需求排好，点一张图确认参考款。"
    return "Click a reference on the right to confirm." if is_english else "右侧已按当前需求排好，点一张图确认参考款。"


def _options_for_field(field: str) -> list[dict[str, str]]:
    cards, _ = clarification_cards({}, 0, None, field)
    if not cards or cards[0].get("field") != field:
        return []
    return [option for option in cards[0].get("options") or [] if option.get("value") != "_skip"]


def _match_field_reply(text: str, field: str) -> str | None:
    raw = (text or "").strip()
    if not raw or _is_skip_text(raw):
        return None
    lowered = raw.lower()
    for option in _options_for_field(field):
        aliases = {
            str(option.get("value") or "").lower(),
            str(option.get("label_zh") or "").strip(),
            str(option.get("label_en") or "").strip().lower(),
        }
        if raw in aliases or lowered in aliases:
            return str(option["value"])
    return None


def _lock_open_field(last: str, intent: dict[str, Any], confirmed: dict[str, Any], skipped: list[str], lead_field: str | None) -> None:
    _, asking = clarification_cards(intent, 0, skipped, lead_field)
    if not last or not asking:
        return
    field = asking[0]
    if _is_skip_text(last):
        if field not in skipped:
            skipped.append(field)
        return
    matched = _match_field_reply(last, field)
    if not matched:
        return
    if field == "styles":
        confirmed[field] = [matched]
    else:
        confirmed[field] = matched


def conversation_response(request: DesignConversationRequest) -> dict[str, Any]:
    user_messages = [message.content.strip() for message in request.messages if message.role == "user" and message.content.strip()]
    last = user_messages[-1] if user_messages else ""
    prior_messages = [item for item in user_messages[:-1] if not _is_skip_text(item)]
    text = "；".join(item for item in user_messages if not _is_skip_text(item))
    confirmed = {key: value for key, value in request.confirmed.items() if not str(key).startswith("_") and value not in (None, "", [])}
    skipped = [str(item) for item in (request.confirmed.get("_skipped") or []) if item]
    prior_intent = dict(request.current_intent or {})
    if not prior_intent and prior_messages:
        prior_intent = parse_design_intent("；".join(prior_messages))
    prior_intent.update(confirmed)
    lead_field = _probe_lead_field(last) or _probe_lead_field(text)
    _lock_open_field(last, prior_intent, confirmed, skipped, lead_field)
    baseline = parse_design_intent(text) if text else dict(request.current_intent)
    baseline.update(confirmed)
    baseline = _soften_vague_intent(baseline, last, confirmed)
    conversation_history = [{"role": message.role, "content": message.content} for message in request.messages]
    skip_model = request.skip_model or not text
    _, next_gap = clarification_cards(baseline, 0, skipped, lead_field)
    next_label_field = next_gap[0] if next_gap else None
    if skip_model:
        intent, model_assistant, model_used = baseline, None, False
    else:
        intent, model_assistant, model_used = enrich_intent_with_model(text, baseline, conversation_history, request.language, request.image_data_urls, next_label_field)
    intent.update(confirmed)
    intent = _soften_vague_intent(intent, last, confirmed)
    if next_label_field and next_label_field not in confirmed:
        intent[next_label_field] = [] if next_label_field == "styles" else None
    confirmed_tags: list[str] = []
    for key, value in confirmed.items():
        if isinstance(value, str):
            confirmed_tags.append(value)
        elif isinstance(value, list):
            confirmed_tags.extend(str(item) for item in value)
    results = ranked_references(text, confirmed_tags, intent)
    version = request.intent_version + 1
    cards, unresolved = clarification_cards(intent, version, skipped, lead_field)
    is_english = request.language.lower().startswith("en")
    if is_english and model_assistant and re.search(r"[\u4e00-\u9fff]", model_assistant):
        model_assistant = None
    assistant = _limit_assistant(model_assistant or _fallback_assistant(intent, cards, is_english, bool(user_messages)))
    summary_keys = {"family", "category", "fit", "neckline", "sleeve", "target_length_cm", "activity", "usage", "styles", "general_shape"}
    confirmed_out = dict(request.confirmed)
    confirmed_out.update(confirmed)
    confirmed_out["_skipped"] = skipped
    return {
        "intent_version": version,
        "assistant_message": assistant,
        "intent": intent,
        "confirmed": confirmed_out,
        "summary": [{"key": key, "value": value} for key, value in intent.items() if key in summary_keys and value not in (None, "", [])],
        "ui_cards": cards,
        "suggestion_chips": SUGGESTION_CHIPS,
        "unresolved": unresolved,
        "facets": semantic_facets(results),
        "items": results,
        "analysis_mode": "model" if model_used else "rules",
        "ranking_mode": "clip" if _clip_ranking_available() else "tags",
    }


def svg_for_ir(ir: dict[str, Any], view: str = "all") -> str:
    from composition_engine import filter_preview_entities

    entities = filter_preview_entities(ir.get("atomic_entities", []))
    if view in {"front", "back"}:
        wanted = "front" if view == "front" else "back"
        filtered = [entity for entity in entities if wanted in str(entity.get("piece_id", "")).lower() or any(part in str(entity.get("piece_id", "")).lower() for part in ("sleeve", "collar", "cuff", "placket"))]
        if filtered:
            entities = filtered
    points = [point for entity in entities for point in entity.get("geometry", {}).get("points", [])]
    if not points:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" />'
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    width, height = max(max_x - min_x, 1), max(max_y - min_y, 1)
    stroke_width = max(width, height) / 1100.0
    lines: list[str] = []
    labels: dict[str, tuple[str, float, float]] = {}
    role_colors = {
        "front_body": "#3f8f83", "front_left": "#3f8f83", "front_right": "#3f8f83",
        "back_body": "#bd8d79", "back_yoke": "#bd8d79",
        "sleeve": "#6f9f91", "sleeve_left": "#6f9f91", "sleeve_right": "#6f9f91",
        "neck_binding": "#9b86d9", "neck_rib": "#9b86d9", "collar": "#9b86d9",
        "collar_stand": "#9b86d9", "collar_interlining": "#9b86d9",
        "front_placket": "#d29a45", "cuff": "#4d86b4", "rib_cuff": "#4d86b4",
        "sleeve_placket": "#d29a45", "sleeve_placket_extension": "#d29a45",
        "reference": "#b0a8a0",
    }
    seam_roles = {"side_seam", "shoulder_seam", "armhole_seam", "sleeve_underarm_seam", "yoke_seam", "collar_attach_line", "cuff_attach_line", "rib_cuff_attach", "rib_hem_attach", "sew", "net_boundary", "seam_allowance"}
    fold_roles = {"pleat_line", "collar_roll_line", "center_front", "center_back", "placket_line", "sleeve_placket_line"}
    for entity in entities:
        raw = entity.get("geometry", {}).get("points", [])
        if len(raw) < 2:
            continue
        # CAD / Pattern IR is Y-up; SVG is Y-down.
        path = " ".join(f"{float(x)-min_x:.3f},{max_y-float(y):.3f}" for x, y in raw)
        role = str(entity.get("_piece_role") or "unknown")
        line_role = str(entity.get("line_role") or "unknown")
        line_kind = "notch" if line_role == "notch" else "fold" if line_role in fold_roles else "grainline" if line_role == "grainline" else "seam" if line_role in seam_roles or line_role.endswith("_seam") else "outline"
        piece_id = str(entity.get("piece_id") or entity.get("entity_id") or "unknown")
        display_only = bool(entity.get("_display_only")) or role == "reference"
        display_flag = "1" if display_only else "0"
        color = role_colors.get(role, "#777286")
        dash = ";stroke-dasharray:7 5" if line_kind == "fold" else ";stroke-dasharray:3 3" if line_kind == "seam" else ""
        weight = stroke_width * (2.2 if line_kind == "notch" else 0.7 if line_role == "internal" else 0.75 if display_only else 1.0)
        lines.append(
            f'<polyline data-piece-role="{escape(role)}" data-line-kind="{line_kind}" data-line-role="{escape(line_role)}" '
            f'data-display-only="{display_flag}" '
            f'points="{path}" fill="none" style="stroke:{color}{dash}" '
            f'stroke-width="{weight:.3f}" vector-effect="non-scaling-stroke" />'
        )
        # Skip labels on unmatched DXF reference lines (not editable pieces).
        if not display_only and piece_id not in labels:
            xs_piece = [float(point[0]) - min_x for point in raw]
            ys_piece = [max_y - float(point[1]) for point in raw]
            labels[piece_id] = (role, sum(xs_piece) / len(xs_piece), sum(ys_piece) / len(ys_piece))
    font_size = max(width, height) / 85.0
    text = [
        f'<text x="{x:.3f}" y="{y:.3f}" font-size="{font_size:.3f}" fill="#817b94" '
        f'text-anchor="middle">{escape(role)}</text>'
        for role, x, y in labels.values()
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-30 -20 {width + 60:.3f} {height + 40:.3f}" preserveAspectRatio="xMidYMid meet">'
        + "".join(lines + text)
        + "</svg>"
    )


@app.get("/health")
def health() -> dict[str, Any]:
    tshirt_count = sum(1 for ir in IR_INDEX.values() if ir.get("_source_format") == "tshirt_pattern_ir_v2")
    shirt_count = sum(1 for ir in IR_INDEX.values() if ir.get("_source_format") == "shirt_pattern_ir_v2")
    public_ir_count = sum(1 for ir in IR_INDEX.values() if not ir.get("_donor_only"))
    donor_only_count = len(IR_INDEX) - public_ir_count
    return {
        "ok": True,
        "service_build": "prototype-parametric-v2",
        "ir_root": str(READY),
        "ir_count": public_ir_count,
        "component_donor_count": donor_only_count,
        "tshirt_v2_count": tshirt_count,
        "shirt_v2_count": shirt_count,
        "dxf_count": len(DXF_INDEX),
        "pattern_options": len(PATTERN_CATALOG["options"]),
        "disk_free_mb": (os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize) // (1024 * 1024),
        "compose_inflight": _COMPOSE_INFLIGHT_COUNT,
        "compose_cache": len(_COMPOSE_CACHE),
    }


@app.get("/pattern-catalog")
def get_pattern_catalog() -> dict[str, Any]:
    return PATTERN_CATALOG


_CATALOG_CACHE: dict[str, Any] | None = None


def _cover_roots() -> list[Path]:
    roots: list[Path] = []
    for path in (WWW_ROOT, PUBLIC_ROOT, ROOT / "apps" / "web" / "public"):
        if path.exists() and path not in roots:
            roots.append(path)
    return roots


def _cover_url(case_id: str, ir: dict[str, Any]) -> str:
    versions = ("v2", "v1") if str(ir.get("_source_format")).endswith("pattern_ir_v2") else ("v1", "v2")
    names = ("cover.png", "cover.jpg", "cover.jpeg", "cover.webp")
    for version in versions:
        for root in _cover_roots():
            image_dir = root / "reference-images" / version / case_id
            cover = next((name for name in names if (image_dir / name).exists()), None)
            if cover:
                return f"/reference-images/{version}/{case_id}/{cover}"
    return f"/reference-images/v2/{case_id}/cover.png"


def _assert_disk(path: Path, min_mb: int = 256) -> None:
    usage = os.statvfs(path)
    free_mb = (usage.f_bavail * usage.f_frsize) // (1024 * 1024)
    if free_mb < min_mb:
        raise HTTPException(status_code=507, detail=f"磁盘空间不足（剩余 {free_mb} MB），请清理后重试")


def _ir_path(case_id: str) -> Path:
    ir = IR_INDEX.get(case_id) or {}
    fmt = str(ir.get("_source_format") or "")
    if fmt == "shirt_pattern_ir_v2":
        return SHIRT_V2 / f"{case_id}.pattern-ir.json"
    if fmt == "tshirt_pattern_ir_v2":
        return TSHIRT_V2 / f"{case_id}.pattern-ir.json"
    return READY / f"{case_id}.rule-ready.json"


def _reload_ir(case_id: str, path: Path) -> dict[str, Any]:
    global _CATALOG_CACHE
    old = IR_INDEX.get(case_id) or {}
    ir = json.loads(path.read_text(encoding="utf-8"))
    ir["_source_format"] = old.get("_source_format") or "tshirt_pattern_ir_v2"
    ir["_dxf_available"] = old.get("_dxf_available")
    ir["_dxf_path"] = old.get("_dxf_path")
    IR_INDEX[case_id] = ir
    _CATALOG_CACHE = None
    return ir


@app.get("/catalog")
def catalog() -> dict[str, Any]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE
    rows = []
    for case_id, ir in sorted(IR_INDEX.items()):
        if ir.get("_donor_only"):
            continue
        semantics = ir.get("design_semantics", {})
        semantic_category = semantics.get("category")
        legacy_category = ir.get("rule_ready", {}).get("category")
        category = semantic_category if semantic_category in {"tshirt", "polo", "shirt", "blouse"} else legacy_category
        remix_ready, readiness_reasons = remix_readiness(ir)
        dxf_match = DXF_INDEX.get(case_id.upper())
        has_dxf = bool(dxf_match and dxf_match.selected)
        status = data_status(case_id, has_dxf, remix_ready)
        supported = category in {"tshirt", "polo", "shirt", "blouse"} and status in {"composition_ready", "tryon_ready"}
        family = "shirt" if category in {"shirt", "blouse"} else "tshirt" if category in {"tshirt", "polo"} else None
        rows.append(
            {
                "case_id": case_id,
                "category": family or category or "other",
                "original_category": category,
                "supported": supported,
                "data_status": status,
                "dxf_available": has_dxf,
                "dxf_conflict": bool(dxf_match and dxf_match.conflict),
                "donor_allowed": has_dxf and case_id not in BLOCKED_DONORS,
                "remix_ready": remix_ready,
                "readiness_reasons": readiness_reasons,
                "cover_url": _cover_url(case_id, ir),
                "semantics": semantics,
                "base_option_ids": reference_base_option_ids(ir, family),
            }
        )
    _CATALOG_CACHE = {"items": rows, "suggestion_chips": SUGGESTION_CHIPS}
    return _CATALOG_CACHE


@app.get("/relabel/queue")
def relabel_queue() -> dict[str, Any]:
    items = []
    for item in RELABEL_QUEUE:
        ir = IR_INDEX.get(item["case_id"])
        if not ir:
            continue
        items.append(summarize(ir, item, _cover_url(item["case_id"], ir)))
    return {"items": items}


@app.get("/relabel/{case_id}")
def relabel_case(case_id: str) -> dict[str, Any]:
    item = next((row for row in RELABEL_QUEUE if row["case_id"] == case_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="not in relabel queue")
    ir = load_ir(case_id)
    payload = svg_payload(piece_outlines(ir))
    extra = ir.get("design_semantics_extra") or {}
    labels = extra.get("part_labels") or {}
    sleeve = labels.get("sleeve_style") if isinstance(labels, dict) else None
    return {
        **summarize(ir, item, _cover_url(case_id, ir)),
        "viewBox": payload["viewBox"],
        "pieces": payload["pieces"],
        "notes": extra.get("relabel_notes") or "",
        "sleeve_style": (sleeve or {}).get("slug") if isinstance(sleeve, dict) else None,
    }


@app.post("/relabel/{case_id}")
def save_relabel(case_id: str, request: RelabelSaveRequest) -> dict[str, Any]:
    if not any(row["case_id"] == case_id for row in RELABEL_QUEUE):
        raise HTTPException(status_code=404, detail="not in relabel queue")
    path = _ir_path(case_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"IR file missing: {path}")
    ir = json.loads(path.read_text(encoding="utf-8"))
    try:
        apply_labels(
            ir,
            piece_roles=request.piece_roles,
            sleeve_style=request.sleeve_style,
            notes=request.notes,
            reviewer=request.reviewer,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path.write_text(json.dumps(ir, ensure_ascii=False) + "\n", encoding="utf-8")
    _reload_ir(case_id, path)
    return relabel_case(case_id)


PRINT_DESIGNER_SYSTEM_PROMPT = """你是 PatternMate 的印花创作师。只返回 JSON。
引导用户设计上衣印花，先锁定类型，再问图案细节，再问位置或密度，最后才允许出图。

内置三类 type：
- small 小印花：局部小图案，如胸口小标、袖标
- chest 胸前印花：胸前主视觉
- allover 全身印花：满幅连续纹样，必须问排列密集程度 density=sparse|medium|dense

规则：
- 每轮只问下一件，先短复述用户刚说的，再提问。assistant_message 不超过 80 字。
- 没锁定 type 时，只引导选择小印花 / 胸前印花 / 全身印花。
- 已有 type 但没有 motif 时，问图案内容、风格、颜色，可让用户上传参考图。
- motif 要累计用户提过的题材、颜色、风格，不要只留最后一句。
- style_prompt 必须是一句完整生图描述，综合全部对话里的类型、题材、颜色、风格、位置或密度，每次更新。
- small/chest 有 motif 后问 placement：left|center|right，并说明会在前片标出放置框。
- allover 有 motif 后问 density。
- ready_to_generate=true 仅当 type、motif 已有，且（small/chest 有 placement，或 allover 有 density）。
- 用户说生成、出图、看看效果时，若信息够就 ready_to_generate=true。
- 用用户的语言回答。不要编造用户没提的图案。
"""


class PrintConversationRequest(BaseModel):
    messages: list[ConversationMessage] = Field(default_factory=list)
    language: str = "zh"
    brief: dict[str, Any] = Field(default_factory=dict)
    image_data_urls: list[str] = Field(default_factory=list, max_length=2)


def _merge_print_brief(brief: dict[str, Any], text: str, parsed: dict[str, Any] | None = None) -> dict[str, Any]:
    next_brief = {
        "type": brief.get("type"),
        "motif": brief.get("motif") or "",
        "style_prompt": brief.get("style_prompt") or "",
        "placement": brief.get("placement"),
        "density": brief.get("density"),
        "ready_to_generate": False,
    }
    if re.search(r"全身|满印|满幅|all[- ]?over", text, re.I):
        next_brief["type"] = "allover"
    elif re.search(r"胸前|胸口|胸印|chest", text, re.I):
        next_brief["type"] = "chest"
    elif re.search(r"小印花|小图案|袖标|logo", text, re.I):
        next_brief["type"] = "small"
    if parsed:
        if parsed.get("type") in {"small", "chest", "allover"}:
            next_brief["type"] = parsed["type"]
        if isinstance(parsed.get("motif"), str) and parsed["motif"].strip():
            incoming = parsed["motif"].strip()
            current = next_brief["motif"]
            next_brief["motif"] = incoming[:240] if not current else (current if incoming in current else f"{current}，{incoming}"[:240])
        if isinstance(parsed.get("style_prompt"), str) and parsed["style_prompt"].strip():
            next_brief["style_prompt"] = parsed["style_prompt"].strip()[:400]
    if len(text) >= 4 and not re.search(r"小印花|胸前印花|全身印花|生成|出图|偏左|偏右|居中|疏一些|密一些|适中", text):
        current = next_brief["motif"]
        if text not in current:
            next_brief["motif"] = (f"{current}，{text}" if current else text)[:240]
    if next_brief.get("motif"):
        if parsed and parsed.get("placement") in {"left", "center", "right"}:
            next_brief["placement"] = parsed["placement"]
        if parsed and parsed.get("density") in {"sparse", "medium", "dense"}:
            next_brief["density"] = parsed["density"]
        if re.search(r"偏左|左边", text) or re.fullmatch(r"left", text, re.I):
            next_brief["placement"] = "left"
        elif re.search(r"偏右|右边", text) or re.fullmatch(r"right", text, re.I):
            next_brief["placement"] = "right"
        elif re.search(r"居中|中间", text) or re.fullmatch(r"center", text, re.I):
            next_brief["placement"] = "center"
        if re.search(r"很密|密集|密一些", text) or re.fullmatch(r"dense", text, re.I):
            next_brief["density"] = "dense"
        elif re.search(r"疏|稀疏", text) or re.fullmatch(r"sparse", text, re.I):
            next_brief["density"] = "sparse"
        elif re.search(r"适中", text) or re.fullmatch(r"medium", text, re.I):
            next_brief["density"] = "medium"
    ready = bool(next_brief["type"] and next_brief["motif"] and (
        (next_brief["type"] == "allover" and next_brief["density"]) or
        (next_brief["type"] in {"small", "chest"} and next_brief["placement"])
    ))
    if ready and ((parsed or {}).get("ready_to_generate") or re.search(r"生成|出图|看看效果", text)):
        next_brief["ready_to_generate"] = True
    return next_brief


def _print_options(brief: dict[str, Any], language: str) -> list[dict[str, str]]:
    zh = not language.lower().startswith("en")
    if not brief.get("type"):
        return [
            {"value": "small", "label_zh": "小印花", "label_en": "Small motif"},
            {"value": "chest", "label_zh": "胸前印花", "label_en": "Chest print"},
            {"value": "allover", "label_zh": "全身印花", "label_en": "All-over print"},
        ]
    if not brief.get("motif"):
        return []
    if brief["type"] == "allover" and not brief.get("density"):
        return [
            {"value": "sparse", "label_zh": "疏一些", "label_en": "Sparse"},
            {"value": "medium", "label_zh": "适中", "label_en": "Medium"},
            {"value": "dense", "label_zh": "密一些", "label_en": "Dense"},
        ]
    if brief["type"] in {"small", "chest"} and not brief.get("placement"):
        return [
            {"value": "left", "label_zh": "偏左", "label_en": "Left"},
            {"value": "center", "label_zh": "居中", "label_en": "Center"},
            {"value": "right", "label_zh": "偏右", "label_en": "Right"},
        ]
    if brief.get("type") and brief.get("motif") and ((brief["type"] == "allover" and brief.get("density")) or brief.get("placement")):
        return [{"value": "generate", "label_zh": "生成印花图", "label_en": "Generate prints"}]
    return []


def print_conversation_response(request: PrintConversationRequest) -> dict[str, Any]:
    messages = [{"role": message.role, "content": message.content} for message in request.messages if message.content.strip()]
    last = next((item["content"] for item in reversed(messages) if item["role"] == "user"), "")
    brief = _merge_print_brief(request.brief or {}, last)
    parsed: dict[str, Any] | None = None
    assistant = ""
    config = design_model_config()
    if config and last:
        payload = json.dumps({
            "model": config["name"],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": PRINT_DESIGNER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "language": "English" if request.language.lower().startswith("en") else "Simplified Chinese",
                    "current_brief": brief,
                    "conversation": messages[-12:],
                    "return": ["assistant_message", "type", "motif", "style_prompt", "placement", "density", "ready_to_generate"],
                }, ensure_ascii=False)},
            ],
        }, ensure_ascii=False).encode("utf-8")
        try:
            http = urllib.request.Request(
                f"{config['base_url']}/chat/completions",
                data=payload,
                headers={"Authorization": f"Bearer {config['key']}", "Content-Type": "application/json"},
                method="POST",
            )
            context = None if config["verify"] else ssl._create_unverified_context()
            with urllib.request.urlopen(http, timeout=config["timeout"], context=context) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", body["choices"][0]["message"]["content"].strip())
            parsed = json.loads(content)
            assistant = str(parsed.get("assistant_message") or "")
            brief = _merge_print_brief(brief, last, parsed)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            parsed = None
    if not assistant:
        if not brief.get("type"):
            assistant = "先选一种印花方式：小印花、胸前印花，还是全身印花？"
        elif not brief.get("motif"):
            assistant = "想印什么图案？可以说风格、颜色，也可以上传参考图。"
        elif brief.get("type") == "allover" and not brief.get("density"):
            assistant = "全身印花的排列要疏一些、适中，还是密一些？"
        elif brief.get("type") in {"small", "chest"} and not brief.get("placement"):
            assistant = "图案放在前片偏左、居中，还是偏右？我会在纸样上标出放置区。"
        else:
            assistant = "前片放置区已标好。可以先出4张印花图，选一张再生成穿着效果。"
        if request.language.lower().startswith("en"):
            assistant = {
                "先选一种印花方式：小印花、胸前印花，还是全身印花？": "Choose a print type: small motif, chest print, or all-over.",
                "想印什么图案？可以说风格、颜色，也可以上传参考图。": "What motif do you want? Describe style and color, or upload a reference.",
                "全身印花的排列要疏一些、适中，还是密一些？": "Should the all-over repeat be sparse, medium, or dense?",
                "图案放在前片偏左、居中，还是偏右？我会在纸样上标出放置区。": "Place it left, center, or right on the front piece? I will mark the zone.",
                "前片放置区已标好。可以先出4张印花图，选一张再生成穿着效果。": "The front-piece zone is marked. Generate 4 print artworks, then pick one for the garment look.",
            }.get(assistant, assistant)
    return {"assistant_message": _limit_assistant(assistant, 80), "brief": brief, "options": _print_options(brief, request.language)}


@app.post("/design/conversation")
def design_conversation(request: DesignConversationRequest) -> dict[str, Any]:
    return conversation_response(request)


@app.post("/print/conversation")
def print_conversation(request: PrintConversationRequest) -> dict[str, Any]:
    return print_conversation_response(request)


@app.post("/design/conversation/correction")
def design_conversation_correction(request: ConversationCorrectionRequest) -> dict[str, Any]:
    allowed_fields = {"family", "category", "fit", "neckline", "sleeve", "activity", "usage", "styles", "target_length_cm"}
    if request.field not in allowed_fields:
        raise HTTPException(status_code=422, detail="Unsupported correction field")
    confirmed = dict(request.confirmed)
    allowed = CHIP_ALLOWED.get(request.field)
    values = [value for value in request.values if not allowed or value in allowed]
    if request.field == "styles":
        confirmed[request.field] = values
    elif values:
        confirmed[request.field] = values[-1]
    else:
        confirmed.pop(request.field, None)
    messages = list(request.messages)
    if request.custom_text.strip():
        messages.append(ConversationMessage(role="user", content=request.custom_text.strip()))
    base = DesignConversationRequest(
        messages=messages,
        language=request.language,
        intent_version=request.intent_version,
        current_intent=request.current_intent,
        confirmed=confirmed,
        skip_model=True,
    )
    response = conversation_response(base)
    response["submitted_card_id"] = request.card_id
    return response


@app.post("/analyze")
def analyze(request: AnalyzeRequest) -> Any:
    intent, model_assistant, _ = enrich_intent_with_model(request.text, parse_design_intent(request.text, request.category), language=request.language)
    results = ranked_references(request.text, request.tags, intent)
    summary = "已记录你的补充，并更新了设计偏好与参考款。"
    if intent.get("family"):
        category_label = "T恤" if intent["family"] == "tshirt" else "衬衫"
        summary = f"我理解你想设计{category_label}。"
    if intent.get("styles"):
        summary += " 已结合你描述的场景与风格更新推荐。"
    if intent.get("sleeve") == "sleeveless":
        summary += " 无袖要求会带入纸样工作台，移除袖片与袖口，并重新校验袖窿收口。"
    return {"query": request.text, "intent": intent, "unresolved": [], "assistant_message": model_assistant or summary, "facets": semantic_facets(results), "items": results}


@app.post("/translate")
def translate(request: TranslateRequest) -> dict[str, str]:
    text = request.text.strip()
    if not text or request.target_language.lower() != "en":
        return {"text": text}
    base_url = os.getenv("MODEL_BASE_URL", "").strip().rstrip("/")
    model_name = os.getenv("MODEL_NAME", "").strip()
    api_key = os.getenv("MODEL_API_KEY", "").strip()
    if os.getenv("MODEL_ENABLED", "false").lower() != "true" or not base_url or not model_name or not api_key or api_key.startswith("fill-your-"):
        fallback = {
            "已记录这次补充，并更新了可选参考款。": "This addition was recorded and the available references were updated.",
            "我理解你想设计": "I understand that you want to design",
            "衬衫": "a shirt",
            "T恤": "a T-shirt",
            "已结合你描述的场景与风格更新推荐。": "Recommendations were updated using the scene and style you described.",
        }
        translated = text
        for source, target in fallback.items():
            translated = translated.replace(source, target)
        return {"text": translated if translated != text else "Design assistant response: " + text}
    payload = json.dumps({"model": model_name, "temperature": 0.1, "messages": [{"role": "system", "content": "Translate the following apparel design assistant reply into concise natural English. Return only the translation."}, {"role": "user", "content": text}]}, ensure_ascii=False).encode("utf-8")
    request_obj = urllib.request.Request(f"{base_url}/chat/completions", data=payload, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request_obj, timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "120"))) as response:
            body = json.loads(response.read().decode("utf-8"))
        translated = str(body["choices"][0]["message"]["content"]).strip()
        return {"text": translated or text}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"text": text}


@app.post("/jobs/{job_id}")
def run_job(job_id: str, job: Job) -> dict[str, Any]:
    # The production adapter will receive the immutable project snapshot from R2,
    # call the refactored grading/morph modules, then upload its result to R2.
    # This endpoint is intentionally deterministic and safe to run in local smoke tests.
    if job_id != job.jobId:
        raise HTTPException(status_code=400, detail="job id mismatch")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{job_id}.json"
        out.write_text(json.dumps({"job_id": job_id, "status": "accepted", "revision": job.revision}), encoding="utf-8")
        return {"job_id": job_id, "status": "accepted", "result_key": f"jobs/{job.userId}/{job.projectId}/{job_id}.json"}


@app.post("/preview/{case_id}")
def preview(case_id: str, view: str = "all") -> Any:
    return {"case_id": case_id, "view": view, "svg": svg_for_ir(load_ir(case_id), view)}


@app.post("/generate-preview")
def generate_preview(request: PreviewRequest) -> Any:
    # The measurement payload is kept with the preview request so the production
    # adapter can apply grading rules; local mode renders the selected IR safely.
    return {"case_id": request.case_id, "measurements": request.measurements, "svg": svg_for_ir(load_ir(request.case_id), "front")}


@app.post("/compose")
async def compose(request: CompositionRecipe) -> Any:
    entities, meta = await asyncio.to_thread(run_composition_cached, request)
    version_svgs = [
        {
            "id": row["id"],
            "label": row["label"],
            "svg": svg_for_ir({"atomic_entities": (meta.get("_version_entities") or {}).get(row["id"]) or entities}),
        }
        for row in (meta.get("versions") or [])
    ]
    return {
        "status": meta["status"],
        "recipe_hash": meta["recipe_hash"],
        "svg": svg_for_ir({"atomic_entities": entities}),
        "version_id": meta.get("version_id") or (version_svgs[-1]["id"] if version_svgs else "current"),
        "versions": version_svgs,
        "pieces": meta["pieces"],
        "paper_info": meta["paper_info"],
        "sources": meta["sources"],
        "validation": meta["validation"],
        "replacement_candidates": meta["replacement_candidates"],
        "sizing_profile": meta["sizing_profile"],
        "source_measurements": meta["source_measurements"],
        "tryon_descriptor": meta["tryon_descriptor"],
        "execution_mode": meta.get("execution_mode", "legacy"),
        "pipeline": meta.get("pipeline"),
        "batch_plan": meta.get("batch_plan"),
        "component_results": meta.get("component_results", []),
        "review_required": meta.get("review_required", False),
        "review_ledger": meta.get("review_ledger"),
    }


@app.get("/sandbox/sleeve-vlm/status")
def sleeve_vlm_status() -> dict[str, Any]:
    from sleeve_vlm_sandbox import model_ready

    return {
        "model_configured": model_ready(),
        "model_base_url": bool(os.getenv("MODEL_BASE_URL", "").strip()),
        "model_name": os.getenv("MODEL_NAME", "").strip() or None,
        "model_enabled_flag": os.getenv("MODEL_ENABLED", "false").lower() == "true",
        "note": "sandbox 不强制 MODEL_ENABLED；配齐 BASE_URL/NAME/KEY 即可。也可在请求体临时覆盖。",
    }


@app.post("/sandbox/sleeve-vlm")
def sleeve_vlm_sandbox(request: SleeveVlmSandboxRequest) -> Any:
    from sleeve_vlm_sandbox import run_sleeve_vlm_sandbox

    overrides = {
        "model_base_url": request.model_base_url,
        "model_name": request.model_name,
        "model_api_key": request.model_api_key,
    }
    try:
        return run_sleeve_vlm_sandbox(
            request.recipe.model_dump(),
            IR_INDEX,
            PATTERN_CATALOG,
            svg_for_ir,
            scales=request.scales,
            call_vlm=request.call_vlm,
            model_overrides=overrides,
            png_width=request.png_width,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"需要 rsvg-convert：{exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"rsvg-convert 失败：{exc.stderr!r}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sandbox/strategy-compose")
def strategy_compose_sandbox(request: StrategyComposeSandboxRequest) -> Any:
    from sleeve_vlm_sandbox import run_strategy_compose_sandbox

    if request.group not in {"sleeve", "neckline", "cuff"}:
        raise HTTPException(status_code=400, detail="group must be sleeve|neckline|cuff")
    overrides = {
        "model_base_url": request.model_base_url,
        "model_name": request.model_name,
        "model_api_key": request.model_api_key,
    }
    try:
        return run_strategy_compose_sandbox(
            request.recipe.model_dump(),
            IR_INDEX,
            PATTERN_CATALOG,
            svg_for_ir,
            group=request.group,
            use_llm=request.use_llm,
            model_overrides=overrides,
            png_width=request.png_width,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"需要 rsvg-convert：{exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"rsvg-convert 失败：{exc.stderr!r}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sandbox/shirt-compose")
def shirt_compose_sandbox(request: ShirtComposeSandboxRequest) -> Any:
    from shirt_sandbox import run_shirt_compose_sandbox

    recipe = request.recipe.model_dump()
    recipe["family"] = "shirt"
    try:
        return run_shirt_compose_sandbox(
            recipe,
            IR_INDEX,
            PATTERN_CATALOG,
            svg_for_ir,
            compare_legacy=request.compare_legacy,
            png_width=request.png_width,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"需要 rsvg-convert：{exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"rsvg-convert 失败：{exc.stderr!r}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def used_print_asset_ids(print_config: dict[str, Any]) -> set[str]:
    modes = print_config.get("face_modes") or {}
    density_assets = print_config.get("density_asset_ids") or {}
    used = {
        str(density_assets.get(face))
        for face in ("front", "back")
        if modes.get(face) == "density" and density_assets.get(face)
    }
    for placement in print_config.get("placements") or []:
        if not isinstance(placement, dict):
            continue
        face = placement.get("view")
        asset_id = placement.get("assetId")
        if face in {"front", "back"} and modes.get(face) == "manual" and asset_id:
            used.add(str(asset_id))
    return used


_GROUP_ZH = {
    "neckline": "领口", "sleeve": "袖型", "garment_length": "衣长", "special": "特殊设计",
    "silhouette": "廓形", "collar": "领型", "placket": "前门襟", "cuff": "袖口",
}
_SEX_ZH = {"female": "女装国标", "male_general": "男装通用"}
_FAMILY_ZH = {"tshirt": "T恤", "shirt": "衬衫"}
_MEAS_ZH = {
    "height": "身高 cm", "chest": "胸围 cm", "waist": "腰围 cm", "shoulder": "肩宽 cm",
    "neck": "领围 cm", "sleeveLength": "袖长 cm", "upperArm": "上臂围 cm", "weight": "体重 kg",
}
_ROLE_ZH = {
    "front_body": "前片", "back_body": "后片", "sleeve": "袖片", "neck_binding": "领条",
    "collar": "领面", "collar_stand": "领座", "cuff": "袖口", "sleeve_placket": "袖开衩",
    "front_placket": "门襟", "back_yoke": "后育克", "front_left": "左前片", "front_right": "右前片",
}


def _option_label(option_id: str | None) -> str:
    if not option_id:
        return "—"
    for option in PATTERN_CATALOG.get("options") or []:
        if option.get("id") == option_id:
            return str(option.get("label_zh") or option_id)
    return str(option_id)


def _decode_data_url(value: object) -> tuple[bytes, str] | None:
    if not isinstance(value, str) or not value.startswith("data:"):
        return None
    header, _, encoded = value.partition(",")
    if not encoded:
        return None
    extension = "jpg" if "jpeg" in header or "jpg" in header else "webp" if "webp" in header else "png"
    try:
        return base64.b64decode(encoded), extension
    except (ValueError, TypeError):
        return None


def _write_data_url(root: Path, stem: str, value: object) -> Path | None:
    decoded = _decode_data_url(value)
    if not decoded:
        return None
    payload, extension = decoded
    path = root / f"{stem}.{extension}"
    path.write_bytes(payload)
    return path


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return (cleaned or "experiment")[:80]


def _ascii_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (cleaned or "experiment")[:80]


def write_tech_sheet(project_name: str, recipe: CompositionRecipe, meta: dict[str, Any], design: dict[str, Any]) -> str:
    experiment = design.get("experiment") if isinstance(design.get("experiment"), dict) else {}
    measurements = recipe.measurements_cm or experiment.get("measurements") or {}
    selections = recipe.selections or {}
    profile = meta.get("sizing_profile") or {}
    pieces = meta.get("pieces") or []
    sources = meta.get("sources") or {}
    rows: list[str] = []

    def row(label: str, value: object) -> None:
        rows.append(f"<tr><th>{escape(str(label))}</th><td>{escape(str(value if value not in (None, '') else '—'))}</td></tr>")

    row("实验记录", project_name)
    row("导出时间", datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"))
    row("品类", _FAMILY_ZH.get(recipe.family, recipe.family))
    row("号型体系", _SEX_ZH.get(recipe.sex, recipe.sex))
    row("基础模板", recipe.base_case_id)
    row("纸样哈希", meta.get("recipe_hash") or "")
    for key, label in _MEAS_ZH.items():
        if key in measurements:
            row(label, measurements.get(key))
    row("放松量 cm", recipe.ease_cm)
    row("合体度", recipe.fit)
    for group, option_id in selections.items():
        row(_GROUP_ZH.get(group, group), _option_label(option_id))
    row("面料", experiment.get("fabric") or recipe.material_id or "—")
    row("颜色", experiment.get("color") or recipe.fabric_color)
    row("工艺", experiment.get("process") or "—")
    for axis in ("width", "length", "shoulder", "armhole", "neck", "sleeve_length", "sleeve_width", "cuff"):
        if axis in profile:
            try:
                row(f"放码·{axis}", f"{float(profile[axis]):.4f}")
            except (TypeError, ValueError):
                row(f"放码·{axis}", profile[axis])
    for piece in pieces:
        role = str(piece.get("role") or "")
        label = _ROLE_ZH.get(role, role)
        size = f"{piece.get('width_mm', '—')} × {piece.get('height_mm', '—')} mm"
        row(f"裁片 {label}", size)
    if isinstance(sources, dict):
        for group, source in sources.items():
            if isinstance(source, dict) and source.get("case_id"):
                row(f"供体 {_GROUP_ZH.get(group, group)}", source.get("case_id"))
            elif group == "base":
                row("供体 基础", source)
    intent = experiment.get("intent") or []
    if isinstance(intent, list) and intent:
        row("设计意图", " / ".join(str(item) for item in intent[-6:]))
    warnings = (meta.get("validation") or {}).get("warnings") or []
    if warnings:
        row("校验备注", "；".join(str(item) for item in warnings[:8]))
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{escape(project_name)} 工艺单</title>"
        "<style>body{font:14px/1.6 -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;"
        "color:#333;max-width:820px;margin:32px auto;padding:0 20px}h1{font-size:22px;margin:0 0 8px}"
        "p{color:#666}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ddd;padding:8px 10px;"
        "text-align:left;vertical-align:top}th{width:28%;background:#f7f4ef;font-weight:600}"
        "@media print{body{margin:0}}</style></head><body>"
        f"<h1>{escape(project_name)}</h1><p>PatternMate 实验记录 / 生产工艺单</p>"
        f"<table>{''.join(rows)}</table></body></html>"
    )


@app.post("/review-decisions")
def record_review_decision(request: ReviewDecisionRequest) -> dict[str, Any]:
    try:
        record = append_review_decision(
            ROOT,
            recipe_hash=request.recipe_hash,
            operation_id=request.operation_id,
            decision=request.decision,
            reviewer=request.reviewer,
            note=request.note,
            geometry_hash_before=request.geometry_hash_before,
            geometry_hash_after=request.geometry_hash_after,
            extra=request.extra,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    history = read_review_history(ROOT, request.recipe_hash)
    return {"status": "recorded", "record": record, "history_count": len(history), "history": history}


@app.post("/export")
async def export_production(request: ProductionRequest) -> Any:
    _assert_disk(Path(tempfile.gettempdir()))
    _assert_disk(ROOT)
    entities, meta = await asyncio.to_thread(run_composition_cached, request.recipe)
    if request.recipe.family != "shirt" and not meta["validation"]["trial_ready"]:
        raise HTTPException(status_code=422, detail={"message": "当前组合未通过自动几何校验", "validation": meta["validation"]})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dxf = root / f"{request.recipe.base_case_id}.{meta['recipe_hash']}.trial.dxf"
        manifest = root / "production_manifest.json"
        validation = root / "geometry_validation.json"
        review_ledger = root / "review-ledger.json"
        design_manifest = json.loads(json.dumps(request.design, ensure_ascii=False))
        extra_files: list[Path] = []
        preview_file = _write_data_url(root, "最终效果图", design_manifest.pop("preview_data_url", None))
        cover_file = _write_data_url(root, "模板", design_manifest.pop("template_cover_data_url", None))
        generated_dir = root / "生成图"
        generated_names: list[str] = []
        for index, item in enumerate(design_manifest.pop("generated_previews", []) or [], 1):
            if not isinstance(item, dict):
                continue
            generated_dir.mkdir(exist_ok=True)
            stem = _safe_filename(str(item.get("name") or f"V{index}"))
            written = _write_data_url(generated_dir, stem, item.get("data_url"))
            if written:
                extra_files.append(written)
                generated_names.append(f"生成图/{written.name}")
        if preview_file:
            extra_files.append(preview_file)
            design_manifest["preview_file"] = preview_file.name
        if cover_file:
            extra_files.append(cover_file)
            design_manifest["template_cover_file"] = cover_file.name
        if generated_names:
            design_manifest["generated_files"] = generated_names
        tech_sheet = root / "工艺单.html"
        tech_sheet.write_text(write_tech_sheet(request.project_name, request.recipe, meta, design_manifest), encoding="utf-8")
        extra_files.append(tech_sheet)
        record = root / "experiment_record.json"
        record.write_text(
            json.dumps(
                {
                    "name": request.project_name,
                    "recipe": request.recipe.model_dump(),
                    "experiment": design_manifest.get("experiment") or {},
                    "recipe_hash": meta.get("recipe_hash"),
                    "pieces": meta.get("pieces"),
                    "sizing_profile": meta.get("sizing_profile"),
                    "sources": meta.get("sources"),
                    "preview_file": design_manifest.get("preview_file"),
                    "template_cover_file": design_manifest.get("template_cover_file"),
                    "generated_files": generated_names,
                    "tech_sheet": tech_sheet.name,
                    "dxf": dxf.name,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        extra_files.append(record)
        print_assets: list[Path] = []
        print_config = design_manifest.get("print") if isinstance(design_manifest, dict) else None
        if isinstance(print_config, dict):
            raw_assets = print_config.get("assets") or []
            used_asset_ids = used_print_asset_ids(print_config)
            filtered_assets = [asset for asset in raw_assets if isinstance(asset, dict) and str(asset.get("id")) in used_asset_ids]
            print_config["assets"] = filtered_assets
            for index, asset in enumerate(filtered_assets, 1):
                if not isinstance(asset, dict):
                    continue
                source = asset.pop("src", None)
                target: Path | None = None
                if isinstance(source, str) and source.startswith("data:image/"):
                    header, encoded = source.split(",", 1)
                    extension = "jpg" if "jpeg" in header else "webp" if "webp" in header else "png"
                    target = root / f"print_asset_{index:02d}.{extension}"
                    target.write_bytes(base64.b64decode(encoded))
                elif isinstance(source, str) and re.fullmatch(r"/print-library/print-test-[1-3]/source\.png", source):
                    source_asset = PUBLIC_ROOT / source.lstrip("/")
                    if source_asset.exists():
                        target = root / f"print_asset_{index:02d}.png"
                        target.write_bytes(source_asset.read_bytes())
                if target:
                    asset["file"] = target.name
                    print_assets.append(target)
        from dxf_export import write_entities_dxf

        roles = {entity.get("piece_id"): entity.get("_piece_role", "") for entity in entities if entity.get("piece_id")}
        dxf_report = write_entities_dxf(entities, str(dxf), piece_role_by_id=roles, optimize=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema": "chi27.production.v2",
                    "export_level": "auto_validated_trial",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "project_name": request.project_name,
                    "recipe": request.recipe.model_dump(),
                    "recipe_hash": meta["recipe_hash"],
                    "sizing_profile": meta["sizing_profile"],
                    "source_measurements": meta["source_measurements"],
                    "sources": meta["sources"],
                    "pieces": meta["pieces"],
                    "review_required": meta.get("review_required", False),
                    "review_ledger_file": review_ledger.name if meta.get("review_ledger") else None,
                    "design": design_manifest,
                    "dxf": {"filename": dxf.name, **dxf_report},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        validation.write_text(json.dumps(meta["validation"], ensure_ascii=False, indent=2), encoding="utf-8")
        if meta.get("review_ledger"):
            review_ledger.write_text(json.dumps(meta["review_ledger"], ensure_ascii=False, indent=2), encoding="utf-8")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(dxf, dxf.name)
            bundle.write(manifest, manifest.name)
            bundle.write(validation, validation.name)
            if review_ledger.exists():
                bundle.write(review_ledger, review_ledger.name)
            for extra in extra_files:
                bundle.write(extra, extra.relative_to(root).as_posix())
            for print_asset in print_assets:
                bundle.write(print_asset, print_asset.name)
        archive.seek(0)
        from fastapi.responses import StreamingResponse
        ascii_name = f"{_ascii_filename(request.project_name)}-{request.recipe.base_case_id}-production.zip"
        utf_name = quote(f"{request.project_name}-生产文件包.zip")
        return StreamingResponse(
            archive,
            media_type="application/zip",
            headers={"content-disposition": f"attachment; filename={ascii_name}; filename*=UTF-8''{utf_name}"},
        )
