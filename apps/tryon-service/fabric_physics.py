from __future__ import annotations

from typing import Any


BASE = {
    "density": 0.30,
    "stretch_warp": 110.0,
    "stretch_weft": 95.0,
    "shear": 14.0,
    "bending_warp": 2.0e-5,
    "bending_weft": 1.5e-5,
    "bending_bias": 7.0e-6,
    "damping": 2.0e-6,
    "body_friction": 0.28,
    "self_friction": 0.20,
}


PRESETS = {
    "light": {"density": 0.18, "stretch_warp": 70.0, "stretch_weft": 62.0, "shear": 8.0, "bending_warp": 7.0e-6, "bending_weft": 5.0e-6, "bending_bias": 3.0e-6, "body_friction": 0.18},
    "heavy": {"density": 0.48, "stretch_warp": 185.0, "stretch_weft": 165.0, "shear": 28.0, "bending_warp": 7.0e-5, "bending_weft": 5.0e-5, "bending_bias": 2.5e-5, "body_friction": 0.36},
    "rib": {"density": 0.34, "stretch_warp": 80.0, "stretch_weft": 38.0, "shear": 8.0, "bending_warp": 1.2e-5, "bending_weft": 8.0e-6, "bending_bias": 5.0e-6, "body_friction": 0.34},
    "stretch": {"density": 0.27, "stretch_warp": 50.0, "stretch_weft": 32.0, "shear": 6.0, "bending_warp": 8.0e-6, "bending_weft": 6.0e-6, "bending_bias": 3.0e-6, "body_friction": 0.30},
    "mesh": {"density": 0.14, "stretch_warp": 58.0, "stretch_weft": 50.0, "shear": 5.0, "bending_warp": 3.0e-6, "bending_weft": 2.0e-6, "bending_bias": 1.0e-6, "body_friction": 0.16},
}


def fabric_physics(material: dict[str, Any] | str | None) -> dict[str, float | str]:
    material_id = str(material.get("id") if isinstance(material, dict) else material or "cotton-jersey")
    lowered = material_id.lower()
    if any(token in lowered for token in ("heavy", "canvas", "terry", "denim", "twill")):
        family = "heavy"
    elif "rib" in lowered:
        family = "rib"
    elif any(token in lowered for token in ("stretch", "tencel", "cooling")):
        family = "stretch"
    elif any(token in lowered for token in ("mesh", "sheer", "chiffon")):
        family = "mesh"
    elif any(token in lowered for token in ("linen", "rayon", "silk", "slub")):
        family = "light"
    else:
        family = "cotton"
    values = {**BASE, **PRESETS.get(family, {})}
    values["stretch_warp"] *= 10.0
    values["stretch_weft"] *= 10.0
    values["shear"] *= 8.0
    values["bending_warp"] *= 3.0
    values["bending_weft"] *= 3.0
    values["bending_bias"] *= 3.0
    return {"material_id": material_id, "preset": family, **values}


def physics_cache_key(values: dict[str, Any]) -> str:
    keys = ("material_id", "density", "stretch_warp", "stretch_weft", "shear", "bending_warp", "bending_weft", "bending_bias", "damping", "body_friction", "self_friction")
    return "|".join(str(values.get(key)) for key in keys)
