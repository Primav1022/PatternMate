"""Shirt sandbox: strategy compose + optional A/B vs legacy batch_preview."""
from __future__ import annotations

import base64
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from composition_engine import compose_recipe
from shirt_compose import PIPELINE_ID
from shirt_strategy import collar_plan, cuff_plan, public_plan, sleeve_plan


def _svg_to_png_data_url(svg: str, width: int = 720) -> str:
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
        raw = png_path.read_bytes()
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    finally:
        svg_path.unlink(missing_ok=True)
        png_path.unlink(missing_ok=True)


def _strategies_for(recipe: dict[str, Any]) -> dict[str, Any]:
    selections = recipe.get("selections") or {}
    base = recipe.get("base_option_ids") or {}
    out: dict[str, Any] = {}
    if selections.get("sleeve") and selections.get("sleeve") != base.get("sleeve"):
        out["sleeve"] = public_plan(sleeve_plan(selections.get("sleeve")))
    if selections.get("collar") and selections.get("collar") != base.get("collar"):
        out["collar"] = public_plan(collar_plan(selections.get("collar")))
    if selections.get("cuff") and selections.get("cuff") != base.get("cuff"):
        out["cuff"] = public_plan(cuff_plan(selections.get("cuff")))
    return out


def run_shirt_compose_sandbox(
    recipe: dict[str, Any],
    index: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
    svg_for_ir: Callable[[dict[str, Any]], str],
    *,
    compare_legacy: bool = True,
    png_width: int = 720,
) -> dict[str, Any]:
    base_recipe = deepcopy(recipe)
    base_recipe["family"] = "shirt"
    strategies = _strategies_for(base_recipe)

    new_recipe = {**base_recipe, "execution_mode": "shirt_strategy"}
    entities_new, meta_new = compose_recipe(new_recipe, index, catalog)
    svg_new = svg_for_ir({"atomic_entities": entities_new})
    payload: dict[str, Any] = {
        "ok": True,
        "pipeline": meta_new.get("pipeline") or PIPELINE_ID,
        "execution_mode": meta_new.get("execution_mode"),
        "strategies": strategies or meta_new.get("strategies") or {},
        "status": meta_new.get("status"),
        "pieces": meta_new.get("pieces"),
        "component_results": meta_new.get("component_results"),
        "sources": meta_new.get("sources"),
        "png_data_url": _svg_to_png_data_url(svg_new, width=png_width),
        "svg": svg_new,
    }

    if compare_legacy:
        legacy_recipe = {**base_recipe, "execution_mode": "batch_preview", "sandbox_compare": True}
        entities_old, meta_old = compose_recipe(legacy_recipe, index, catalog)
        svg_old = svg_for_ir({"atomic_entities": entities_old})
        payload["legacy"] = {
            "execution_mode": meta_old.get("execution_mode"),
            "pipeline": meta_old.get("pipeline"),
            "status": meta_old.get("status"),
            "pieces": meta_old.get("pieces"),
            "component_results": meta_old.get("component_results"),
            "png_data_url": _svg_to_png_data_url(svg_old, width=png_width),
        }
    return payload
