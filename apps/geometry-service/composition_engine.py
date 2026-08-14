from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from geometry_ops import bounds_of_entities, entity_length, entity_points, role_edge_length, transform_entity
from interface_morph import match_neck_to_neckline, match_sleeve_cap_to_armhole
from tryon_descriptor import build_tryon_descriptor
from batch_executor import entity_hash, execute_batch_preview
from batch_planner import build_composition_plan
from run_experiments import (
    ARMHOLE_ROLES,
    BODY_ROLES,
    NECKLINE_ROLES,
    NECK_ROLES,
    SLEEVE_CAP_ROLES,
    SLEEVE_ROLES,
    piece_entities,
    role_map,
    scale_piece_group,
    scale_sleeve_anisotropic,
    sleeve_length_axis,
)


FRONT_ROLES = {"front_body", "front_left", "front_right"}
BACK_ROLES = {"back_body", "back_yoke"}
COLLAR_ROLES = {"neck_binding", "neck_rib", "collar", "collar_stand", "collar_interlining"}
PLACKET_ROLES = {"front_placket"}
PURE_SLEEVE_ROLES = {"sleeve", "sleeve_left", "sleeve_right"}
CUFF_ROLES = {"cuff", "rib_cuff", "sleeve_placket", "sleeve_placket_extension"}

# Compose/preview: keep labeled pieces only. Unmatched fused DXF lines are dropped.
_PREVIEW_DROP_PIECE_ROLES = {"unknown", "", "none", "unlabeled"}
_PREVIEW_DROP_LINE_ROLES = {
    "drill_hole", "text", "construction", "auxiliary", "grainline",
}
# Note: shirt IR often stores cut outlines as line_role=unknown on a labeled
# piece (collar/yoke/placket). Keep those; drop entities with no piece.


def _entity_points(entity: dict[str, Any]) -> list[list[float]]:
    points = (entity.get("geometry") or {}).get("points") or []
    return [p for p in points if isinstance(p, (list, tuple)) and len(p) >= 2]


def _is_closed_cut(entity: dict[str, Any], tol: float = 1.0) -> bool:
    pts = _entity_points(entity)
    if len(pts) < 3:
        return False
    return math.hypot(float(pts[0][0]) - float(pts[-1][0]), float(pts[0][1]) - float(pts[-1][1])) <= tol


def _is_labeled_piece_entity(entity: dict[str, Any]) -> bool:
    role = str(entity.get("_piece_role") or entity.get("piece_role") or "unknown")
    piece_id = str(entity.get("piece_id") or "")
    if entity.get("_display_only"):
        return False
    if role in _PREVIEW_DROP_PIECE_ROLES or not piece_id or piece_id.startswith("inferred:"):
        return False
    return True


def prefer_piece_cut_outlines(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep labeled-piece geometry only. Drop unmatched fused DXF lines."""
    from preview_outline import build_closed_preview_outline

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
            if not usable:
                continue
            if any(_is_closed_cut(entity) for entity in usable):
                out.extend(usable)
                continue
            try:
                out.append(
                    build_closed_preview_outline(
                        usable,
                        piece_role=role,
                        entity_id=f"{piece_id}:cut_outline",
                        piece_id=piece_id,
                    )
                )
            except ValueError:
                out.append(max(usable, key=lambda entity: len(_entity_points(entity))))
    return out


def filter_preview_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep labeled pattern pieces; drop unmatched DXF lines and CAD junk."""
    entities = prefer_piece_cut_outlines(entities)
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
        if family != "shirt":
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


def reshape_body_neckline(
    entities: list[dict[str, Any]], ir: dict[str, Any], slug: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Redraw the neckline cut edge on front/back body pieces.

    Replacing only a collar or binding is not a valid component remix: its host
    body edge must change as well.  This function keeps the two shoulder join
    points fixed and redraws only the annotated neckline chains toward the body
    centroid, so adjacent shoulder geometry stays locked.
    """
    entity_by_id = {entity.get("entity_id"): entity for entity in entities}
    piece_entities_current: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        piece_entities_current.setdefault(entity.get("piece_id") or "", []).append(entity)

    # Some v1 edge-chain records point at a different connected component's
    # piece_id. The atomic entity carries the reliable physical piece role, so
    # build the host chains from the entities instead of trusting that pointer.
    chains_by_piece: dict[tuple[str, str], list[str]] = {}
    for entity in entities:
        piece_role = str(entity.get("_piece_role") or "")
        if piece_role not in FRONT_ROLES | BACK_ROLES or str(entity.get("line_role") or "") != "neckline":
            continue
        key = (str(entity.get("piece_id") or ""), piece_role)
        chains_by_piece.setdefault(key, []).append(str(entity.get("entity_id")))

    # Newer annotations keep the role on the atomic entity, while some source
    # files only declare it on edge_chains. Merge both representations before
    # deciding that a front or back neckline is absent.
    for chain in ir.get("edge_chains") or []:
        if "neckline" not in str(chain.get("edge_role") or ""):
            continue
        for entity_id in chain.get("ordered_entity_ids") or []:
            entity = entity_by_id.get(entity_id)
            if not entity:
                continue
            piece_role = str(entity.get("_piece_role") or "")
            if piece_role not in FRONT_ROLES | BACK_ROLES:
                continue
            key = (str(entity.get("piece_id") or ""), piece_role)
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
        if key in chains_by_piece:
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

    changed: dict[str, dict[str, Any]] = {}
    reports = []
    for piece_id, piece_role, ids in chain_rows:
        chain_points = [
            point
            for entity_id in ids
            for point in entity_points(entity_by_id[entity_id])
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

        front_depth = {
            "crew": 0.18,
            "v-neck": 0.48,
            "polo": 0.17,
            "high-mock": 0.07,
            "cowl": 0.25,
            "scrunch": 0.13,
            "boat": 0.045,
            "asymmetric": 0.28,
            "bow-tie": 0.14,
            "pointed": 0.16,
            "peter-pan": 0.13,
            "casual-wide-lapel": 0.22,
            "open-v-pointed": 0.42,
        }.get(slug, 0.16)
        back_depth = {
            "crew": .055,
            "v-neck": .10,
            "polo": .06,
            "high-mock": .035,
            "boat": .035,
            "open-v-pointed": .095,
        }.get(slug, .065)
        depth_ratio = front_depth if piece_role in FRONT_ROLES else back_depth
        depth = chord_length * depth_ratio

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
            changed_entity = _map_points(entity_by_id[entity_id], redraw)
            if entity_id in inferred_ids:
                changed_entity["line_role"] = "neckline"
            changed[entity_id] = changed_entity
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

    return [changed.get(entity.get("entity_id"), entity) for entity in entities], {
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


def grading_profile(recipe: dict[str, Any]) -> dict[str, float | str]:
    if recipe.get("skip_grading"):
        return {
            "mode": "source",
            "width": 1.0,
            "length": 1.0,
            "sleeve_length": 1.0,
            "sleeve_width": 1.0,
            "neck": 1.0,
            "ease_cm": 0.0,
            "fit": str(recipe.get("fit") or "regular"),
            "waist_scale": 1.0,
            "material_shrink_rate": 0.0,
        }
    measurements = recipe.get("measurements_cm") or {}
    sex = recipe.get("sex", "female")
    if sex == "male_general":
        base_height, base_chest, base_shoulder, base_neck, base_sleeve, base_upper_arm = 175.0, 92.0, 44.0, 39.0, 60.0, 32.0
        base_waist = 78.0
        mode = "male_general_trial"
    else:
        base_height, base_chest, base_shoulder, base_neck, base_sleeve, base_upper_arm = 160.0, 84.0, 38.88, 34.0, 58.0, 28.0
        base_waist = 68.0
        mode = "gbt_1335_2_2008_female"
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
    profile = {
        "mode": mode,
        "width": max(0.82, min(1.28, (width_scale * 0.72 + shoulder_scale * 0.28) * shrink_correction)),
        "length": max(0.85, min(1.25, height / base_height * shrink_correction)),
        "sleeve_length": max(0.82, min(1.25, sleeve / base_sleeve)),
        "sleeve_width": max(0.85, min(1.24, (width_scale * 0.55 + upper_arm / base_upper_arm * 0.45) * shrink_correction)),
        "neck": max(0.85, min(1.18, neck / base_neck)),
        "ease_cm": ease,
        "fit": fit,
        "waist_scale": round(waist_scale, 4),
        "material_shrink_rate": shrink_rate,
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
        elif role in FRONT_ROLES | BACK_ROLES | {"back_yoke", "front_placket"}:
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
    body_roles = FRONT_ROLES | BACK_ROLES
    large = [row for row in pieces if str(row[1][0].get("_piece_role") or "") in body_roles]
    small = [row for row in pieces if str(row[1][0].get("_piece_role") or "") not in body_roles]
    if not large:
        large, small = pieces, []

    def pack(rows: list[tuple], y0: float) -> tuple[list[dict[str, Any]], float]:
        if not rows:
            return [], y0
        widths = sorted((width for _, _, _, width, _height in rows), reverse=True)
        area = sum((width + gap) * (height + gap) for _, _, _, width, height in rows)
        pair = widths[0] + (widths[1] + gap if len(widths) > 1 else 0)
        row_limit = max(pair, math.sqrt(max(area, 1.0)))
        cursor_x = row_height = 0.0
        cursor_y = y0
        packed: list[dict[str, Any]] = []
        for _, group, bounds, width, height in rows:
            if cursor_x and cursor_x + width > row_limit:
                cursor_x = 0.0
                cursor_y += row_height + gap
                row_height = 0.0
            packed.extend(transform_entity(entity, dx=cursor_x - bounds[0], dy=cursor_y - bounds[1]) for entity in group)
            cursor_x += width + gap
            row_height = max(row_height, height)
        return packed, cursor_y + row_height + gap

    small_out, y1 = pack(small, 0.0)
    large_out, _ = pack(large, y1)
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
    if not sleeveless and not roles & PURE_SLEEVE_ROLES:
        errors.append("缺少袖片")
    if family == "shirt":
        if not roles & {"collar", "collar_stand"}:
            errors.append("缺少衬衫领片")
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
    if (sources.get("neckline") or sources.get("collar")) and not body_neckline.get("applied"):
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
    """Batch preview is a trial generator: sparse legacy annotations go to human review, not hard failure."""
    downgraded: list[str] = []
    kept_errors: list[str] = []
    for message in validation.get("errors") or []:
        if family == "shirt" and message == "缺少门襟来源":
            downgraded.append("基础版型未提供可审核门襟来源；已保留组合预览，生产前需人工审核")
            continue
        if family == "shirt" and message == "缺少袖口版片":
            downgraded.append("基础版型未标注袖口版片；已保留组合预览，生产前需人工审核")
            continue
        if family == "shirt" and message == "缺少袖片":
            downgraded.append("基础版型未标注袖片；已从可用来源补袖，生产前需人工审核")
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
    return requested or "legacy"


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

    family = recipe["family"]
    base_case_id = recipe["base_case_id"]
    base_ir = index.get(base_case_id)
    if not base_ir:
        raise ValueError(f"找不到基础纸样 {base_case_id}")
    actual_family = normalize_family((base_ir.get("design_semantics") or {}).get("category"))
    if actual_family != family:
        raise ValueError(f"基础纸样 {base_case_id} 属于 {actual_family}，不能在 {family} 工作台中组合")

    option_by_id = {option["id"]: option for option in catalog["options"]}
    profile = grading_profile(recipe)
    entities = _scale_complete_base(base_ir, profile)
    selections = recipe.get("selections") or {}
    base_option_ids = recipe.get("base_option_ids") or {}
    sources: dict[str, Any] = {"base": base_case_id}
    interface_meta: dict[str, Any] = {}

    silhouette_id = selections.get("silhouette")
    special_id = selections.get("special")
    shape_id = silhouette_id or special_id
    if shape_id and shape_id not in set(base_option_ids.values()):
        option = option_by_id.get(shape_id)
        if option:
            body = [entity for entity in entities if entity.get("_piece_role") in FRONT_ROLES | BACK_ROLES]
            others = [entity for entity in entities if entity.get("_piece_role") not in FRONT_ROLES | BACK_ROLES]
            if option["slug"] == "relaxed-h":
                body = scale_piece_group(body, sx=1.06, sy=1.02)
            elif option["slug"] == "oversized":
                body = scale_piece_group(body, sx=1.12, sy=1.08)
            body = _piecewise_shape(body, option["slug"])
            entities = others + body
            sources[option["group"]] = base_case_id

    constraints = recipe.get("intent_constraints") or {}
    short_sleeve = constraints.get("sleeve") == "short"
    sleeveless = constraints.get("sleeve") == "sleeveless"
    if short_sleeve:
        entities = [entity for entity in entities if entity.get("_piece_role") not in CUFF_ROLES]
        sources["intent_sleeve"] = "short"
    elif sleeveless:
        host = _host_ir(base_ir, entities)
        armhole_length = role_edge_length(host, ARMHOLE_ROLES, FRONT_ROLES | BACK_ROLES)
        entities = [entity for entity in entities if entity.get("_piece_role") not in PURE_SLEEVE_ROLES | CUFF_ROLES]
        sources["intent_sleeve"] = "sleeveless"
        sources["armhole_finish"] = "narrow_hem_or_binding_trial"
        interface_meta["armhole_finish"] = {"applied": armhole_length > 0, "length_mm": round(armhole_length, 3), "rule": "retain_host_armhole_remove_sleeve_and_cuff"}
    target_length_cm = constraints.get("target_length_cm")
    if target_length_cm:
        body_roles = FRONT_ROLES | BACK_ROLES | {"back_yoke", "front_placket"}
        body = [entity for entity in entities if entity.get("_piece_role") in body_roles]
        others = [entity for entity in entities if entity.get("_piece_role") not in body_roles]
        bounds = bounds_of_entities(body)
        if bounds:
            current_height = max(bounds[3] - bounds[1], 1.0)
            body = scale_piece_group(body, sx=1.0, sy=max(0.55, min(1.45, float(target_length_cm) * 10.0 / current_height)))
            entities = others + body
            sources["target_length_cm"] = float(target_length_cm)

    group_order = ["neckline", "collar", "placket", "sleeve", "cuff"]
    for group in group_order:
        if (short_sleeve and group == "cuff") or (sleeveless and group in {"sleeve", "cuff"}):
            continue
        option_id = selections.get(group)
        if not option_id:
            continue
        if option_id == base_option_ids.get(group):
            continue
        option = option_by_id.get(option_id)
        if not option:
            raise ValueError(f"未知版型选项 {option_id}")
        compatible = set(option.get("compatible_families") or [option["family"]])
        if family not in compatible:
            raise ValueError(f"{option['label_zh']} 不兼容当前品类")
        donor_case_id, confidence, reasons = choose_donor(option, index, base_case_id)
        if not donor_case_id or reasons:
            raise ValueError(reasons[0] if reasons else f"{option['label_zh']} 没有可用来源")
        donor_ir = index[donor_case_id]
        exact_donor = _label_slug(donor_ir, group) == option["slug"]
        replacement = _entities_for_roles(donor_ir, REPLACE_ROLES_BY_GROUP[group])
        replacement = _select_replacement_components(replacement, group, option["slug"])
        replacement = _modify_component(replacement, group, option["slug"], donor_ir)
        if group == "sleeve" and short_sleeve:
            replacement = scale_sleeve_anisotropic(
                replacement,
                length_scale=0.46,
                width_scale=1.0,
                ir=donor_ir,
            )
        if group in {"neckline", "collar"}:
            entities, body_neckline_meta = reshape_body_neckline(entities, base_ir, option["slug"])
            interface_meta["body_neckline"] = body_neckline_meta
        if group == "sleeve":
            host = _host_ir(base_ir, entities)
            armhole = role_edge_length(host, ARMHOLE_ROLES, FRONT_ROLES | BACK_ROLES)
            if armhole > 0:
                replacement, meta = match_sleeve_cap_to_armhole(replacement, donor_ir, armhole, ease=1.04)
                if abs(float(meta.get("length_error") or 0.0)) > 2.0:
                    replacement, meta = match_sleeve_cap_to_armhole(replacement, donor_ir, armhole, ease=1.04)
                    meta["retry"] = True
                interface_meta["sleeve"] = meta
        elif group in {"neckline", "collar"}:
            host = _host_ir(base_ir, entities)
            neckline = role_edge_length(host, NECKLINE_ROLES, FRONT_ROLES | BACK_ROLES)
            if neckline > 0:
                replacement, meta = match_neck_to_neckline(replacement, donor_ir, neckline)
                after = float(meta.get("length_after") or 0.0)
                target = float(meta.get("target_length") or neckline)
                if after > 0 and abs(after - target) > 2.0:
                    correction = max(0.03, min(5.0, target / after))
                    replacement = scale_piece_group(replacement, sx=correction, sy=correction)
                    meta["post_scale_correction"] = round(correction, 5)
                    meta["length_after"] = round(after * correction, 3)
                    meta["length_error"] = round(after * correction - target, 3)
                    meta["length_error_ratio"] = round((after * correction - target) / max(target, 1e-6), 5)
                interface_meta["neck"] = meta
        elif group == "placket":
            body_bounds = bounds_of_entities(
                [entity for entity in entities if entity.get("_piece_role") in FRONT_ROLES]
            )
            placket_bounds = bounds_of_entities(replacement)
            if body_bounds and placket_bounds:
                host_height = body_bounds[3] - body_bounds[1]
                donor_height = max(placket_bounds[3] - placket_bounds[1], 1.0)
                target_ratio = 0.4 if option["slug"] == "half" else 1.0
                axis = "y" if donor_height >= placket_bounds[2] - placket_bounds[0] else "x"
                scale = max(0.35, min(2.5, host_height * target_ratio / donor_height))
                replacement = scale_piece_group(
                    replacement, sx=scale if axis == "x" else 1.0, sy=scale if axis == "y" else 1.0
                )
        elif group == "cuff":
            sleeve_bounds = bounds_of_entities(
                [entity for entity in entities if entity.get("_piece_role") in PURE_SLEEVE_ROLES]
            )
            cuff_bounds = bounds_of_entities(replacement)
            if sleeve_bounds and cuff_bounds:
                target = min(sleeve_bounds[2] - sleeve_bounds[0], sleeve_bounds[3] - sleeve_bounds[1])
                current = max(cuff_bounds[2] - cuff_bounds[0], cuff_bounds[3] - cuff_bounds[1], 1.0)
                replacement = scale_piece_group(replacement, sx=max(0.6, min(1.8, target / current)), sy=1.0)
            replacement = add_cuff_style_guides(replacement, option["slug"])
        entities = _replace_roles(entities, REPLACE_ROLES_BY_GROUP[group], replacement)
        sources[group] = {
            "case_id": donor_case_id,
            "option_id": option_id,
            "confidence": confidence,
            "geometry_rule": option["geometry_rule"],
            "mapping_mode": "exact_component" if exact_donor else "parametric_from_closest_component",
        }

    entities = _normalize_physical_components(filter_preview_entities(entities))
    validation = _validate(family, entities, interface_meta, sources)
    _downgrade_review_only_batch_errors(validation, family)
    laid_out = _layout_complete(entities, gap=52.0 if recipe.get("compact_layout") else 90.0)
    replacement_candidates: dict[str, list[dict[str, str]]] = {}
    if suggest_replacements and not validation["trial_ready"]:
        for group, current_id in selections.items():
            if not current_id:
                continue
            verified: list[dict[str, str]] = []
            for option in catalog["options"]:
                compatible = set(option.get("compatible_families") or [option["family"]])
                if option["group"] != group or option["id"] == current_id or family not in compatible:
                    continue
                candidate_recipe = {**recipe, "selections": {**selections, group: option["id"]}}
                try:
                    _, candidate_meta = compose_recipe(candidate_recipe, index, catalog, suggest_replacements=False)
                except (KeyError, TypeError, ValueError):
                    continue
                if candidate_meta["validation"]["trial_ready"]:
                    verified.append({"option_id": option["id"], "label": option["label_zh"]})
                    break
            if verified:
                replacement_candidates[group] = verified
    canonical = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    recipe_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    tryon_descriptor = build_tryon_descriptor(entities, recipe_hash, family)
    meta = {
        "recipe_hash": recipe_hash,
        "family": family,
        "sizing_profile": profile,
        "source_measurements": source_measurements(base_ir),
        "tryon_descriptor": tryon_descriptor,
        "sources": sources,
        "pieces": _piece_summary(laid_out),
        "paper_info": _paper_info(laid_out),
        "validation": validation,
        "replacement_candidates": replacement_candidates,
        "status": "valid" if validation["valid"] else "invalid",
    }
    return laid_out, meta
