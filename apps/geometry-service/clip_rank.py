"""CLIP text ranking over design_semantics captions.

Image side is the annotated design-semantics caption, not cover pixels.
Optional: cn_clip or transformers Chinese-CLIP. Falls back to None without torch.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np

SEMANTIC_ZH = {
    "tshirt": "T恤", "polo": "Polo衫", "shirt": "衬衫", "blouse": "女式衬衫",
    "relaxed": "宽松", "regular": "合体", "fitted": "修身", "oversized": "超宽松", "tight": "紧身",
    "sleeveless": "无袖", "short": "短袖", "long": "长袖",
    "v-neck": "V领", "crew": "圆领", "polo": "Polo领",
    "casual": "休闲", "basic": "基础", "unisex": "中性", "streetwear": "街头",
    "vintage_washed": "复古水洗", "artistic": "艺术", "athleisure": "运动休闲",
    "commuter": "通勤", "preppy": "学院", "elegant": "优雅", "sweet": "甜美",
    "avant_garde": "先锋", "niche": "小众", "retro": "复古", "minimal": "简约",
    "conservative": "保守", "quiet_luxury": "静奢", "romantic": "浪漫", "sporty": "运动",
    "hot_girl": "辣妹", "business": "商务", "outdoor": "户外", "workwear": "工装",
    "oriental": "东方", "punk": "朋克", "y2k": "Y2K",
    "workplace": "职场通勤", "fashion": "时尚", "sports": "运动",
    "low": "低", "medium": "中等", "high": "高",
    "H": "H型廓形", "X": "X型廓形", "A": "A型廓形", "oversize": "oversize廓形",
    "upper_body": "上身", "neckline": "领口", "none": "",
    "female": "女装", "male": "男装",
    "soft": "亲肤面料", "healthy": "康态体型",
}

_MODEL = None
_INDEX: dict[str, Any] | None = None


def semantics_caption(ir: dict[str, Any]) -> str:
    sem = ir.get("design_semantics") or {}
    labels = ((ir.get("design_semantics_extra") or {}).get("part_labels") or {})
    bits: list[str] = []

    def add(value: Any, prefix: str = "") -> None:
        if value in (None, "", "unknown", "none"):
            return
        text = SEMANTIC_ZH.get(str(value), str(value).replace("_", " "))
        if text:
            bits.append(f"{prefix}{text}" if prefix else text)

    add(sem.get("category"))
    add(sem.get("fit"), "版型")
    add(sem.get("silhouette"))
    add(sem.get("coverage"), "覆盖")
    add(sem.get("activity"), "活动量")
    add(sem.get("visual_focus"), "视觉重点")
    add(sem.get("target_gender"))
    add(sem.get("fabric_skin_friendliness"))
    for tag in sem.get("style_tags") or []:
        add(tag)
    for use in sem.get("usage") or []:
        add(use)
    if isinstance(labels, dict):
        notes = labels.get("notes")
        for key, info in labels.items():
            if key in {"special", "notes"} or not isinstance(info, dict):
                continue
            zh = str(info.get("label_zh") or "").strip()
            if zh:
                bits.append(zh)
        if isinstance(notes, str) and notes.strip():
            bits.append(notes.strip())
    return "，".join(bits) or "上衣"


def clip_query_text(user_text: str, intent: dict[str, Any]) -> str:
    search = str(intent.get("search_query") or "").strip()
    if search:
        return search
    parts = [user_text.strip()] if user_text.strip() else []
    for key in ("family", "category", "fit", "neckline", "sleeve", "usage", "activity"):
        add(intent.get(key), parts)
    for tag in intent.get("styles") or []:
        add(tag, parts)
    for label in intent.get("labels") or []:
        if label:
            parts.append(str(label))
    return "，".join(parts) if parts else user_text


def add(value: Any, parts: list[str]) -> None:
    if value in (None, "", "unknown"):
        return
    text = SEMANTIC_ZH.get(str(value), str(value).replace("_", " "))
    if text:
        parts.append(text)


def _enabled() -> bool:
    return (os.getenv("CLIP_ENABLED") or "true").strip().lower() not in {"0", "false", "no"}


def _load_model() -> Any:
    global _MODEL
    if _MODEL is not None:
        return _MODEL if _MODEL is not False else None
    if not _enabled():
        _MODEL = False
        return None
    device = os.getenv("CLIP_DEVICE") or ""
    try:
        import torch
        if not device:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        download_root = os.getenv("CLIP_DOWNLOAD_ROOT") or os.path.join(os.getenv("CHI27_ROOT") or ".", ".cache", "cn-clip")
        import cn_clip.clip as clip
        model, _preprocess = clip.load_from_name(os.getenv("CLIP_MODEL") or "ViT-B-16", device=device, download_root=download_root)
        model.eval()
        _MODEL = ("cn_clip", model, device, clip)
        return _MODEL
    except Exception:
        pass
    try:
        import torch
        from transformers import ChineseCLIPModel, ChineseCLIPProcessor
        if not device:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        name = os.getenv("CLIP_MODEL_ID") or "OFA-Sys/chinese-clip-vit-base-patch16"
        processor = ChineseCLIPProcessor.from_pretrained(name)
        model = ChineseCLIPModel.from_pretrained(name).to(device).eval()
        _MODEL = ("hf", model, device, processor)
        return _MODEL
    except Exception:
        _MODEL = False
        return None


def _encode(texts: list[str]) -> np.ndarray | None:
    packed = _load_model()
    if not packed:
        return None
    kind, model, device, helper = packed
    import torch
    cleaned = [text[:120] or "上衣" for text in texts]
    with torch.no_grad():
        if kind == "cn_clip":
            tokens = helper.tokenize(cleaned, context_length=52).to(device)
            feats = model.encode_text(tokens)
        else:
            inputs = helper(text=cleaned, padding=True, truncation=True, max_length=52, return_tensors="pt")
            inputs = {key: value.to(device) for key, value in inputs.items()}
            feats = model.get_text_features(**inputs)
        feats = feats.float()
        feats = feats / feats.norm(dim=-1, keepdim=True).clamp_min(1e-8)
        return feats.detach().cpu().numpy()


def _catalog_index(ir_index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    global _INDEX
    if _INDEX is not None:
        return _INDEX if _INDEX is not False else None
    items = [(str(ir.get("case_id") or case_id), semantics_caption(ir)) for case_id, ir in ir_index.items() if not ir.get("_donor_only")]
    if not items:
        _INDEX = False
        return None
    ids = [item[0] for item in items]
    matrix = _encode([item[1] for item in items])
    if matrix is None:
        _INDEX = False
        return None
    _INDEX = {"ids": ids, "matrix": matrix, "captions": dict(items)}
    return _INDEX


def score_clip(ir_index: dict[str, dict[str, Any]], query: str) -> dict[str, float] | None:
    if not (query or "").strip():
        return None
    index = _catalog_index(ir_index)
    if not index:
        return None
    query_vec = _encode([query.strip()])
    if query_vec is None:
        return None
    sims = index["matrix"] @ query_vec[0]
    lo, hi = float(sims.min()), float(sims.max())
    scale = (hi - lo) or 1.0
    return {case_id: float((value - lo) / scale) for case_id, value in zip(index["ids"], sims)}


def ranking_available(ir_index: dict[str, dict[str, Any]]) -> bool:
    return _catalog_index(ir_index) is not None
