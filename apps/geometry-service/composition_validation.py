"""Shared finite-geometry validation helpers for batch composition."""
from __future__ import annotations

import math
from typing import Any


def all_coordinates_finite(entities: list[dict[str, Any]]) -> bool:
    coords = [value for entity in entities for point in (entity.get("geometry") or {}).get("points") or [] for value in point]
    return bool(coords) and all(math.isfinite(float(value)) for value in coords)


__all__ = ["all_coordinates_finite"]
