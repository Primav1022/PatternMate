"""Local interface-arc morph: only deform seam/interface curves; lock the rest."""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Iterable

from geometry_ops import entity_length, entity_points, polyline_length


Point = list[float]

ARMHOLE_ROLES = {
    "armhole", "armhole_front", "armhole_back", "armhole_seam",
}
SLEEVE_CAP_ROLES = {
    "sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "sleeve_head",
}
NECKLINE_ROLES = {
    "neckline", "front_neckline", "back_neckline", "collar_edge", "neck_binding_line",
}
# underarm on sleeve is often part of armhole join; treat carefully
INTERFACE_ROLE_GROUPS = {
    "armhole": ARMHOLE_ROLES | {"underarm"},
    "sleeve_cap": SLEEVE_CAP_ROLES,
    "neckline": NECKLINE_ROLES,
}


def _chain_entity_ids(ir: dict, roles: set[str], piece_ids: set[str] | None = None) -> list[str]:
    ids: list[str] = []
    seen = set()
    for chain in ir.get("edge_chains") or []:
        if (chain.get("edge_role") or "") not in roles:
            continue
        if piece_ids is not None and chain.get("piece_id") not in piece_ids:
            continue
        for eid in chain.get("ordered_entity_ids") or []:
            if eid and eid not in seen:
                seen.add(eid)
                ids.append(eid)
    return ids


def interface_entity_ids(ir: dict, group: str, piece_roles: set[str] | None = None) -> list[str]:
    roles = INTERFACE_ROLE_GROUPS[group]
    piece_ids = None
    if piece_roles is not None:
        piece_ids = {
            p["piece_id"] for p in ir.get("piece_instances") or []
            if p.get("piece_role") in piece_roles
        }
    ids = _chain_entity_ids(ir, roles, piece_ids)
    if ids:
        return ids
    # fallback: atomic line_role contains keyword
    keys = tuple(roles)
    out = []
    for atom in ir.get("atomic_entities") or []:
        lr = atom.get("line_role") or ""
        if piece_ids is not None and atom.get("piece_id") not in piece_ids:
            continue
        if lr in roles or any(k in lr for k in keys):
            eid = atom.get("entity_id")
            if eid:
                out.append(eid)
    return out


def morph_polyline_keep_ends(points: list[Point], target_len: float) -> list[Point]:
    """Morph polyline toward target_len.

    Stage A: fixed-end bulge scale (preferred, preserves junctions).
    Stage B: if still off (esp. need shrink below chord), soft radial scale
             from centroid with end weight < mid weight, then caller may snap ends.
    """
    if len(points) < 2:
        return [p[:] for p in points]
    cur0 = polyline_length(points)
    if cur0 < 1e-9:
        return [p[:] for p in points]

    a = points[0][:]
    b = points[-1][:]
    chord = [b[0] - a[0], b[1] - a[1]]
    clen = math.hypot(chord[0], chord[1]) or 1.0
    ux, uy = chord[0] / clen, chord[1] / clen

    def bulge(scale: float) -> list[Point]:
        if len(points) == 2:
            return [a[:], b[:]]
        out = [a[:]]
        for p in points[1:-1]:
            vx, vy = p[0] - a[0], p[1] - a[1]
            t = vx * ux + vy * uy
            cx, cy = a[0] + ux * t, a[1] + uy * t
            ox, oy = p[0] - cx, p[1] - cy
            out.append([cx + ox * scale, cy + oy * scale])
        out.append(b[:])
        return out

    # nearly straight + need longer → add normal bulge
    if len(points) >= 2 and abs(cur0 - clen) < 1e-3 * max(clen, 1.0) and target_len > cur0 * 1.01:
        mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
        nx, ny = -uy, ux
        h = max(0.0, target_len - cur0) / 2.0
        return [a[:], [mid[0] + nx * h, mid[1] + ny * h], b[:]]

    lo, hi = 0.0, 4.0
    for _ in range(8):
        if polyline_length(bulge(hi)) >= target_len:
            break
        hi *= 1.5
    for _ in range(32):
        mid_s = (lo + hi) / 2.0
        if polyline_length(bulge(mid_s)) < target_len:
            lo = mid_s
        else:
            hi = mid_s
    cand = bulge((lo + hi) / 2.0)
    err = abs(polyline_length(cand) - target_len) / max(target_len, 1e-6)
    if err <= 0.03:
        return cand

    # Soft radial morph from centroid (allows shrink below chord; ends move a little)
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    n = len(points)

    def soft(scale: float) -> list[Point]:
        out = []
        for i, p in enumerate(points):
            # ends weight 0.35, middle weight 1.0
            t = 0.0 if n == 1 else i / (n - 1)
            w = 0.35 + 0.65 * math.sin(math.pi * t)
            out.append([cx + (p[0] - cx) * (1 + (scale - 1) * w), cy + (p[1] - cy) * (1 + (scale - 1) * w)])
        return out

    lo, hi = 0.2, 2.5
    # direction
    if polyline_length(soft(1.0)) < target_len:
        for _ in range(8):
            if polyline_length(soft(hi)) >= target_len:
                break
            hi *= 1.4
    for _ in range(36):
        mid_s = (lo + hi) / 2.0
        if polyline_length(soft(mid_s)) < target_len:
            lo = mid_s
        else:
            hi = mid_s
    return soft((lo + hi) / 2.0)


def snap_interface_ends_to_locked(entities: list[dict], interface_ids: set[str], *, max_snap: float = 25.0) -> list[dict]:
    """Pull interface endpoints to nearest locked endpoint within max_snap."""
    locked_ends: list[Point] = []
    for e in entities:
        if e.get("entity_id") in interface_ids:
            continue
        pts = entity_points(e)
        if len(pts) >= 2:
            locked_ends.append(pts[0][:])
            locked_ends.append(pts[-1][:])
    if not locked_ends:
        return entities

    out = []
    for e in entities:
        if e.get("entity_id") not in interface_ids:
            out.append(e)
            continue
        pts = entity_points(e)
        if len(pts) < 2:
            out.append(e)
            continue
        new_pts = [p[:] for p in pts]
        for idx in (0, -1):
            p = new_pts[idx]
            best = None
            best_d = 1e18
            for q in locked_ends:
                d = math.hypot(p[0] - q[0], p[1] - q[1])
                if d < best_d:
                    best_d = d
                    best = q
            if best is not None and best_d <= max_snap:
                new_pts[idx] = best[:]
        out.append(_set_entity_points(e, new_pts))
    return out


def _set_entity_points(entity: dict, points: list[Point]) -> dict:
    e = deepcopy(entity)
    geom = dict(e.get("geometry") or {})
    geom["points"] = [p[:] for p in points]
    # drop stale circle fields if we converted to polyline points
    e["geometry"] = geom
    e["_morphed_interface"] = True
    return e


def _smooth_polyline_keep_ends(points: list[Point], passes: int = 2) -> list[Point]:
    """Light Laplacian smooth; endpoints frozen to limit spikes after large morph."""
    if len(points) <= 3:
        return [p[:] for p in points]
    pts = [p[:] for p in points]
    for _ in range(passes):
        nxt = [pts[0][:]]
        for i in range(1, len(pts) - 1):
            nxt.append([
                0.25 * pts[i - 1][0] + 0.5 * pts[i][0] + 0.25 * pts[i + 1][0],
                0.25 * pts[i - 1][1] + 0.5 * pts[i][1] + 0.25 * pts[i + 1][1],
            ])
        nxt.append(pts[-1][:])
        pts = nxt
    return pts


def _morph_group_to_length(iface: list[dict], target_total_len: float) -> list[dict]:
    cur_total = sum(entity_length(e) for e in iface)
    if not iface or cur_total < 1e-6:
        return [deepcopy(e) for e in iface]
    out = []
    for e in iface:
        pts = entity_points(e)
        share = entity_length(e) / cur_total
        seg_target = target_total_len * share
        if len(pts) >= 2:
            morphed = morph_polyline_keep_ends(pts, seg_target)
            morphed = _smooth_polyline_keep_ends(morphed, passes=2)
            # re-hit length after smoothing
            morphed = morph_polyline_keep_ends(morphed, seg_target)
            out.append(_set_entity_points(e, morphed))
        else:
            out.append(deepcopy(e))
    after = sum(entity_length(e) for e in out)
    if after > 1e-6 and abs(after - target_total_len) / target_total_len > 0.02:
        factor = target_total_len / after
        fixed = []
        for e in out:
            pts = entity_points(e)
            seg_target = entity_length(e) * factor
            if len(pts) >= 2:
                morphed = _smooth_polyline_keep_ends(morph_polyline_keep_ends(pts, seg_target), passes=1)
                fixed.append(_set_entity_points(e, morph_polyline_keep_ends(morphed, seg_target)))
            else:
                fixed.append(e)
        out = fixed
    return out


def morph_entities_to_total_length(
    entities: list[dict],
    interface_ids: Iterable[str],
    target_total_len: float,
    *,
    per_piece: bool = False,
) -> tuple[list[dict], dict]:
    """Morph only interface entities so length ≈ target; others unchanged.

    If per_piece=True, each piece_id group is morphed independently to target
    (e.g. each sleeve's cap matches one armhole).
    """
    id_set = set(interface_ids)
    iface = [e for e in entities if e.get("entity_id") in id_set]
    locked = [e for e in entities if e.get("entity_id") not in id_set]

    meta = {
        "interface_entity_count": len(iface),
        "locked_entity_count": len(locked),
        "target_length": round(target_total_len, 3),
        "per_piece": per_piece,
    }
    if not iface or target_total_len <= 1e-6:
        meta["applied"] = False
        meta["reason"] = "no_interface_or_zero_length"
        meta["length_before"] = 0
        return [deepcopy(e) for e in entities], meta

    if per_piece:
        groups: dict[str, list[dict]] = {}
        for e in iface:
            groups.setdefault(e.get("piece_id") or "_none", []).append(e)
        out_iface = []
        before_sum = 0.0
        after_parts = []
        for pid, group in groups.items():
            before_sum += sum(entity_length(e) for e in group)
            morphed = _morph_group_to_length(group, target_total_len)
            out_iface.extend(morphed)
            after_parts.append(sum(entity_length(e) for e in morphed))
        meta["piece_groups"] = len(groups)
        meta["length_before"] = round(before_sum, 3)
        meta["length_after_per_piece"] = [round(x, 3) for x in after_parts]
        after_report = sum(after_parts) / max(len(after_parts), 1)
    else:
        meta["length_before"] = round(sum(entity_length(e) for e in iface), 3)
        out_iface = _morph_group_to_length(iface, target_total_len)
        after_report = sum(entity_length(e) for e in out_iface)

    by_id = {e.get("entity_id"): e for e in out_iface}
    merged = []
    for e in entities:
        eid = e.get("entity_id")
        merged.append(by_id[eid] if eid in by_id else deepcopy(e))

    merged = snap_interface_ends_to_locked(merged, id_set, max_snap=20.0)
    # after snap, optional remorph per group with current ends
    if per_piece:
        groups = {}
        for e in merged:
            if e.get("entity_id") in id_set:
                groups.setdefault(e.get("piece_id") or "_none", []).append(e)
        remorphed = {}
        after_parts = []
        for pid, group in groups.items():
            mg = _morph_group_to_length(group, target_total_len)
            after_parts.append(sum(entity_length(e) for e in mg))
            for e in mg:
                remorphed[e.get("entity_id")] = e
        merged = [remorphed.get(e.get("entity_id"), e) if e.get("entity_id") in id_set else e for e in merged]
        merged = snap_interface_ends_to_locked(merged, id_set, max_snap=12.0)
        after_report = sum(after_parts) / max(len(after_parts), 1)
        meta["length_after_per_piece"] = [round(x, 3) for x in after_parts]
    else:
        after_report = sum(entity_length(e) for e in merged if e.get("entity_id") in id_set)

    meta.update({
        "applied": True,
        "length_after": round(after_report, 3),
        "length_error": round(after_report - target_total_len, 3),
        "length_error_ratio": round((after_report - target_total_len) / max(target_total_len, 1e-6), 4),
        "snapped_ends": True,
    })
    return merged, meta


DEFAULT_SLEEVE_ROLES = {
    "sleeve", "sleeve_left", "sleeve_right", "cuff",
    "sleeve_placket", "sleeve_placket_extension", "rib_cuff",
}
DEFAULT_NECK_ROLES = {"neck_binding", "collar", "collar_stand", "collar_interlining"}


def match_sleeve_cap_to_armhole(
    sleeve_entities: list[dict],
    sleeve_ir: dict,
    armhole_len: float,
    *,
    ease: float = 1.04,
    sleeve_piece_roles: set[str] | None = None,
) -> tuple[list[dict], dict]:
    """Only morph sleeve_cap arcs so total ≈ armhole_len * ease."""
    roles = sleeve_piece_roles or DEFAULT_SLEEVE_ROLES
    ids = interface_entity_ids(sleeve_ir, "sleeve_cap", roles)
    target = armhole_len * ease
    # each sleeve piece independently matches one armhole
    ents, meta = morph_entities_to_total_length(sleeve_entities, ids, target, per_piece=True)
    meta["interface_ids"] = ids
    meta["interface_group"] = "sleeve_cap"
    return ents, meta


def match_neck_to_neckline(
    neck_entities: list[dict],
    neck_ir: dict,
    neckline_len: float,
    *,
    ease: float = 1.0,
    neck_piece_roles: set[str] | None = None,
) -> tuple[list[dict], dict]:
    roles = neck_piece_roles or DEFAULT_NECK_ROLES
    ids = interface_entity_ids(neck_ir, "neckline", roles)
    if not ids:
        ids = _chain_entity_ids(
            neck_ir,
            {"collar_attach_line", "collar_roll_line", "hem"} | NECKLINE_ROLES,
            {p["piece_id"] for p in neck_ir.get("piece_instances") or [] if p.get("piece_role") in roles},
        )
    # last resort: longest entities on neck pieces (likely attach edge)
    if not ids:
        scored = sorted(
            ((entity_length(e), e.get("entity_id")) for e in neck_entities if e.get("entity_id")),
            reverse=True,
        )
        ids = [eid for _, eid in scored[:2] if eid]
    ents, meta = morph_entities_to_total_length(neck_entities, ids, neckline_len * ease)
    meta["interface_ids"] = ids
    meta["interface_group"] = "neckline"
    return ents, meta
