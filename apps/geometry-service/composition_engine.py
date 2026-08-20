from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from geometry_ops import bounds_of_entities, entity_length, entity_points, role_edge_length, transform_entity
from tryon_descriptor import build_tryon_descriptor
from run_experiments import (
    ARMHOLE_ROLES,
    NECKLINE_ROLES,
    SLEEVE_CAP_ROLES,
    piece_entities,
    role_map,
    scale_piece_group,
    scale_sleeve_anisotropic,
)


FRONT_ROLES = {"front_body", "front_left", "front_right"}
BACK_ROLES = {"back_body", "back_yoke"}
COLLAR_ROLES = {"neck_binding", "neck_rib", "collar", "collar_stand", "collar_interlining"}
PLACKET_ROLES = {"front_placket"}
PURE_SLEEVE_ROLES = {"sleeve", "sleeve_left", "sleeve_right"}
CUFF_ROLES = {"cuff", "rib_cuff", "sleeve_placket", "sleeve_placket_extension"}
_PIECE_SWAP_MODES = {"piece_swap", "piece_swap_similarity", "simple_piece_swap"}

# Compose/preview: keep labeled pieces only. Unmatched fused DXF lines are dropped.
_PREVIEW_DROP_PIECE_ROLES = {"unknown", "", "none", "unlabeled", "scrap"}
_PREVIEW_DROP_LINE_ROLES = {
    "drill_hole", "text", "construction", "auxiliary", "internal",
    "front_neckline", "back_neckline", "armhole_front", "armhole_back",
    "shoulder", "shoulder_seam", "sleeve_cap",
}
# Note: shirt IR often stores cut outlines as line_role=unknown on a labeled
# piece (collar/yoke/placket). Keep those; drop entities with no piece.


def _entity_points(entity: dict[str, Any]) -> list[list[float]]:
    points = (entity.get("geometry") or {}).get("points") or []
    return [p for p in points if isinstance(p, (list, tuple)) and len(p) >= 2]


def _is_labeled_piece_entity(entity: dict[str, Any]) -> bool:
    role = str(entity.get("_piece_role") or entity.get("piece_role") or "unknown")
    piece_id = str(entity.get("piece_id") or "")
    if entity.get("_display_only"):
        return False
    if role in _PREVIEW_DROP_PIECE_ROLES or not piece_id or piece_id.startswith("inferred:"):
        return False
    return True


def prefer_piece_cut_outlines(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep labeled-piece geometry only. Drop unmatched fused DXF lines.

    Never convex-hull a piece: hull fills armhole/neck and becomes the
    compose source, so grading morphs a slab instead of the cut.
    """
    scrap = _PREVIEW_DROP_LINE_ROLES
    by_role: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        if not _is_labeled_piece_entity(entity):
            continue
        role = str(entity.get("_piece_role") or entity.get("piece_role") or "unknown")
        by_role.setdefault(role, []).append(entity)

    out: list[dict[str, Any]] = []
    for role, rows in by_role.items():
        by_piece: dict[str, list[dict[str, Any]]] = {}
        for entity in rows:
            by_piece.setdefault(str(entity.get("piece_id") or entity.get("entity_id")), []).append(entity)

        for piece_id, piece_rows in by_piece.items():
            usable = [
                entity
                for entity in piece_rows
                if len(_entity_points(entity)) >= 2
                and str(entity.get("line_role") or "").lower() not in scrap
            ]
            if usable:
                out.extend(usable)
    return out


def filter_preview_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep labeled pattern pieces; drop unmatched DXF lines and CAD junk."""
    from shirt_side_seam import drop_extra_closed_outlines

    entities = drop_extra_closed_outlines(prefer_piece_cut_outlines(entities))
    out: list[dict[str, Any]] = []
    for entity in entities:
        role = str(entity.get("_piece_role") or entity.get("piece_role") or "unknown")
        if role in _PREVIEW_DROP_PIECE_ROLES:
            continue
        line_role = str(entity.get("line_role") or entity.get("edge_role") or "").lower()
        if line_role in _PREVIEW_DROP_LINE_ROLES:
            continue
        points = _entity_points(entity)
        if len(points) < 2:
            continue
        out.append(entity)
    return _keep_one_grainline(out)


def _keep_one_grainline(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CAD 别布 hatches explode into dozens of grainlines; preview keeps one."""
    best: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if str(entity.get("line_role") or "").lower() != "grainline":
            continue
        pid = str(entity.get("piece_id") or "")
        if pid not in best or entity_length(entity) > entity_length(best[pid]):
            best[pid] = entity
    out: list[dict[str, Any]] = []
    for entity in entities:
        if str(entity.get("line_role") or "").lower() != "grainline":
            out.append(entity)
        elif best.get(str(entity.get("piece_id") or "")) is entity:
            out.append(entity)
    return out


GROUP_LABELS = {
    "silhouette": "廓形",
    "collar": "领型",
    "placket": "前门襟",
    "cuff": "袖口",
    "sleeve": "袖型",
    "neckline": "领口",
    "special": "特殊设计",
}

FIT_BY_SLUG = {
    "relaxed-h": "relaxed",
    "fitted-x": "fitted",
    "oversized": "oversized",
    "regular-fit": "regular",
    "a-line": "relaxed",
}

LABEL_KEY_BY_GROUP = {
    "neckline": "neckline",
    "collar": "collar",
    "placket": "placket",
    "cuff": "cuff",
    "sleeve": "sleeve_style",
    "silhouette": "silhouette",
}

REQUIRED_ROLES_BY_GROUP = {
    "neckline": ({"neck_binding", "neck_rib", "collar"},),
    "collar": ({"collar"}, {"collar_stand", "neck_binding"}),
    "placket": ({"front_placket", "front_left", "front_right"},),
    "sleeve": ({"sleeve", "sleeve_left", "sleeve_right"},),
    "cuff": ({"cuff", "rib_cuff"},),
}

REPLACE_ROLES_BY_GROUP = {
    "neckline": COLLAR_ROLES,
    "collar": COLLAR_ROLES,
    "placket": PLACKET_ROLES,
    "sleeve": PURE_SLEEVE_ROLES,
    "cuff": CUFF_ROLES,
}


def normalize_family(category: str | None) -> str:
    if category in {"shirt", "blouse"}:
        return "shirt"
    return "tshirt"


def _role_set(ir: dict[str, Any]) -> set[str]:
    return {item.get("piece_role") or "unknown" for item in ir.get("piece_instances") or []}


def _label_slug(ir: dict[str, Any], group: str) -> str | None:
    labels = ((ir.get("design_semantics_extra") or {}).get("part_labels") or {})
    value = labels.get(LABEL_KEY_BY_GROUP.get(group, group))
    if isinstance(value, dict):
        slug = value.get("slug")
        return str(slug) if slug else None
    return None


def _infer_piece_role_from_line_role(line_role: str) -> str:
    role = line_role.lower()
    if role.startswith("sleeve_") or role in {"sleeve_cap", "underarm"}:
        return "sleeve"
    if role.startswith("cuff_") or role in {"cuff_edge", "cuff_attach_line"}:
        return "cuff"
    if role.startswith("collar_"):
        return "collar"
    if role in {"armhole_front", "side_seam", "center_front", "placket_line"}:
        return "front_body"
    if role in {"armhole_back", "center_back", "yoke_seam"}:
        return "back_body"
    if "placket" in role:
        return "front_placket"
    if "hem" in role:
        return "front_body"
    return "unknown"


def _piece_assembly_map(ir: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Map entity_id → (piece_id, piece_role) from piece_instances boundaries.

    Shirt IR from final_shirt_dataset often stores cut outlines as unlabeled
    polylines, while piece_instances.boundary_entity_ids holds the assembly
    (sometimes as ``line_XXXX__seg_01``). Bind parent line ids too.

    When multiple pieces share a polyline (common for L/R sleeves), the highest
    score wins here — prefer ``_annotated_entities`` piece-first expansion for
    complete previews.
    """
    ranking: dict[str, tuple[int, str, str]] = {}
    for piece in ir.get("piece_instances") or []:
        piece_id = str(piece.get("piece_id") or "")
        role = str(piece.get("piece_role") or "unknown")
        if not piece_id or role in {"", "unknown", "none", "unlabeled"}:
            continue
        ids = list(piece.get("boundary_entity_ids") or []) + list(piece.get("internal_entity_ids") or [])
        score = len(ids)
        bbox = piece.get("bbox") or {}
        try:
            w = float(bbox.get("max_x", 0) or 0) - float(bbox.get("min_x", 0) or 0)
            h = float(bbox.get("max_y", 0) or 0) - float(bbox.get("min_y", 0) or 0)
            score += int(max(w, 0) * max(h, 0) / 1000.0)
        except (TypeError, ValueError):
            pass
        for raw_id in ids:
            eid = str(raw_id or "")
            if not eid:
                continue
            keys = {eid, eid.split("__seg_")[0]}
            for key in keys:
                prev = ranking.get(key)
                if not prev or score > prev[0]:
                    ranking[key] = (score, piece_id, role)
    return {key: (piece_id, role) for key, (_score, piece_id, role) in ranking.items()}


def _merged_segment_parent(by_id: dict[str, dict[str, Any]], parent: str) -> dict[str, Any] | None:
    """Rebuild a parent polyline when IR only stores ``line_XXXX__seg_*`` fragments."""
    keys = sorted(key for key in by_id if key.startswith(f"{parent}__seg_"))
    if not keys:
        return None
    points: list[list[float]] = []
    for key in keys:
        for point in _entity_points(by_id[key]):
            pt = [float(point[0]), float(point[1])]
            if not points or points[-1][0] != pt[0] or points[-1][1] != pt[1]:
                points.append(pt)
    if len(points) < 2:
        return None
    return {
        "entity_id": parent,
        "line_role": "unknown",
        "geometry": {"points": points},
        "source": {"synthetic": "merged_seg_parent", "parent": parent},
    }


def _annotated_entities(ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Stamp piece roles onto DXF-derived polylines.

    Piece-first: expand ``piece_instances`` onto parent polylines (merging
    ``__seg_*`` fragments when the parent is absent) so shared outlines stay
    complete on every claiming piece.
    """
    roles = role_map(ir)
    by_id = {
        str(raw.get("entity_id") or ""): raw
        for raw in (ir.get("atomic_entities") or [])
        if raw.get("entity_id")
    }
    out: list[dict[str, Any]] = []
    seen_piece_entity: set[tuple[str, str]] = set()
    claimed_ids: set[str] = set()

    for piece in ir.get("piece_instances") or []:
        piece_id = str(piece.get("piece_id") or "")
        role = str(piece.get("piece_role") or "unknown")
        if not piece_id or role in {"", "unknown", "none", "unlabeled"}:
            continue
        ids = list(piece.get("boundary_entity_ids") or []) + list(piece.get("internal_entity_ids") or [])
        parents: list[str] = []
        seen_parents: set[str] = set()
        for raw_id in ids:
            parent = str(raw_id or "").split("__seg_")[0]
            if not parent or parent in seen_parents:
                continue
            seen_parents.add(parent)
            parents.append(parent)
        for parent in parents:
            key = (piece_id, parent)
            if key in seen_piece_entity:
                continue
            raw = by_id.get(parent) or _merged_segment_parent(by_id, parent)
            if not raw:
                continue
            seen_piece_entity.add(key)
            claimed_ids.add(parent)
            claimed_ids.update(key for key in by_id if key.startswith(f"{parent}__seg_"))
            entity = deepcopy(raw)
            entity["piece_id"] = piece_id
            entity["_piece_role"] = role
            entity["_source_case"] = ir.get("case_id")
            out.append(entity)

    assembly = _piece_assembly_map(ir)
    for raw in ir.get("atomic_entities") or []:
        eid = str(raw.get("entity_id") or "")
        if eid in claimed_ids or eid.split("__seg_")[0] in claimed_ids:
            continue
        piece_id = raw.get("piece_id")
        inferred_role = roles.get(str(piece_id or ""), "") if piece_id else ""
        if eid in assembly:
            piece_id, inferred_role = assembly[eid]
        if not piece_id or inferred_role in {"", "unknown", "none", "unlabeled"}:
            continue
        entity = deepcopy(raw)
        entity["piece_id"] = piece_id
        entity["_piece_role"] = inferred_role
        entity["_source_case"] = ir.get("case_id")
        out.append(entity)
    return out


def remix_readiness(ir: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return whether a source can safely host live component replacement.

    Catalog hits this for every IR — avoid ``_annotated_entities`` deepcopy of
    full fused DXF baselines (10k+ polylines). Roles come from piece_instances
    plus a single pass over atomic_entities metadata.
    """
    cached = ir.get("_remix_readiness_cache")
    if isinstance(cached, tuple) and len(cached) == 2:
        return cached  # type: ignore[return-value]

    role_by_piece = role_map(ir)
    roles = {str(role) for role in role_by_piece.values() if role and role not in {"unknown", "", "none", "unlabeled"}}
    for piece in ir.get("piece_instances") or []:
        role = str(piece.get("piece_role") or "")
        if role and role not in {"unknown", "", "none", "unlabeled"}:
            roles.add(role)

    has_body_neckline = False
    has_any_neckline = False
    has_collar_line = False
    for entity in ir.get("atomic_entities") or []:
        piece_id = str(entity.get("piece_id") or "")
        piece_role = role_by_piece.get(piece_id, "") or str(entity.get("piece_role") or "")
        if piece_role and piece_role not in {"unknown", "", "none", "unlabeled"}:
            roles.add(piece_role)
        elif not piece_id:
            # Only infer when unlabeled; skip bulk unknown lines.
            inferred = _infer_piece_role_from_line_role(str(entity.get("line_role") or "unknown"))
            if inferred not in {"unknown", "", "none", "unlabeled"}:
                roles.add(inferred)
        line_role = str(entity.get("line_role") or "")
        if "neckline" in line_role:
            has_any_neckline = True
            if piece_role in FRONT_ROLES | BACK_ROLES:
                has_body_neckline = True
        if "collar" in line_role:
            has_collar_line = True

    family = normalize_family((ir.get("design_semantics") or {}).get("category"))
    reasons: list[str] = []
    if not roles & FRONT_ROLES:
        reasons.append("missing_front_body")
    if not roles & BACK_ROLES:
        reasons.append("missing_back_body")
    if not roles & PURE_SLEEVE_ROLES:
        # Shirt hosts may be cuff-integrated / sleeveless; still usable for collar/body swaps.
        # T-shirt flutter/sleeveless/batwing/raglan may be body-integrated: no independent sleeve.
        sleeve_slug = _label_slug(ir, "sleeve")
        if family != "shirt" and sleeve_slug not in {"flutter", "sleeveless", "batwing", "raglan"}:
            reasons.append("missing_sleeve")
    # Prefer neckline on body pieces. Shirts often park neckline on unlabeled
    # pieces or only have collar / collar_attach geometry — still a valid host.
    has_collar_host = bool(roles & COLLAR_ROLES) or has_collar_line
    shirt_body_host = (
        family == "shirt"
        and bool(roles & FRONT_ROLES)
        and bool(roles & BACK_ROLES)
        and (bool(roles & PURE_SLEEVE_ROLES) or has_collar_host)
    )
    if not (has_body_neckline or has_any_neckline or (family == "shirt" and has_collar_host) or shirt_body_host):
        reasons.append("missing_host_neckline")
    result = (not reasons, reasons)
    ir["_remix_readiness_cache"] = result
    return result


def _entities_for_roles(ir: dict[str, Any], roles: set[str]) -> list[dict[str, Any]]:
    entities = piece_entities(ir, roles)
    role_by_piece = role_map(ir)
    for entity in entities:
        entity["_piece_role"] = role_by_piece.get(entity.get("piece_id") or "", "unknown")
        entity["_source_case"] = ir.get("case_id")
    return entities


def _replace_roles(
    entities: list[dict[str, Any]], roles: set[str], replacement: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [entity for entity in entities if entity.get("_piece_role") not in roles] + replacement


def _map_points(entity: dict[str, Any], fn) -> dict[str, Any]:
    item = deepcopy(entity)
    geometry = dict(item.get("geometry") or {})
    geometry["points"] = [fn(float(x), float(y)) for x, y in geometry.get("points") or []]
    item["geometry"] = geometry
    return item


def _piecewise_shape(entities: list[dict[str, Any]], slug: str) -> list[dict[str, Any]]:
    bounds = bounds_of_entities(entities)
    if not bounds:
        return entities
    min_x, min_y, max_x, max_y = bounds
    cx = (min_x + max_x) / 2
    cy = (min_y + max_y) / 2
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)

    def deform(x: float, y: float) -> list[float]:
        nx = (x - cx) / width
        ny = (y - cy) / height
        factor = 1.0
        if slug == "fitted-x":
            factor = 1.0 - 0.12 * max(0.0, 1.0 - abs(ny) * 3.0)
        elif slug == "a-line":
            factor = 1.0 + 0.16 * max(0.0, ny + 0.5)
        elif slug == "side-waist-pleats":
            factor = 1.0 + 0.08 * max(0.0, 1.0 - abs(ny) * 4.0)
        elif slug == "waist-gathers":
            factor = 1.0 + 0.14 * max(0.0, 1.0 - abs(ny) * 5.0)
        elif slug == "wrap-v":
            return [cx + (x - cx) * 1.05 + ny * width * 0.08, y]
        elif slug == "shoulder-pleats":
            factor = 1.0 + 0.10 * max(0.0, -ny * 2.0)
        return [cx + (x - cx) * factor, y]

    return [_map_points(entity, deform) for entity in entities]


def _modify_component(entities: list[dict[str, Any]], group: str, slug: str, donor_ir: dict[str, Any]) -> list[dict[str, Any]]:
    if not entities:
        return entities
    if group == "collar" or group == "neckline":
        scale = {
            "bow-tie": (1.75, 0.78),
            "pointed": (1.0, 1.18),
            "peter-pan": (1.10, 0.72),
            "casual-wide-lapel": (1.22, 1.10),
            "open-v-pointed": (1.05, 1.25),
            "v-neck": (1.03, 1.10),
            "high-mock": (0.98, 1.35),
            "cowl": (1.35, 1.18),
            "scrunch": (1.25, 1.40),
            "boat": (1.30, 0.62),
            "asymmetric": (1.12, 1.0),
        }.get(slug, (1.0, 1.0))
        return scale_piece_group(entities, sx=scale[0], sy=scale[1])
    if group == "sleeve":
        length_scale, width_scale = {
            "puff": (1.02, 1.24),
            "bell": (1.08, 1.20),
            "flutter": (0.55, 1.30),
            "batwing": (1.08, 1.28),
            "raglan": (1.05, 1.10),
        }.get(slug, (1.0, 1.0))
        return scale_sleeve_anisotropic(
            entities, length_scale=length_scale, width_scale=width_scale, ir=donor_ir
        )
    if group == "cuff":
        sx, sy = {"ruffled": (1.25, 1.18), "gathered": (1.18, 1.0)}.get(slug, (1.0, 1.0))
        return scale_piece_group(entities, sx=sx, sy=sy)
    if group == "placket":
        sx, sy = {
            "half": (1.0, 0.38),
            "concealed": (1.35, 1.0),
            "ruffled": (1.25, 1.0),
            "diagonal": (1.0, 0.82),
        }.get(slug, (1.0, 1.0))
        modified = scale_piece_group(entities, sx=sx, sy=sy)
        if slug == "diagonal":
            bounds = bounds_of_entities(modified)
            if bounds:
                height = max(bounds[3] - bounds[1], 1.0)
                modified = [
                    _map_points(entity, lambda x, y: [x + (y - bounds[1]) / height * 90.0, y])
                    for entity in modified
                ]
        return modified
    return entities


def add_cuff_style_guides(entities: list[dict[str, Any]], slug: str) -> list[dict[str, Any]]:
    if slug not in {"ruffled", "gathered"} or not entities:
        return entities
    bounds = bounds_of_entities(entities)
    if not bounds:
        return entities
    min_x, min_y, max_x, max_y = bounds
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    template = entities[0]
    guides: list[list[list[float]]] = []
    if slug == "ruffled":
        center_y = min_y + height * 0.34
        guides.append([
            [min_x + width * index / 20.0, center_y + math.sin(index * math.pi / 2.0) * height * 0.09]
            for index in range(21)
        ])
    else:
        for index in range(1, 8):
            x = min_x + width * index / 8.0
            guides.append([[x, min_y + height * 0.14], [x, max_y - height * 0.14]])
    annotations = []
    for index, points in enumerate(guides, 1):
        annotations.append({
            "entity_id": f"{template.get('entity_id', 'cuff')}__{slug}_guide_{index:02d}",
            "source": {"layer": "AI4M_CONSTRUCTION", "entity_type": "POLYLINE"},
            "geometry": {"points": points},
            "line_role": "pleat_line",
            "piece_id": template.get("piece_id"),
            "suggestion": None,
            "review": "approved",
            "_piece_role": template.get("_piece_role", "cuff"),
            "_source_case": template.get("_source_case"),
        })
    return entities + annotations


def _piece_area(rows: list[dict[str, Any]]) -> float:
    bounds = bounds_of_entities(rows)
    if not bounds:
        return 0.0
    return max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1])


def _primary_body_piece_keys(entities: list[dict[str, Any]]) -> set[tuple[str, str]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entity in entities:
        role = str(entity.get("_piece_role") or "")
        if role not in FRONT_ROLES | BACK_ROLES:
            continue
        grouped.setdefault((str(entity.get("piece_id") or ""), role), []).append(entity)
    best: dict[str, tuple[float, tuple[str, str]]] = {}
    for key, rows in grouped.items():
        family = "front" if key[1] in FRONT_ROLES else "back"
        area = _piece_area(rows)
        if family not in best or area > best[family][0]:
            best[family] = (area, key)
    return {row[1] for row in best.values()}


def _ring_path(count: int, start: int, end: int, reverse: bool) -> list[int]:
    if count <= 0:
        return []
    if not reverse:
        if start <= end:
            return list(range(start, end + 1))
        return list(range(start, count)) + list(range(0, end + 1))
    if start >= end:
        return list(range(start, end - 1, -1))
    return list(range(start, -1, -1)) + list(range(count - 1, end - 1, -1))


def _splice_neck_span(
    points: list[list[float]],
    start: list[float],
    end: list[float],
    redraw,
    max_dist: float,
) -> list[list[float]] | None:
    if len(points) < 16:
        return None
    closed = math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) <= 1.0
    ring = points[:-1] if closed and len(points) > 1 else points

    def nearest(target: list[float]) -> tuple[int, float]:
        best_i, best_d = 0, float("inf")
        for index, point in enumerate(ring):
            dist = math.hypot(point[0] - target[0], point[1] - target[1])
            if dist < best_d:
                best_i, best_d = index, dist
        return best_i, best_d

    i, dist_i = nearest(start)
    j, dist_j = nearest(end)
    if dist_i > max_dist or dist_j > max_dist or i == j:
        return None
    count = len(ring)
    forward = _ring_path(count, i, j, False)
    reverse = _ring_path(count, i, j, True)
    def mean_y(indexes: list[int]) -> float:
        return sum(ring[k][1] for k in indexes) / max(len(indexes), 1)
    span = forward if mean_y(forward) >= mean_y(reverse) else reverse
    if len(span) < 3:
        return None
    chosen = set(span)
    out = [redraw(p[0], p[1]) if index in chosen else p[:] for index, p in enumerate(ring)]
    if closed:
        out.append(out[0][:])
    return out


def _neckline_depth(slug: str, piece_role: str, chord_length: float) -> float:
    front_depth = {
        "crew": 0.18, "v-neck": 0.72, "polo": 0.17, "high-mock": 0.07,
        "cowl": 0.25, "scrunch": 0.13, "boat": 0.045, "asymmetric": 0.28,
        "bow-tie": 0.14, "pointed": 0.16, "peter-pan": 0.13,
        "casual-wide-lapel": 0.22, "open-v-pointed": 0.42,
    }.get(slug, 0.16)
    back_depth = {
        "crew": .055, "v-neck": .10, "polo": .06, "high-mock": .035,
        "boat": .035, "open-v-pointed": .095,
    }.get(slug, .065)
    ratio = front_depth if piece_role in FRONT_ROLES else back_depth
    return chord_length * ratio


def _neckline_redraw(slug: str, piece_role: str, start: list[float], end: list[float], nx: float, ny: float, depth: float):
    chord_x, chord_y = end[0] - start[0], end[1] - start[1]
    chord_length = math.hypot(chord_x, chord_y) or 1.0
    ux, uy = chord_x / chord_length, chord_y / chord_length

    def redraw(x: float, y: float) -> list[float]:
        projection = ((x - start[0]) * ux + (y - start[1]) * uy) / chord_length
        t = max(0.0, min(1.0, projection))
        if slug in {"v-neck", "open-v-pointed"} and piece_role in FRONT_ROLES:
            inward = depth * (1.0 - abs(2.0 * t - 1.0))
        elif slug == "asymmetric" and piece_role in FRONT_ROLES:
            peak = 0.68
            inward = depth * (t / peak if t <= peak else (1.0 - t) / (1.0 - peak))
        else:
            inward = depth * math.sin(math.pi * t)
        return [start[0] + chord_x * t + nx * inward, start[1] + chord_y * t + ny * inward]

    return redraw


def _higher_span(ring: list[list[float]], i: int, j: int) -> list[int]:
    count = len(ring)
    forward = _ring_path(count, i, j, False)
    reverse = _ring_path(count, i, j, True)

    def mean_y(indexes: list[int]) -> float:
        return sum(ring[k][1] for k in indexes) / max(len(indexes), 1)

    return forward if mean_y(forward) >= mean_y(reverse) else reverse


def _replace_ring_span(
    points: list[list[float]], span: list[int], new_span: list[list[float]]
) -> list[list[float]] | None:
    if len(points) < 8 or len(span) < 2 or len(new_span) < 2:
        return None
    closed = math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) <= 1.0
    ring = points[:-1] if closed and len(points) > 1 else points
    count = len(ring)
    out: list[list[float]] = []
    index = (span[-1] + 1) % count
    while index != span[0]:
        out.append(ring[index][:])
        index = (index + 1) % count
    out.extend(point[:] for point in new_span)
    if len(out) < 8:
        return None
    if out[0] != out[-1]:
        out.append(out[0][:])
    return out


def _split_front_cuts(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fronts = [
        entity
        for entity in entities
        if (entity.get("source") or {}).get("origin") == "compose_ir"
        and str(entity.get("line_role") or "") == "cut"
        and str(entity.get("_piece_role") or "") in FRONT_ROLES
    ]
    if len(fronts) < 2:
        return []
    areas = [_piece_area([entity]) for entity in fronts]
    largest = max(areas)
    return [entity for entity, area in zip(fronts, areas) if area >= largest * 0.55]


def _reshape_compose_neckline(
    entities: list[dict[str, Any]], slug: str
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Only rewrite front/back compose cuts on their neckline span. Never touch sleeves."""
    if not any((entity.get("source") or {}).get("origin") == "compose_ir" for entity in entities):
        return None
    split_fronts = _split_front_cuts(entities)
    if split_fronts and slug not in {"v-neck", "boat"}:
        return entities, {"applied": False, "reason": "split_front_keep_original", "slug": slug}
    primary = _primary_body_piece_keys(entities)
    target_ids = {str(entity.get("piece_id") or "") for entity in split_fronts} if split_fronts else None
    piece_entities_current: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        piece_entities_current.setdefault(str(entity.get("piece_id") or ""), []).append(entity)
    changed: dict[tuple[str, str], dict[str, Any]] = {}
    reports = []
    for entity in entities:
        if (entity.get("source") or {}).get("origin") != "compose_ir":
            continue
        if str(entity.get("line_role") or "") != "cut":
            continue
        piece_role = str(entity.get("_piece_role") or "")
        piece_id = str(entity.get("piece_id") or "")
        if target_ids is not None:
            if piece_id not in target_ids:
                continue
        elif (piece_id, piece_role) not in primary:
            continue
        neck = next(
            (edge for edge in (entity.get("_compose_edges") or []) if "neckline" in str(edge.get("role") or "")),
            None,
        )
        points = entity_points(entity)
        if not neck or len(points) < 8:
            continue
        closed = math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) <= 1.0
        ring = points[:-1] if closed and len(points) > 1 else points
        i, j = int(neck["start_i"]), int(neck["end_i"])
        if i < 0 or j < 0 or i >= len(ring) or j >= len(ring) or i == j:
            continue
        span = _higher_span(ring, i, j)
        if len(span) < 3:
            continue
        start, end = ring[span[0]], ring[span[-1]]
        if split_fronts and slug == "v-neck":
            spliced = _replace_ring_span(points, span, [start, end])
            mode = "split_front_v_line"
        elif split_fronts and slug == "boat":
            line_y = max(start[1], end[1])
            spliced = _replace_ring_span(points, span, [[start[0], line_y], [end[0], line_y]])
            mode = "split_front_boat_line"
        else:
            chord_x, chord_y = end[0] - start[0], end[1] - start[1]
            chord_length = math.hypot(chord_x, chord_y)
            if chord_length <= 1e-6:
                continue
            nx, ny = -chord_y / chord_length, chord_x / chord_length
            mid_x, mid_y = (start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0
            body_bounds = bounds_of_entities(piece_entities_current.get(piece_id, []))
            if body_bounds:
                body_center = ((body_bounds[0] + body_bounds[2]) / 2.0, (body_bounds[1] + body_bounds[3]) / 2.0)
                if (body_center[0] - mid_x) * nx + (body_center[1] - mid_y) * ny < 0:
                    nx, ny = -nx, -ny
            depth = _neckline_depth(slug, piece_role, chord_length)
            redraw = _neckline_redraw(slug, piece_role, start, end, nx, ny, depth)
            spliced = _splice_neck_span(points, start, end, redraw, max(12.0, chord_length * 0.12))
            mode = "compose_cut_span"
        if not spliced:
            continue
        item = deepcopy(entity)
        geometry = dict(item.get("geometry") or {})
        geometry["points"] = spliced
        item["geometry"] = geometry
        eid = str(entity.get("entity_id") or "")
        changed[(piece_id, eid)] = item
        if split_fronts:
            line_y = max(start[1], end[1])
            new_neck = [start[:], end[:]] if slug == "v-neck" else [[start[0], line_y], [end[0], line_y]]
        for child in piece_entities_current.get(piece_id, []):
            if str(child.get("line_role") or "") != str(neck.get("role") or ""):
                continue
            parent = (child.get("source") or {}).get("parent")
            if parent and parent != eid:
                continue
            if split_fronts:
                copied = deepcopy(child)
                copied["geometry"] = {**(copied.get("geometry") or {}), "points": [p[:] for p in new_neck]}
                changed[(piece_id, str(child.get("entity_id") or ""))] = copied
                continue
            child_pts = entity_points(child)
            child_spliced = _splice_neck_span(child_pts, start, end, redraw, max(12.0, chord_length * 0.12))
            if not child_spliced and len(child_pts) >= 2:
                child_spliced = [redraw(p[0], p[1]) for p in child_pts]
            if child_spliced:
                copied = deepcopy(child)
                copied["geometry"] = {**(copied.get("geometry") or {}), "points": child_spliced}
                changed[(piece_id, str(child.get("entity_id") or ""))] = copied
        reports.append({
            "piece_id": piece_id,
            "piece_role": piece_role,
            "entity_count": 1,
            "mode": mode,
        })
    if not changed:
        return None
    return [
        changed.get((str(entity.get("piece_id") or ""), str(entity.get("entity_id") or "")), entity)
        for entity in entities
    ], {
        "applied": True,
        "slug": slug,
        "chains": reports,
        "locked_shoulders": True,
        "modified_entity_ids": [eid for (_pid, eid) in changed],
        "mode": reports[0]["mode"] if reports else "compose_cut_span",
    }


def reshape_body_neckline(
    entities: list[dict[str, Any]], ir: dict[str, Any], slug: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Redraw the neckline cut edge on front/back body pieces.

    Replacing only a collar or binding is not a valid component remix: its host
    body edge must change as well.  This function keeps the two shoulder join
    points fixed and redraws only the annotated neckline chains toward the body
    centroid, so adjacent shoulder geometry stays locked.
    """
    composed = _reshape_compose_neckline(entities, slug)
    if composed:
        return composed
    entity_by_id = {entity.get("entity_id"): entity for entity in entities}
    entity_by_key = {
        (str(entity.get("piece_id") or ""), str(entity.get("entity_id") or "")): entity
        for entity in entities
    }
    piece_entities_current: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        piece_entities_current.setdefault(str(entity.get("piece_id") or ""), []).append(entity)
    primary = _primary_body_piece_keys(entities)

    # Some v1 edge-chain records point at a different connected component's
    # piece_id. The atomic entity carries the reliable physical piece role, so
    # build the host chains from the entities instead of trusting that pointer.
    chains_by_piece: dict[tuple[str, str], list[str]] = {}
    for entity in entities:
        piece_role = str(entity.get("_piece_role") or "")
        line_role = str(entity.get("line_role") or "").lower()
        key = (str(entity.get("piece_id") or ""), piece_role)
        if key not in primary or "neckline" not in line_role:
            continue
        chains_by_piece.setdefault(key, []).append(str(entity.get("entity_id")))

    # Newer annotations keep the role on the atomic entity, while some source
    # files only declare it on edge_chains. Merge both representations before
    # deciding that a front or back neckline is absent.
    for chain in ir.get("edge_chains") or []:
        if "neckline" not in str(chain.get("edge_role") or ""):
            continue
        for entity_id in chain.get("ordered_entity_ids") or []:
            chain_piece = str(chain.get("piece_id") or "")
            entity = entity_by_key.get((chain_piece, str(entity_id))) or entity_by_id.get(entity_id)
            if not entity:
                continue
            piece_role = str(entity.get("_piece_role") or "")
            key = (str(entity.get("piece_id") or ""), piece_role)
            if key not in primary:
                continue
            ids = chains_by_piece.setdefault(key, [])
            if str(entity_id) not in ids:
                ids.append(str(entity_id))

    inferred_ids: set[str] = set()
    physical_body_pieces: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entity in entities:
        piece_role = str(entity.get("_piece_role") or "")
        if piece_role in FRONT_ROLES | BACK_ROLES:
            physical_body_pieces.setdefault((str(entity.get("piece_id") or ""), piece_role), []).append(entity)

    for key, piece_entities in physical_body_pieces.items():
        if key not in primary or key in chains_by_piece:
            continue
        bounds = bounds_of_entities(piece_entities)
        if not bounds:
            continue
        min_x, min_y, max_x, max_y = bounds
        width, height = max_x - min_x, max_y - min_y
        if width <= 1e-6 or height <= 1e-6:
            continue
        center_x = (min_x + max_x) / 2.0
        candidates: list[tuple[dict[str, Any], list[float], list[float]]] = []
        for entity in piece_entities:
            points = entity_points(entity)
            if len(points) < 2:
                continue
            start, end = points[0], points[-1]
            length = entity_length(entity)
            if not width * .05 <= length <= width * .65:
                continue
            if min(start[1], end[1]) < min_y + height * .64:
                continue
            candidates.append((entity, start, end))
        best: tuple[float, str, str] | None = None
        tolerance = max(4.0, width * .018)
        for left_index, (left_entity, left_start, left_end) in enumerate(candidates):
            for right_entity, right_start, right_end in candidates[left_index + 1:]:
                for left_common, left_outer in ((left_start, left_end), (left_end, left_start)):
                    for right_common, right_outer in ((right_start, right_end), (right_end, right_start)):
                        common_distance = math.hypot(left_common[0] - right_common[0], left_common[1] - right_common[1])
                        if common_distance > tolerance:
                            continue
                        common_x = (left_common[0] + right_common[0]) / 2.0
                        common_y = (left_common[1] + right_common[1]) / 2.0
                        if (left_outer[0] - common_x) * (right_outer[0] - common_x) >= 0:
                            continue
                        span = abs(right_outer[0] - left_outer[0])
                        if not width * .18 <= span <= width * .72:
                            continue
                        outer_y = (left_outer[1] + right_outer[1]) / 2.0
                        if outer_y + tolerance < common_y:
                            continue
                        role_bonus = sum(
                            24.0 if str(row.get("line_role") or "") == "pattern_boundary" else
                            18.0 if str(row.get("line_role") or "") == "cut_line" else 0.0
                            for row in (left_entity, right_entity)
                        )
                        symmetry_error = abs(abs(left_outer[0] - common_x) - abs(right_outer[0] - common_x)) / width
                        score = role_bonus + span / width * 40.0 + max(0.0, outer_y - common_y) / height * 25.0
                        score -= abs(common_x - center_x) / width * 80.0 + symmetry_error * 50.0 + common_distance
                        row = (score, str(left_entity.get("entity_id")), str(right_entity.get("entity_id")))
                        if best is None or row[0] > best[0]:
                            best = row
        if best:
            chains_by_piece[key] = [best[1], best[2]]
            inferred_ids.update((best[1], best[2]))
    chain_rows = [(piece_id, piece_role, ids) for (piece_id, piece_role), ids in chains_by_piece.items()]

    if not chain_rows:
        return entities, {"applied": False, "reason": "host_neckline_chain_missing", "slug": slug}

    changed: dict[tuple[str, str], dict[str, Any]] = {}
    reports = []
    for piece_id, piece_role, ids in chain_rows:
        chain_points = [
            point
            for entity_id in ids
            for point in entity_points(entity_by_key.get((piece_id, entity_id)) or entity_by_id[entity_id])
        ]
        if len(chain_points) < 2:
            continue
        # Neckline annotations are short curves; their farthest point pair is
        # the stable shoulder-to-shoulder chord.
        start, end = chain_points[0], chain_points[-1]
        max_distance = -1.0
        for left in chain_points:
            for right in chain_points:
                distance = (right[0] - left[0]) ** 2 + (right[1] - left[1]) ** 2
                if distance > max_distance:
                    max_distance = distance
                    start, end = left, right
        chord_x, chord_y = end[0] - start[0], end[1] - start[1]
        chord_length = math.hypot(chord_x, chord_y)
        if chord_length <= 1e-6:
            continue
        ux, uy = chord_x / chord_length, chord_y / chord_length
        nx, ny = -uy, ux
        mid_x, mid_y = (start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0
        body_bounds = bounds_of_entities(piece_entities_current.get(piece_id, []))
        if body_bounds:
            body_center = ((body_bounds[0] + body_bounds[2]) / 2.0, (body_bounds[1] + body_bounds[3]) / 2.0)
            if (body_center[0] - mid_x) * nx + (body_center[1] - mid_y) * ny < 0:
                nx, ny = -nx, -ny

        depth = _neckline_depth(slug, piece_role, chord_length)
        redraw = _neckline_redraw(slug, piece_role, start, end, nx, ny, depth)

        # The source DXF commonly contains a cut-line copy over the semantic
        # neckline. Redraw that copy too; otherwise the old round neckline is
        # still visible and would also survive into the production export.
        tolerance = max(18.0, chord_length * 0.14)
        duplicate_ids: list[str] = []
        for candidate in piece_entities_current.get(piece_id, []):
            if str(candidate.get("line_role") or "") not in {"cut_line", "pattern_boundary", "net_boundary"}:
                continue
            candidate_points = entity_points(candidate)
            if len(candidate_points) < 2:
                continue
            left, right = candidate_points[0], candidate_points[-1]
            direct = math.hypot(left[0] - start[0], left[1] - start[1]) + math.hypot(right[0] - end[0], right[1] - end[1])
            reverse = math.hypot(right[0] - start[0], right[1] - start[1]) + math.hypot(left[0] - end[0], left[1] - end[1])
            if min(direct, reverse) <= tolerance * 2.0:
                duplicate_ids.append(str(candidate.get("entity_id")))

        for entity_id in ids + duplicate_ids:
            source = entity_by_key.get((piece_id, entity_id)) or entity_by_id[entity_id]
            changed_entity = _map_points(source, redraw)
            if entity_id in inferred_ids:
                changed_entity["line_role"] = "neckline"
            changed[(piece_id, entity_id)] = changed_entity
        for candidate in piece_entities_current.get(piece_id, []):
            cid = str(candidate.get("entity_id") or "")
            if not cid or cid in ids or cid in duplicate_ids:
                continue
            spliced = _splice_neck_span(
                entity_points(candidate),
                start,
                end,
                redraw,
                max(12.0, chord_length * 0.12),
            )
            if spliced:
                item = deepcopy(candidate)
                geometry = dict(item.get("geometry") or {})
                geometry["points"] = spliced
                item["geometry"] = geometry
                changed[(piece_id, cid)] = item
        reports.append(
            {
                "piece_id": piece_id,
                "piece_role": piece_role,
                "entity_count": len(ids) + len(duplicate_ids),
                "duplicate_cut_edges": len(duplicate_ids),
                "inferred_from_dxf_boundary": any(entity_id in inferred_ids for entity_id in ids),
                "chord_length": round(chord_length, 3),
                "depth": round(depth, 3),
            }
        )

    return [
        changed.get((str(entity.get("piece_id") or ""), str(entity.get("entity_id") or "")), entity)
        for entity in entities
    ], {
        "applied": bool(changed),
        "slug": slug,
        "chains": reports,
        "locked_shoulders": True,
    }


def _group_compatible(roles: set[str], group: str) -> bool:
    requirements = REQUIRED_ROLES_BY_GROUP.get(group)
    if not requirements:
        return True
    return all(bool(roles & alternatives) for alternatives in requirements)


def _edge_score(ir: dict[str, Any], group: str) -> int:
    edge_roles = {edge.get("edge_role") for edge in ir.get("edge_chains") or []}
    if group == "sleeve":
        return 10 if edge_roles & {"sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "sleeve_head"} else 0
    if group in {"collar", "neckline"}:
        return 10 if edge_roles & NECKLINE_ROLES else 0
    if group == "cuff":
        return 8 if edge_roles & {"cuff_edge", "cuff_attach_line", "sleeve_hem"} else 0
    if group == "placket":
        return 8 if edge_roles & {"placket_line", "center_front"} else 0
    return 0


EXPECTED_LINE_ROLES_BY_GROUP = {
    "neckline": {"neckline", "neck_binding_line", "pattern_boundary", "net_boundary", "cut_line"},
    "collar": {"neckline", "collar_attach_line", "collar_roll_line", "pattern_boundary", "net_boundary", "cut_line"},
    "sleeve": {"armhole_front", "armhole_back", "sleeve_cap_front", "sleeve_cap_back", "sleeve_hem", "sleeve_underarm_seam"},
    "placket": {"placket_line", "center_front", "pattern_boundary", "net_boundary", "cut_line"},
    "cuff": {"cuff_attach_line", "sleeve_placket_line", "pattern_boundary", "net_boundary", "cut_line"},
}


def _component_score(group: list[dict[str, Any]], selection_group: str) -> float:
    expected = EXPECTED_LINE_ROLES_BY_GROUP.get(selection_group, set())
    line_roles = {str(entity.get("line_role") or "") for entity in group}
    recognized = len(line_roles & expected)
    bounds = bounds_of_entities(group)
    area = 0.0 if not bounds else max(bounds[2] - bounds[0], 0.0) * max(bounds[3] - bounds[1], 0.0)
    return recognized * 1000.0 + len(group) * 3.0 + math.log1p(area)


def _geometry_quality(ir: dict[str, Any], group: str) -> float:
    entities = _entities_for_roles(ir, REPLACE_ROLES_BY_GROUP[group])
    by_component: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_component.setdefault(str(entity.get("piece_id") or "unknown"), []).append(entity)
    return max((_component_score(component, group) for component in by_component.values()), default=0.0)


def _select_replacement_components(entities: list[dict[str, Any]], group: str, slug: str) -> list[dict[str, Any]]:
    by_role: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for entity in entities:
        role = str(entity.get("_piece_role") or "unknown")
        by_role.setdefault(role, {}).setdefault(str(entity.get("piece_id") or "unknown"), []).append(entity)

    best_by_role: dict[str, list[dict[str, Any]]] = {}
    for role, components in by_role.items():
        best_by_role[role] = max(components.values(), key=lambda component: _component_score(component, group))

    if group == "neckline" and slug != "polo":
        candidates = [component for role, component in best_by_role.items() if role in COLLAR_ROLES]
        return max(candidates, key=lambda component: _component_score(component, group), default=[])
    if group == "sleeve" and "sleeve" in best_by_role:
        return best_by_role["sleeve"]
    selected_roles = {
        "neckline": COLLAR_ROLES,
        "collar": COLLAR_ROLES,
        "sleeve": PURE_SLEEVE_ROLES,
        "placket": PLACKET_ROLES,
        "cuff": CUFF_ROLES,
    }[group]
    return [entity for role in selected_roles for entity in best_by_role.get(role, [])]


def build_index(ir_root: Path, tshirt_root: Path | None = None, shirt_root: Path | None = None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(ir_root.glob("*.rule-ready.json")):
        ir = json.loads(path.read_text(encoding="utf-8"))
        family = normalize_family((ir.get("design_semantics") or {}).get("category"))
        if tshirt_root and tshirt_root.exists() and family == "tshirt":
            continue
        if shirt_root and shirt_root.exists() and family == "shirt":
            # The v2 shirt records remain the authoritative recommendation
            # library. Keep non-overlapping legacy shirt patterns only as a
            # geometry donor pool: several contain valid cuff/placket pieces
            # that are not yet named in the 31 semantic records.
            ir["_source_format"] = "legacy_shirt_component_donor"
            ir["_donor_only"] = True
        else:
            ir["_source_format"] = "rule_ready_v1"
        result[str(ir.get("case_id"))] = ir
    if tshirt_root and tshirt_root.exists():
        for path in sorted(tshirt_root.glob("*.pattern-ir.json")):
            ir = json.loads(path.read_text(encoding="utf-8"))
            ir["_source_format"] = "tshirt_pattern_ir_v2"
            result[str(ir.get("case_id"))] = ir
    if shirt_root and shirt_root.exists():
        for path in sorted(shirt_root.glob("*.pattern-ir.json")):
            ir = json.loads(path.read_text(encoding="utf-8"))
            ir["_source_format"] = "shirt_pattern_ir_v2"
            result[str(ir.get("case_id"))] = ir
    return result


def choose_donor(
    option: dict[str, Any], index: dict[str, dict[str, Any]], base_case_id: str | None = None
) -> tuple[str | None, float, list[str]]:
    group = option["group"]
    if group in {"silhouette", "special", "garment_length"}:
        return base_case_id, 1.0, []
    family = option["family"]
    slug = option["slug"]
    candidates: list[tuple[float, str, int, bool]] = []
    for case_id, ir in index.items():
        if case_id == "C2690430" or ir.get("_dxf_available") is False:
            continue
        semantics = ir.get("design_semantics") or {}
        candidate_family = normalize_family(semantics.get("category"))
        roles = _role_set(ir)
        if not _group_compatible(roles, group):
            continue
        label_slug = _label_slug(ir, group)
        if label_slug in {"unknown", "non_composable"}:
            continue
        exact_label = label_slug == slug
        score = 55.0 if candidate_family == family else 15.0
        if exact_label:
            score += 120.0
        if slug == "polo" and semantics.get("category") == "polo":
            score += 35.0
        if FIT_BY_SLUG.get(slug) == semantics.get("fit"):
            score += 18.0
        edge_score = _edge_score(ir, group)
        score += edge_score
        score += min(_geometry_quality(ir, group), 4000.0) / 40.0
        score += min(len(ir.get("piece_instances") or []), 20) * 0.2
        candidates.append((score, case_id, edge_score, exact_label))
    if any(exact for _, _, _, exact in candidates):
        candidates = [row for row in candidates if row[3]]
    if any(edge_score > 0 for _, _, edge_score, _ in candidates):
        candidates = [row for row in candidates if row[2] > 0]
    candidates.sort(key=lambda row: (-row[0], row[1]))
    if not candidates:
        base_ir = index.get(base_case_id or "")
        if base_ir and _group_compatible(_role_set(base_ir), group):
            return base_case_id, 0.35, []
        return None, 0.0, ["原型纸样与可用供体均缺少该部件所需的基础几何"]
    # Distribute shape variants across equally capable sources while keeping selection deterministic.
    best_score = candidates[0][0]
    top = [candidate for candidate in candidates if candidate[0] >= best_score - 3.0][:3]
    offset = sum(ord(char) for char in slug) % len(top)
    score, case_id, _, _ = top[offset]
    return case_id, round(min(score / 100.0, 0.99), 3), []


def pattern_catalog(catalog_path: Path, index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    options = []
    for raw in catalog["options"]:
        item = dict(raw)
        item["thumbnail"] = item["thumbnail"].rsplit(".", 1)[0] + ".png"
        donor, confidence, reasons = choose_donor(item, index)
        item.update(
            {
                "donor_case_id": donor,
                "mapping_confidence": confidence,
                "mapping_status": "auto_validated" if donor or item["group"] in {"silhouette", "special", "garment_length"} else "unavailable",
                "disabled_reason": reasons[0] if reasons else None,
                "geometry_rule": f"{item['group']}.{item['slug']}.v1",
            }
        )
        options.append(item)
    return {"version": "v2-auto", "asset_extension": "png", "options": options}


def _measurement_number(measurements: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(measurements.get(key, default))
        return value if math.isfinite(value) and value > 0 else default
    except (TypeError, ValueError):
        return default


_FIT_EASE_CM = {
    "tight": 2.0,
    "fitted": 4.0,
    "regular": 8.0,
    "relaxed": 12.0,
    "oversized": 16.0,
}

_SIZE_BASES = {
    "female": {
        "height": 160.0, "chest": 84.0, "shoulder": 38.88, "neck": 34.0,
        "sleeve": 58.0, "upper_arm": 28.0, "waist": 68.0, "mode": "gbt_1335_2_2008_female",
    },
    "male_general": {
        "height": 175.0, "chest": 92.0, "shoulder": 44.0, "neck": 39.0,
        "sleeve": 60.0, "upper_arm": 32.0, "waist": 78.0, "mode": "male_general_trial",
    },
}


def _sex_prototype_scales(sex: str) -> dict[str, Any]:
    """Map female/unisex source blocks onto the male prototype before grading."""
    identity = {
        "applied": False,
        "mode": "source",
        "width": 1.0,
        "length": 1.0,
        "sleeve_length": 1.0,
        "sleeve_width": 1.0,
        "neck": 1.0,
        "shoulder": 1.0,
        "armhole": 1.0,
        "cuff": 1.0,
    }
    if sex != "male_general":
        return identity
    src = _SIZE_BASES["female"]
    dst = _SIZE_BASES["male_general"]
    chest = (dst["chest"] + 8.0) / (src["chest"] + 8.0)
    waist = (dst["waist"] + 4.0) / (src["waist"] + 4.0)
    width = chest * 0.78 + waist * 0.22
    shoulder = dst["shoulder"] / src["shoulder"]
    return {
        "applied": True,
        "mode": "female_to_male_prototype",
        "width": width * 0.72 + shoulder * 0.28,
        "length": dst["height"] / src["height"],
        "sleeve_length": dst["sleeve"] / src["sleeve"],
        "sleeve_width": chest * 0.55 + (dst["upper_arm"] / src["upper_arm"]) * 0.45,
        "neck": dst["neck"] / src["neck"],
        "shoulder": shoulder,
        "armhole": chest * 0.55 + (dst["upper_arm"] / src["upper_arm"]) * 0.45,
        "cuff": dst["upper_arm"] / src["upper_arm"],
    }


def grading_profile(recipe: dict[str, Any]) -> dict[str, float | str]:
    if recipe.get("skip_grading"):
        return {
            "mode": "source",
            "width": 1.0,
            "length": 1.0,
            "sleeve_length": 1.0,
            "sleeve_width": 1.0,
            "neck": 1.0,
            "shoulder": 1.0,
            "armhole": 1.0,
            "cuff": 1.0,
            "ease_cm": 0.0,
            "fit": str(recipe.get("fit") or "regular"),
            "waist_scale": 1.0,
            "material_shrink_rate": 0.0,
            "prototype": _sex_prototype_scales(""),
        }
    measurements = recipe.get("measurements_cm") or {}
    sex = str(recipe.get("sex") or "female")
    base = _SIZE_BASES["male_general"] if sex == "male_general" else _SIZE_BASES["female"]
    base_height, base_chest, base_shoulder = base["height"], base["chest"], base["shoulder"]
    base_neck, base_sleeve, base_upper_arm, base_waist = base["neck"], base["sleeve"], base["upper_arm"], base["waist"]
    mode = str(base["mode"])
    prototype = _sex_prototype_scales(sex)
    height = _measurement_number(measurements, "height", base_height)
    chest = _measurement_number(measurements, "chest", base_chest)
    waist = _measurement_number(measurements, "waist", chest * 0.78)
    shoulder = _measurement_number(measurements, "shoulder", 0.32 * chest + 12.0)
    neck = _measurement_number(measurements, "neck", 0.25 * chest + 13.0)
    sleeve = _measurement_number(measurements, "sleeveLength", base_sleeve)
    upper_arm = _measurement_number(measurements, "upperArm", base_upper_arm)
    fit = str(recipe.get("fit") or "regular").lower()
    fit_ease = _FIT_EASE_CM.get(fit, 8.0)
    # Prefer explicit ease_cm when provided; otherwise derive from fit.
    if recipe.get("ease_cm") is None:
        ease = fit_ease
    else:
        ease = float(recipe.get("ease_cm") or fit_ease)
        # If caller left the default 8 while selecting a non-regular fit, honor fit.
        if abs(ease - 8.0) < 1e-9 and fit in _FIT_EASE_CM and fit != "regular":
            ease = fit_ease
    chest_scale = (chest + ease) / (base_chest + 8.0)
    waist_scale = (waist + ease * 0.5) / (base_waist + 4.0)
    width_scale = chest_scale * 0.78 + waist_scale * 0.22
    shoulder_scale = shoulder / base_shoulder
    material_id = str(recipe.get("material_id") or "")
    shrink_rate = 0.0
    if any(key in material_id for key in ("tencel-cotton", "stretch-jersey", "rib-knit", "cooling-fiber")):
        shrink_rate = 0.08
    elif any(key in material_id for key in ("cotton-jersey", "heavy-cotton", "terry-cloth", "slub-cotton")):
        shrink_rate = 0.04
    elif material_id.startswith("tshirt."):
        shrink_rate = 0.025
    shrink_correction = 1.0 / max(1.0 - shrink_rate, 0.8)
    grade_width = (width_scale * 0.72 + shoulder_scale * 0.28) * shrink_correction
    grade_length = height / base_height * shrink_correction
    grade_sleeve_length = sleeve / base_sleeve
    grade_sleeve_width = (width_scale * 0.55 + upper_arm / base_upper_arm * 0.45) * shrink_correction
    grade_neck = neck / base_neck
    grade_shoulder = shoulder_scale * shrink_correction
    grade_armhole = (width_scale * 0.55 + (upper_arm / base_upper_arm) * 0.45) * shrink_correction
    grade_cuff = (upper_arm / base_upper_arm) * 0.65 + grade_sleeve_width * 0.35
    profile = {
        "mode": mode,
        "width": max(0.75, min(1.55, grade_width * float(prototype["width"]))),
        "length": max(0.80, min(1.45, grade_length * float(prototype["length"]))),
        "sleeve_length": max(0.75, min(1.45, grade_sleeve_length * float(prototype["sleeve_length"]))),
        "sleeve_width": max(0.80, min(1.45, grade_sleeve_width * float(prototype["sleeve_width"]))),
        "neck": max(0.80, min(1.35, grade_neck * float(prototype["neck"]))),
        "shoulder": max(0.80, min(1.40, grade_shoulder * float(prototype.get("shoulder") or 1.0))),
        "armhole": max(0.80, min(1.40, grade_armhole * float(prototype.get("armhole") or 1.0))),
        "cuff": max(0.80, min(1.40, grade_cuff * float(prototype.get("cuff") or 1.0))),
        "ease_cm": ease,
        "fit": fit,
        "waist_scale": round(waist_scale, 4),
        "material_shrink_rate": shrink_rate,
        "prototype": {key: round(value, 5) if isinstance(value, float) else value for key, value in prototype.items()},
        "grade_width": round(grade_width, 5),
        "grade_length": round(grade_length, 5),
    }
    constraints = recipe.get("intent_constraints") or {}
    if constraints.get("sleeve") == "short":
        profile["sleeve_length"] = 0.46
    elif constraints.get("sleeve") == "long":
        profile["sleeve_length"] = max(float(profile["sleeve_length"]), 0.96)
    return profile


def source_measurements(ir: dict[str, Any]) -> dict[str, Any]:
    entities = _annotated_entities(ir)
    by_piece: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_piece.setdefault(entity.get("piece_id") or "unknown", []).append(entity)
    body_widths: list[float] = []
    body_lengths: list[float] = []
    cuff_widths: list[float] = []
    placket_lengths: list[float] = []
    for group in by_piece.values():
        bounds = bounds_of_entities(group)
        if not bounds:
            continue
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        role = group[0].get("_piece_role")
        if role in FRONT_ROLES | BACK_ROLES:
            body_widths.append(width)
            body_lengths.append(height)
        if role in CUFF_ROLES:
            cuff_widths.append(max(width, height))
        if role in PLACKET_ROLES:
            placket_lengths.append(max(width, height))
    return {
        "schema": "chi27.source-measurements.v1",
        "unit": "source_dxf_unit",
        "body_piece_width_sum": round(sum(body_widths), 3),
        "body_length_max": round(max(body_lengths, default=0.0), 3),
        "neckline_length": round(_interface_length(ir, entities, NECKLINE_ROLES, FRONT_ROLES | BACK_ROLES), 3),
        "armhole_length": round(_interface_length(ir, entities, ARMHOLE_ROLES, FRONT_ROLES | BACK_ROLES), 3),
        "sleeve_cap_length": round(_interface_length(ir, entities, SLEEVE_CAP_ROLES, PURE_SLEEVE_ROLES), 3),
        "cuff_width_max": round(max(cuff_widths, default=0.0), 3),
        "placket_length_max": round(max(placket_lengths, default=0.0), 3),
    }


def _scale_complete_base(ir: dict[str, Any], profile: dict[str, Any]) -> list[dict[str, Any]]:
    by_piece: dict[str, list[dict[str, Any]]] = {}
    for entity in _annotated_entities(ir):
        by_piece.setdefault(entity.get("piece_id") or "unknown", []).append(entity)
    out: list[dict[str, Any]] = []
    for entities in by_piece.values():
        role = entities[0].get("_piece_role") or "unknown"
        if role in PURE_SLEEVE_ROLES:
            scaled = scale_sleeve_anisotropic(
                entities,
                length_scale=float(profile["sleeve_length"]),
                width_scale=float(profile["sleeve_width"]),
                ir=ir,
            )
        elif role in FRONT_ROLES | BACK_ROLES | {"back_yoke", "front_placket", "side_panel"}:
            scaled = scale_piece_group(entities, sx=float(profile["width"]), sy=float(profile["length"]))
        elif role in COLLAR_ROLES:
            scaled = scale_piece_group(entities, sx=float(profile["neck"]), sy=float(profile["neck"]))
        else:
            average = (float(profile["width"]) + float(profile["length"])) / 2.0
            scaled = scale_piece_group(entities, sx=average, sy=average)
        for entity in scaled:
            entity["_piece_role"] = role
            entity["_source_case"] = ir.get("case_id")
        out.extend(scaled)
    return out


def _host_ir(base_ir: dict[str, Any], entities: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "atomic_entities": entities,
        "piece_instances": base_ir.get("piece_instances") or [],
        "edge_chains": base_ir.get("edge_chains") or [],
    }


def _normalize_physical_components(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one physical pattern per source/role and reattach detached annotations.

    v1 connected-component ids sometimes classify a grainline as another body
    piece. Moving those ids independently breaks the pattern preview and DXF.
    """
    grouped: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for entity in entities:
        key = (str(entity.get("_source_case") or "unknown"), str(entity.get("_piece_role") or "unknown"))
        grouped.setdefault(key, {}).setdefault(str(entity.get("piece_id") or "unknown"), []).append(entity)

    normalized: list[dict[str, Any]] = []
    annotation_roles = {"grainline", "notch", "construction", "pleat_line", "pocket_position_line"}
    for components in grouped.values():
        if len(components) == 1:
            normalized.extend(next(iter(components.values())))
            continue
        primary_id, primary = max(
            components.items(),
            key=lambda item: (
                sum(1 for entity in item[1] if str(entity.get("line_role") or "") not in annotation_roles) * 1000,
                (lambda bounds: 0.0 if not bounds else (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]))(bounds_of_entities(item[1])),
            ),
        )
        primary_bounds = bounds_of_entities(primary)
        normalized.extend(primary)
        if not primary_bounds:
            continue
        primary_center = ((primary_bounds[0] + primary_bounds[2]) / 2.0, (primary_bounds[1] + primary_bounds[3]) / 2.0)
        margin_x = max((primary_bounds[2] - primary_bounds[0]) * 0.12, 20.0)
        margin_y = max((primary_bounds[3] - primary_bounds[1]) * 0.12, 20.0)
        for piece_id, component in components.items():
            if piece_id == primary_id:
                continue
            component_bounds = bounds_of_entities(component)
            component_roles = {str(entity.get("line_role") or "") for entity in component}
            if not component_bounds or not component_roles <= annotation_roles:
                continue
            center = ((component_bounds[0] + component_bounds[2]) / 2.0, (component_bounds[1] + component_bounds[3]) / 2.0)
            inside = (
                primary_bounds[0] - margin_x <= center[0] <= primary_bounds[2] + margin_x
                and primary_bounds[1] - margin_y <= center[1] <= primary_bounds[3] + margin_y
            )
            dx, dy = (0.0, 0.0) if inside else (primary_center[0] - center[0], primary_center[1] - center[1])
            for entity in component:
                attached = transform_entity(entity, dx=dx, dy=dy)
                attached["piece_id"] = primary_id
                normalized.append(attached)
    return normalized


def _layout_complete(entities: list[dict[str, Any]], gap: float = 90.0) -> list[dict[str, Any]]:
    by_piece: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for entity in entities:
        key = (
            str(entity.get("_source_case") or "unknown"),
            str(entity.get("_piece_role") or "unknown"),
            str(entity.get("piece_id") or entity.get("entity_id") or "unknown"),
        )
        by_piece.setdefault(key, []).append(entity)
    pieces = []
    for piece_id, group in by_piece.items():
        bounds = bounds_of_entities(group)
        if bounds:
            pieces.append((piece_id, group, bounds, max(bounds[2] - bounds[0], 1), max(bounds[3] - bounds[1], 1)))
    if not pieces:
        return []
    pieces.sort(key=lambda row: (-row[3] * row[4], -row[4], row[0]))
    body_roles = FRONT_ROLES | BACK_ROLES | {"side_panel"}
    large = [row for row in pieces if str(row[1][0].get("_piece_role") or "") in body_roles]
    small = [row for row in pieces if str(row[1][0].get("_piece_role") or "") not in body_roles]
    if not large:
        large, small = pieces, []

    def pack(rows: list[tuple], y0: float, row_limit: float, biggest_on_top: bool) -> tuple[list[dict[str, Any]], float]:
        if not rows:
            return [], y0
        bands: list[list[tuple]] = []
        current: list[tuple] = []
        cursor_x = 0.0
        for row in rows:
            _key, group, bounds, width, height = row
            if current and cursor_x + width > row_limit:
                bands.append(current)
                current = []
                cursor_x = 0.0
            current.append(row)
            cursor_x += width + gap
        if current:
            bands.append(current)
        if biggest_on_top:
            bands.reverse()
        packed: list[dict[str, Any]] = []
        cursor_y = y0
        for band in bands:
            row_height = max(height for _key, _group, _bounds, _width, height in band)
            cursor_x = 0.0
            for _key, group, bounds, width, _height in band:
                packed.extend(transform_entity(entity, dx=cursor_x - bounds[0], dy=cursor_y - bounds[1]) for entity in group)
                cursor_x += width + gap
            cursor_y += row_height + gap
        return packed, cursor_y

    widths = sorted((width for _key, _group, _bounds, width, _height in pieces), reverse=True)
    n = min(4, len(widths))
    row_limit = max(widths[0], sum(widths[:n]) + gap * (n - 1))
    small_out, y1 = pack(small, 0.0, row_limit, False)
    large_out, _ = pack(large, y1, row_limit, True)
    return small_out + large_out


def _piece_summary(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entity in entities:
        source_case_id = str(entity.get("_source_case") or "unknown")
        role = str(entity.get("_piece_role") or "unknown")
        physical_piece_id = str(entity.get("piece_id") or entity.get("entity_id") or "unknown")
        piece_key = (source_case_id, role, physical_piece_id)
        row = grouped.setdefault(
            piece_key,
            {
                "piece_id": f"{source_case_id}:{role}:{physical_piece_id}",
                "role": role,
                "source_case_id": source_case_id,
                "entity_count": 0,
            },
        )
        row["entity_count"] += 1
    for (source_case_id, role, physical_piece_id), row in grouped.items():
        piece_bounds = bounds_of_entities([entity for entity in entities if str(entity.get("_source_case") or "unknown") == source_case_id and str(entity.get("_piece_role") or "unknown") == role and str(entity.get("piece_id") or entity.get("entity_id") or "unknown") == physical_piece_id])
        if piece_bounds:
            row["width_mm"] = round(piece_bounds[2] - piece_bounds[0], 1)
            row["height_mm"] = round(piece_bounds[3] - piece_bounds[1], 1)
    return sorted(grouped.values(), key=lambda row: (row["role"], row["piece_id"]))


def _paper_info(entities: list[dict[str, Any]]) -> dict[str, Any]:
    bounds = bounds_of_entities(entities)
    if not bounds:
        return {"unit": "mm", "width_mm": 0, "height_mm": 0, "recommended_sheet": "未计算"}
    width = round(bounds[2] - bounds[0], 1)
    height = round(bounds[3] - bounds[1], 1)
    long_edge, short_edge = max(width, height), min(width, height)
    standard = next(
        (name for name, sheet_width, sheet_height in (("A2", 420, 594), ("A1", 594, 841), ("A0", 841, 1189))
         if short_edge <= sheet_width and long_edge <= sheet_height),
        "卷筒纸/分片输出",
    )
    return {"unit": "mm", "width_mm": width, "height_mm": height, "recommended_sheet": standard}


def _interface_length(ir: dict[str, Any], entities: list[dict[str, Any]], edge_roles: set[str], piece_roles: set[str]) -> float:
    mini = _host_ir(ir, entities)
    return role_edge_length(mini, edge_roles, piece_roles)


def _neck_source_swapped(sources: dict[str, Any]) -> bool:
    src = sources.get("neckline") or sources.get("collar")
    return isinstance(src, dict) and str(src.get("mode") or "") in _PIECE_SWAP_MODES


def _validate(
    family: str,
    entities: list[dict[str, Any]],
    interface_meta: dict[str, Any],
    sources: dict[str, Any],
) -> dict[str, Any]:
    roles = {entity.get("_piece_role") for entity in entities}
    errors: list[str] = []
    warnings: list[str] = []
    if not roles & FRONT_ROLES:
        errors.append("缺少前片")
    if not roles & BACK_ROLES:
        errors.append("缺少后片")
    sleeveless = sources.get("intent_sleeve") == "sleeveless"
    integrated_sleeve = str(sources.get("sleeve_mode") or "") in {"body_integrated", "body_and_sleeve"}
    if not sleeveless and not integrated_sleeve and not roles & PURE_SLEEVE_ROLES:
        errors.append("缺少袖片")
    elif integrated_sleeve and not roles & PURE_SLEEVE_ROLES:
        warnings.append("插肩/蝙蝠/飞袖无独立袖片，袖与衣身一体")
    neck_swapped = _neck_source_swapped(sources)
    if family == "shirt":
        # 休闲翻领等领型切在左右前片上，语料经常没有独立领面/领座。
        if not roles & COLLAR_ROLES and not (neck_swapped and roles & FRONT_ROLES):
            errors.append("缺少衬衫领片")
        elif not roles & COLLAR_ROLES:
            warnings.append("翻领已随前后衣身换片，无独立领面/领座")
        if sources.get("intent_sleeve") not in {"short", "sleeveless"} and not roles & CUFF_ROLES:
            errors.append("缺少袖口版片")
        if not roles & PLACKET_ROLES:
            errors.append("缺少门襟来源")
    sleeve = interface_meta.get("sleeve") or {}
    if sleeveless:
        armhole = interface_meta.get("armhole_finish") or {}
        if float(armhole.get("length_mm") or 0.0) <= 0:
            errors.append("无袖组合缺少可识别的袖窿轮廓")
        else:
            warnings.append("无袖试样已移除袖片与袖口；袖窿按窄卷边/包边预留，生产前需纸样师确认松量与收口工艺")
    elif sleeve.get("applied"):
        error = abs(float(sleeve.get("length_error") or 0.0))
        if error > 2.0:
            errors.append(f"袖山/袖窿接口误差 {error / 10.0:.2f}cm，超过0.2cm")
    else:
        warnings.append("源IR缺少可识别的袖山接口，已按轮廓比例衔接")
    neck = interface_meta.get("neck") or {}
    body_neckline = interface_meta.get("body_neckline") or {}
    if (sources.get("neckline") or sources.get("collar")) and not body_neckline.get("applied") and not neck_swapped:
        errors.append("基础纸样缺少可重绘的前后片领圈，不能生成可信的领口组合")
    if neck.get("applied"):
        error = abs(float(neck.get("length_error") or 0.0))
        if error > 2.0:
            errors.append(f"领片/领圈接口误差 {error / 10.0:.2f}cm，超过0.2cm")
    elif neck:
        warnings.append("源IR缺少可识别的领圈接口，已按领围比例衔接")
    degenerate = sum(1 for entity in entities if len(entity_points(entity)) < 2)
    if degenerate:
        errors.append(f"存在 {degenerate} 条退化几何")
    return {
        "valid": not errors,
        "trial_ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "metrics": interface_meta,
        "standard": "自动校验试样；未经过人工纸样师确认",
    }


def _batch_component_payload(results: list[Any]) -> list[dict[str, Any]]:
    payload = []
    for result in results:
        row = asdict(result)
        row["validation_issues"] = [asdict(issue) for issue in result.validation_issues]
        payload.append(row)
    return payload


def _downgrade_review_only_batch_errors(validation: dict[str, Any], family: str) -> None:
    """Trial compose: shirt never hard-fails. Keep the preview and continue."""
    downgraded: list[str] = []
    kept_errors: list[str] = []
    for message in validation.get("errors") or []:
        if family == "shirt":
            downgraded.append(f"{message}；已保留当前纸样，不阻断下一步")
            continue
        if message.startswith("存在 ") and " 条退化几何" in message:
            downgraded.append(f"{message}；已作为源IR质量问题进入人工审核，不阻断组合预览")
            continue
        kept_errors.append(message)
    validation["errors"] = kept_errors
    validation.setdefault("warnings", []).extend(downgraded)
    validation["valid"] = not kept_errors
    validation["trial_ready"] = not kept_errors


def _compose_batch_preview(
    recipe: dict[str, Any],
    index: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    family = recipe["family"]
    base_case_id = recipe["base_case_id"]
    base_ir = index.get(base_case_id)
    if not base_ir:
        raise ValueError(f"找不到基础纸样 {base_case_id}")
    actual_family = normalize_family((base_ir.get("design_semantics") or {}).get("category"))
    if actual_family != family:
        raise ValueError(f"基础纸样 {base_case_id} 属于 {actual_family}，不能在 {family} 工作台中组合")

    option_by_id = {option["id"]: option for option in catalog["options"]}
    selections = recipe.get("selections") or {}
    base_option_ids = recipe.get("base_option_ids") or {}
    for group, option_id in selections.items():
        if not option_id or option_id == base_option_ids.get(group):
            continue
        option = option_by_id.get(option_id)
        if not option:
            raise ValueError(f"未知版型选项 {option_id}")
        compatible = set(option.get("compatible_families") or [option["family"]])
        if family not in compatible:
            raise ValueError(f"{option['label_zh']} 不兼容当前品类")

    from batch_executor import entity_hash, execute_batch_preview
    from batch_planner import build_composition_plan

    profile = grading_profile(recipe)
    scaled_entities = _scale_complete_base(base_ir, profile)
    scaled_ir = {**base_ir, "atomic_entities": scaled_entities}
    plan = build_composition_plan(recipe, scaled_ir)
    donor_index = {case_id: ir for case_id, ir in index.items() if case_id != base_case_id}
    entities, component_results = execute_batch_preview(scaled_ir, recipe, plan, donor_index=donor_index)

    sources: dict[str, Any] = {"base": base_case_id}
    for result in component_results:
        if result.status == "applied":
            sources[result.group] = {
                "case_id": base_case_id,
                "option_id": result.option_id,
                "confidence": 0.72,
                "geometry_rule": f"{result.group}.edge_role_batch.v1",
                "mapping_mode": "edge_role_local",
                "status": result.status,
            }
    interface_meta: dict[str, Any] = {}
    if any(result.group in {"neckline", "collar"} and result.status == "applied" for result in component_results):
        interface_meta["body_neckline"] = {"applied": True, "rule": "edge_role_batch_local_neckline"}
    validation = _validate(family, entities, interface_meta, sources)
    _downgrade_review_only_batch_errors(validation, family)
    for result in component_results:
        for issue in result.validation_issues:
            validation["warnings"].append(issue.message)
    validation["standard"] = "auto_validated_trial_edge_role_review_required"
    laid_out = _layout_complete(_normalize_physical_components(filter_preview_entities(entities)), gap=52.0 if recipe.get("compact_layout") else 90.0)
    canonical = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    recipe_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    tryon_descriptor = build_tryon_descriptor(entities, recipe_hash, family)
    protected_hashes = {
        str(entity.get("entity_id")): entity_hash(entity)
        for entity in scaled_entities
        if entity.get("entity_id")
    }
    component_payload = _batch_component_payload(component_results)
    meta = {
        "recipe_hash": recipe_hash,
        "family": family,
        "execution_mode": "batch_preview",
        "sizing_profile": profile,
        "source_measurements": source_measurements(base_ir),
        "tryon_descriptor": tryon_descriptor,
        "sources": sources,
        "pieces": _piece_summary(laid_out),
        "paper_info": _paper_info(laid_out),
        "validation": validation,
        "replacement_candidates": {},
        "status": "valid" if validation["valid"] else "invalid",
        "batch_plan": asdict(plan),
        "component_results": component_payload,
        "review_required": bool(component_results),
        "review_ledger": {
            "schema": "chi27.review-ledger.edge-role-batch.v1",
            "trial_status": "auto_validated_trial",
            "human_review_required": bool(component_results),
            "operations": component_payload,
            "protected_entity_hashes": protected_hashes,
        },
    }
    return laid_out, meta


def resolve_execution_mode(recipe: dict[str, Any]) -> str:
    """Family-locked compose pipeline.

    - tshirt: frozen mainline ``simple_piece_swap``
    - shirt: ``shirt_strategy`` → ``shirt.simple_piece_swap.v1``; sandbox may request ``batch_preview`` for A/B
    """
    family = normalize_family(str(recipe.get("family") or ""))
    requested = str(recipe.get("execution_mode") or "").strip()
    if family == "tshirt":
        # T-shirt pipeline is frozen — ignore legacy/batch clients.
        return "simple_piece_swap"
    if family == "shirt":
        # Main site must hit shirt.simple_piece_swap.v1. Legacy batch is sandbox A/B only.
        if requested == "batch_preview" and recipe.get("sandbox_compare"):
            return "batch_preview"
        return "shirt_strategy"
    raise ValueError(f"不支持的品类 {family}")


def compose_recipe(
    recipe: dict[str, Any],
    index: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
    suggest_replacements: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mode = resolve_execution_mode(recipe)
    recipe = {**recipe, "execution_mode": mode}
    if mode in {"simple_piece_swap", "simple"}:
        from simple_compose import compose_simple

        return compose_simple(recipe, index, catalog)
    if mode == "shirt_strategy":
        from shirt_compose import compose_shirt

        return compose_shirt(recipe, index, catalog)
    if mode == "batch_preview":
        return _compose_batch_preview(recipe, index, catalog)
    raise ValueError(f"不支持的 execution_mode: {mode}")
