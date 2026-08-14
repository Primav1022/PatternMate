"""Closed preview outlines for fragmented donor component bundles.

These outlines are visualization/review aids. They are not a substitute for
production pattern-boundary reconstruction.
"""
from __future__ import annotations

from typing import Any

Point = tuple[float, float]


def _points(entity: dict[str, Any]) -> list[Point]:
    out: list[Point] = []
    for raw in (entity.get("geometry") or {}).get("points") or []:
        if len(raw) >= 2:
            out.append((float(raw[0]), float(raw[1])))
    return out


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull(points: list[Point]) -> list[Point]:
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts
    lower: list[Point] = []
    for point in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[Point] = []
    for point in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def build_closed_preview_outline(entities: list[dict[str, Any]], *, piece_role: str, entity_id: str, piece_id: str | None = None) -> dict[str, Any]:
    pts = [point for entity in entities for point in _points(entity)]
    hull = convex_hull(pts)
    if len(hull) < 3:
        raise ValueError("not enough geometry to build preview outline")
    closed = [[round(x, 4), round(y, 4)] for x, y in hull]
    if closed[0] != closed[-1]:
        closed.append(closed[0][:])
    return {
        "entity_id": entity_id,
        "piece_id": piece_id or f"{entity_id}:piece",
        "_piece_role": piece_role,
        "line_role": "pattern_boundary",
        "geometry": {"points": closed},
        "_transfer_mode": "closed_preview_outline",
        "_review_required": True,
        "_review_reason": "convex_hull_preview_from_fragmented_donor_bundle",
    }


__all__ = ["build_closed_preview_outline", "convex_hull"]
