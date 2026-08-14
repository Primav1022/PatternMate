from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any

from batch_planner import build_composition_plan
from batch_operators import (
    BODY_ROLES,
    apply_collar,
    apply_cuff,
    apply_cuff_from_donor,
    apply_garment_length,
    apply_neckline_from_donor,
    apply_sleeve_from_donor,
    modified_ids,
    replace_body_from_donor,
    scale_sleeve_edges,
)
from edge_role_resolver import SLEEVE_ROLES
from shirt_strategy import cuff_plan, public_plan, sleeve_plan


def _entity_piece_role(entity: dict[str, Any]) -> str:
    return str(entity.get("_piece_role") or entity.get("piece_role") or "unknown")
from composition_contracts import ComponentResult, CompositionPlan, PlanOperation, ValidationIssue
from edge_role_resolver import resolve_edge_chains
from donor_similarity import rank_donors
from piece_topology import validate_paired_component
from preview_outline import build_closed_preview_outline


def entity_hash(entity: dict[str, Any]) -> str:
    stable = {
        "entity_id": entity.get("entity_id"),
        "piece_id": entity.get("piece_id"),
        "line_role": entity.get("line_role"),
        "geometry": entity.get("geometry") or {},
    }
    payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chains_by_role(ir: dict[str, Any]) -> dict[str, list[str]]:
    rows = resolve_edge_chains(ir)
    result: dict[str, list[str]] = {}
    for row in rows:
        if row.status != "resolved" or not row.canonical_role:
            continue
        result.setdefault(row.canonical_role, []).extend(row.ordered_entity_ids)
    return result




def _piece_role_map(ir: dict[str, Any]) -> dict[str, str]:
    return {
        str(piece.get("piece_id")): str(piece.get("piece_role") or "unknown")
        for piece in ir.get("piece_instances") or []
        if piece.get("piece_id")
    }


def _component_gate(operation: PlanOperation, entities: list[dict[str, Any]], ir: dict[str, Any]) -> dict[str, Any]:
    piece_roles = _piece_role_map(ir)
    if operation.group == "sleeve":
        return validate_paired_component(entities, "sleeve", piece_roles=piece_roles)
    if operation.group == "cuff":
        cuff_report = validate_paired_component(entities, "cuff", piece_roles=piece_roles)
        if cuff_report.get("valid"):
            return cuff_report
        sleeve_report = validate_paired_component(entities, "sleeve", piece_roles=piece_roles)
        if sleeve_report.get("valid"):
            return {**sleeve_report, "role": "sleeve_hem", "warning": "cuff_piece_missing_validated_against_sleeve_pair"}
        return cuff_report
    return {"valid": True, "role": operation.group}

def _issue(operation: PlanOperation, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, severity="warning", message=message, operation_id=operation.operation_id)


def _requires_tshirt_armhole_adaptation_gate(operation: PlanOperation, transfer_extra: dict[str, Any]) -> bool:
    """R3: donor/preview T-shirt sleeve commits need recorded armhole adaptation."""
    if operation.group != "sleeve" or not operation.option_id.startswith("tshirt.sleeve."):
        return False
    mode = str(transfer_extra.get("mode") or "")
    if mode not in {
        "donor_sleeve_edge_bundle",
        "donor_piece_bundle_preview",
        "parametric_tshirt_sleeve_pair_preview",
        "experiment_remix_bodyA_sleeveB",
    }:
        return False
    return (transfer_extra.get("armhole_adaptation") or {}).get("status") != "applied"


def _missing_required(operation: PlanOperation, available_roles: set[str]) -> tuple[str, ...]:
    required = set(operation.mutable_roles)
    if operation.group in {"neckline", "collar"}:
        required = {"front_neckline", "back_neckline"}
    elif operation.group == "sleeve":
        required = {"sleeve_cap", "sleeve_hem"}
    elif operation.group == "cuff":
        required = {"cuff_attach", "cuff_outer"}
    elif operation.group == "garment_length":
        required = {"garment_hem"}
    return tuple(sorted(role for role in required if role not in available_roles))


def _entity_points(entity: dict[str, Any]) -> list[list[float]]:
    return [[float(x), float(y)] for x, y in (entity.get("geometry") or {}).get("points") or []]


def _set_points(entity: dict[str, Any], points: list[list[float]]) -> dict[str, Any]:
    updated = deepcopy(entity)
    geometry = dict(updated.get("geometry") or {})
    geometry["points"] = [[round(float(x), 4), round(float(y), 4)] for x, y in points]
    updated["geometry"] = geometry
    return updated




def _polyline_for_ids(entities: list[dict[str, Any]], ids: list[str] | tuple[str, ...] | set[str]) -> list[list[float]]:
    by_id = {str(entity.get("entity_id")): entity for entity in entities}
    points: list[list[float]] = []
    for entity_id in ids:
        pts = _entity_points(by_id.get(str(entity_id), {}))
        if not pts:
            continue
        if points and pts and math.hypot(points[-1][0] - pts[0][0], points[-1][1] - pts[0][1]) <= 1.0:
            points.extend(pts[1:])
        else:
            points.extend(pts)
    return points


def _transform_polyline_to_endpoints(points: list[list[float]], target_start: list[float], target_end: list[float]) -> list[list[float]]:
    if len(points) < 2:
        return [target_start[:], target_end[:]]
    src_start, src_end = points[0], points[-1]
    src_dx, src_dy = src_end[0] - src_start[0], src_end[1] - src_start[1]
    dst_dx, dst_dy = target_end[0] - target_start[0], target_end[1] - target_start[1]
    src_len = math.hypot(src_dx, src_dy) or 1.0
    dst_len = math.hypot(dst_dx, dst_dy) or 1.0
    src_ang = math.atan2(src_dy, src_dx)
    dst_ang = math.atan2(dst_dy, dst_dx)
    scale = dst_len / src_len
    ca, sa = math.cos(dst_ang - src_ang), math.sin(dst_ang - src_ang)
    out: list[list[float]] = []
    for x, y in points:
        vx, vy = (x - src_start[0]) * scale, (y - src_start[1]) * scale
        out.append([target_start[0] + vx * ca - vy * sa, target_start[1] + vx * sa + vy * ca])
    out[0] = target_start[:]
    out[-1] = target_end[:]
    return out


def _resample_polyline(points: list[list[float]], count: int) -> list[list[float]]:
    if count <= 2 or len(points) < 2:
        return [points[0][:], points[-1][:]] if len(points) >= 2 else points
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = lengths[-1]
    if total <= 1e-6:
        return [points[0][:] for _ in range(count)]
    out = []
    seg = 0
    for i in range(count):
        target = total * i / (count - 1)
        while seg < len(lengths) - 2 and lengths[seg + 1] < target:
            seg += 1
        span = max(lengths[seg + 1] - lengths[seg], 1e-6)
        t = (target - lengths[seg]) / span
        a, b = points[seg], points[seg + 1]
        out.append([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])
    return out





def _polyline_length(points: list[list[float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _bounds_of_entities(rows: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    pts = [point for entity in rows for point in _entity_points(entity)]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _bounds_of_points(points: list[list[float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _piece_roles(ir: dict[str, Any]) -> dict[str, str]:
    return {str(piece.get("piece_id")): str(piece.get("piece_role") or "unknown") for piece in ir.get("piece_instances") or [] if piece.get("piece_id")}


def _entities_for_piece_roles(ir: dict[str, Any], roles: set[str]) -> list[dict[str, Any]]:
    role_by_piece = _piece_roles(ir)
    out: list[dict[str, Any]] = []
    for entity in ir.get("atomic_entities") or []:
        piece_id = str(entity.get("piece_id") or "")
        piece_role = str(entity.get("_piece_role") or role_by_piece.get(piece_id) or "")
        line_role = str(entity.get("line_role") or "")
        if piece_role in roles or ("sleeve" in roles and line_role.startswith("sleeve_")):
            copied = deepcopy(entity)
            copied["_piece_role"] = piece_role if piece_role in roles else "sleeve"
            out.append(copied)
    return out


def _transform_entities_to_bounds(rows: list[dict[str, Any]], target: tuple[float, float, float, float], *, entity_prefix: str, piece_suffix: str = "") -> list[dict[str, Any]]:
    source = _bounds_of_entities(rows)
    if not rows or not source:
        return []
    sx0, sy0, sx1, sy1 = source
    tx0, ty0, tx1, ty1 = target
    sw, sh = max(sx1 - sx0, 1.0), max(sy1 - sy0, 1.0)
    tw, th = max(tx1 - tx0, 1.0), max(ty1 - ty0, 1.0)
    scale = min(tw / sw, th / sh)
    out: list[dict[str, Any]] = []
    for idx, entity in enumerate(rows):
        copied = deepcopy(entity)
        old_id = str(copied.get("entity_id") or f"entity_{idx}")
        old_piece = str(copied.get("piece_id") or "piece")
        copied["entity_id"] = f"{entity_prefix}:{old_id}{piece_suffix}"
        copied["piece_id"] = f"{entity_prefix}:{old_piece}{piece_suffix}"
        pts = []
        for x, y in _entity_points(copied):
            pts.append([tx0 + (x - sx0) * scale, ty0 + (y - sy0) * scale])
        copied = _set_points(copied, pts)
        copied["_source_case"] = entity_prefix
        copied["_transfer_mode"] = "donor_piece_bundle_preview"
        out.append(copied)
    return out


def _mirror_entities_horiz(rows: list[dict[str, Any]], *, gap: float = 60.0, suffix: str = ":mirror") -> list[dict[str, Any]]:
    bounds = _bounds_of_entities(rows)
    if not bounds:
        return []
    x0, _y0, x1, _y1 = bounds
    axis = x1 + gap / 2.0
    out: list[dict[str, Any]] = []
    for entity in rows:
        copied = deepcopy(entity)
        copied["entity_id"] = f"{copied.get('entity_id')}{suffix}"
        copied["piece_id"] = f"{copied.get('piece_id')}{suffix}"
        pts = [[axis + (axis - x), y] for x, y in _entity_points(copied)]
        out.append(_set_points(copied, pts))
    return out





def _template_sleeve_outline(entity_id: str, piece_id: str, *, x: float, y: float, w: float, h: float, slug: str, piece_role: str = "sleeve") -> dict[str, Any]:
    if slug == "flutter":
        pts = [
            [x + w * 0.28, y + h * 0.05],
            [x + w * 0.50, y],
            [x + w * 0.72, y + h * 0.05],
            [x + w * 0.88, y + h * 0.78],
            [x + w * 0.65, y + h * 0.96],
            [x + w * 0.50, y + h],
            [x + w * 0.35, y + h * 0.96],
            [x + w * 0.12, y + h * 0.78],
        ]
    elif slug == "puff":
        pts = [
            [x + w * 0.18, y + h * 0.24],
            [x + w * 0.30, y + h * 0.06],
            [x + w * 0.50, y],
            [x + w * 0.70, y + h * 0.06],
            [x + w * 0.82, y + h * 0.24],
            [x + w * 0.78, y + h * 0.82],
            [x + w * 0.62, y + h],
            [x + w * 0.38, y + h],
            [x + w * 0.22, y + h * 0.82],
        ]
    else:
        pts = [
            [x + w * 0.18, y + h * 0.18],
            [x + w * 0.36, y + h * 0.03],
            [x + w * 0.64, y + h * 0.03],
            [x + w * 0.82, y + h * 0.18],
            [x + w * 0.78, y + h],
            [x + w * 0.22, y + h],
        ]
    closed = [[round(px, 4), round(py, 4)] for px, py in pts]
    closed.append(closed[0][:])
    return {
        "entity_id": entity_id,
        "piece_id": piece_id,
        "_piece_role": piece_role,
        "line_role": "pattern_boundary",
        "geometry": {"points": closed},
        "_transfer_mode": "closed_preview_outline",
        "_review_required": True,
        "_review_reason": f"parametric_tshirt_{slug}_sleeve_preview_from_donor_part_label",
    }


def _build_tshirt_sleeve_template_pair(donor_case_id: str, target: tuple[float, float, float, float], slug: str) -> list[dict[str, Any]]:
    x0, y0, x1, y1 = target
    target_w = max(x1 - x0, 1.0)
    target_h = max(y1 - y0, 1.0)
    gap = min(max(target_w * 0.08, 36.0), 60.0)
    if slug == "flutter":
        piece_w = min(max((target_w - gap) / 2.0, 180.0), 310.0)
        piece_h = min(max(target_h * 0.82, 150.0), 220.0)
    elif slug == "puff":
        piece_w = min(max((target_w - gap) / 2.0, 200.0), 330.0)
        piece_h = min(max(target_h * 0.92, 170.0), 260.0)
    else:
        piece_w = min(max((target_w - gap) / 2.0, 210.0), 360.0)
        piece_h = min(max(target_h * 0.95, 220.0), 520.0)
    total_w = piece_w * 2 + gap
    start_x = x0 + max((target_w - total_w) / 2.0, 0.0)
    start_y = y0 + max((target_h - piece_h) / 2.0, 0.0)
    return [
        _template_sleeve_outline(
            f"{donor_case_id}:sleeve_parametric_preview:left",
            f"{donor_case_id}:sleeve_parametric_piece:left",
            x=start_x,
            y=start_y,
            w=piece_w,
            h=piece_h,
            slug=slug,
            piece_role="sleeve_left",
        ),
        _template_sleeve_outline(
            f"{donor_case_id}:sleeve_parametric_preview:right",
            f"{donor_case_id}:sleeve_parametric_piece:right",
            x=start_x + piece_w + gap,
            y=start_y,
            w=piece_w,
            h=piece_h,
            slug=slug,
            piece_role="sleeve_right",
        ),
    ]


def _reshape_armhole_points(points: list[list[float]], slug: str) -> list[list[float]]:
    if len(points) < 3:
        return points
    start = points[0]
    end = points[-1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    span = math.hypot(dx, dy)
    if span <= 1e-6:
        return points
    tx, ty = dx / span, dy / span
    nx, ny = -ty, tx
    factor = {"puff": 1.08, "flutter": 0.92}.get(slug, 1.0)
    reshaped: list[list[float]] = []
    for index, point in enumerate(points):
        if index == 0 or index == len(points) - 1:
            reshaped.append(point[:])
            continue
        vx, vy = point[0] - start[0], point[1] - start[1]
        along = vx * tx + vy * ty
        base = [start[0] + tx * along, start[1] + ty * along]
        offset = (point[0] - base[0]) * nx + (point[1] - base[1]) * ny
        reshaped.append([base[0] + nx * offset * factor, base[1] + ny * offset * factor])
    return reshaped


def _adapt_tshirt_armholes(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], slug: str) -> tuple[list[dict[str, Any]], tuple[str, ...], dict[str, Any]]:
    if slug not in {"puff", "flutter"}:
        return entities, (), {"status": "not_supported", "reason": f"{slug}_requires_dedicated_body_operator"}
    target_ids = set(ids_by_role.get("armhole_front", [])) | set(ids_by_role.get("armhole_back", []))
    if not ids_by_role.get("armhole_front") or not ids_by_role.get("armhole_back"):
        return entities, (), {"status": "missing_roles", "required": ["armhole_front", "armhole_back"]}
    changed: dict[str, dict[str, Any]] = {}
    before_lengths: dict[str, float] = {}
    after_lengths: dict[str, float] = {}
    for entity in entities:
        entity_id = str(entity.get("entity_id") or "")
        if entity_id not in target_ids:
            continue
        points = _entity_points(entity)
        if len(points) < 2:
            continue
        before_lengths[entity_id] = round(_polyline_length(points), 4)
        new_points = _reshape_armhole_points(points, slug)
        after_lengths[entity_id] = round(_polyline_length(new_points), 4)
        changed[entity_id] = _set_points(entity, new_points)
    if not changed:
        return entities, (), {"status": "no_mutable_armhole_entities"}
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], tuple(changed), {
        "status": "applied",
        "rule": "preserve_endpoints_scale_existing_curvature",
        "modified_entity_ids": sorted(changed),
        "before_lengths": before_lengths,
        "after_lengths": after_lengths,
    }

def _sleeve_target_bounds(entities: list[dict[str, Any]], option_id: str, measurements: dict[str, Any] | None = None) -> tuple[float, float, float, float]:
    body_roles = {"front_body", "back_body", "front_left", "front_right"}
    body = [entity for entity in entities if str(entity.get("_piece_role") or "") in body_roles]
    body_bounds = _bounds_of_entities(body) or _bounds_of_entities(entities) or (0.0, 0.0, 600.0, 800.0)
    bx0, by0, bx1, by1 = body_bounds
    body_w = max(bx1 - bx0, 300.0)
    body_h = max(by1 - by0, 450.0)
    slug = option_id.split(".")[-1]
    family = option_id.split(".")[0] if "." in option_id else "tshirt"
    # Sleeve sizing intentionally uses a conservative body-relative rule rather
    # than the old host sleeve bbox. Some source IR sleeves are unlaid-out or
    # anomalously long; using them as the target is what made migrated sleeves
    # fly outside the garment preview.
    max_h = 620.0 if family == "tshirt" else 820.0
    max_w = 760.0 if family == "tshirt" else 820.0
    if slug == "flutter":
        length_ratio, width_ratio = 0.28, 0.46
        max_h, max_w = (260.0, 720.0) if family == "tshirt" else (360.0, 680.0)
    elif slug == "puff":
        length_ratio, width_ratio = (0.34 if family == "tshirt" else 0.52), (0.62 if family == "tshirt" else 0.50)
        max_h, max_w = (320.0, 780.0) if family == "tshirt" else (620.0, 760.0)
    elif slug == "raglan":
        length_ratio, width_ratio = (0.52 if family == "tshirt" else 0.66), 0.56
        max_h, max_w = (560.0, 820.0) if family == "tshirt" else (780.0, 860.0)
    elif slug == "batwing":
        length_ratio, width_ratio = (0.56 if family == "tshirt" else 0.68), 0.62
        max_h, max_w = (600.0, 920.0) if family == "tshirt" else (800.0, 900.0)
    elif slug == "bell":
        length_ratio, width_ratio = 0.68, 0.48
        max_h, max_w = (620.0, 760.0) if family == "tshirt" else (820.0, 820.0)
    else:
        length_ratio, width_ratio = (0.52 if family == "tshirt" else 0.66), (0.44 if family == "tshirt" else 0.38)
    measurements = measurements or {}
    upper_arm = float(measurements.get("upperArm") or 28.0)
    sleeve_length_cm = float(measurements.get("sleeveLength") or 58.0)
    length_ratio *= max(0.82, min(1.18, sleeve_length_cm / 58.0))
    width_ratio *= max(0.88, min(1.18, upper_arm / 28.0))
    target_h = max(120.0, min(body_h * 0.86, body_h * length_ratio, max_h))
    width_cap_ratio = 0.74 if family == "tshirt" and slug in {"flutter", "puff"} else 0.62
    target_w = max(140.0, min(body_w * width_cap_ratio, body_w * width_ratio, max_w))
    x0 = bx1 + body_w * 0.12
    y0 = by0 + body_h * 0.03
    return (x0, y0, x0 + target_w, y0 + target_h)

def _replace_sleeve_from_donor(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], donor_ir: dict[str, Any], option_id: str, measurements: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], tuple[str, ...], dict[str, Any]]:
    donor_rows = _entities_for_piece_roles(donor_ir, {"sleeve", "sleeve_left", "sleeve_right"})
    if not donor_rows:
        return entities, (), {"mode": "donor_piece_bundle_missing", "donor_case_id": donor_ir.get("case_id")}
    target = _sleeve_target_bounds(entities, option_id, measurements)
    slug = option_id.split(".")[-1]
    family = option_id.split(".")[0] if "." in option_id else "tshirt"
    transformed = _transform_entities_to_bounds(donor_rows, target, entity_prefix=str(donor_ir.get("case_id") or "donor"))
    piece_ids = {str(entity.get("piece_id")) for entity in transformed}
    mirrored = False
    if len(piece_ids) == 1:
        transformed = transformed + _mirror_entities_horiz(transformed)
        mirrored = True
    for entity in transformed:
        entity["_review_layer"] = "AI4M_REVIEW_RETAINED"
        entity["_review_reason"] = "raw_donor_sleeve_fragment_retained_for_audit"
    if family == "tshirt" and slug in {"flutter", "puff"}:
        preview_outlines = _build_tshirt_sleeve_template_pair(str(donor_ir.get("case_id") or "donor"), target, slug)
        preview_mode = "parametric_tshirt_sleeve_pair_preview"
        armhole_entities, armhole_modified, armhole_report = _adapt_tshirt_armholes(entities, ids_by_role, slug)
    else:
        preview_outlines = []
        armhole_entities, armhole_modified, armhole_report = entities, (), {"status": "not_attempted"}
        by_piece: dict[str, list[dict[str, Any]]] = {}
        for entity in transformed:
            by_piece.setdefault(str(entity.get("piece_id") or "sleeve"), []).append(entity)
        for index, (piece_id, rows) in enumerate(sorted(by_piece.items())):
            try:
                preview_outlines.append(build_closed_preview_outline(
                    rows,
                    piece_role="sleeve",
                    entity_id=f"{donor_ir.get('case_id')}:sleeve_preview_outline:{index:02d}",
                    piece_id=f"{donor_ir.get('case_id')}:sleeve_preview_piece:{index:02d}",
                ))
            except ValueError:
                continue
        preview_mode = "donor_piece_bundle_preview"
    inserted = preview_outlines + transformed
    replaced_roles = {"sleeve", "sleeve_left", "sleeve_right"}
    kept = [entity for entity in armhole_entities if str(entity.get("_piece_role") or "") not in replaced_roles]
    modified = tuple(str(entity.get("entity_id")) for entity in inserted if entity.get("entity_id")) + tuple(armhole_modified)
    return kept + inserted, modified, {"mode": preview_mode, "donor_case_id": donor_ir.get("case_id"), "donor_entity_count": len(donor_rows), "inserted_entity_count": len(inserted), "preview_outline_count": len(preview_outlines), "target_bounds": [round(v, 3) for v in target], "mirrored": mirrored, "armhole_adaptation": armhole_report, "rule_id": "R3" if family == "tshirt" else "R4"}


def _replace_neckline_from_donor(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], donor_ir: dict[str, Any]) -> tuple[list[dict[str, Any]], tuple[str, ...], dict[str, Any]]:
    donor_ids_by_role = _chains_by_role(donor_ir)
    donor_entities = donor_ir.get("atomic_entities") or []
    changed: dict[str, dict[str, Any]] = {}
    applied_roles: list[str] = []
    rejected_roles: list[dict[str, Any]] = []
    body_roles = {"front_body", "back_body", "front_left", "front_right"}
    body_bounds = _bounds_of_entities([entity for entity in entities if str(entity.get("_piece_role") or "") in body_roles])
    for role in ("front_neckline", "back_neckline"):
        host_ids = ids_by_role.get(role) or []
        donor_ids = donor_ids_by_role.get(role) or []
        if not host_ids or not donor_ids:
            continue
        host_points = _polyline_for_ids(entities, host_ids)
        donor_points = _polyline_for_ids(donor_entities, donor_ids)
        if len(host_points) < 2 or len(donor_points) < 2:
            continue
        transformed = _transform_polyline_to_endpoints(donor_points, host_points[0], host_points[-1])
        host_bounds = _bounds_of_points(host_points)
        transformed_bounds = _bounds_of_points(transformed)
        if host_bounds and transformed_bounds:
            host_w = max(host_bounds[2] - host_bounds[0], 1.0)
            host_h = max(host_bounds[3] - host_bounds[1], 1.0)
            transformed_w = transformed_bounds[2] - transformed_bounds[0]
            transformed_h = transformed_bounds[3] - transformed_bounds[1]
            body_w = max((body_bounds[2] - body_bounds[0]) if body_bounds else host_w, host_w, 1.0)
            body_h = max((body_bounds[3] - body_bounds[1]) if body_bounds else host_h, host_h, 1.0)
            if (
                transformed_w > max(host_w * 2.2, body_w * 0.9, 900.0)
                or transformed_h > max(host_h * 2.8, body_h * 0.55, 520.0)
            ):
                rejected_roles.append({
                    "role": role,
                    "reason": "neckline_donor_chain_runaway",
                    "host_bounds": [round(v, 3) for v in host_bounds],
                    "transformed_bounds": [round(v, 3) for v in transformed_bounds],
                })
                continue
        cursor = 0
        total_host_points = max(sum(len(_entity_points(next((row for row in entities if str(row.get("entity_id")) == str(entity_id)), {}) or {})) for entity_id in host_ids), 2)
        full = _resample_polyline(transformed, total_host_points)
        for entity_id in host_ids:
            entity = next((row for row in entities if str(row.get("entity_id")) == str(entity_id)), None)
            if not entity:
                continue
            old_points = _entity_points(entity)
            if len(old_points) < 2:
                continue
            n = len(old_points)
            # Split the donor-shaped chain across host segments instead of pasting
            # the full curve into every segment (that blew up piece bounds).
            end = min(len(full), cursor + n)
            segment = full[cursor:end]
            if len(segment) < 2:
                segment = _resample_polyline(transformed, max(n, 2))
            elif len(segment) != n:
                segment = _resample_polyline(segment, n)
            changed[str(entity_id)] = _set_points(entity, segment)
            cursor = max(0, end - 1)
        applied_roles.append(role)
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], tuple(changed), {"mode": "donor_edge_chain_shape", "roles": applied_roles, "rejected_roles": rejected_roles, "donor_case_id": donor_ir.get("case_id")}


def _neck_depth(slug: str, role: str, span: float) -> float:
    front = {
        "crew": 0.08, "v-neck": 0.34, "open-v-pointed": 0.38, "boat": 0.03,
        "high-mock": -0.04, "cowl": 0.18, "asymmetric": 0.24, "polo": 0.10,
    }
    back = {"crew": 0.03, "v-neck": 0.07, "open-v-pointed": 0.08, "boat": 0.02, "high-mock": -0.02}
    ratio = front.get(slug, 0.12) if role == "front_neckline" else back.get(slug, 0.04)
    return span * ratio


def _redraw_neckline(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], slug: str) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    changed: dict[str, dict[str, Any]] = {}
    for role in ("front_neckline", "back_neckline"):
        for entity_id in ids_by_role.get(role, []):
            entity = next((row for row in entities if str(row.get("entity_id")) == entity_id), None)
            if not entity:
                continue
            points = _entity_points(entity)
            if len(points) < 2:
                continue
            start, end = points[0], points[-1]
            dx, dy = end[0] - start[0], end[1] - start[1]
            span = max(math.hypot(dx, dy), 1.0)
            nx, ny = -dy / span, dx / span
            # Prefer garment-inward direction for common upright data; this is
            # a local trial deformation and remains review_required.
            if role == "front_neckline":
                ny = abs(ny) if abs(ny) > abs(nx) else ny
            depth = _neck_depth(slug, role, span)
            new_points: list[list[float]] = []
            for index, point in enumerate(points):
                t = index / max(len(points) - 1, 1)
                if slug in {"v-neck", "open-v-pointed"} and role == "front_neckline":
                    amount = depth * (1.0 - abs(2.0 * t - 1.0))
                elif slug == "asymmetric" and role == "front_neckline":
                    amount = depth * (t / 0.65 if t <= 0.65 else (1.0 - t) / 0.35)
                else:
                    amount = depth * math.sin(math.pi * t)
                new_points.append([point[0] + nx * amount, point[1] + ny * amount])
            changed[entity_id] = _set_points(entity, new_points)
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], tuple(changed)


def _selected_ids(ids_by_role: dict[str, list[str]], roles: tuple[str, ...] | set[str]) -> set[str]:
    selected: set[str] = set()
    for role in roles:
        selected.update(ids_by_role.get(role, []))
    return selected


def _length_of_ids(entities: list[dict[str, Any]], ids: set[str]) -> float:
    return sum(
        sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
        for entity in entities
        if str(entity.get("entity_id")) in ids
        for points in [_entity_points(entity)]
    )


def _bounds_of_ids(entities: list[dict[str, Any]], ids: set[str]) -> tuple[float, float, float, float] | None:
    points = [point for entity in entities if str(entity.get("entity_id")) in ids for point in _entity_points(entity)]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _scale_ids_about_center(entities: list[dict[str, Any]], ids: set[str], sx: float, sy: float) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    bounds = _bounds_of_ids(entities, ids)
    if not bounds:
        return entities, ()
    cx = (bounds[0] + bounds[2]) / 2.0
    cy = (bounds[1] + bounds[3]) / 2.0
    changed: dict[str, dict[str, Any]] = {}
    for entity in entities:
        entity_id = str(entity.get("entity_id"))
        if entity_id not in ids:
            continue
        changed[entity_id] = _set_points(entity, [[cx + (x - cx) * sx, cy + (y - cy) * sy] for x, y in _entity_points(entity)])
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], tuple(changed)


def _adjust_sleeve_edges(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], option_id: str) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    ids = _selected_ids(ids_by_role, {"sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "sleeve_underarm", "sleeve_hem"})
    if not ids:
        return entities, ()
    slug = option_id.split(".")[-1]
    sx, sy = {
        "puff": (1.18, 1.06),
        "bell": (1.12, 1.12),
        "flutter": (1.28, 0.68),
        "batwing": (1.24, 1.08),
        "raglan": (1.08, 1.03),
    }.get(slug, (1.04, 1.0))
    return _scale_ids_about_center(entities, ids, sx=sx, sy=sy)


def _adjust_cuff_edges(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], option_id: str) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    cuff_ids = _selected_ids(ids_by_role, {"cuff_attach", "cuff_outer"})
    if not cuff_ids:
        return entities, ()
    sleeve_hem_ids = _selected_ids(ids_by_role, {"sleeve_hem"})
    cuff_attach_ids = _selected_ids(ids_by_role, {"cuff_attach"})
    sleeve_len = _length_of_ids(entities, sleeve_hem_ids)
    cuff_len = _length_of_ids(entities, cuff_attach_ids)
    ratio = 1.0 if sleeve_len <= 1e-6 or cuff_len <= 1e-6 else max(0.55, min(1.8, sleeve_len / cuff_len))
    slug = option_id.split(".")[-1]
    gather = 1.12 if slug in {"gathered", "ruffled"} else 1.0
    return _scale_ids_about_center(entities, cuff_ids, sx=ratio * gather, sy=1.0)


def _adjust_garment_length(entities: list[dict[str, Any]], ids_by_role: dict[str, list[str]], option_id: str) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    ids = set(ids_by_role.get("garment_hem", []))
    if not ids:
        return entities, ()
    slug = option_id.split(".")[-1]
    delta = {"short": -45.0, "regular": 0.0, "long": 55.0}.get(slug, 0.0)
    changed: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if str(entity.get("entity_id")) not in ids:
            continue
        changed[str(entity.get("entity_id"))] = _set_points(entity, [[x, y + delta] for x, y in _entity_points(entity)])
    return [changed.get(str(entity.get("entity_id")), entity) for entity in entities], tuple(changed)


def _protected_hashes(before: list[dict[str, Any]], modified: tuple[str, ...]) -> dict[str, str]:
    modified_set = set(modified)
    return {
        str(entity.get("entity_id")): entity_hash(entity)
        for entity in before
        if entity.get("entity_id") and str(entity.get("entity_id")) not in modified_set
    }


def _closure_report(entities: list[dict[str, Any]], modified: tuple[str, ...]) -> dict[str, Any]:
    modified_set = set(modified)
    max_endpoint_gap = 0.0
    checked = 0
    for entity in entities:
        if str(entity.get("entity_id")) not in modified_set:
            continue
        points = _entity_points(entity)
        if len(points) < 2:
            continue
        checked += 1
        max_endpoint_gap = max(max_endpoint_gap, math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]))
    status = "closed" if checked and max_endpoint_gap <= 1.0 else "open_review_required"
    return {"status": status, "checked_edge_count": checked, "max_endpoint_gap_mm": round(max_endpoint_gap, 4)}


def _edge_transfer_report(operation: PlanOperation, ids_by_role: dict[str, list[str]], modified: tuple[str, ...], donor_payload: list[dict[str, Any]]) -> dict[str, Any]:
    modified_set = set(modified)
    host_roles = sorted(role for role, ids in ids_by_role.items() if modified_set.intersection(ids))
    return {
        "operation_id": operation.operation_id,
        "host_roles": host_roles,
        "mutable_roles": list(operation.mutable_roles),
        "dependent_roles": list(operation.dependent_roles),
        "modified_entity_ids": list(modified),
        "donor_case_id": donor_payload[0]["case_id"] if donor_payload else None,
        "donor_score": donor_payload[0]["score"] if donor_payload else None,
    }


def _result(operation: PlanOperation, status: str, modified: tuple[str, ...] = (), issue: ValidationIssue | None = None, extra: dict[str, Any] | None = None, donor_case_id: str | None = None, protected_hashes: dict[str, str] | None = None) -> ComponentResult:
    return ComponentResult(
        operation_id=operation.operation_id,
        group=operation.group,
        status=status,  # type: ignore[arg-type]
        donor_case_id=donor_case_id,
        option_id=operation.option_id,
        modified_entity_ids=modified,
        protected_entity_hashes=protected_hashes or {},
        validation_issues=tuple([issue] if issue else []),
        review_required=True,
        provenance={"operator": "edge-role-batch.v1", **(extra or {})},
    )


def execute_batch_preview(base_ir: dict[str, Any], recipe: dict[str, Any], plan: CompositionPlan | None = None, donor_index: dict[str, dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[ComponentResult]]:
    plan = plan or build_composition_plan(recipe, base_ir)
    entities = deepcopy(base_ir.get("atomic_entities") or [])
    working_ir = {**base_ir, "atomic_entities": entities}
    results: list[ComponentResult] = []
    for operation in plan.operations:
        ids_by_role = _chains_by_role(working_ir)
        available_roles = set(ids_by_role)
        donor_rows = rank_donors(operation.group, working_ir, donor_index or {}, max_donors=operation.max_donors, target_option_id=operation.option_id) if donor_index else []
        donor_payload = [
            {"case_id": row.case_id, "score": row.score, "breakdown": row.breakdown, "reasons": list(row.reasons)}
            for row in donor_rows
        ]
        donor_case_id = donor_rows[0].case_id if donor_rows else None
        missing = _missing_required(operation, available_roles)
        # Neckline/collar/garment_length can proceed with geometric fallbacks even if
        # canonical roles are incomplete; sleeve still needs donor or host sleeve edges.
        soft_groups = {"neckline", "collar", "garment_length", "cuff"}
        allow_donor_bundle_without_host_edges = operation.group == "sleeve" and bool(donor_case_id)
        if missing and operation.group not in soft_groups and not allow_donor_bundle_without_host_edges:
            issue = _issue(operation, "missing_required_edge_roles", f"missing canonical edge roles: {', '.join(missing)}")
            results.append(_result(operation, "retained_current", issue=issue, extra={"missing_roles": missing, "donor_candidates": donor_payload}, donor_case_id=donor_case_id))
            continue
        before = deepcopy(entities)
        transfer_extra: dict[str, Any] = {}
        modified: tuple[str, ...] = ()
        if operation.group == "neckline":
            donor_ir = (donor_index or {}).get(donor_case_id or "") if donor_case_id else None
            entities, transfer_extra = apply_neckline_from_donor(
                entities, working_ir, operation.option_id, ids_by_role, donor_ir,
            )
            modified = modified_ids(before, entities, entity_hash)
        elif operation.group == "collar":
            donor_ir = (donor_index or {}).get(donor_case_id or "") if donor_case_id else None
            entities, transfer_extra = apply_collar(
                entities, working_ir, operation.option_id, ids_by_role, donor_ir,
            )
            modified = modified_ids(before, entities, entity_hash)
        elif operation.group == "sleeve":
            donor_ir = (donor_index or {}).get(donor_case_id or "") if donor_case_id else None
            shirt_mode = str(recipe.get("family") or "") == "shirt" or str(recipe.get("execution_mode") or "") == "shirt_strategy"
            strategy = sleeve_plan(operation.option_id) if shirt_mode else None
            transfer_extra = {"strategy": public_plan(strategy)} if strategy else {}
            skip_sleeve_insert = False
            body_checkpoint: list[dict[str, Any]] | None = None
            if shirt_mode and strategy and donor_ir:
                if strategy.get("mode") == "body_and_sleeve":
                    entities, body_meta = replace_body_from_donor(entities, donor_ir, BODY_ROLES)
                    transfer_extra["body_swap"] = body_meta
                    body_checkpoint = deepcopy(entities)
                    working_ir = {**base_ir, "atomic_entities": entities}
                    ids_by_role = _chains_by_role(working_ir)
                elif strategy.get("mode") == "body_integrated":
                    entities = [
                        entity for entity in entities
                        if _entity_piece_role(entity) not in (set(SLEEVE_ROLES) | {"cuff", "rib_cuff"})
                    ]
                    entities, body_meta = replace_body_from_donor(entities, donor_ir, BODY_ROLES)
                    transfer_extra.update({"body_swap": body_meta, "mode": "shirt_body_integrated", "drop_host_sleeves": True})
                    modified = modified_ids(before, entities, entity_hash)
                    skip_sleeve_insert = True
            if donor_ir and not skip_sleeve_insert:
                entities, sleeve_extra = apply_sleeve_from_donor(
                    entities,
                    ids_by_role,
                    donor_ir,
                    operation.option_id,
                    recipe.get("measurements_cm") or {},
                    preview_only=shirt_mode,
                )
                transfer_extra = {**transfer_extra, **sleeve_extra}
                if strategy:
                    transfer_extra["strategy"] = public_plan(strategy)
                modified = modified_ids(before, entities, entity_hash)
                if transfer_extra.get("mode") in {"donor_sleeve_blocked", "donor_piece_bundle_missing", "donor_sleeve_roles_incomplete"}:
                    # Keep successful body swap for batwing-like strategies; only drop sleeve insert.
                    if body_checkpoint is not None and (transfer_extra.get("body_swap") or {}).get("mode") == "body_piece_swap":
                        entities = body_checkpoint
                        working_ir = {**base_ir, "atomic_entities": entities}
                        transfer_extra["mode"] = "shirt_body_swap_sleeve_unavailable"
                        transfer_extra["sleeve_rollback"] = sleeve_extra.get("mode")
                        modified = modified_ids(before, entities, entity_hash)
                    else:
                        issue = _issue(
                            operation,
                            "armhole_sleeve_cap_not_adapted" if transfer_extra.get("mode") == "donor_sleeve_blocked" else "donor_sleeve_unavailable",
                            transfer_extra.get("reason") or transfer_extra.get("mode") or "sleeve donor could not be applied",
                        )
                        results.append(_result(
                            operation, "retained_current", issue=issue,
                            extra={"donor_candidates": donor_payload, "edge_transfer": transfer_extra, "rule_id": transfer_extra.get("rule_id"), "rollback_reason": transfer_extra.get("mode")},
                            donor_case_id=donor_case_id,
                        ))
                        entities = before
                        working_ir = {**base_ir, "atomic_entities": entities}
                        continue
            elif not skip_sleeve_insert:
                entities, modified = scale_sleeve_edges(entities, ids_by_role, operation.option_id)
                transfer_extra = {**transfer_extra, "mode": "parametric_sleeve_scale_no_donor"}
        elif operation.group == "cuff":
            donor_ir = (donor_index or {}).get(donor_case_id or "") if donor_case_id else None
            shirt_mode = str(recipe.get("family") or "") == "shirt" or str(recipe.get("execution_mode") or "") == "shirt_strategy"
            if shirt_mode and donor_ir:
                entities, transfer_extra = apply_cuff_from_donor(entities, ids_by_role, donor_ir, operation.option_id)
                transfer_extra["strategy"] = public_plan(cuff_plan(operation.option_id))
                modified = modified_ids(before, entities, entity_hash)
                if transfer_extra.get("mode") in {"cuff_donor_missing", "cuff_donor_empty"}:
                    # Fall back to host cuff scale so sandbox still produces an edit.
                    entities, modified, transfer_extra = apply_cuff(entities, ids_by_role, operation.option_id)
                    transfer_extra["fallback_from"] = "cuff_donor_swap"
            else:
                entities, modified, transfer_extra = apply_cuff(entities, ids_by_role, operation.option_id)
            if transfer_extra.get("mode") == "missing_cuff_roles":
                issue = _issue(operation, "missing_required_edge_roles", "missing cuff_attach/cuff_outer (or cuff pieces)")
                results.append(_result(operation, "retained_current", issue=issue, extra={"donor_candidates": donor_payload, "edge_transfer": transfer_extra}, donor_case_id=donor_case_id))
                entities = before
                continue
        elif operation.group == "garment_length":
            entities, modified, transfer_extra = apply_garment_length(entities, ids_by_role, operation.option_id)
            if transfer_extra.get("mode") in {"missing_body_hem", "garment_length_no_change"} and not modified:
                issue = _issue(operation, "missing_body_hem" if "missing" in transfer_extra.get("mode", "") else "no_mutable_entities_changed", transfer_extra.get("reason") or transfer_extra.get("mode") or "garment length unchanged")
                results.append(_result(operation, "retained_current", issue=issue, extra={"donor_candidates": donor_payload, "edge_transfer": transfer_extra}, donor_case_id=donor_case_id))
                entities = before
                continue
        else:
            issue = _issue(operation, "operator_review_only", "edge roles exist, but this operator is retained for human review in the current prototype")
            results.append(_result(operation, "retained_current", issue=issue, extra={"donor_candidates": donor_payload}, donor_case_id=donor_case_id))
            entities = before
            continue
        if not modified:
            issue = _issue(operation, "no_mutable_entities_changed", "resolved edge roles did not contain editable entities")
            results.append(_result(operation, "retained_current", issue=issue, extra={"donor_candidates": donor_payload, "edge_transfer": transfer_extra}, donor_case_id=donor_case_id))
            entities = before
            continue
        if any(not math.isfinite(float(value)) for entity in entities for point in _entity_points(entity) for value in point):
            issue = _issue(operation, "non_finite_geometry", "candidate generated non-finite geometry")
            results.append(_result(operation, "retained_current", issue=issue, extra={"donor_candidates": donor_payload, "edge_transfer": transfer_extra}, donor_case_id=donor_case_id))
            entities = before
            continue
        # T-shirt sleeve still requires recorded armhole adaptation (R3).
        if _requires_tshirt_armhole_adaptation_gate(operation, transfer_extra):
            issue = _issue(
                operation,
                "armhole_sleeve_cap_not_adapted",
                "R3 requires front/back body armholes to be adapted to the inserted sleeve cap before a T-shirt sleeve operation can be applied",
            )
            results.append(_result(
                operation, "retained_current", issue=issue,
                extra={"donor_candidates": donor_payload, "edge_transfer": transfer_extra, "rule_id": "R3", "rollback_reason": "armhole_sleeve_cap_not_adapted"},
                donor_case_id=donor_case_id,
            ))
            entities = before
            working_ir = {**base_ir, "atomic_entities": entities}
            continue
        transfer_report = {**_edge_transfer_report(operation, ids_by_role, modified, donor_payload), **transfer_extra}
        closure_report = _closure_report(entities, modified)
        candidate_ir = {**base_ir, "atomic_entities": entities}
        gate_report = _component_gate(operation, entities, candidate_ir)
        shirt_partial = str(transfer_extra.get("mode") or "") in {
            "shirt_body_swap_sleeve_unavailable",
            "shirt_body_integrated",
            "cuff_donor_swap",
            "experiment_remix_bodyA_sleeveB",
            "experiment_neckC_collar_to_bodyA",
        } and (
            str(recipe.get("family") or "") == "shirt"
            or str(recipe.get("execution_mode") or "") == "shirt_strategy"
        )
        if not gate_report.get("valid", False) and not shirt_partial:
            code = str(gate_report.get("code") or "component_topology_invalid")
            issue = _issue(operation, code, "component operation requires human topology review after donor bundle transfer")
            results.append(_result(
                operation, "retained_current", issue=issue,
                extra={"donor_candidates": donor_payload, "edge_transfer": transfer_report, "closure": closure_report, "topology_gate": gate_report},
                donor_case_id=donor_case_id,
            ))
            entities = before
            working_ir = {**base_ir, "atomic_entities": entities}
            continue
        if not gate_report.get("valid", False) and shirt_partial:
            transfer_report["topology_gate_softened"] = gate_report
        working_ir = candidate_ir
        results.append(_result(
            operation,
            "applied",
            modified=modified,
            extra={"donor_candidates": donor_payload, "edge_transfer": transfer_report, "closure": closure_report, "topology_gate": gate_report},
            donor_case_id=donor_case_id,
            protected_hashes=_protected_hashes(before, modified),
        ))
    return entities, results
