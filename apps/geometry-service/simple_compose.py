"""T-shirt compose: piece swap (拼贴) then structure-point grading (放码).

Pipeline id: ``tshirt.simple_piece_swap.v2``

袖型: puff/set-in/bell/regular 只换袖片；raglan/batwing 换衣身+袖；flutter 换衣身去袖。
领口: 双前片时 V领=斜直线、一字肩=水平直线+侧缝延长，其余领口不动；单前片仍重绘领圈。Polo/高领/堆堆领换前后片。
衣长/衣宽/领围: 结构点放码（侧缝外放、胸围线以下加长、只动领圈）。
袖山按针织工业逻辑：矮袖山、前后弧对袖窿，袖长只加在袖肥线以下。
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from composition_contracts import ComponentResult, ValidationIssue
from composition_engine import (
    BACK_ROLES,
    COLLAR_ROLES,
    CUFF_ROLES,
    FRONT_ROLES,
    PURE_SLEEVE_ROLES,
    _layout_complete,
    _normalize_physical_components,
    _paper_info,
    _piece_summary,
    _validate,
    filter_preview_entities,
    grading_profile,
    normalize_family,
    reshape_body_neckline,
    source_measurements,
)
from donor_similarity import rank_donors, score_piece_to_host
from geometry_ops import bounds_of_entities, transform_entity
from tryon_descriptor import build_tryon_descriptor

_HANDOFF_SCRIPTS = Path(__file__).resolve().parents[2] / "_handoff_pack" / "scripts"
if _HANDOFF_SCRIPTS.is_dir() and str(_HANDOFF_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_HANDOFF_SCRIPTS))

BODY_SWAP_ROLES = FRONT_ROLES | BACK_ROLES | COLLAR_ROLES | {"front_placket"}
BODY_ROLES = FRONT_ROLES | BACK_ROLES | {"front_placket"}
SLEEVE_SWAP_ROLES = PURE_SLEEVE_ROLES | CUFF_ROLES
LENGTH_FACTOR = {"short": 0.92, "regular": 1.0, "long": 1.10}
SLEEVE_CAP_ROLES = {"sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "sleeve_head"}
SLEEVE_INTERFACE_ROLES = SLEEVE_CAP_ROLES | {"armhole_front", "armhole_back", "armhole_seam"}
SLEEVE_LINE_KEEP = SLEEVE_CAP_ROLES | {
    "cut",
    "cut_line",
    "pattern_boundary",
    "sew",
    "internal",
    "grainline",
    "sleeve_hem",
    "sleeve_underarm",
    "sleeve_underarm_seam",
    "armhole_front",
    "armhole_back",
    "armhole_seam",
    "hem_line",
    "notch",
}
SLEEVE_CAP_EASE = {
    "puff": 1.08,
    "bell": 1.05,
    "set-in": 1.04,
    "regular": 1.04,
    "raglan": 1.03,
    "batwing": 1.02,
}

# sleeve slug → which physical roles must move together
SLEEVE_STRATEGY = {
    # 只动袖
    "set-in": {"mode": "sleeve_only", "roles": PURE_SLEEVE_ROLES},
    "puff": {"mode": "sleeve_only", "roles": PURE_SLEEVE_ROLES},
    "bell": {"mode": "sleeve_only", "roles": PURE_SLEEVE_ROLES},
    "regular": {"mode": "sleeve_only", "roles": PURE_SLEEVE_ROLES},
    # 插肩族：衣身肩袖结构 + 袖片一起换
    "raglan": {"mode": "body_and_sleeve", "roles": BODY_ROLES | PURE_SLEEVE_ROLES},
    "batwing": {"mode": "body_and_sleeve", "roles": BODY_ROLES | PURE_SLEEVE_ROLES},
    # 飞袖：无独立袖片
    "flutter": {"mode": "body_integrated", "roles": BODY_ROLES, "drop_host_sleeves": True},
}


# Polo / 高领 / 堆堆领：换前后衣身。其余（V领、一字肩、圆领）只重绘领圈线。
NECKLINE_BODY_SWAP_SLUGS = {"polo", "high-mock", "cowl", "scrunch"}


def _option_slug(option_id: str | None) -> str:
    return str(option_id or "").split(".")[-1].strip().lower()


def _sleeve_plan(option_id: str | None) -> dict[str, Any]:
    slug = _option_slug(option_id)
    plan = dict(SLEEVE_STRATEGY.get(slug) or {"mode": "sleeve_only", "roles": PURE_SLEEVE_ROLES})
    plan["slug"] = slug
    return plan


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe strategy payload (roles must be lists, not sets)."""
    out = dict(plan)
    roles = out.get("roles")
    if isinstance(roles, (set, frozenset, tuple)):
        out["roles"] = sorted(str(r) for r in roles)
    elif isinstance(roles, list):
        out["roles"] = [str(r) for r in roles]
    return out


def _role(entity: dict[str, Any]) -> str:
    return str(entity.get("_piece_role") or entity.get("piece_role") or "unknown")


def _line_role(entity: dict[str, Any]) -> str:
    return str(entity.get("line_role") or entity.get("edge_role") or "").lower()


def _edge_role_map(ir: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for chain in ir.get("edge_chains") or []:
        role = str(chain.get("edge_role") or "").strip()
        if not role:
            continue
        for eid in chain.get("ordered_entity_ids") or []:
            if eid:
                mapping[str(eid)] = role
    return mapping


def _stamp_edge_roles(entities: list[dict[str, Any]], ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Copy edge_chains roles onto entities (needed: sleeve_cap often only in chains)."""
    role_by_id = _edge_role_map(ir)
    if not role_by_id:
        return entities
    out: list[dict[str, Any]] = []
    for entity in entities:
        eid = str(entity.get("entity_id") or "")
        raw = eid.split(":", 1)[-1] if eid else ""
        role = role_by_id.get(eid) or role_by_id.get(raw)
        if not role:
            out.append(entity)
            continue
        copied = deepcopy(entity)
        copied["edge_role"] = role
        # Prefer chain role for interface morph (sleeve_cap often missing as line_role).
        if role in SLEEVE_CAP_ROLES or role.startswith("armhole") or role in {
            "front_neckline", "back_neckline", "sleeve_underarm", "sleeve_hem",
        }:
            copied["line_role"] = role
        elif not copied.get("line_role"):
            copied["line_role"] = role
        out.append(copied)
    return out


def _sleeve_cap_ease(slug: str | None, recipe: dict[str, Any] | None = None) -> float:
    constraints = (recipe or {}).get("intent_constraints") or {}
    raw = constraints.get("sleeve_cap_ease")
    if raw is not None:
        try:
            value = float(raw)
            if math.isfinite(value) and 0.9 <= value <= 1.3:
                return value
        except (TypeError, ValueError):
            pass
    return SLEEVE_CAP_EASE.get(str(slug or "").lower(), 1.04)


def _polyline_bulge_ratio(points: list[Any]) -> float:
    """Max perpendicular offset / chord. ~0 means a flat chord (dangerous to morph)."""
    if len(points) < 2:
        return 0.0
    a = (float(points[0][0]), float(points[0][1]))
    b = (float(points[-1][0]), float(points[-1][1]))
    chord = math.hypot(b[0] - a[0], b[1] - a[1])
    if chord < 1e-6:
        return 0.0
    ux, uy = (b[0] - a[0]) / chord, (b[1] - a[1]) / chord
    nx, ny = -uy, ux
    best = 0.0
    for point in points[1:-1]:
        ox = float(point[0]) - a[0]
        oy = float(point[1]) - a[1]
        best = max(best, abs(ox * nx + oy * ny))
    return best / chord


def _entity_poly_len(entity: dict[str, Any]) -> float:
    return _poly_len((entity.get("geometry") or {}).get("points") or [])


def _is_usable_sleeve_interface(entity: dict[str, Any]) -> bool:
    pts = (entity.get("geometry") or {}).get("points") or []
    if len(pts) < 3:
        return False
    length = _poly_len(pts)
    if length < 25.0:
        return False
    # Flat chords morph into giant triangular spikes — reject them.
    if _polyline_bulge_ratio(pts) < 0.015 and length > 80.0:
        return False
    return True


def _primary_sleeve_cap_ids(piece_rows: list[dict[str, Any]]) -> list[str]:
    """Pick real sleeve-cap arcs (prefer armhole_front/back on sleeve; never flat chords)."""
    split = [
        entity
        for entity in piece_rows
        if _line_role(entity) in {"armhole_front", "armhole_back"} and _is_usable_sleeve_interface(entity)
    ]
    caps = split or [
        entity
        for entity in piece_rows
        if _line_role(entity) in SLEEVE_CAP_ROLES and _is_usable_sleeve_interface(entity)
    ]
    if not caps:
        return []
    cluster = _largest_cluster(caps)
    if len(cluster) > 14:
        scored = sorted(cluster, key=_entity_poly_len, reverse=True)
        cluster = scored[:12]
    return [str(entity.get("entity_id")) for entity in cluster if entity.get("entity_id")]


def _body_armhole_length(body_entities: list[dict[str, Any]]) -> float:
    labeled = _armhole_length(body_entities, on_sleeve=False)
    if labeled >= 40.0:
        return labeled
    # Fallback when donor body lacks armhole_* labels (common on raglan packs).
    body_wh = _mean_body_wh(body_entities)
    if not body_wh:
        return 0.0
    return max(body_wh[0], body_wh[1]) * 0.92


def _morph_sleeve_caps_to_armholes(
    entities: list[dict[str, Any]],
    *,
    ease: float = 1.04,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Morph only real sleeve-cap arcs so length ≈ body armhole × ease; lock the rest."""
    from interface_morph import morph_entities_to_total_length

    body_entities = [entity for entity in entities if _role(entity) in BODY_ROLES]
    body_ah = _body_armhole_length(body_entities)
    meta: dict[str, Any] = {
        "method": "sleeve_cap_arc_to_armhole",
        "ease": ease,
        "body_armhole": round(body_ah, 3),
        "pieces": [],
    }
    if body_ah < 40.0:
        meta["applied"] = False
        meta["reason"] = "body_armhole_too_short_or_unlabeled"
        return entities, meta

    target = body_ah * ease
    non_sleeve = [entity for entity in entities if _role(entity) not in PURE_SLEEVE_ROLES]
    sleeve_rows = [entity for entity in entities if _role(entity) in PURE_SLEEVE_ROLES]
    if not sleeve_rows:
        meta["applied"] = False
        meta["reason"] = "no_sleeve_pieces"
        return entities, meta

    out_sleeves: list[dict[str, Any]] = []
    piece_reports: list[dict[str, Any]] = []
    any_applied = False
    for piece_id, rows in _group_by_piece(sleeve_rows).items():
        cap_ids = _primary_sleeve_cap_ids(rows)
        if not cap_ids:
            out_sleeves.extend(rows)
            piece_reports.append({"piece_id": piece_id, "applied": False, "reason": "no_usable_sleeve_cap_arc"})
            continue
        before = sum(_entity_poly_len(entity) for entity in rows if str(entity.get("entity_id")) in set(cap_ids))
        ratio = before / max(target, 1e-6)
        cut_len = sum(_entity_poly_len(entity) for entity in rows if _line_role(entity) == "cut_line")
        # When a full cut outline exists, morphing labeled cap arcs doubles the silhouette.
        if cut_len >= max(180.0, before * 0.75):
            out_sleeves.extend(rows)
            piece_reports.append({
                "piece_id": piece_id,
                "applied": False,
                "reason": "has_cut_outline_keep_uniform_scale",
                "length_before": round(before, 3),
                "cut_line_length": round(cut_len, 3),
                "target_length": round(target, 3),
                "cap_entity_ids": cap_ids,
            })
            continue
        # Uniform scale already handles coarse size; morph only fine-tunes nearby arcs.
        if ratio < 0.55 or ratio > 1.55:
            out_sleeves.extend(rows)
            piece_reports.append({
                "piece_id": piece_id,
                "applied": False,
                "reason": "cap_length_ratio_out_of_range",
                "length_before": round(before, 3),
                "target_length": round(target, 3),
                "ratio": round(ratio, 4),
                "cap_entity_ids": cap_ids,
            })
            continue
        morphed, piece_meta = morph_entities_to_total_length(rows, cap_ids, target, per_piece=False)
        err = abs(float(piece_meta.get("length_error_ratio") or 0.0))
        if piece_meta.get("applied") and err > 0.12:
            out_sleeves.extend(rows)
            piece_reports.append({
                **piece_meta,
                "piece_id": piece_id,
                "cap_entity_ids": cap_ids,
                "applied": False,
                "reason": "morph_error_too_high_keep_uniform_scale",
                "rejected_error_ratio": round(err, 4),
            })
            continue
        iface = "armhole_split_on_sleeve" if any(
            _line_role(entity) in {"armhole_front", "armhole_back"}
            for entity in rows
            if str(entity.get("entity_id")) in set(cap_ids)
        ) else "sleeve_cap"
        piece_meta = {
            **piece_meta,
            "piece_id": piece_id,
            "cap_entity_ids": cap_ids,
            "body_armhole": round(body_ah, 3),
            "ease": ease,
            "interface": iface,
        }
        if piece_meta.get("applied"):
            any_applied = True
        piece_reports.append(piece_meta)
        out_sleeves.extend(morphed)

    meta["applied"] = any_applied
    meta["pieces"] = piece_reports
    meta["target_length"] = round(target, 3)
    if any_applied:
        errs = [abs(float(p.get("length_error_ratio") or 0)) for p in piece_reports if p.get("applied")]
        meta["max_abs_error_ratio"] = round(max(errs), 4) if errs else None
    return non_sleeve + out_sleeves, meta


def _annotate(ir: dict[str, Any]) -> list[dict[str, Any]]:
    from compose_ir import entities_from_compose, load_compose
    from composition_engine import _annotated_entities, prefer_piece_cut_outlines

    compose = ir.get("_compose_ir") or load_compose(str(ir.get("case_id") or ""))
    if compose:
        return entities_from_compose(compose)

    # IR = semantic labels on DXF-derived polylines. Bind piece_instances;
    # drop unmatched fused DXF lines (no piece_id) from compose.
    out = []
    for entity in prefer_piece_cut_outlines(_annotated_entities(ir)):
        pts = (entity.get("geometry") or {}).get("points") or []
        if len(pts) >= 2:
            out.append(entity)
    return _stamp_edge_roles(out, ir)

def _entities_for_roles(ir: dict[str, Any], roles: set[str]) -> list[dict[str, Any]]:
    return [entity for entity in _annotate(ir) if _role(entity) in roles]


def _group_by_piece(entities: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        key = str(entity.get("piece_id") or entity.get("entity_id") or "x")
        grouped.setdefault(key, []).append(entity)
    return grouped


def _bbox_gap(a: list[float], b: list[float]) -> float:
    dx = 0.0 if a[2] >= b[0] and b[2] >= a[0] else min(abs(a[0] - b[2]), abs(b[0] - a[2]))
    dy = 0.0 if a[3] >= b[1] and b[3] >= a[1] else min(abs(a[1] - b[3]), abs(b[1] - a[3]))
    return math.hypot(dx, dy)


def _spatial_clusters(entities: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split a piece_id into spatially separate panels (nests / mislabeled body)."""
    items: list[tuple[dict[str, Any], list[float]]] = []
    for entity in entities:
        box = bounds_of_entities([entity])
        if box:
            items.append((entity, box))
    if not items:
        return [entities] if entities else []
    sizes = [max(box[2] - box[0], box[3] - box[1]) for _, box in items]
    link = max(80.0, sorted(sizes)[len(sizes) // 2] * 1.25)
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _bbox_gap(items[i][1], items[j][1]) <= link:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    clusters: dict[int, list[dict[str, Any]]] = {}
    for idx, (entity, _) in enumerate(items):
        clusters.setdefault(find(idx), []).append(entity)
    return list(clusters.values())


def _largest_cluster(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the densest nest copy. Marker DXFs often duplicate the same sleeve far apart."""
    groups = _spatial_clusters(entities)
    if not groups:
        return entities
    return max(
        groups,
        key=lambda rows: (
            (lambda b: 0.0 if not b else (b[2] - b[0]) * (b[3] - b[1]))(bounds_of_entities(rows)),
            len(rows),
        ),
    )


def _content_bounds(entities: list[dict[str, Any]]) -> list[float] | None:
    """Sizing bbox = largest spatial cluster, not nest-scattered union."""
    return bounds_of_entities(_largest_cluster(entities))


def _keep_largest_clusters(entities: list[dict[str, Any]], roles: set[str] | None = None) -> list[dict[str, Any]]:
    """Drop far nest duplicates so scaling/layout use one physical panel."""
    roles = roles or (PURE_SLEEVE_ROLES | BODY_ROLES | CUFF_ROLES)
    body_wh = _mean_body_wh(entities)
    has_back = any(_role(entity) in BACK_ROLES for entity in entities)
    out: list[dict[str, Any]] = []
    by_role: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_role.setdefault(_role(entity), []).append(entity)
    for role, rows in by_role.items():
        if role not in roles:
            out.extend(rows)
            continue
        if role in PURE_SLEEVE_ROLES:
            clusters: list[list[dict[str, Any]]] = []
            for piece_rows in _group_by_piece(rows).values():
                clusters.extend(_spatial_clusters(piece_rows) or [piece_rows])
            body_like = [cluster for cluster in clusters if _looks_like_body(cluster, body_wh)]
            sleeve_like = [cluster for cluster in clusters if not _looks_like_body(cluster, body_wh)]
            if not has_back and body_like and body_wh:
                body_like.sort(
                    key=lambda cluster: abs(_piece_wh(cluster)[0] - body_wh[0]) + abs(_piece_wh(cluster)[1] - body_wh[1])
                )
                out.extend(_with_meta(body_like[0], _piece_role="back_body", piece_role="back_body"))
                has_back = True
            seen_pid: dict[str, int] = {}
            for cluster in _pick_sleeve_panels(sleeve_like, body_wh):
                pid = str((cluster[0].get("piece_id") if cluster else "") or "sleeve")
                n = seen_pid.get(pid, 0)
                seen_pid[pid] = n + 1
                out.extend(_with_meta(cluster, piece_id=f"{pid}:panel{n}" if n else pid))
            continue
        for piece_rows in _group_by_piece(rows).values():
            out.extend(_largest_cluster(piece_rows))
    return out


def _piece_wh(rows: list[dict[str, Any]]) -> tuple[float, float]:
    box = bounds_of_entities(rows)
    if not box:
        return 0.0, 0.0
    return max(box[2] - box[0], 0.0), max(box[3] - box[1], 0.0)


def _with_meta(rows: list[dict[str, Any]], **fields: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entity in rows:
        row = dict(entity)
        row.update(fields)
        out.append(row)
    return out


def _looks_like_body(rows: list[dict[str, Any]], body_wh: tuple[float, float] | None) -> bool:
    if not body_wh:
        return False
    w, h = _piece_wh(rows)
    bw, bh = body_wh
    if min(w, h, bw, bh) < 1:
        return False
    wr, hr = w / bw, h / bh
    aspect = max(w, h) / max(min(w, h), 1.0)
    return (0.95 <= wr <= 1.05 and 0.95 <= hr <= 1.05) or (
        0.82 <= wr <= 1.22 and 0.82 <= hr <= 1.22 and aspect <= 1.55
    )


def _usable_sleeve_panel(rows: list[dict[str, Any]], body_wh: tuple[float, float] | None = None) -> bool:
    """Reject nest strips / plackets / puff-cuffs mislabeled as sleeves."""
    w, h = _piece_wh(rows)
    if min(w, h) < 80.0:
        return False
    if max(w, h) / min(w, h) > 4.2:
        return False
    if body_wh and min(body_wh) > 1:
        bw, bh = body_wh
        if w > bw * 1.25 or h > max(bh, 400.0) * 1.45:
            return False
        # Gathered hem band (e.g. C2431105) is short-wide, not a sleeve body.
        if max(w, h) / min(w, h) > 2.4 and min(w, h) < bh * 0.42:
            return False
    return True


def _has_usable_sleeves(entities: list[dict[str, Any]], host_ref: list[dict[str, Any]] | None = None) -> bool:
    body_wh = _mean_body_wh(host_ref or entities) or _mean_body_wh(entities)
    for rows in _group_by_piece([entity for entity in entities if _role(entity) in PURE_SLEEVE_ROLES]).values():
        if _usable_sleeve_panel(_largest_cluster(rows), body_wh):
            return True
    return False


def _sleeve_panel_score(rows: list[dict[str, Any]], body_wh: tuple[float, float] | None) -> float:
    """Prefer a real sleeve panel over nest leftovers / unknown slabs / flat hems."""
    labeled = [
        entity
        for entity in rows
        if _line_role(entity) in SLEEVE_LINE_KEEP or _line_role(entity).startswith("sleeve")
    ]
    labeled_panel = _largest_cluster(labeled) if labeled else []
    lw, lh = _piece_wh(labeled_panel)
    collapsed_label = bool(labeled) and min(lw, lh) < 18.0
    panel = labeled_panel if labeled and not collapsed_label else rows
    w, h = _piece_wh(panel)
    if min(w, h) < 18.0:
        return -1.0
    if max(w, h) / min(w, h) > 7.0:
        return -1.0
    if body_wh:
        bw, bh = body_wh
        if bw > 1 and (w > bw * 1.18 or h > bh * 1.15):
            return -1.0
        if _looks_like_body(rows, body_wh):
            return -1.0
    area = w * h
    if collapsed_label:
        area *= 0.2
    lrs = {_line_role(entity) for entity in rows}
    if not (lrs & (SLEEVE_LINE_KEEP | {"pattern_boundary"})):
        area *= 0.08
    if lrs & SLEEVE_INTERFACE_ROLES:
        area *= 4.0
    elif lrs & {"cut_line", "pattern_boundary"}:
        area *= 2.5
    elif lrs & {"sleeve_hem", "hem_line", "sleeve_underarm_seam"}:
        area *= 1.25
    if body_wh:
        bw, bh = body_wh
        if bw > 1 and (w > bw * 0.95 and h < bh * 0.55):
            area *= 0.05
        if bw > 1 and (w < bw * 0.10 or h < bh * 0.10):
            area *= 0.15
    return area


def _similar_panel(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    wa, ha = _piece_wh(a)
    wb, hb = _piece_wh(b)
    if min(wa, wb, ha, hb) < 1:
        return False
    return 0.72 <= wa / wb <= 1.38 and 0.72 <= ha / hb <= 1.38


def _pick_sleeve_panels(
    pieces: list[list[dict[str, Any]]],
    body_wh: tuple[float, float] | None,
) -> list[list[dict[str, Any]]]:
    """Keep 1 sleeve, or 2 if they look like a left/right pair — not nest copies."""
    scored = [( _sleeve_panel_score(rows, body_wh), rows) for rows in pieces]
    scored = [(score, rows) for score, rows in scored if score > 0]
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        fallback = [rows for rows in pieces if _usable_sleeve_panel(rows, body_wh)]
        return fallback[:1] if fallback else []
    best_score, best = scored[0]
    kept = [best]
    for score, rows in scored[1:]:
        if score < best_score * 0.35:
            break
        if not _similar_panel(best, rows):
            continue
        if abs(_centroid_x(best) - _centroid_x(rows)) < 40.0:
            continue
        kept.append(rows)
        break
    return kept


def _centroid_x(rows: list[dict[str, Any]]) -> float:
    box = bounds_of_entities(rows)
    if not box:
        return 0.0
    return (box[0] + box[2]) / 2.0


def _select_sleeve_pieces(
    host_ref: list[dict[str, Any]],
    donor_ir: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Match sleeves as a pool, not by sleeve_left/right name.

    Host often has left+right; donor may only have one side or a generic `sleeve`.
    Same-role matching would delete the unmatched host side.
    """
    donor_rows = _entities_for_roles(donor_ir, PURE_SLEEVE_ROLES)
    if not donor_rows:
        return [], {}
    host_pieces = {
        pid: _largest_cluster(rows)
        for pid, rows in _group_by_piece([entity for entity in host_ref if _role(entity) in PURE_SLEEVE_ROLES]).items()
    }
    donor_pieces = {pid: _largest_cluster(rows) for pid, rows in _group_by_piece(donor_rows).items()}
    host_order = sorted(host_pieces.items(), key=lambda item: _centroid_x(item[1]))
    body_wh = _mean_body_wh(host_ref) or _mean_body_wh(_annotate(donor_ir))
    donor_order = sorted(
        ((pid, rows) for pid, rows in donor_pieces.items() if _usable_sleeve_panel(rows, body_wh)),
        key=lambda item: _centroid_x(item[1]),
    )
    if not donor_order:
        return [], {}
    if not host_order:
        selected = [entity for _, rows in donor_order for entity in rows]
        return selected, {"mode": "no_host_sleeve"}
    selected: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for index, (host_pid, host_rows) in enumerate(host_order):
        donor_pid, donor_cluster = donor_order[index] if index < len(donor_order) else donor_order[0]
        reused = index >= len(donor_order)
        host_role = _role(host_rows[0]) if host_rows else "sleeve"
        for entity in donor_cluster:
            row = deepcopy(entity)
            if reused:
                row["entity_id"] = f"{row.get('entity_id')}:mirror{index}"
                row["piece_id"] = f"{row.get('piece_id')}:mirror{index}"
            row["_piece_role"] = host_role
            if row.get("piece_role"):
                row["piece_role"] = host_role
            selected.append(row)
        pairs.append({"host": host_pid, "donor": donor_pid, "role": host_role, "reused": reused})
    return selected, {"mode": "sleeve_pool", "pairs": pairs}


def _select_similar_donor_pieces(
    host_ref: list[dict[str, Any]],
    donor_ir: dict[str, Any],
    roles: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """For each role, keep the donor piece_id most similar to the host piece."""
    if roles and roles <= PURE_SLEEVE_ROLES:
        return _select_sleeve_pieces(host_ref, donor_ir)
    donor_rows = _entities_for_roles(donor_ir, roles)
    if not donor_rows:
        return [], {}
    host_body = _mean_body_wh(host_ref)
    donor_body = _mean_body_wh(_annotate(donor_ir))
    selected: list[dict[str, Any]] = []
    match_meta: dict[str, Any] = {}
    by_role: dict[str, list[dict[str, Any]]] = {}
    for entity in donor_rows:
        by_role.setdefault(_role(entity), []).append(entity)
    for role, rows in by_role.items():
        host_role = [entity for entity in host_ref if _role(entity) == role]
        host_primary = None
        host_ok = False
        if host_role:
            host_pieces = _group_by_piece(host_role)
            host_primary = max(
                host_pieces.values(),
                key=lambda pe: (
                    (lambda b: 0.0 if not b else (b[2] - b[0]) * (b[3] - b[1]))(_content_bounds(pe))
                ),
            )
            host_primary = _largest_cluster(host_primary)
            hb = bounds_of_entities(host_primary)
            if hb and host_body:
                host_ok = _sleeve_aspect_ok(hb[2] - hb[0], hb[3] - hb[1], host_body) if role in PURE_SLEEVE_ROLES else True
            else:
                host_ok = role not in PURE_SLEEVE_ROLES
        donor_pieces = _group_by_piece(rows)
        scored: list[tuple[float, str, list[dict[str, Any]]]] = []
        for piece_id, piece_rows in donor_pieces.items():
            cluster_rows = _largest_cluster(piece_rows)
            box = bounds_of_entities(cluster_rows)
            if host_primary and (host_ok or role not in PURE_SLEEVE_ROLES):
                sim = score_piece_to_host(host_primary, cluster_rows)
            else:
                # Host sleeve is nest/flat junk — prefer donor pieces with healthy
                # body-relative proportions instead of cloning the bad aspect.
                if not box:
                    sim = 0.0
                else:
                    sw, sh = box[2] - box[0], box[3] - box[1]
                    ref_body = donor_body or host_body
                    if ref_body and _sleeve_aspect_ok(sw, sh, ref_body):
                        sim = 0.75 + min(0.2, len(cluster_rows) / 200.0)
                    else:
                        area = max(0.0, sw) * max(0.0, sh)
                        sim = min(0.55, math.log1p(area) / 14.0) * min(1.0, len(cluster_rows) / 12.0)
            # Prefer sleeves that stay usable after uniform width-lock to host body.
            if role in PURE_SLEEVE_ROLES and box and host_body and (box[2] - box[0]) > 1e-3:
                pred_h = (box[3] - box[1]) * ((host_body[0] * 0.98) / (box[2] - box[0]))
                if 0.30 * host_body[1] <= pred_h <= 0.90 * host_body[1]:
                    sim += 0.18
                else:
                    sim -= 0.28
            scored.append((sim, piece_id, cluster_rows))
        scored.sort(key=lambda row: (-row[0], -len(row[2]), row[1]))
        best_sim, best_id, best_rows = scored[0]
        selected.extend(best_rows)
        match_meta[role] = {
            "piece_id": best_id,
            "similarity": round(best_sim, 5),
            "host_aspect_ok": host_ok if role in PURE_SLEEVE_ROLES else None,
            "candidates": [
                {"piece_id": piece_id, "similarity": round(sim, 5), "entity_count": len(piece_rows)}
                for sim, piece_id, piece_rows in scored[:5]
            ],
        }
    return selected, match_meta


def _primary_bounds(entities: list[dict[str, Any]]) -> list[float] | None:
    """Largest piece_id component — ignore far-away duplicates of the same role."""
    by_pid: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_pid.setdefault(str(entity.get("piece_id") or entity.get("entity_id") or "x"), []).append(entity)
    best: list[float] | None = None
    best_area = -1.0
    for rows in by_pid.values():
        box = _content_bounds(rows)
        if not box:
            continue
        area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        if area > best_area:
            best_area = area
            best = box
    return best


def _body_ref(entities: list[dict[str, Any]]) -> list[float] | None:
    body = [entity for entity in entities if _role(entity) in FRONT_ROLES | BACK_ROLES]
    return _primary_bounds(body)


def _mean_body_wh(entities: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Average primary front/back size so neither side alone drives scaling."""
    widths: list[float] = []
    heights: list[float] = []
    for role_set in (FRONT_ROLES, BACK_ROLES):
        rows = [entity for entity in entities if _role(entity) in role_set]
        box = _primary_bounds(rows)
        if not box:
            continue
        widths.append(max(box[2] - box[0], 1.0))
        heights.append(max(box[3] - box[1], 1.0))
    if not widths:
        body = _body_ref(entities)
        if not body:
            return None
        return max(body[2] - body[0], 1.0), max(body[3] - body[1], 1.0)
    return sum(widths) / len(widths), sum(heights) / len(heights)


def _scale_pieces(
    entities: list[dict[str, Any]],
    *,
    roles: set[str],
    sx: float,
    sy: float,
    anchor: str = "top",
) -> list[dict[str, Any]]:
    """Apply the same sx/sy to every piece_id in roles (front/back stay locked)."""
    sx = max(0.2, min(5.0, sx))
    sy = max(0.2, min(5.0, sy))
    out: list[dict[str, Any]] = []
    by_piece: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entity in entities:
        role = _role(entity)
        key = (role, str(entity.get("piece_id") or entity.get("entity_id") or "x"))
        by_piece.setdefault(key, []).append(entity)
    for (role, _), rows in by_piece.items():
        if role in roles:
            out.extend(_scale_group(rows, sx=sx, sy=sy, anchor=anchor))
        else:
            out.extend(rows)
    return out


def _poly_len(points: list[Any]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(points)):
        x0, y0 = float(points[i - 1][0]), float(points[i - 1][1])
        x1, y1 = float(points[i][0]), float(points[i][1])
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def _armhole_length(entities: list[dict[str, Any]], *, on_sleeve: bool = False) -> float:
    """One-arm armscye length. Max-per-role avoids cut/net and L/R double counting."""
    if on_sleeve:
        # Prefer true cap split (armhole_front/back on sleeve). Ignore flat mislabeled sleeve_cap.
        split_roles = {"armhole_front", "armhole_back"}
        by_split: dict[str, list[float]] = {}
        for entity in entities:
            lr = _line_role(entity)
            if lr not in split_roles or not _is_usable_sleeve_interface(entity):
                continue
            by_split.setdefault(lr, []).append(_poly_len((entity.get("geometry") or {}).get("points") or []))
        if by_split:
            return sum(max(vals) for vals in by_split.values())
        roles = {"sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "armhole_seam"}
    else:
        roles = {
            "armhole_front",
            "armhole_back",
            "armhole_seam",
        }
    by_role: dict[str, list[float]] = {}
    for entity in entities:
        lr = _line_role(entity)
        if lr not in roles:
            continue
        pts = (entity.get("geometry") or {}).get("points") or []
        length = _poly_len(pts)
        if on_sleeve and lr in SLEEVE_CAP_ROLES and not _is_usable_sleeve_interface(entity):
            continue
        if length > 1.0:
            by_role.setdefault(lr, []).append(length)
    return sum(max(vals) for vals in by_role.values()) if by_role else 0.0


def _sleeve_body_target(body_wh: tuple[float, float]) -> tuple[float, float]:
    """Fallback sleeve slot when the host has no usable sleeve panel."""
    bw, bh = body_wh
    return bw * 0.55, bh * 0.38


def _host_sleeve_wh(host_ref: list[dict[str, Any]], body_wh: tuple[float, float]) -> tuple[float, float]:
    """Host sleeve content size after nest prune; otherwise a short-tee slot."""
    bw, bh = body_wh
    canon = _sleeve_body_target(body_wh)
    sleeve_rows = [entity for entity in host_ref if _role(entity) in PURE_SLEEVE_ROLES]
    box = _primary_bounds(sleeve_rows)
    if box:
        sw, sh = max(box[2] - box[0], 1.0), max(box[3] - box[1], 1.0)
        if min(sw, sh) >= 18.0 and sw <= bw * 1.15 and sh <= bh * 1.10:
            return sw, sh
    return canon


def _sleeve_aspect_ok(width: float, height: float, body_wh: tuple[float, float]) -> bool:
    bw, bh = body_wh
    if bw <= 0 or bh <= 0:
        return False
    return 0.45 <= (width / bw) <= 1.35 and 0.28 <= (height / bh) <= 0.95


def _clean_sleeve_rows(piece_rows: list[dict[str, Any]], *, preview: bool = True) -> list[dict[str, Any]]:
    """Drop body-pollution lines that sometimes land on sleeve piece_ids in nests."""
    kept = []
    for entity in piece_rows:
        lr = _line_role(entity)
        if lr and lr not in SLEEVE_LINE_KEEP and not lr.startswith("sleeve"):
            continue
        kept.append(entity)
    cleaned = _largest_cluster(kept) if kept else []
    box = bounds_of_entities(cleaned)
    if not cleaned or not box or min(box[2] - box[0], box[3] - box[1]) < 12.0:
        cleaned = _largest_cluster(piece_rows)
    cut_len = sum(_entity_poly_len(entity) for entity in cleaned if _line_role(entity) == "cut_line")
    # Prefer a single cut outline for preview; labeled/seam scraps duplicate or jagged-hem the sleeve.
    if preview and cut_len >= 180.0:
        cleaned = [
            entity
            for entity in cleaned
            if _line_role(entity) in {"cut", "cut_line", "sew", "internal", "grainline", "notch"}
        ]
    # One grainline is enough for preview; duplicates look like double axes.
    grain = [entity for entity in cleaned if _line_role(entity) == "grainline"]
    if len(grain) > 1:
        best = max(grain, key=_entity_poly_len)
        cleaned = [entity for entity in cleaned if _line_role(entity) != "grainline" or entity is best]
    return cleaned


def _scale_sleeve_to_body(
    piece_rows: list[dict[str, Any]],
    body_entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Uniform scale sleeve so armscye length matches body (fallback: body width)."""
    # Measure interface on role-filtered geometry, then emit a clean preview silhouette.
    measure_rows = _clean_sleeve_rows(piece_rows, preview=False)
    cluster = _clean_sleeve_rows(piece_rows, preview=True)
    outline = [
        entity
        for entity in measure_rows
        if str(entity.get("line_role") or entity.get("edge_role") or "").lower()
        not in {"construction", "grainline", "notch", "pleat_line"}
    ]
    size_rows = outline or measure_rows
    box = bounds_of_entities(cluster) or bounds_of_entities(size_rows)
    if not box:
        return piece_rows
    body_ah = _armhole_length(body_entities, on_sleeve=False)
    sleeve_ah = _armhole_length(size_rows, on_sleeve=True)
    body_wh = _mean_body_wh(body_entities)
    cw = max(box[2] - box[0], 1e-3)
    ch = max(box[3] - box[1], 1e-3)
    if min(cw, ch) < 12.0:
        return piece_rows
    if body_ah > 40.0 and sleeve_ah > 40.0:
        s = body_ah / sleeve_ah
    elif body_wh:
        tw, th = _sleeve_body_target(body_wh)
        s = tw / cw
        if ch * s > th * 1.35:
            s = min(s, (th * 1.15) / ch)
    else:
        return cluster
    s = max(0.35, min(2.8, s))
    if body_wh:
        out_w, out_h = cw * s, ch * s
        # Short-tee preview: reject absurdly large panels from long-sleeve/wrong clusters.
        if out_h > body_wh[1] * 0.90 or out_w > body_wh[0] * 1.25:
            s = min(s, (body_wh[0] * 0.98) / cw, (body_wh[1] * 0.70) / ch)
            s = max(0.35, min(2.8, s))
        elif out_w < body_wh[0] * 0.55:
            s = max(s, (body_wh[0] * 0.70) / cw)
            s = min(s, 2.8)
    ox = (box[0] + box[2]) / 2.0
    oy = (box[1] + box[3]) / 2.0
    scaled = [transform_entity(entity, sx=s, sy=s, ox=ox, oy=oy) for entity in cluster]
    return _largest_cluster(scaled)


def _scale_uniform_to_target(
    piece_rows: list[dict[str, Any]],
    tw: float,
    th: float,
    *,
    anchor: str = "center",
    mode: str = "geom",
) -> list[dict[str, Any]]:
    """Uniform scale toward target box (non-sleeve roles)."""
    box = bounds_of_entities(piece_rows)
    if not box:
        return piece_rows
    cw, ch = max(box[2] - box[0], 1e-3), max(box[3] - box[1], 1e-3)
    if min(cw, ch) < 5.0:
        base = cw if cw >= ch else ch
        target = tw if cw >= ch else th
        s = max(0.15, min(6.0, target / base))
        return _scale_group(piece_rows, sx=s, sy=s, anchor=anchor)
    if mode == "width":
        s = tw / cw
        out_h = ch * s
        if out_h > th * 1.35:
            s = min(s, (th * 1.25) / ch)
        s = max(0.15, min(6.0, s))
    else:
        s = math.sqrt((tw / cw) * (th / ch))
        s = max(0.15, min(6.0, s))
    return _scale_group(piece_rows, sx=s, sy=s, anchor=anchor)


def _scale_roles_to_host(
    entities: list[dict[str, Any]],
    roles: set[str],
    host_ref: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match swapped pieces to host using one shared scale (no per-piece squash)."""
    host_body = _mean_body_wh(host_ref) or _mean_body_wh(entities)
    if not host_body or not roles:
        return entities
    bw, bh = host_body
    body_roles = roles & BODY_ROLES
    if body_roles:
        donor_wh = _mean_body_wh([entity for entity in entities if _role(entity) in body_roles])
        if donor_wh:
            entities = _scale_pieces(
                entities,
                roles=body_roles,
                sx=bw / donor_wh[0],
                sy=bh / donor_wh[1],
                anchor="top",
            )

    body_entities = [entity for entity in entities if _role(entity) in BODY_ROLES] or [
        entity for entity in host_ref if _role(entity) in BODY_ROLES
    ]
    out: list[dict[str, Any]] = []
    by_role: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_role.setdefault(_role(entity), []).append(entity)
    for role, rows in by_role.items():
        if role not in roles or role in BODY_ROLES:
            out.extend(rows)
            continue
        if role in PURE_SLEEVE_ROLES:
            tw, th = _host_sleeve_wh(host_ref, host_body)
            for piece_rows in _group_by_piece(rows).values():
                dw, dh = _piece_wh(piece_rows)
                if dw > 1 and dh / dw > th / max(tw, 1.0):
                    th = dh * (tw / dw)
                out.extend(_scale_uniform_to_target(piece_rows, tw, th, anchor="top"))
            continue
        if role in COLLAR_ROLES:
            tw, th = bw * 0.85, max(bh * 0.06, 28.0)
        elif role in CUFF_ROLES:
            tw, th = bw * 0.45, max(bh * 0.1, 40.0)
        else:
            tw, th = bw, bh
        for piece_rows in _group_by_piece(rows).values():
            out.extend(_scale_uniform_to_target(piece_rows, tw, th))
    return out


def _clamp_insane_roles(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shrink nest-scale outliers. Do not retarget sleeves to armscye."""
    body_wh = _mean_body_wh(entities)
    if not body_wh:
        return entities
    bw, bh = body_wh
    body_max = max(bw, bh)
    out: list[dict[str, Any]] = []
    by_role: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_role.setdefault(_role(entity), []).append(entity)
    for role, rows in by_role.items():
        if role in BODY_ROLES:
            out.extend(rows)
            continue
        # Unmatched DXF production lines: show as-is, never shrink/scale.
        if role == "reference" or any(entity.get("_display_only") for entity in rows):
            out.extend(rows)
            continue
        for piece_rows in _group_by_piece(rows).values():
            box = bounds_of_entities(piece_rows)
            if not box:
                out.extend(piece_rows)
                continue
            cw, ch = box[2] - box[0], box[3] - box[1]
            if max(cw, ch) <= body_max * 2.0:
                out.extend(piece_rows)
                continue
            if role in COLLAR_ROLES:
                tw, th = bw * 0.85, max(bh * 0.06, 28.0)
            elif role in PURE_SLEEVE_ROLES:
                tw, th = _host_sleeve_wh(entities, body_wh)
            else:
                tw, th = bw, bh
            out.extend(_scale_uniform_to_target(piece_rows, tw, th))
    return out


def _harmonize_sleeves_to_body(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Final pass: uniform scale sleeves so armscye matches body."""
    body_entities = [entity for entity in entities if _role(entity) in BODY_ROLES]
    if not body_entities:
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
            out.extend(_scale_sleeve_to_body(piece_rows, body_entities))
    return out


def _replace_roles(
    host: list[dict[str, Any]],
    donor_ir: dict[str, Any],
    roles: set[str],
    host_ref: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    ref = host_ref or host
    donor_rows, piece_match = _select_similar_donor_pieces(ref, donor_ir, roles)
    if not donor_rows:
        return host, 0, {}
    donor_case = str(donor_ir.get("case_id") or "donor")
    inserted = []
    for entity in donor_rows:
        copied = deepcopy(entity)
        eid = str(copied.get("entity_id") or "")
        pid = str(copied.get("piece_id") or "")
        if eid and not eid.startswith(f"{donor_case}:"):
            copied["entity_id"] = f"{donor_case}:{eid}"
        if pid and not pid.startswith(f"{donor_case}:"):
            copied["piece_id"] = f"{donor_case}:{pid}"
        copied["_source_case"] = donor_case
        inserted.append(copied)
    inserted = _stamp_edge_roles(inserted, donor_ir)
    kept = [entity for entity in host if _role(entity) not in roles]
    merged = kept + inserted
    merged = _scale_roles_to_host(merged, roles, ref)
    return merged, len(inserted), piece_match


def _pick_donor(
    group: str,
    host_ir: dict[str, Any],
    donor_index: dict[str, dict[str, Any]],
    option_id: str | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    rows = rank_donors(group, host_ir, donor_index, max_donors=8, target_option_id=option_id)
    payload = [
        {"case_id": row.case_id, "score": row.score, "breakdown": row.breakdown, "reasons": list(row.reasons)}
        for row in rows
    ]
    if not rows:
        return None, payload
    return donor_index.get(rows[0].case_id), payload


def _result(
    operation_id: str,
    group: str,
    status: str,
    *,
    option_id: str | None = None,
    donor_case_id: str | None = None,
    modified: tuple[str, ...] = (),
    issue: ValidationIssue | None = None,
    extra: dict[str, Any] | None = None,
) -> ComponentResult:
    return ComponentResult(
        operation_id=operation_id,
        group=group,
        status=status,  # type: ignore[arg-type]
        donor_case_id=donor_case_id,
        option_id=option_id,
        modified_entity_ids=modified,
        protected_entity_hashes={},
        validation_issues=tuple([issue] if issue else []),
        review_required=True,
        provenance={"operator": "simple-piece-swap.v1", **(extra or {})},
    )


def _scale_group(entities: list[dict[str, Any]], *, sx: float, sy: float, anchor: str = "center") -> list[dict[str, Any]]:
    box = bounds_of_entities(entities)
    if not box:
        return entities
    ox = (box[0] + box[2]) / 2.0
    if anchor == "top":
        oy = box[3]  # CAD Y-up: keep shoulder/neck band fixed
    elif anchor == "bottom":
        oy = box[1]
    else:
        oy = (box[1] + box[3]) / 2.0
    return [transform_entity(entity, sx=sx, sy=sy, ox=ox, oy=oy) for entity in entities]


def _fit_relative_to_host(
    entities: list[dict[str, Any]],
    profile: dict[str, Any],
    length_slug: str | None,
    host_ref: list[dict[str, Any]] | None = None,
    *,
    fit_sleeves: bool = False,
    knit: bool = False,
    sleeve_slug: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep native IR/DXF size; grade structure points from the host base.

    Chest lets out side seams; length grows below the chest line; neck is
    independent. T-shirt sleeves use knit cap-to-armhole; shirts keep bbox fit.
    """
    from shirt_side_seam import grade_body_structure
    from shirt_sleeve_fit import fit_knit_sleeves, fit_sleeves_to_armholes

    length_factor = LENGTH_FACTOR.get(str(length_slug or "regular"), 1.0)
    body_sx = float(profile.get("width") or 1.0)
    body_sy = float(profile.get("length") or 1.0) * length_factor
    sleeve_sy = float(profile.get("sleeve_length") or 1.0)
    neck_s = float(profile.get("neck") or 1.0)
    anchor = _mean_body_wh(host_ref or []) or _mean_body_wh(entities)
    meta = {
        "mode": "relative_host_dxf",
        "body_sx": round(body_sx, 5),
        "body_sy": round(body_sy, 5),
        "sleeve_sy": round(sleeve_sy, 5),
        "neck_s": round(neck_s, 5),
        "length_factor": length_factor,
        "fit_sleeves": bool(fit_sleeves or knit),
        "knit": bool(knit),
        "host_width_mm": round(anchor[0], 2) if anchor else None,
        "host_height_mm": round(anchor[1], 2) if anchor else None,
    }
    entities, grade_meta = grade_body_structure(
        entities, width_sx=body_sx, length_sy=body_sy, neck_s=neck_s,
    )
    meta["grade"] = grade_meta
    if abs(neck_s - 1.0) >= 1e-6:
        entities = _scale_pieces(entities, roles=COLLAR_ROLES, sx=neck_s, sy=neck_s, anchor="center")
    if knit:
        entities, sleeve_fit = fit_knit_sleeves(entities, sleeve_sy=sleeve_sy, slug=sleeve_slug)
        meta["sleeve_armhole_fit"] = sleeve_fit
        if not sleeve_fit.get("applied") and abs(sleeve_sy - 1.0) >= 1e-6:
            entities = _scale_pieces(entities, roles=PURE_SLEEVE_ROLES, sx=1.0, sy=sleeve_sy, anchor="top")
        return entities, meta
    if fit_sleeves:
        entities, sleeve_fit = fit_sleeves_to_armholes(entities)
        meta["sleeve_armhole_fit"] = sleeve_fit
    if abs(sleeve_sy - 1.0) >= 1e-6:
        entities = _scale_pieces(entities, roles=PURE_SLEEVE_ROLES, sx=1.0, sy=sleeve_sy, anchor="top")
    return entities, meta


def compose_simple(
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

    selections = recipe.get("selections") or {}
    base_option_ids = recipe.get("base_option_ids") or {}
    donor_index = {case_id: ir for case_id, ir in index.items() if case_id != base_case_id}
    entities = _annotate(base_ir)
    if not any((entity.get("source") or {}).get("origin") == "compose_ir" for entity in entities):
        entities = _keep_largest_clusters(entities)
    host_ref = list(entities)  # fixed size reference so swaps don't drift
    results: list[ComponentResult] = []
    sources: dict[str, Any] = {"base": base_case_id}
    versions: list[dict[str, Any]] = [{"id": "original", "label": "原纸样", "entities": deepcopy(entities)}]

    # 1) Neckline / collar
    for group in ("neckline", "collar"):
        option_id = selections.get(group)
        if not option_id or option_id == base_option_ids.get(group):
            continue
        if group == "collar" and family == "tshirt":
            continue
        if group == "neckline" and family == "shirt" and selections.get("collar"):
            continue
        donor_ir, candidates = _pick_donor(group, base_ir, donor_index, option_id)
        before_ids = {str(entity.get("entity_id")) for entity in entities}
        slug = _option_slug(option_id)
        piece_match: dict[str, Any] = {}
        count = 0
        if family == "tshirt" and group == "neckline" and slug in NECKLINE_BODY_SWAP_SLUGS and donor_ir:
            swap_roles = FRONT_ROLES | BACK_ROLES | (COLLAR_ROLES - BODY_ROLES)
            entities, count, piece_match = _replace_roles(entities, donor_ir, swap_roles, host_ref=host_ref)
            if count > 0:
                modified = tuple(
                    str(entity.get("entity_id")) for entity in entities if str(entity.get("entity_id")) not in before_ids
                )
                sources[group] = {
                    "case_id": donor_ir.get("case_id"),
                    "option_id": option_id,
                    "mode": "piece_swap_similarity",
                    "piece_match": piece_match,
                }
                results.append(_result(
                    f"op:{group}", group, "applied", option_id=option_id, donor_case_id=str(donor_ir.get("case_id")),
                    modified=modified,
                    extra={
                        "donor_candidates": candidates,
                        "mode": "simple_piece_swap",
                        "strategy": "body_piece_swap",
                        "replaced_roles": sorted(swap_roles),
                        "piece_match": piece_match,
                    },
                ))
                continue
        if family == "tshirt" and group == "neckline":
            entities, neck_meta = reshape_body_neckline(entities, {**base_ir, "atomic_entities": entities}, slug)
            if donor_ir:
                entities, att_count, piece_match = _replace_roles(
                    entities, donor_ir, COLLAR_ROLES - BODY_ROLES, host_ref=host_ref
                )
                count = att_count
            sources[group] = {
                "case_id": (donor_ir or {}).get("case_id") if donor_ir else base_case_id,
                "option_id": option_id,
                "mode": "neckline_edge_reshape",
                "neck_meta": neck_meta,
                "piece_match": piece_match,
            }
            applied = bool(neck_meta.get("applied")) or count > 0
            modified = tuple(
                str(entity.get("entity_id"))
                for entity in entities
                if str(entity.get("entity_id")) not in before_ids
                or str(entity.get("entity_id")) in set(neck_meta.get("modified_entity_ids") or [])
            )
            results.append(_result(
                f"op:{group}", group, "applied" if applied else "retained_current",
                option_id=option_id,
                donor_case_id=str((donor_ir or {}).get("case_id") or "") or None,
                modified=modified,
                issue=None if applied else ValidationIssue(
                    code="neckline_edge_unavailable",
                    severity="warning",
                    message="host neckline edge reshape not applied",
                    operation_id=f"op:{group}",
                ),
                extra={
                    "donor_candidates": candidates,
                    "mode": "neckline_edge_reshape",
                    "neck_meta": neck_meta,
                    "replaced_roles": sorted(COLLAR_ROLES - BODY_ROLES),
                    "strategy": "edge_only",
                },
            ))
            continue

        # Shirt collar path: still piece-level for collar + related body if needed.
        roles = COLLAR_ROLES | FRONT_ROLES | BACK_ROLES
        if not donor_ir:
            results.append(_result(
                f"op:{group}", group, "retained_current", option_id=option_id,
                issue=ValidationIssue(code="donor_unavailable", severity="warning", message=f"no donor for {group}", operation_id=f"op:{group}"),
                extra={"donor_candidates": candidates, "mode": "simple_piece_swap"},
            ))
            continue
        entities, count, piece_match = _replace_roles(entities, donor_ir, roles, host_ref=host_ref)
        if count <= 0:
            results.append(_result(
                f"op:{group}", group, "retained_current", option_id=option_id, donor_case_id=str(donor_ir.get("case_id")),
                issue=ValidationIssue(code="donor_pieces_missing", severity="warning", message=f"donor has no {group} pieces", operation_id=f"op:{group}"),
                extra={"donor_candidates": candidates, "mode": "simple_piece_swap"},
            ))
            continue
        modified = tuple(str(entity.get("entity_id")) for entity in entities if str(entity.get("entity_id")) not in before_ids)
        top_score = candidates[0]["score"] if candidates else None
        sources[group] = {
            "case_id": donor_ir.get("case_id"),
            "option_id": option_id,
            "mode": "piece_swap_similarity",
            "donor_score": top_score,
            "piece_match": piece_match,
        }
        results.append(_result(
            f"op:{group}", group, "applied", option_id=option_id, donor_case_id=str(donor_ir.get("case_id")),
            modified=modified,
            extra={
                "donor_candidates": candidates,
                "mode": "simple_piece_swap",
                "size_matched": True,
                "similarity_matched": True,
                "donor_score": top_score,
                "piece_match": piece_match,
                "replaced_roles": sorted(roles),
                "inserted": count,
            },
        ))

    if sources.get("neckline") or sources.get("collar"):
        versions.append({"id": "neckline", "label": "领口", "entities": deepcopy(entities)})

    # 2) Sleeve / cuff — strategy depends on sleeve slug (连接方式)
    for group in ("sleeve", "cuff"):
        option_id = selections.get(group)
        if not option_id or option_id == base_option_ids.get(group):
            continue
        if group == "cuff":
            swap_roles = CUFF_ROLES
            plan = {"mode": "cuff_only", "roles": CUFF_ROLES, "slug": _option_slug(option_id)}
        else:
            override = recipe.get("strategy_override")
            if isinstance(override, dict) and override.get("mode"):
                mode = str(override.get("mode"))
                roles_raw = override.get("roles")
                if isinstance(roles_raw, (list, tuple, set)) and roles_raw:
                    swap_set = set(str(r) for r in roles_raw)
                elif mode == "body_and_sleeve":
                    swap_set = BODY_ROLES | PURE_SLEEVE_ROLES
                elif mode == "body_integrated":
                    swap_set = set(BODY_ROLES)
                else:
                    swap_set = set(PURE_SLEEVE_ROLES)
                plan = {
                    "mode": mode,
                    "roles": swap_set,
                    "drop_host_sleeves": bool(override.get("drop_host_sleeves")) or mode == "body_integrated",
                    "slug": override.get("slug") or _option_slug(option_id),
                    "source": override.get("source") or "override",
                    "reason": override.get("reason"),
                }
            else:
                plan = _sleeve_plan(option_id)
            swap_roles = set(plan["roles"])
        donor_ir, candidates = _pick_donor(group if group == "cuff" else "sleeve", base_ir, donor_index, option_id)
        if not donor_ir:
            results.append(_result(
                f"op:{group}", group, "retained_current", option_id=option_id,
                issue=ValidationIssue(code="donor_unavailable", severity="warning", message=f"no donor for {group}", operation_id=f"op:{group}"),
                extra={"donor_candidates": candidates, "mode": "simple_piece_swap", "strategy": _public_plan(plan)},
            ))
            continue

        before_ids = {str(entity.get("entity_id")) for entity in entities}
        # flutter: drop independent host sleeves first
        if plan.get("drop_host_sleeves"):
            entities = [entity for entity in entities if _role(entity) not in PURE_SLEEVE_ROLES | CUFF_ROLES]

        entities, count, piece_match = _replace_roles(entities, donor_ir, swap_roles, host_ref=host_ref)
        # After body+sleeve raglan swap, host sleeves already replaced; ensure no leftover set-in scraps.
        if plan.get("mode") == "body_and_sleeve":
            # Keep only sleeves that came from the donor (or none if donor sleeveless-integrated).
            donor_case = str(donor_ir.get("case_id") or "")
            kept = []
            for entity in entities:
                if _role(entity) not in PURE_SLEEVE_ROLES:
                    kept.append(entity)
                    continue
                if donor_case and donor_case in str(entity.get("_source_case") or entity.get("piece_id") or ""):
                    kept.append(entity)
            entities = kept

        if count <= 0 and not plan.get("drop_host_sleeves"):
            results.append(_result(
                f"op:{group}", group, "retained_current", option_id=option_id, donor_case_id=str(donor_ir.get("case_id")),
                issue=ValidationIssue(code="donor_pieces_missing", severity="warning", message=f"donor has no {group} pieces", operation_id=f"op:{group}"),
                extra={"donor_candidates": candidates, "mode": "simple_piece_swap", "strategy": _public_plan(plan)},
            ))
            continue
        modified = tuple(str(entity.get("entity_id")) for entity in entities if str(entity.get("entity_id")) not in before_ids)
        top_score = candidates[0]["score"] if candidates else None
        sources[group] = {
            "case_id": donor_ir.get("case_id"),
            "option_id": option_id,
            "mode": "piece_swap_similarity",
            "strategy": _public_plan(plan),
            "donor_score": top_score,
            "piece_match": piece_match,
        }
        results.append(_result(
            f"op:{group}", group, "applied", option_id=option_id, donor_case_id=str(donor_ir.get("case_id")),
            modified=modified,
            extra={
                "donor_candidates": candidates,
                "mode": "simple_piece_swap",
                "strategy": _public_plan(plan),
                "size_matched": True,
                "similarity_matched": True,
                "donor_score": top_score,
                "piece_match": piece_match,
                "replaced_roles": sorted(swap_roles),
                "inserted": count,
            },
        ))

    if sources.get("sleeve"):
        versions.append({"id": "sleeve", "label": "换袖", "entities": deepcopy(entities)})
    if sources.get("cuff"):
        versions.append({"id": "cuff", "label": "换袖口", "entities": deepcopy(entities)})

    # Filter scrap first so measurement fit isn't skewed by construction clutter.
    entities = filter_preview_entities(entities)
    if not any((entity.get("source") or {}).get("origin") == "compose_ir" for entity in entities):
        entities = _keep_largest_clusters(entities)
        entities = _normalize_physical_components(entities)
    entities = _clamp_insane_roles(entities)

    # 3) Relative grading from native host DXF/IR size (front/back share one sx/sy).
    profile = grading_profile(recipe)
    # Always apply the selected length slug (do not require diff vs base labels).
    length_option = selections.get("garment_length")
    length_slug = str(length_option or "x.regular").split(".")[-1]
    length_factor = LENGTH_FACTOR.get(length_slug, 1.0)
    before = deepcopy(entities)
    sleeve_swapped = (sources.get("sleeve") or {}).get("mode") in {
        "piece_swap", "piece_swap_similarity", "simple_piece_swap",
    }
    entities, body_fit = _fit_relative_to_host(
        entities,
        profile,
        length_slug,
        host_ref=host_ref,
        fit_sleeves=sleeve_swapped,
        knit=family == "tshirt",
        sleeve_slug=_option_slug(selections.get("sleeve") or base_option_ids.get("sleeve")),
    )
    sources["sizing"] = {**body_fit, "fit": profile.get("fit"), "prototype": profile.get("prototype")}

    if length_factor != 1.0:
        modified = tuple(
            str(entity.get("entity_id"))
            for entity, prev in zip(entities, before)
            if entity.get("entity_id") and entity.get("geometry") != prev.get("geometry")
        )
        sources["garment_length"] = {
            "option_id": length_option,
            "mode": "relative_host_dxf",
            "factor": length_factor,
            **body_fit,
        }
        results.append(_result(
            "op:garment_length", "garment_length", "applied" if modified else "retained_current",
            option_id=length_option, modified=modified,
            extra={"mode": "relative_host_dxf", "length_factor": length_factor, **body_fit},
        ))

    if (body_fit.get("grade") or {}).get("applied") and not recipe.get("skip_grading"):
        versions.append({"id": "grade", "label": "放码", "entities": deepcopy(entities)})
    gap = 52.0 if recipe.get("compact_layout") else 90.0
    laid_versions = [
        {"id": row["id"], "label": row["label"], "entities": _layout_complete(row["entities"], gap=gap)}
        for row in versions
    ]
    wanted = str(recipe.get("compose_version") or laid_versions[-1]["id"])
    chosen = next((row for row in laid_versions if row["id"] == wanted), laid_versions[-1])
    laid_out = chosen["entities"]
    interface_meta: dict[str, Any] = {}
    neck_src = sources.get("neckline") or sources.get("collar")
    if isinstance(neck_src, dict):
        neck_meta = dict(neck_src.get("neck_meta") or {})
        swapped = str(neck_src.get("mode") or "") in {"piece_swap_similarity", "simple_piece_swap"}
        if neck_meta.get("applied") or swapped:
            interface_meta["body_neckline"] = {**neck_meta, "applied": True}
    validation = _validate(family, laid_out, interface_meta, sources)
    validation["standard"] = "simple_piece_swap_trial"
    canonical = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    recipe_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    component_payload = [
        {
            "operation_id": row.operation_id,
            "group": row.group,
            "status": row.status,
            "donor_case_id": row.donor_case_id,
            "option_id": row.option_id,
            "modified_entity_ids": list(row.modified_entity_ids),
            "validation_issues": [
                {"code": issue.code, "severity": issue.severity, "message": issue.message, "operation_id": issue.operation_id}
                for issue in row.validation_issues
            ],
            "review_required": row.review_required,
            "provenance": row.provenance,
        }
        for row in results
    ]
    meta = {
        "recipe_hash": recipe_hash,
        "family": family,
        "pipeline": "tshirt.simple_piece_swap.v2",
        "execution_mode": "simple_piece_swap",
        "sizing_profile": profile,
        "source_measurements": source_measurements(base_ir),
        "tryon_descriptor": build_tryon_descriptor(laid_out, recipe_hash, family),
        "sources": sources,
        "pieces": _piece_summary(laid_out),
        "paper_info": _paper_info(laid_out),
        "validation": validation,
        "replacement_candidates": {},
        "status": "valid" if validation.get("valid") else "invalid",
        "version_id": chosen["id"],
        "versions": [{"id": row["id"], "label": row["label"]} for row in laid_versions],
        "_version_entities": {row["id"]: row["entities"] for row in laid_versions},
        "batch_plan": {"operations": [{"group": row.group, "option_id": row.option_id} for row in results]},
        "component_results": component_payload,
        "review_required": bool(results),
        "review_ledger": {
            "schema": "chi27.review-ledger.simple-piece-swap.v1",
            "trial_status": "auto_validated_trial",
            "human_review_required": bool(results),
            "operations": component_payload,
        },
    }
    return laid_out, meta
