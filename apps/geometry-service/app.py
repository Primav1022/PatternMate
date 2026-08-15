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
import zipfile
import sys
import urllib.request
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
    return {"family": "tshirt" if category in {"tshirt", "polo"} else category, "category": category, "sleeve": sleeve, "target_length_cm": float(length_match.group(1)) if length_match else None, "fit": fit, "neckline": neckline, "activity": activity, "usage": usage, "styles": styles, "labels": labels, "source_text": text}


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
        "廓形": "H / X / A 等",
        "覆盖度": "覆盖多少身体",
        "视觉重点": "领口 / 上身等",
    }


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
        },
        "fields": ["family", "category", "sleeve", "target_length_cm", "fit", "neckline", "activity", "usage", "styles", "labels", "search_query", "assistant_message"],
        "baseline": baseline,
        "conversation": messages or [{"role": "user", "content": text}],
        "assistant_reply_language": "English" if is_english else "Simplified Chinese",
        "search_query_rule": "search_query is one Chinese sentence for CLIP retrieval, using category, fit, sleeve, neckline, style and scene words from the brief.",
        "ask_rule": "assistant_message is spoken to the user, max 100 characters. Recap confirmed catalog dimensions, then ask the next_label_field dimension so the user can tap a label chip. Do not list all dimensions. Do not invent values outside allowed.",
        "next_label_field": next_label_field,
    }
    images = [value for value in (image_data_urls or []) if re.match(r"^data:image/(?:png|jpeg|webp);base64,", value) and len(value) <= 6_000_000]
    user_content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(schema_prompt, ensure_ascii=False)}]
    user_content.extend({"type": "image_url", "image_url": {"url": value}} for value in images)
    payload = json.dumps({
        "model": model_name,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": (
                "You are PatternMate, a multimodal apparel design assistant. Return JSON only. "
                "Guide the user to think in our database dimensions: 品类, 场景, 风格, 合体度, 领型, 袖长, 活动量, 廓形, 覆盖度, 视觉重点. "
                "assistant_message must be at most 100 characters, one short spoken reply. "
                "After you ask one missing dimension, the product UI pushes label chips for the user to tap."
            )},
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
    selected = any(intent.get(key) not in (None, "", [], "unknown") for key in ("family", "category", "fit", "usage", "styles", "activity", "sleeve", "neckline"))
    results = []
    for ir in IR_INDEX.values():
        if ir.get("_donor_only"):
            continue
        semantics = ir.get("design_semantics", {})
        if selected and not _matches_hard_intent(semantics, intent):
            continue
        score, reasons = score_semantics(semantics, text, tags, intent)
        if clip_scores is not None:
            clip = float(clip_scores.get(str(ir.get("case_id")), 0.0))
            score = round(min(0.99, 0.55 * clip + 0.45 * score), 4)
            reasons = ["CLIP图文相似"] + reasons
        results.append({"case_id": ir.get("case_id"), "score": score, "match_reasons": reasons, "semantics": semantics})
    if selected:
        tight = [row for row in results if _matches_selected_fields(row["semantics"], intent, tags)]
        if tight:
            results = tight
    results.sort(key=lambda item: (-item["score"], str(item["case_id"])))
    return results


def _is_skip_text(text: str) -> bool:
    return text.strip().lower() in {item.lower() for item in SKIP_TEXTS}


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


def clarification_cards(intent: dict[str, Any], version: int, skipped: list[str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    skipped_set = {str(item) for item in (skipped or [])}
    slots = (
        ("family", "你更想做哪一类？", "Which garment family?", [
            {"value": "tshirt", "label_zh": "T恤", "label_en": "T-shirt"},
            {"value": "polo", "label_zh": "Polo", "label_en": "Polo"},
            {"value": "shirt", "label_zh": "衬衫", "label_en": "Shirt"},
        ], True),
        ("usage", "主要在哪穿？", "Where will you wear it?", _usage_options(), False),
        ("styles", "更偏向哪种风格？", "Which style feels closer?", _style_options(), False),
        ("fit", "松量想要怎样？", "Which fit do you prefer?", [_chip_option(value) for value in ("relaxed", "regular", "fitted")], False),
        ("neckline", "领型呢？", "Which neckline?", [_chip_option(value) for value in ALLOWED_NECKLINE_VALUES], False),
        ("sleeve", "袖长呢？", "Which sleeve length?", [_chip_option(value) for value in ALLOWED_SLEEVE_VALUES], False),
        ("activity", "活动量大概怎样？", "How much movement?", [_chip_option(value) for value in ("low", "medium", "high")], False),
    )
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


def conversation_response(request: DesignConversationRequest) -> dict[str, Any]:
    user_messages = [message.content.strip() for message in request.messages if message.role == "user" and message.content.strip()]
    text = "；".join(item for item in user_messages if not _is_skip_text(item))
    baseline = parse_design_intent(text) if text else dict(request.current_intent)
    confirmed = {key: value for key, value in request.confirmed.items() if not str(key).startswith("_") and value not in (None, "", [])}
    baseline.update(confirmed)
    skipped = [str(item) for item in (request.confirmed.get("_skipped") or []) if item]
    last = user_messages[-1] if user_messages else ""
    if last and _is_skip_text(last):
        _, gap = clarification_cards(baseline, 0, skipped)
        if gap and gap[0] not in skipped:
            skipped.append(gap[0])
    conversation_history = [{"role": message.role, "content": message.content} for message in request.messages]
    skip_model = request.skip_model or not text or bool(last and _is_skip_text(last))
    _, next_gap = clarification_cards(baseline, 0, skipped)
    next_label_field = next_gap[0] if next_gap else None
    if skip_model:
        intent, model_assistant, model_used = baseline, None, False
    else:
        intent, model_assistant, model_used = enrich_intent_with_model(text, baseline, conversation_history, request.language, request.image_data_urls, next_label_field)
    intent.update(confirmed)
    confirmed_tags: list[str] = []
    for key, value in confirmed.items():
        if isinstance(value, str):
            confirmed_tags.append(value)
        elif isinstance(value, list):
            confirmed_tags.extend(str(item) for item in value)
    results = ranked_references(text, confirmed_tags, intent)
    version = request.intent_version + 1
    cards, unresolved = clarification_cards(intent, version, skipped)
    is_english = request.language.lower().startswith("en")
    if is_english and model_assistant and re.search(r"[\u4e00-\u9fff]", model_assistant):
        model_assistant = None
    assistant = _limit_assistant(model_assistant or _fallback_assistant(intent, cards, is_english, bool(user_messages)))
    summary_keys = {"family", "category", "fit", "neckline", "sleeve", "target_length_cm", "activity", "usage", "styles"}
    confirmed_out = dict(request.confirmed)
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
    seam_roles = {"side_seam", "shoulder_seam", "armhole_seam", "sleeve_underarm_seam", "yoke_seam", "collar_attach_line", "cuff_attach_line", "rib_cuff_attach", "rib_hem_attach"}
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
        weight = stroke_width * (2.2 if line_kind == "notch" else 0.75 if display_only else 1.0)
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
    return {"ok": True, "service_build": "prototype-parametric-v2", "ir_root": str(READY), "ir_count": public_ir_count, "component_donor_count": donor_only_count, "tshirt_v2_count": tshirt_count, "shirt_v2_count": shirt_count, "dxf_count": len(DXF_INDEX), "pattern_options": len(PATTERN_CATALOG["options"])}


@app.get("/pattern-catalog")
def get_pattern_catalog() -> dict[str, Any]:
    return PATTERN_CATALOG


_CATALOG_CACHE: dict[str, Any] | None = None


def _cover_url(case_id: str, ir: dict[str, Any]) -> str:
    image_version = "v2" if str(ir.get("_source_format")).endswith("pattern_ir_v2") else "v1"
    image_dir = PUBLIC_ROOT / "reference-images" / image_version / case_id
    cover = next((candidate for candidate in ("cover.png", "cover.jpg", "cover.jpeg", "cover.webp") if (image_dir / candidate).exists()), "cover.jpg")
    return f"/reference-images/{image_version}/{case_id}/{cover}"


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


@app.post("/design/conversation")
def design_conversation(request: DesignConversationRequest) -> dict[str, Any]:
    return conversation_response(request)


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
    entities, meta = await asyncio.to_thread(run_composition, request)
    return {
        "status": meta["status"],
        "recipe_hash": meta["recipe_hash"],
        "svg": svg_for_ir({"atomic_entities": entities}),
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
    entities, meta = await asyncio.to_thread(run_composition, request.recipe)
    if not meta["validation"]["trial_ready"]:
        raise HTTPException(status_code=422, detail={"message": "当前组合未通过自动几何校验", "validation": meta["validation"]})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dxf = root / f"{request.recipe.base_case_id}.{meta['recipe_hash']}.trial.dxf"
        manifest = root / "production_manifest.json"
        validation = root / "geometry_validation.json"
        review_ledger = root / "review-ledger.json"
        design_manifest = json.loads(json.dumps(request.design, ensure_ascii=False))
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
            for print_asset in print_assets:
                bundle.write(print_asset, print_asset.name)
        archive.seek(0)
        from fastapi.responses import StreamingResponse
        filename = f"smart-pattern-{request.recipe.base_case_id}-{meta['recipe_hash']}-trial.zip"
        return StreamingResponse(archive, media_type="application/zip", headers={"content-disposition": f"attachment; filename={filename}"})
