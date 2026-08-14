"""Component operators for batch_preview — rewritten to match atomic rules.

Rules (CURSOR guide §6–§7 + grading handoff A/B/C):
- Replace edge_role chains (with piece_role as context), not invent lines.
- part_label donors preferred; missing/ambiguous → retained_current.
- Neckline: donor C → host A interface match (parametric fallback).
- Sleeve: remix body A + sleeve B, then sleeve_fb_morph / length-scale match.
- Cuff: after sleeve; pair both sides; no invented connectors.
- Garment length: real body hem_line + side_seam continuity.
"""
from __future__ import annotations

import math
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

_HANDOFF_SCRIPTS = Path(__file__).resolve().parents[2] / "_handoff_pack" / "scripts"
if _HANDOFF_SCRIPTS.is_dir() and str(_HANDOFF_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_HANDOFF_SCRIPTS))

from edge_role_resolver import BACK_ROLES, FRONT_ROLES, SLEEVE_ROLES

BODY_ROLES = FRONT_ROLES | BACK_ROLES
COLLAR_PIECE_ROLES = {"collar", "collar_stand", "collar_interlining", "neck_binding", "neck_rib"}
HEM_LINE_ROLES = {"hem_line", "hem", "bottom_hem", "garment_hem", "bottom_line"}
BAD_HEM_LINE_ROLES = {
    "armhole_front", "armhole_back", "armhole", "neckline", "shoulder_line", "shoulder_seam",
    "sleeve_cap", "sleeve_hem", "sleeve_underarm", "cuff_attach", "cuff_outer",
}


def _points(entity: dict[str, Any]) -> list[list[float]]:
    return [[float(x), float(y)] for x, y in (entity.get("geometry") or {}).get("points") or []]


def _set_points(entity: dict[str, Any], points: list[list[float]]) -> dict[str, Any]:
    updated = deepcopy(entity)
    geometry = dict(updated.get("geometry") or {})
    geometry["points"] = [[round(float(x), 4), round(float(y), 4)] for x, y in points]
    updated["geometry"] = geometry
    return updated


def _length(points: list[list[float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _bounds(rows: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    pts = [p for entity in rows for p in _points(entity)]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _piece_role(entity: dict[str, Any]) -> str:
    return str(entity.get("_piece_role") or entity.get("piece_role") or "")


def _line_role(entity: dict[str, Any]) -> str:
    return str(entity.get("line_role") or "").lower()


def modified_ids(before: list[dict[str, Any]], after: list[dict[str, Any]], hash_fn) -> tuple[str, ...]:
    before_map = {str(entity.get("entity_id")): hash_fn(entity) for entity in before if entity.get("entity_id")}
    after_map = {str(entity.get("entity_id")): hash_fn(entity) for entity in after if entity.get("entity_id")}
    changed = [eid for eid, digest in after_map.items() if before_map.get(eid) != digest]
    added = [eid for eid in after_map if eid not in before_map]
    return tuple(dict.fromkeys([*changed, *added]))


# ----- neckline / collar -------------------------------------------------


def _annotate_piece_roles(entities: list[dict[str, Any]], working_ir: dict[str, Any]) -> list[dict[str, Any]]:
    role_by_piece = {
        str(piece.get("piece_id") or ""): str(piece.get("piece_role") or "unknown")
        for piece in working_ir.get("piece_instances") or []
    }
    out = []
    for entity in entities:
        copied = deepcopy(entity)
        if not copied.get("_piece_role"):
            copied["_piece_role"] = role_by_piece.get(str(copied.get("piece_id") or ""), "unknown")
        out.append(copied)
    return out


def apply_neckline_reshape(entities: list[dict[str, Any]], working_ir: dict[str, Any], option_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fallback only: parametric host neckline reshape (no donor)."""
    from composition_engine import reshape_body_neckline

    slug = option_id.split(".")[-1]
    annotated = _annotate_piece_roles(entities, working_ir)
    next_entities, meta = reshape_body_neckline(annotated, {**working_ir, "atomic_entities": annotated}, slug)
    return next_entities, {"mode": "parametric_body_neckline_reshape", "reshape": meta, "slug": slug}


def _polyline_for_ids(entities: list[dict[str, Any]], ids: list[str]) -> list[list[float]]:
    by_id = {str(entity.get("entity_id")): entity for entity in entities}
    points: list[list[float]] = []
    for eid in ids:
        entity = by_id.get(str(eid))
        if entity:
            points.extend(_points(entity))
    return points


def _resample(points: list[list[float]], count: int) -> list[list[float]]:
    if len(points) < 2 or count < 2:
        return [p[:] for p in points]
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = lengths[-1] or 1.0
    out = []
    for i in range(count):
        target = total * i / (count - 1)
        j = 0
        while j + 1 < len(lengths) and lengths[j + 1] < target:
            j += 1
        if j + 1 >= len(points):
            out.append(points[-1][:])
            continue
        span = lengths[j + 1] - lengths[j] or 1.0
        t = (target - lengths[j]) / span
        a, b = points[j], points[j + 1]
        out.append([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
    return out


def _fit_polyline_to_ends(points: list[list[float]], start: list[float], end: list[float]) -> list[list[float]]:
    if len(points) < 2:
        return [start[:], end[:]]
    sx0, sy0 = points[0]
    sx1, sy1 = points[-1]
    src_dx, src_dy = sx1 - sx0, sy1 - sy0
    dst_dx, dst_dy = end[0] - start[0], end[1] - start[1]
    src_len = math.hypot(src_dx, src_dy) or 1.0
    dst_len = math.hypot(dst_dx, dst_dy) or 1.0
    scale = dst_len / src_len
    src_ang = math.atan2(src_dy, src_dx)
    dst_ang = math.atan2(dst_dy, dst_dx)
    cos_a = math.cos(dst_ang - src_ang)
    sin_a = math.sin(dst_ang - src_ang)
    out = []
    for x, y in points:
        vx, vy = (x - sx0) * scale, (y - sy0) * scale
        out.append([start[0] + vx * cos_a - vy * sin_a, start[1] + vx * sin_a + vy * cos_a])
    out[0] = start[:]
    out[-1] = end[:]
    return out


def _transfer_neckline_from_donor(
    entities: list[dict[str, Any]],
    ids_by_role: dict[str, list[str]],
    donor_ir: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Experiment-style: map donor C neckline chains onto host A endpoints (with runaway guards)."""
    from edge_role_resolver import resolve_edge_chains

    donor_ids_by_role: dict[str, list[str]] = {}
    for chain in resolve_edge_chains(donor_ir):
        if chain.status != "resolved" or not chain.canonical_role:
            continue
        donor_ids_by_role.setdefault(chain.canonical_role, []).extend(chain.ordered_entity_ids)
    donor_entities = donor_ir.get("atomic_entities") or []
    body_bounds = _bounds([entity for entity in entities if _piece_role(entity) in BODY_ROLES])
    changed: dict[str, dict[str, Any]] = {}
    applied: list[str] = []
    rejected: list[dict[str, Any]] = []
    for role in ("front_neckline", "back_neckline"):
        host_ids = [str(v) for v in ids_by_role.get(role, [])]
        donor_ids = [str(v) for v in donor_ids_by_role.get(role, [])]
        if not host_ids or not donor_ids:
            continue
        host_points = []
        by_id = {str(entity.get("entity_id")): entity for entity in entities}
        for eid in host_ids:
            host_points.extend(_points(by_id[eid]) if eid in by_id else [])
        donor_points = []
        donor_by_id = {str(entity.get("entity_id")): entity for entity in donor_entities}
        for eid in donor_ids:
            donor_points.extend(_points(donor_by_id[eid]) if eid in donor_by_id else [])
        if len(host_points) < 2 or len(donor_points) < 2:
            continue
        fitted = _fit_polyline_to_ends(donor_points, host_points[0], host_points[-1])
        # Prefer morph_polyline_keep_ends toward fitted length (experiment interface morph).
        try:
            from interface_morph import morph_polyline_keep_ends
            from geometry_ops import polyline_length

            target_len = polyline_length(fitted)
            fitted = morph_polyline_keep_ends(host_points, target_len)
            fitted[0] = host_points[0][:]
            fitted[-1] = host_points[-1][:]
        except Exception:
            pass
        hb = _bounds([{"geometry": {"points": host_points}}])
        tb = _bounds([{"geometry": {"points": fitted}}])
        if hb and tb:
            host_w, host_h = max(hb[2] - hb[0], 1.0), max(hb[3] - hb[1], 1.0)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            body_w = max((body_bounds[2] - body_bounds[0]) if body_bounds else host_w, host_w, 1.0)
            body_h = max((body_bounds[3] - body_bounds[1]) if body_bounds else host_h, host_h, 1.0)
            if tw > max(host_w * 2.2, body_w * 0.9, 900.0) or th > max(host_h * 2.8, body_h * 0.55, 520.0):
                rejected.append({"role": role, "reason": "neckline_donor_chain_runaway"})
                continue
        total_n = max(sum(len(_points(by_id[eid])) for eid in host_ids if eid in by_id), 2)
        full = _resample(fitted, total_n)
        cursor = 0
        for eid in host_ids:
            entity = by_id.get(eid)
            if not entity:
                continue
            n = len(_points(entity))
            if n < 2:
                continue
            end = min(len(full), cursor + n)
            segment = full[cursor:end]
            if len(segment) < 2:
                segment = _resample(fitted, n)
            elif len(segment) != n:
                segment = _resample(segment, n)
            changed[eid] = _set_points(entity, segment)
            cursor = max(0, end - 1)
        applied.append(role)
    if not changed:
        return entities, {"mode": "donor_neckline_unavailable", "applied_roles": applied, "rejected_roles": rejected}
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], {
        "mode": "experiment_neckC_to_bodyA",
        "applied_roles": applied,
        "rejected_roles": rejected,
        "donor_case_id": donor_ir.get("case_id"),
    }


def apply_neckline_from_donor(
    entities: list[dict[str, Any]],
    working_ir: dict[str, Any],
    option_id: str,
    ids_by_role: dict[str, list[str]],
    donor_ir: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prefer experiment donor C → host A neckline match; fallback to parametric reshape."""
    annotated = _annotate_piece_roles(entities, working_ir)
    if donor_ir:
        next_entities, meta = _transfer_neckline_from_donor(annotated, ids_by_role, donor_ir)
        if meta.get("mode") == "experiment_neckC_to_bodyA" and meta.get("applied_roles"):
            # Sync overlapping body cut with same slug so silhouette stays coherent.
            try:
                from composition_engine import reshape_body_neckline
                synced, reshape_meta = reshape_body_neckline(
                    next_entities, {**working_ir, "atomic_entities": next_entities}, option_id.split(".")[-1],
                )
                meta = {**meta, "reshape_sync": reshape_meta}
                return synced, meta
            except Exception:
                return next_entities, meta
    return apply_neckline_reshape(annotated, working_ir, option_id)


def apply_collar(
    entities: list[dict[str, Any]],
    working_ir: dict[str, Any],
    option_id: str,
    ids_by_role: dict[str, list[str]],
    donor_ir: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Experiment: adapt donor/host collar pieces to host neckline length."""
    from geometry_ops import role_edge_length
    from interface_morph import match_neck_to_neckline
    from run_experiments import NECK_ROLES, NECKLINE_ROLES, BODY_ROLES as EXP_BODY, piece_entities, role_map

    annotated = _annotate_piece_roles(entities, working_ir)
    host_ir = {**working_ir, "atomic_entities": annotated}
    neckline_len = role_edge_length(host_ir, NECKLINE_ROLES, EXP_BODY - NECK_ROLES) or role_edge_length(host_ir, NECKLINE_ROLES)
    source_ir = donor_ir or working_ir
    neck_ents = piece_entities(source_ir, NECK_ROLES)
    if not neck_ents:
        # No collar pieces → just transfer neckline shape.
        return apply_neckline_from_donor(annotated, working_ir, option_id, ids_by_role, donor_ir)

    roles = role_map(source_ir)
    for entity in neck_ents:
        entity["_piece_role"] = roles.get(entity.get("piece_id") or "", entity.get("_piece_role") or "collar")
        entity["_source_case"] = source_ir.get("case_id")
    morphed, morph_meta = match_neck_to_neckline(neck_ents, source_ir, max(neckline_len, 1.0), ease=1.0)
    donor_case = str(source_ir.get("case_id") or "neck")
    inserted = []
    for entity in morphed:
        copied = deepcopy(entity)
        copied["entity_id"] = f"{donor_case}:{copied.get('entity_id')}"
        copied["piece_id"] = f"{donor_case}:{copied.get('piece_id')}"
        inserted.append(copied)
    kept = [entity for entity in annotated if _piece_role(entity) not in COLLAR_PIECE_ROLES]
    # Keep host body neckline; optionally sync if donor also provided neckline style.
    body_synced, neck_meta = apply_neckline_from_donor(kept, working_ir, option_id, ids_by_role, donor_ir)
    return body_synced + inserted, {
        "mode": "experiment_neckC_collar_to_bodyA",
        "donor_case_id": source_ir.get("case_id"),
        "neck_morph": morph_meta,
        "neckline": neck_meta,
        "inserted_entity_count": len(inserted),
    }


# ----- sleeve ------------------------------------------------------------


def _reshape_armhole_points(points: list[list[float]], slug: str) -> list[list[float]]:
    if len(points) < 3:
        return [p[:] for p in points]
    start, end = points[0], points[-1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    span = math.hypot(dx, dy)
    if span <= 1e-6:
        return [p[:] for p in points]
    tx, ty = dx / span, dy / span
    nx, ny = -ty, tx
    factor = {
        "puff": 1.10, "flutter": 0.90, "raglan": 1.06, "batwing": 1.12,
        "bell": 1.04, "set-in": 1.0, "regular": 1.0,
    }.get(slug, 1.03)
    out: list[list[float]] = []
    for index, point in enumerate(points):
        if index == 0 or index == len(points) - 1:
            out.append(point[:])
            continue
        along = (point[0] - start[0]) * tx + (point[1] - start[1]) * ty
        base = [start[0] + tx * along, start[1] + ty * along]
        offset = (point[0] - base[0]) * nx + (point[1] - base[1]) * ny
        out.append([base[0] + nx * offset * factor, base[1] + ny * offset * factor])
    return out


def adapt_armholes(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], slug: str) -> tuple[list[dict[str, Any]], tuple[str, ...], dict[str, Any]]:
    front = ids_by_role.get("armhole_front") or []
    back = ids_by_role.get("armhole_back") or []
    if not front or not back:
        return entities, (), {"status": "missing_roles", "required": ["armhole_front", "armhole_back"]}
    target = set(front) | set(back)
    changed: dict[str, dict[str, Any]] = {}
    for entity in entities:
        eid = str(entity.get("entity_id") or "")
        if eid not in target:
            continue
        pts = _points(entity)
        if len(pts) < 2:
            continue
        changed[eid] = _set_points(entity, _reshape_armhole_points(pts, slug))
    if not changed:
        return entities, (), {"status": "no_mutable_armhole_entities"}
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], tuple(changed), {
        "status": "applied",
        "rule": "preserve_endpoints_scale_existing_curvature",
        "modified_entity_ids": sorted(changed),
        "slug": slug,
    }


def _sleeve_target_bounds(entities: list[dict[str, Any]], option_id: str, measurements: dict[str, Any] | None) -> tuple[float, float, float, float]:
    body = [entity for entity in entities if _piece_role(entity) in BODY_ROLES]
    box = _bounds(body) or _bounds(entities) or (0.0, 0.0, 600.0, 800.0)
    bx0, by0, bx1, by1 = box
    body_w, body_h = max(bx1 - bx0, 300.0), max(by1 - by0, 450.0)
    slug = option_id.split(".")[-1]
    family = option_id.split(".")[0] if "." in option_id else "tshirt"
    length_ratio, width_ratio = {
        "flutter": (0.28, 0.46), "puff": (0.34, 0.62), "raglan": (0.52, 0.56),
        "batwing": (0.56, 0.62), "bell": (0.68, 0.48),
    }.get(slug, (0.52, 0.44))
    if family == "shirt" and slug not in {"flutter", "puff"}:
        length_ratio, width_ratio = max(length_ratio, 0.66), min(width_ratio, 0.40)
    measurements = measurements or {}
    length_ratio *= max(0.82, min(1.18, float(measurements.get("sleeveLength") or 58) / 58.0))
    width_ratio *= max(0.88, min(1.18, float(measurements.get("upperArm") or 28) / 28.0))
    target_h = max(120.0, min(body_h * 0.86, body_h * length_ratio, 620.0 if family == "tshirt" else 820.0))
    target_w = max(140.0, min(body_w * 0.62, body_w * width_ratio, 760.0 if family == "tshirt" else 820.0))
    x0 = bx1 + body_w * 0.12
    y0 = by0 + body_h * 0.03
    return x0, y0, x0 + target_w, y0 + target_h


def _transform_entities_to_bounds(rows: list[dict[str, Any]], target: tuple[float, float, float, float], *, entity_prefix: str) -> list[dict[str, Any]]:
    source = _bounds(rows)
    if not rows or not source:
        return []
    sx0, sy0, sx1, sy1 = source
    tx0, ty0, tx1, ty1 = target
    scale = min(max(tx1 - tx0, 1.0) / max(sx1 - sx0, 1.0), max(ty1 - ty0, 1.0) / max(sy1 - sy0, 1.0))
    out: list[dict[str, Any]] = []
    for idx, entity in enumerate(rows):
        copied = deepcopy(entity)
        old_id = str(copied.get("entity_id") or f"entity_{idx}")
        old_piece = str(copied.get("piece_id") or "piece")
        copied["entity_id"] = f"{entity_prefix}:{old_id}"
        copied["piece_id"] = f"{entity_prefix}:{old_piece}"
        copied["_source_case"] = entity_prefix
        copied["_transfer_mode"] = "donor_sleeve_edge_bundle"
        if _piece_role(copied) not in SLEEVE_ROLES:
            copied["_piece_role"] = "sleeve"
        pts = [[tx0 + (x - sx0) * scale, ty0 + (y - sy0) * scale] for x, y in _points(copied)]
        out.append(_set_points(copied, pts))
    return out


def _mirror_entities(rows: list[dict[str, Any]], *, gap: float = 60.0) -> list[dict[str, Any]]:
    box = _bounds(rows)
    if not box:
        return []
    axis = box[2] + gap / 2.0
    out = []
    for entity in rows:
        copied = deepcopy(entity)
        copied["entity_id"] = f"{copied.get('entity_id')}:mirror"
        copied["piece_id"] = f"{copied.get('piece_id')}:mirror"
        role = _piece_role(copied)
        if role == "sleeve_left":
            copied["_piece_role"] = "sleeve_right"
        elif role == "sleeve_right":
            copied["_piece_role"] = "sleeve_left"
        elif role not in SLEEVE_ROLES:
            copied["_piece_role"] = "sleeve"
        out.append(_set_points(copied, [[axis + (axis - x), y] for x, y in _points(copied)]))
    return out


def _closed_preview_outlines(transformed: list[dict[str, Any]], donor_case_id: str) -> list[dict[str, Any]]:
    """Convex hulls from real donor geometry — not invented templates."""
    from preview_outline import build_closed_preview_outline

    by_piece: dict[str, list[dict[str, Any]]] = {}
    for entity in transformed:
        by_piece.setdefault(str(entity.get("piece_id") or "sleeve"), []).append(entity)
    outlines: list[dict[str, Any]] = []
    for index, (piece_id, rows) in enumerate(sorted(by_piece.items())):
        box = _bounds(rows)
        if not box:
            continue
        # Drop tiny fragment "sleeves" (labels/scraps mis-tagged as sleeve).
        if (box[2] - box[0]) * (box[3] - box[1]) < 800.0:
            continue
        try:
            outline = build_closed_preview_outline(
                rows,
                piece_role="sleeve",
                entity_id=f"{donor_case_id}:sleeve_preview_outline:{index:02d}",
                piece_id=f"{donor_case_id}:sleeve_preview_piece:{index:02d}",
            )
            outlines.append(outline)
        except ValueError:
            continue
    return outlines


def _largest_piece_bundle(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_piece: dict[str, list[dict[str, Any]]] = {}
    for entity in rows:
        by_piece.setdefault(str(entity.get("piece_id") or "sleeve"), []).append(entity)
    best: list[dict[str, Any]] = []
    best_area = -1.0
    for group in by_piece.values():
        box = _bounds(group)
        if not box:
            continue
        area = (box[2] - box[0]) * (box[3] - box[1])
        if area > best_area:
            best_area = area
            best = group
    return best


def replace_body_from_donor(
    entities: list[dict[str, Any]],
    donor_ir: dict[str, Any],
    roles: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Swap front/back (and optional placket) pieces from donor onto host layout."""
    from geometry_ops import bounds_of_entities, transform_entity
    from run_experiments import piece_entities, role_map

    target_roles = set(roles or BODY_ROLES)
    donor_case = str(donor_ir.get("case_id") or "donor")
    donor_roles = role_map(donor_ir)
    donor_rows = [
        entity
        for entity in (donor_ir.get("atomic_entities") or [])
        if str(donor_roles.get(entity.get("piece_id") or "") or entity.get("_piece_role") or entity.get("piece_role") or "") in target_roles
    ]
    if not donor_rows:
        donor_rows = piece_entities(donor_ir, target_roles)
    if not donor_rows:
        return entities, {"mode": "body_donor_missing", "donor_case_id": donor_case}

    host_body = [entity for entity in entities if _piece_role(entity) in target_roles]
    host_box = _bounds(host_body) or bounds_of_entities(host_body)
    donor_box = _bounds(donor_rows) or bounds_of_entities(donor_rows)
    if not host_box or not donor_box:
        return entities, {"mode": "body_bounds_missing", "donor_case_id": donor_case}

    hx0, hy0, hx1, hy1 = host_box
    dx0, dy0, dx1, dy1 = donor_box
    hw, hh = max(hx1 - hx0, 1e-3), max(hy1 - hy0, 1e-3)
    dw, dh = max(dx1 - dx0, 1e-3), max(dy1 - dy0, 1e-3)
    scale = min(hw / dw, hh / dh)
    # Fit donor body into host body bbox, centered.
    dcx, dcy = (dx0 + dx1) / 2.0, (dy0 + dy1) / 2.0
    hcx, hcy = (hx0 + hx1) / 2.0, (hy0 + hy1) / 2.0

    inserted: list[dict[str, Any]] = []
    for entity in donor_rows:
        copied = deepcopy(entity)
        role = str(donor_roles.get(entity.get("piece_id") or "") or _piece_role(entity) or "front_body")
        copied["_piece_role"] = role
        copied["_source_case"] = donor_case
        copied["entity_id"] = f"{donor_case}:{copied.get('entity_id')}"
        copied["piece_id"] = f"{donor_case}:{copied.get('piece_id')}"
        copied = transform_entity(
            copied, sx=scale, sy=scale, ox=dcx, oy=dcy, dx=hcx - dcx, dy=hcy - dcy,
        )
        inserted.append(copied)

    kept = [entity for entity in entities if _piece_role(entity) not in target_roles]
    return kept + inserted, {
        "mode": "body_piece_swap",
        "donor_case_id": donor_case,
        "scale": round(scale, 4),
        "inserted_entity_count": len(inserted),
        "roles": sorted(target_roles),
    }


def apply_sleeve_from_donor(
    entities: list[dict[str, Any]],
    ids_by_role: dict[str, list[str]],
    donor_ir: dict[str, Any],
    option_id: str,
    measurements: dict[str, Any] | None = None,
    *,
    preview_only: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Experiment remix: host body A + donor sleeve B, then sleeve_fb_morph to armhole."""
    from geometry_ops import entity_length, role_edge_length
    from preview_outline import build_closed_preview_outline
    from run_experiments import (
        ARMHOLE_ROLES,
        BODY_ROLES as EXP_BODY,
        NECK_ROLES,
        SLEEVE_CAP_ROLES,
        body_length_span,
        match_scale_for_interface,
        piece_entities,
        role_map,
        scale_sleeve_anisotropic,
    )
    from sleeve_fb_morph import match_sleeve_front_back

    family = option_id.split(".")[0] if "." in option_id else "tshirt"
    donor_case_id = str(donor_ir.get("case_id") or "donor")
    host_ir = {
        "case_id": "host",
        "piece_instances": [
            {"piece_id": str(entity.get("piece_id") or ""), "piece_role": _piece_role(entity)}
            for entity in entities
            if entity.get("piece_id")
        ],
        "edge_chains": [],
        "atomic_entities": entities,
    }
    chains = []
    by_id = {str(entity.get("entity_id")): entity for entity in entities if entity.get("entity_id")}
    for role, eids in ids_by_role.items():
        if not eids:
            continue
        piece_id = str((by_id.get(str(eids[0])) or {}).get("piece_id") or "")
        edge_role = "neckline" if role in {"front_neckline", "back_neckline"} else role
        chains.append({
            "edge_chain_id": f"host:{role}",
            "piece_id": piece_id,
            "edge_role": edge_role,
            "ordered_entity_ids": list(eids),
        })
    for entity in entities:
        lr = _line_role(entity)
        if lr in {"armhole_front", "armhole_back", "armhole"}:
            chains.append({
                "edge_chain_id": f"host:lr:{entity.get('entity_id')}",
                "piece_id": str(entity.get("piece_id") or ""),
                "edge_role": lr,
                "ordered_entity_ids": [str(entity.get("entity_id"))],
            })
    host_ir["edge_chains"] = chains

    sleeve_ents = piece_entities(donor_ir, {"sleeve", "sleeve_left", "sleeve_right"})
    if not sleeve_ents:
        sleeve_ents = piece_entities(donor_ir, {"sleeve", "sleeve_left", "sleeve_right", "cuff", "rib_cuff"})
    if not sleeve_ents:
        return entities, {"mode": "donor_piece_bundle_missing", "donor_case_id": donor_case_id}

    arm = role_edge_length(host_ir, ARMHOLE_ROLES, EXP_BODY - NECK_ROLES) or role_edge_length(host_ir, ARMHOLE_ROLES)
    cap = role_edge_length(donor_ir, SLEEVE_CAP_ROLES, {"sleeve", "sleeve_left", "sleeve_right"})
    if cap <= 1e-6:
        cap = sum(entity_length(e) for e in sleeve_ents) * 0.22
    host_body = {"atomic_entities": entities, "piece_instances": host_ir["piece_instances"]}
    host_len = body_length_span(piece_entities(host_body, EXP_BODY - NECK_ROLES))
    donor_len = body_length_span(piece_entities(donor_ir, EXP_BODY - NECK_ROLES)) or host_len or 1.0
    length_scale = max(0.85, min(1.25, (host_len / donor_len) if host_len and donor_len > 1e-6 else 1.0))
    width_scale = max(0.82, min(1.25, match_scale_for_interface(cap, arm or cap)))
    if measurements:
        try:
            length_scale *= max(0.85, min(1.15, float(measurements.get("sleeveLength") or 58) / 58.0))
            width_scale *= max(0.88, min(1.15, float(measurements.get("upperArm") or 28) / 28.0))
        except (TypeError, ValueError):
            pass

    roles = role_map(donor_ir)
    for entity in sleeve_ents:
        entity["_piece_role"] = roles.get(entity.get("piece_id") or "", "sleeve")
        entity["_source_case"] = donor_case_id
    sleeve_scaled = scale_sleeve_anisotropic(
        sleeve_ents, length_scale=length_scale, width_scale=width_scale, ir=donor_ir,
    )
    sleeve_morphed, morph_meta = match_sleeve_front_back(
        sleeve_scaled, donor_ir, host_ir, ease_front=0.013, ease_back=0.027, height_k=0.9,
    )
    pieces_meta = morph_meta.get("pieces") or []
    morph_ok = bool(pieces_meta) or bool(morph_meta.get("applied"))
    # Track A (length-matched scale) is enough to keep geometry usable when FB morph
    # cannot find tagged/geometric cap arcs on sparse donors.
    scale_matched = bool(arm and cap and width_scale > 0)
    if family == "tshirt" and not morph_ok and not scale_matched:
        return entities, {
            "mode": "donor_sleeve_blocked",
            "donor_case_id": donor_case_id,
            "armhole_adaptation": {"status": "missing_roles", "morph": morph_meta},
            "rule_id": "R3",
            "reason": "sleeve_fb_morph_failed",
        }
    if not morph_ok:
        sleeve_morphed = sleeve_scaled

    inserted: list[dict[str, Any]] = []
    for entity in sleeve_morphed:
        copied = deepcopy(entity)
        copied["entity_id"] = f"{donor_case_id}:{copied.get('entity_id')}"
        copied["piece_id"] = f"{donor_case_id}:{copied.get('piece_id')}"
        if _piece_role(copied) not in SLEEVE_ROLES:
            copied["_piece_role"] = "sleeve"
        inserted.append(copied)
    mirrored = False
    if len({str(entity.get("piece_id")) for entity in inserted}) == 1:
        inserted = inserted + _mirror_entities(inserted)
        mirrored = True

    preview_outlines: list[dict[str, Any]] = []
    by_piece: dict[str, list[dict[str, Any]]] = {}
    for entity in inserted:
        by_piece.setdefault(str(entity.get("piece_id") or "sleeve"), []).append(entity)
    for index, (_piece_id, rows) in enumerate(sorted(by_piece.items())):
        box = _bounds(rows)
        if not box or (box[2] - box[0]) * (box[3] - box[1]) < 800.0:
            continue
        try:
            preview_outlines.append(build_closed_preview_outline(
                rows,
                piece_role="sleeve",
                entity_id=f"{donor_case_id}:sleeve_preview_outline:{index:02d}",
                piece_id=f"{donor_case_id}:sleeve_preview_piece:{index:02d}",
            ))
        except ValueError:
            continue
    for entity in inserted:
        entity["_review_layer"] = "AI4M_REVIEW_RETAINED"
        entity["_review_reason"] = "raw_donor_sleeve_fragment_retained_for_audit"
    if len(preview_outlines) < 2 and by_piece:
        primary = max(
            by_piece.values(),
            key=lambda rows: (lambda b: 0.0 if not b else (b[2] - b[0]) * (b[3] - b[1]))(_bounds(rows)),
        )
        try:
            preview_outlines = [
                build_closed_preview_outline(
                    primary, piece_role="sleeve",
                    entity_id=f"{donor_case_id}:sleeve_preview_outline:00",
                    piece_id=f"{donor_case_id}:sleeve_preview_piece:00",
                ),
                build_closed_preview_outline(
                    _mirror_entities(primary), piece_role="sleeve",
                    entity_id=f"{donor_case_id}:sleeve_preview_outline:01",
                    piece_id=f"{donor_case_id}:sleeve_preview_piece:01",
                ),
            ]
            mirrored = True
        except ValueError:
            pass
    if len(preview_outlines) < 2:
        return entities, {
            "mode": "donor_sleeve_roles_incomplete",
            "donor_case_id": donor_case_id,
            "reason": "could_not_build_two_closed_sleeve_preview_outlines",
            "preview_outline_count": len(preview_outlines),
        }

    kept = [entity for entity in entities if _piece_role(entity) not in SLEEVE_ROLES]
    armhole_report = {
        "status": "applied" if (morph_ok or scale_matched or family != "tshirt") else "not_attempted",
        "rule": "sleeve_fb_morph.match_sleeve_front_back" if morph_ok else "experiment_trackA_interface_length_scale",
        "morph": {
            "piece_count": len(pieces_meta),
            "Af": (morph_meta.get("armhole") or {}).get("Af"),
            "Ab": (morph_meta.get("armhole") or {}).get("Ab"),
        },
    }
    # Shirt strategy path: keep closed preview outlines only — drop double-line donor scraps.
    sleeve_payload = preview_outlines if (preview_only and preview_outlines) else (preview_outlines + inserted)
    return kept + sleeve_payload, {
        "mode": "experiment_remix_bodyA_sleeveB",
        "donor_case_id": donor_case_id,
        "donor_entity_count": len(sleeve_ents),
        "inserted_entity_count": len(sleeve_payload),
        "preview_outline_count": len(preview_outlines),
        "preview_only": bool(preview_only and preview_outlines),
        "mirrored": mirrored,
        "length_scale": round(length_scale, 4),
        "width_scale": round(width_scale, 4),
        "armhole_len_A": round(arm, 3) if arm else None,
        "sleeve_cap_len_B": round(cap, 3) if cap else None,
        "armhole_adaptation": armhole_report,
        "rule_id": "R3" if family == "tshirt" else "R4",
    }


def scale_sleeve_edges(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], option_id: str) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    ids = set()
    for role in ("sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "sleeve_underarm", "sleeve_hem"):
        ids.update(ids_by_role.get(role, []))
    if not ids:
        return entities, ()
    slug = option_id.split(".")[-1]
    sx, sy = {"puff": (1.18, 1.06), "bell": (1.12, 1.12), "flutter": (1.28, 0.68), "batwing": (1.24, 1.08), "raglan": (1.08, 1.03)}.get(slug, (1.04, 1.0))
    pts_all = [p for entity in entities if str(entity.get("entity_id")) in ids for p in _points(entity)]
    if not pts_all:
        return entities, ()
    xs, ys = [p[0] for p in pts_all], [p[1] for p in pts_all]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    changed = {}
    for entity in entities:
        eid = str(entity.get("entity_id"))
        if eid not in ids:
            continue
        changed[eid] = _set_points(entity, [[cx + (x - cx) * sx, cy + (y - cy) * sy] for x, y in _points(entity)])
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], tuple(changed)


# ----- cuff --------------------------------------------------------------


CUFF_PIECE_ROLES = {"cuff", "rib_cuff", "sleeve_placket", "sleeve_placket_extension"}


def apply_cuff_from_donor(
    entities: list[dict[str, Any]],
    ids_by_role: dict[str, list[str]],
    donor_ir: dict[str, Any],
    option_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replace host cuff pieces with donor cuffs, scaled to current sleeve hem length."""
    from geometry_ops import transform_entity
    from run_experiments import piece_entities, role_map

    donor_case = str(donor_ir.get("case_id") or "donor")
    donor_roles = role_map(donor_ir)
    cuff_rows = [
        entity
        for entity in (donor_ir.get("atomic_entities") or [])
        if str(donor_roles.get(entity.get("piece_id") or "") or entity.get("_piece_role") or "") in CUFF_PIECE_ROLES
    ]
    if not cuff_rows:
        cuff_rows = piece_entities(donor_ir, CUFF_PIECE_ROLES)
    if not cuff_rows:
        return entities, {"mode": "cuff_donor_missing", "donor_case_id": donor_case}

    sleeve_hem_ids = set(ids_by_role.get("sleeve_hem", []))
    sleeve_len = sum(_length(_points(entity)) for entity in entities if str(entity.get("entity_id")) in sleeve_hem_ids)
    if sleeve_len <= 1e-6:
        # geometric fallback: bottom edge of sleeve pieces
        sleeve_rows = [entity for entity in entities if _piece_role(entity) in SLEEVE_ROLES]
        box = _bounds(sleeve_rows)
        sleeve_len = (box[2] - box[0]) if box else 0.0
    donor_box = _bounds(cuff_rows)
    if not donor_box:
        return entities, {"mode": "cuff_donor_empty", "donor_case_id": donor_case}
    donor_w = max(donor_box[2] - donor_box[0], 1e-3)
    slug = option_id.split(".")[-1]
    gather = 1.12 if slug in {"gathered", "ruffled"} else 1.0
    scale = max(0.55, min(1.85, (sleeve_len / donor_w) * gather)) if sleeve_len > 1e-6 else gather

    # Anchor near sleeve hem center if possible.
    hem_pts = [p for entity in entities if str(entity.get("entity_id")) in sleeve_hem_ids for p in _points(entity)]
    if hem_pts:
        tx = sum(p[0] for p in hem_pts) / len(hem_pts)
        ty = sum(p[1] for p in hem_pts) / len(hem_pts)
    else:
        sleeve_box = _bounds([entity for entity in entities if _piece_role(entity) in SLEEVE_ROLES])
        tx = ((sleeve_box[0] + sleeve_box[2]) / 2.0) if sleeve_box else 0.0
        ty = sleeve_box[1] if sleeve_box else 0.0
    dcx = (donor_box[0] + donor_box[2]) / 2.0
    dcy = (donor_box[1] + donor_box[3]) / 2.0

    inserted: list[dict[str, Any]] = []
    for entity in cuff_rows:
        copied = deepcopy(entity)
        copied["_piece_role"] = str(donor_roles.get(entity.get("piece_id") or "") or _piece_role(entity) or "cuff")
        copied["_source_case"] = donor_case
        copied["entity_id"] = f"{donor_case}:{copied.get('entity_id')}"
        copied["piece_id"] = f"{donor_case}:{copied.get('piece_id')}"
        copied = transform_entity(
            copied, sx=scale, sy=scale, ox=dcx, oy=dcy, dx=tx - dcx, dy=ty - dcy,
        )
        inserted.append(copied)
    if len({str(entity.get("piece_id")) for entity in inserted}) == 1:
        inserted = inserted + _mirror_entities(inserted)

    kept = [entity for entity in entities if _piece_role(entity) not in CUFF_PIECE_ROLES]
    return kept + inserted, {
        "mode": "cuff_donor_swap",
        "donor_case_id": donor_case,
        "scale": round(scale, 4),
        "inserted_entity_count": len(inserted),
        "slug": slug,
    }


def apply_cuff(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], option_id: str) -> tuple[list[dict[str, Any]], tuple[str, ...], dict[str, Any]]:
    cuff_ids = set(ids_by_role.get("cuff_attach", [])) | set(ids_by_role.get("cuff_outer", []))
    if not cuff_ids:
        # fall back to cuff piece entities
        cuff_ids = {str(entity.get("entity_id")) for entity in entities if _piece_role(entity) in {"cuff", "rib_cuff"}}
    if not cuff_ids:
        return entities, (), {"mode": "missing_cuff_roles"}
    sleeve_hem_ids = set(ids_by_role.get("sleeve_hem", []))
    cuff_attach_ids = set(ids_by_role.get("cuff_attach", [])) or cuff_ids
    sleeve_len = sum(_length(_points(entity)) for entity in entities if str(entity.get("entity_id")) in sleeve_hem_ids)
    cuff_len = sum(_length(_points(entity)) for entity in entities if str(entity.get("entity_id")) in cuff_attach_ids)
    ratio = 1.0 if sleeve_len <= 1e-6 or cuff_len <= 1e-6 else max(0.55, min(1.8, sleeve_len / cuff_len))
    slug = option_id.split(".")[-1]
    gather = 1.12 if slug in {"gathered", "ruffled"} else 1.0
    scale = ratio * gather
    pts_all = [p for entity in entities if str(entity.get("entity_id")) in cuff_ids for p in _points(entity)]
    if not pts_all:
        return entities, (), {"mode": "cuff_empty_geometry"}
    xs, ys = [p[0] for p in pts_all], [p[1] for p in pts_all]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    changed = {}
    for entity in entities:
        eid = str(entity.get("entity_id"))
        if eid not in cuff_ids:
            continue
        changed[eid] = _set_points(entity, [[cx + (x - cx) * scale, cy + (y - cy) * scale] for x, y in _points(entity)])
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], tuple(changed), {
        "mode": "cuff_scale_to_sleeve_hem",
        "scale": round(scale, 4),
    }


# ----- garment length ----------------------------------------------------


def resolve_body_hem_ids(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]]) -> set[str]:
    """Only true body hems — never armhole/neckline mislabeled as hem."""
    ids: set[str] = set()
    by_id = {str(entity.get("entity_id")): entity for entity in entities if entity.get("entity_id")}
    for entity in entities:
        if _piece_role(entity) not in BODY_ROLES:
            continue
        lr = _line_role(entity)
        if lr in HEM_LINE_ROLES or lr.endswith("_hem") and "sleeve" not in lr:
            ids.add(str(entity.get("entity_id")))
    for eid in ids_by_role.get("garment_hem", []):
        entity = by_id.get(str(eid))
        if not entity or _piece_role(entity) not in BODY_ROLES:
            continue
        lr = _line_role(entity)
        if lr in BAD_HEM_LINE_ROLES or any(token in lr for token in ("armhole", "neck", "sleeve", "shoulder", "cuff")):
            continue
        if lr in HEM_LINE_ROLES or "hem" in lr:
            ids.add(str(eid))
    if ids:
        return ids
    # geometric fallback: bottom-most cut/boundary on each body piece (CAD Y-up → min y)
    by_piece: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        if _piece_role(entity) not in BODY_ROLES:
            continue
        by_piece.setdefault(str(entity.get("piece_id") or ""), []).append(entity)
    for rows in by_piece.values():
        box = _bounds(rows)
        if not box:
            continue
        min_y, height = box[1], max(box[3] - box[1], 1.0)
        threshold = min_y + height * 0.12
        for entity in rows:
            if _line_role(entity) not in {"cut_line", "pattern_boundary", "net_boundary"}:
                continue
            pts = _points(entity)
            if len(pts) < 2:
                continue
            if sum(p[1] for p in pts) / len(pts) <= threshold:
                ids.add(str(entity.get("entity_id")))
    return ids


def apply_garment_length(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], option_id: str) -> tuple[list[dict[str, Any]], tuple[str, ...], dict[str, Any]]:
    hem_ids = resolve_body_hem_ids(entities, ids_by_role)
    if not hem_ids:
        return entities, (), {"mode": "missing_body_hem", "reason": "no_reliable_hem_line_on_body"}
    slug = option_id.split(".")[-1]
    delta = {"short": -45.0, "regular": 0.0, "long": 55.0}.get(slug, 0.0)
    if abs(delta) < 1e-6:
        return entities, (), {"mode": "garment_length_noop", "delta": 0.0}
    side_ids = set(ids_by_role.get("side_seam", []))
    for entity in entities:
        if _line_role(entity) == "side_seam" and _piece_role(entity) in BODY_ROLES:
            side_ids.add(str(entity.get("entity_id")))
    changed: dict[str, dict[str, Any]] = {}
    for entity in entities:
        eid = str(entity.get("entity_id") or "")
        pts = _points(entity)
        if len(pts) < 2:
            continue
        if eid in hem_ids:
            changed[eid] = _set_points(entity, [[x, y + delta] for x, y in pts])
            continue
        if eid in side_ids:
            # move the bottom endpoint (CAD Y-up → smaller y)
            ys = [p[1] for p in pts]
            bottom = min(ys)
            new_pts = []
            for x, y in pts:
                if abs(y - bottom) <= max(8.0, (max(ys) - min(ys)) * 0.08):
                    new_pts.append([x, y + delta])
                else:
                    new_pts.append([x, y])
            changed[eid] = _set_points(entity, new_pts)
    if not changed:
        return entities, (), {"mode": "garment_length_no_change"}
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], tuple(changed), {
        "mode": "body_hem_and_side_seam",
        "delta": delta,
        "hem_ids": sorted(hem_ids),
        "side_ids": sorted(side_ids & set(changed)),
    }
