"""Minimal sandbox: mechanical sleeve-scale candidates → preview PNGs → optional VLM pick.

Input is intentionally tiny: base case + sleeve option + a few scale multipliers.
VLM sees only the preview images (+ one short prompt), not raw IR/DXF.
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

from composition_engine import PURE_SLEEVE_ROLES, compose_recipe
from geometry_ops import bounds_of_entities, transform_entity
from simple_compose import _group_by_piece, _role

DEFAULT_SCALES = (0.90, 1.00, 1.15)

CRITIC_PROMPT = """你是服装纸样质检助手。图中每张是 T 恤版片预览（衣身+袖片）。
请比较候选 A/B/C（或图上标注的 scale），只看袖片相对衣身是否比例正常、袖山是否可能对上袖窿、有没有明显过小或被拉长变形。

只返回 JSON：
{
  "best": "A" | "B" | "C",
  "scores": {"A": 1-5, "B": 1-5, "C": 1-5},
  "reason": "一句中文理由",
  "suggested_scale": 0.8-1.3 或 null
}
"""


def _scale_sleeves(entities: list[dict[str, Any]], factor: float) -> list[dict[str, Any]]:
    factor = max(0.5, min(1.6, float(factor)))
    if abs(factor - 1.0) < 1e-6:
        return entities
    out: list[dict[str, Any]] = []
    by_role: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_role.setdefault(_role(entity), []).append(entity)
    for role, rows in by_role.items():
        if role not in PURE_SLEEVE_ROLES:
            out.extend(rows)
            continue
        for piece_rows in _group_by_piece(rows).values():
            box = bounds_of_entities(piece_rows)
            if not box:
                out.extend(piece_rows)
                continue
            ox = (box[0] + box[2]) / 2.0
            oy = (box[1] + box[3]) / 2.0
            out.extend(transform_entity(entity, sx=factor, sy=factor, ox=ox, oy=oy) for entity in piece_rows)
    return out


def _svg_to_png_b64(svg: str, *, width: int = 640) -> str:
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        tmp.write(svg.encode("utf-8"))
        svg_path = Path(tmp.name)
    png_path = svg_path.with_suffix(".png")
    try:
        subprocess.run(
            ["rsvg-convert", "-w", str(width), "-b", "white", str(svg_path), "-o", str(png_path)],
            check=True,
            capture_output=True,
        )
        return base64.b64encode(png_path.read_bytes()).decode("ascii")
    finally:
        svg_path.unlink(missing_ok=True)
        png_path.unlink(missing_ok=True)


def _model_config(overrides: dict[str, Any] | None = None) -> dict[str, str]:
    overrides = overrides or {}
    base_url = str(overrides.get("model_base_url") or os.getenv("MODEL_BASE_URL", "")).strip().rstrip("/")
    model_name = str(overrides.get("model_name") or os.getenv("MODEL_NAME", "")).strip()
    api_key = str(overrides.get("model_api_key") or os.getenv("MODEL_API_KEY", "")).strip()
    return {"base_url": base_url, "model_name": model_name, "api_key": api_key}


def model_ready(overrides: dict[str, Any] | None = None) -> bool:
    cfg = _model_config(overrides)
    return bool(
        cfg["base_url"]
        and cfg["model_name"]
        and cfg["api_key"]
        and not cfg["api_key"].startswith("fill-your-")
    )


def call_vlm_pick(
    candidates: list[dict[str, Any]],
    *,
    overrides: dict[str, Any] | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    cfg = _model_config(overrides)
    if not model_ready(overrides):
        return {"ok": False, "error": "model_not_configured", "hint": "设置 MODEL_BASE_URL / MODEL_NAME / MODEL_API_KEY，或在请求里传临时覆盖"}

    labels = [str(row.get("label") or chr(65 + i)) for i, row in enumerate(candidates)]
    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (prompt or CRITIC_PROMPT)
            + "\n候选对应："
            + ", ".join(f"{lab}=scale {row.get('scale')}" for lab, row in zip(labels, candidates)),
        }
    ]
    for lab, row in zip(labels, candidates):
        b64 = row.get("png_base64")
        if not b64:
            continue
        user_content.append({"type": "text", "text": f"候选 {lab} (scale={row.get('scale')})："})
        user_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    payload = json.dumps(
        {
            "model": cfg["model_name"],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return JSON only. No markdown."},
                {"role": "user", "content": user_content},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "120"))
    ssl_verify = os.getenv("MODEL_SSL_VERIFY", "true").lower() not in {"0", "false", "no"}
    ctx = ssl.create_default_context()
    if not ssl_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ctx) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = str(body["choices"][0]["message"]["content"]).strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        parsed = json.loads(content)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": "vlm_call_failed", "detail": str(exc)[:400]}

    best = str(parsed.get("best") or "").strip().upper()
    if best not in labels:
        # tolerate "B" vs index
        best = labels[0]
    best_idx = labels.index(best)
    return {
        "ok": True,
        "best": best,
        "best_index": best_idx,
        "best_scale": candidates[best_idx].get("scale"),
        "scores": parsed.get("scores"),
        "reason": parsed.get("reason"),
        "suggested_scale": parsed.get("suggested_scale"),
        "raw": parsed,
    }


def run_sleeve_vlm_sandbox(
    recipe: dict[str, Any],
    index: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
    svg_for_ir,
    *,
    scales: list[float] | None = None,
    call_vlm: bool = True,
    model_overrides: dict[str, Any] | None = None,
    png_width: int = 640,
) -> dict[str, Any]:
    """Compose once, then emit scaled sleeve candidates and optionally ask VLM."""
    scales = [float(s) for s in (scales or list(DEFAULT_SCALES))]
    base_recipe = {
        **recipe,
        "execution_mode": recipe.get("execution_mode") or "simple_piece_swap",
        "compact_layout": True,
    }
    entities, meta = compose_recipe(base_recipe, index, catalog)
    labels = [chr(65 + i) for i in range(len(scales))]
    candidates: list[dict[str, Any]] = []
    for label, scale in zip(labels, scales):
        scaled = _scale_sleeves(deepcopy(entities), scale)
        svg = svg_for_ir({"atomic_entities": scaled})
        png_b64 = _svg_to_png_b64(svg, width=png_width)
        sleeve_pieces = [p for p in (meta.get("pieces") or []) if p.get("role") == "sleeve"]
        candidates.append(
            {
                "label": label,
                "scale": scale,
                "svg": svg,
                "png_base64": png_b64,
                "png_data_url": f"data:image/png;base64,{png_b64}",
                "status": meta.get("status"),
                "sleeve_pieces": sleeve_pieces,
            }
        )

    vlm: dict[str, Any] = {"ok": False, "skipped": True}
    if call_vlm:
        vlm = call_vlm_pick(candidates, overrides=model_overrides)

    best_index = int(vlm.get("best_index") or scales.index(1.0) if 1.0 in scales else 0)
    if not vlm.get("ok"):
        best_index = scales.index(1.0) if 1.0 in scales else 0

    return {
        "ok": True,
        "base_case_id": recipe.get("base_case_id"),
        "scales": scales,
        "candidates": [
            {
                "label": row["label"],
                "scale": row["scale"],
                "png_data_url": row["png_data_url"],
                "status": row["status"],
            }
            for row in candidates
        ],
        # full svgs kept for re-export; omit huge duplicate png list in top-level if needed
        "candidate_svgs": {row["label"]: row["svg"] for row in candidates},
        "vlm": vlm,
        "best_index": best_index,
        "best_label": labels[best_index],
        "best_scale": scales[best_index],
        "model_configured": model_ready(model_overrides),
        "compose_meta": {
            "status": meta.get("status"),
            "sources": meta.get("sources"),
            "execution_mode": meta.get("execution_mode"),
        },
    }


def run_strategy_compose_sandbox(
    recipe: dict[str, Any],
    index: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
    svg_for_ir,
    *,
    group: str = "sleeve",
    use_llm: bool = True,
    model_overrides: dict[str, Any] | None = None,
    png_width: int = 720,
) -> dict[str, Any]:
    """Judge edit strategy (LLM→rule fallback), compose once, return preview."""
    from edit_strategy import judge_edit_strategy, rule_strategy

    selections = recipe.get("selections") or {}
    base_opts = recipe.get("base_option_ids") or {}
    option_id = selections.get(group) or base_opts.get(group)
    plan = judge_edit_strategy(
        group=group,
        option_id=option_id,
        family=str(recipe.get("family") or "tshirt"),
        host_base_option_id=base_opts.get(group),
        model_overrides=model_overrides,
        use_llm=use_llm,
    )
    compose_recipe_body = {
        **recipe,
        "execution_mode": recipe.get("execution_mode") or "simple_piece_swap",
        "compact_layout": True,
        "strategy_override": plan if group == "sleeve" else recipe.get("strategy_override"),
    }
    entities, meta = compose_recipe(compose_recipe_body, index, catalog)
    svg = svg_for_ir({"atomic_entities": entities})
    png_b64 = _svg_to_png_b64(svg, width=png_width)
    piece_sources = [
        {
            "role": p.get("role"),
            "source_case_id": p.get("source_case_id"),
            "width_mm": p.get("width_mm"),
            "height_mm": p.get("height_mm"),
        }
        for p in (meta.get("pieces") or [])
    ]
    return {
        "ok": True,
        "group": group,
        "option_id": option_id,
        "strategy": plan,
        "rule_strategy": plan.get("rule_fallback") or rule_strategy(group, option_id),
        "png_data_url": f"data:image/png;base64,{png_b64}",
        "svg": svg,
        "pieces": piece_sources,
        "component_results": meta.get("component_results"),
        "sources": meta.get("sources"),
        "sleeve_cap_match": (meta.get("sources") or {}).get("sleeve_cap_match"),
        "status": meta.get("status"),
        "model_configured": model_ready(model_overrides),
    }
