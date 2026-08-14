"""Piece-level topology and garment inventory validation.

This module is intentionally conservative: it proves whether production
boundary geometry is closed enough to be reviewable. It does not try to repair
or infer missing garment construction.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

Point = tuple[float, float]

BOUNDARY_ROLES = {
    "pattern_boundary", "cut_line", "net_boundary", "boundary",
    "neckline", "front_neckline", "back_neckline",
    "armhole", "armhole_front", "armhole_back", "armhole_seam",
    "shoulder_seam", "shoulder_line", "side_seam", "garment_hem", "hem", "hem_line", "bottom_hem", "bottom_line",
    "sleeve_cap", "sleeve_cap_line", "sleeve_cap_front", "sleeve_cap_back", "sleeve_head",
    "sleeve_underarm", "sleeve_underarm_seam", "underarm", "sleeve_hem", "sleeve_hem_line",
    "cuff_attach", "cuff_outer", "cuff_attach_line", "cuff_edge",
    "collar_attach_line", "collar_edge", "neck_binding_line",
}

PRIMARY_BOUNDARY_ROLES = {"pattern_boundary", "cut_line", "net_boundary", "boundary"}

GARMENT_INVENTORY_RULES = {
    "tshirt": {
        "required": {
            "front_body": {"min": 1, "max": 1},
            "back_body": {"min": 1, "max": 1},
            "sleeve": {"min": 2, "max": 2},
        },
        "aliases": {
            "front_body": {"front", "front_body"},
            "back_body": {"back", "back_body"},
            "sleeve": {"sleeve", "sleeve_left", "sleeve_right"},
        },
    },
    "shirt": {
        "required": {
            "front_body": {"min": 1, "max": 2},
            "back_body": {"min": 1, "max": 1},
            "sleeve": {"min": 2, "max": 2},
        },
        "aliases": {
            "front_body": {"front", "front_body", "front_left", "front_right"},
            "back_body": {"back", "back_body"},
            "sleeve": {"sleeve", "sleeve_left", "sleeve_right"},
        },
    },
}


def _points(entity: dict[str, Any]) -> list[Point]:
    out: list[Point] = []
    for raw in (entity.get("geometry") or {}).get("points") or []:
        if len(raw) >= 2:
            out.append((float(raw[0]), float(raw[1])))
    return out


def _role(entity: dict[str, Any]) -> str:
    return str(entity.get("line_role") or entity.get("edge_role") or "")


def _is_boundary(entity: dict[str, Any]) -> bool:
    role = _role(entity)
    return role in BOUNDARY_ROLES or role.endswith("_boundary") or role.endswith("_hem")


def _piece_role(entity: dict[str, Any], piece_roles: dict[str, str] | None = None) -> str:
    role = str(entity.get("_piece_role") or "")
    if role:
        return role
    pid = str(entity.get("piece_id") or "")
    return (piece_roles or {}).get(pid, "unknown")


def _snap_key(point: Point, tolerance: float) -> tuple[int, int]:
    return (round(point[0] / tolerance), round(point[1] / tolerance))


def _polygon_area_from_edges(edges: list[tuple[Point, Point]], tolerance: float) -> float:
    if len(edges) < 3:
        return 0.0
    unused = edges[:]
    start, current = unused.pop(0)
    chain = [start, current]
    while unused:
        cur_key = _snap_key(current, tolerance)
        found = None
        reverse = False
        for idx, (a, b) in enumerate(unused):
            if _snap_key(a, tolerance) == cur_key:
                found = idx
                reverse = False
                break
            if _snap_key(b, tolerance) == cur_key:
                found = idx
                reverse = True
                break
        if found is None:
            break
        a, b = unused.pop(found)
        current = a if reverse else b
        chain.append(current)
        if _snap_key(current, tolerance) == _snap_key(start, tolerance):
            break
    if len(chain) < 4 or _snap_key(chain[0], tolerance) != _snap_key(chain[-1], tolerance):
        return 0.0
    area = 0.0
    for a, b in zip(chain, chain[1:]):
        area += a[0] * b[1] - b[0] * a[1]
    return abs(area) / 2.0


def validate_closed_pieces(
    entities: list[dict[str, Any]],
    required_roles: set[str] | None = None,
    *,
    piece_roles: dict[str, str] | None = None,
    snap_tolerance_mm: float = 1.0,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        if str(entity.get("_review_layer") or "") == "AI4M_REVIEW_RETAINED":
            continue
        if not _is_boundary(entity):
            continue
        pid = str(entity.get("piece_id") or entity.get("entity_id") or "unknown")
        if required_roles and _piece_role(entity, piece_roles) not in required_roles:
            continue
        if len(_points(entity)) >= 2:
            grouped[pid].append(entity)

    pieces: dict[str, dict[str, Any]] = {}
    valid = True
    for pid, rows in grouped.items():
        primary_rows = [row for row in rows if _role(row) in PRIMARY_BOUNDARY_ROLES or _role(row).endswith("_boundary")]
        if primary_rows:
            rows = primary_rows
        degree: Counter[tuple[int, int]] = Counter()
        loose_ids: list[str] = []
        edges: list[tuple[Point, Point]] = []
        for row in rows:
            pts = _points(row)
            if len(pts) < 2:
                loose_ids.append(str(row.get("entity_id") or ""))
                continue
            for a, b in zip(pts, pts[1:]):
                if math.hypot(b[0] - a[0], b[1] - a[1]) <= 1e-6:
                    continue
                degree[_snap_key(a, snap_tolerance_mm)] += 1
                degree[_snap_key(b, snap_tolerance_mm)] += 1
                edges.append((a, b))
        open_endpoint_count = sum(1 for count in degree.values() if count % 2 == 1)
        closed = bool(edges) and open_endpoint_count == 0 and len(degree) >= 3
        area = _polygon_area_from_edges(edges, snap_tolerance_mm) if closed else 0.0
        if area <= 1e-6:
            closed = False
        if not closed:
            valid = False
        pieces[pid] = {
            "piece_id": pid,
            "piece_role": _piece_role(rows[0], piece_roles),
            "entity_count": len(rows),
            "open_endpoint_count": open_endpoint_count,
            "closed_loop_count": 1 if closed else 0,
            "area": round(area, 4),
            "loose_entity_ids": loose_ids,
            "valid": closed,
        }
    if not pieces:
        valid = False
    return {"valid": valid, "pieces": pieces, "piece_count": len(pieces)}


def validate_garment_inventory(entities: list[dict[str, Any]], garment_type: str, *, piece_roles: dict[str, str] | None = None) -> dict[str, Any]:
    key = garment_type.lower()
    rule = GARMENT_INVENTORY_RULES.get(key)
    if not rule:
        return {"valid": False, "code": "unknown_garment_type", "garment_type": garment_type}
    closure = validate_closed_pieces(entities, piece_roles=piece_roles)
    counts: Counter[str] = Counter()
    closed_piece_ids_by_role: dict[str, list[str]] = defaultdict(list)
    for pid, piece in closure["pieces"].items():
        if not piece.get("valid"):
            continue
        role = str(piece.get("piece_role") or "unknown")
        for canonical, aliases in rule["aliases"].items():
            if role in aliases:
                counts[canonical] += 1
                closed_piece_ids_by_role[canonical].append(pid)
                break
    missing: dict[str, Any] = {}
    for canonical, expected in rule["required"].items():
        count = counts.get(canonical, 0)
        if count < expected["min"] or count > expected["max"]:
            missing[canonical] = {"expected": expected, "actual": count, "piece_ids": closed_piece_ids_by_role.get(canonical, [])}
    return {
        "valid": not missing,
        "garment_type": key,
        "counts": dict(counts),
        "missing_or_invalid": missing,
        "closure": closure,
    }


def validate_paired_component(entities: list[dict[str, Any]], role: str, *, piece_roles: dict[str, str] | None = None) -> dict[str, Any]:
    aliases = {role, f"{role}_left", f"{role}_right"}
    closure = validate_closed_pieces(entities, required_roles=aliases, piece_roles=piece_roles)
    closed = [pid for pid, row in closure["pieces"].items() if row.get("valid")]
    if len(closed) < 2:
        return {
            "valid": False,
            "code": "paired_component_incomplete",
            "role": role,
            "closed_piece_count": len(closed),
            "closed_piece_ids": closed,
            "closure": closure,
        }
    return {"valid": True, "role": role, "closed_piece_count": len(closed), "closed_piece_ids": closed, "closure": closure}


__all__ = [
    "BOUNDARY_ROLES",
    "GARMENT_INVENTORY_RULES",
    "validate_closed_pieces",
    "validate_garment_inventory",
    "validate_paired_component",
]
