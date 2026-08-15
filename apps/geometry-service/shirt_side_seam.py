"""Shirt silhouette: morph front/back side-seam curvature, keep armhole join.

line_role=side_seam on remix IR is mostly degenerate, so this edits the closed
body outline (pattern_boundary / cut_line). Armhole junction stays; hem y stays;
hem x may change so A-line can flare and X can indent.
"""
from __future__ import annotations

import math
from typing import Any

from batch_operators import _fit_polyline_to_ends, _points, _set_points

BODY_SIDE_ROLES = {"front_body", "back_body", "front", "back", "front_left", "front_right"}
FRONT_ROLES = {"front_body", "front", "front_left", "front_right"}
OUTLINE_LINE_ROLES = {"pattern_boundary", "cut_line"}


def _hypot(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _open_loop(points: list[list[float]], tol: float = 2.0) -> list[list[float]]:
    pts = [[float(p[0]), float(p[1])] for p in points]
    if len(pts) >= 2 and _hypot(pts[0], pts[-1]) <= tol:
        pts = pts[:-1]
    return pts


def _is_closed(points: list[list[float]], tol: float = 2.0) -> bool:
    return len(points) >= 8 and _hypot(points[0], points[-1]) <= tol


def _piece_role(entity: dict[str, Any]) -> str:
    return str(entity.get("_piece_role") or entity.get("piece_role") or "")


def _line_role(entity: dict[str, Any]) -> str:
    return str(entity.get("line_role") or entity.get("edge_role") or "").lower()


def _coarse_role(role: str) -> str:
    return "front" if role in FRONT_ROLES else "back"


def _band_extreme(pts: list[list[float]], y0: float, y1: float, side: str) -> int:
    lo, hi = min(y0, y1), max(y0, y1)
    cand = [i for i, p in enumerate(pts) if lo <= p[1] <= hi]
    if not cand:
        cand = list(range(len(pts)))
    key = (lambda i: pts[i][0]) if side == "left" else (lambda i: -pts[i][0])
    return min(cand, key=key)


def _walk(n: int, start: int, end: int, step: int) -> list[int]:
    out = [start]
    k = start
    guard = 0
    while k != end and guard <= n:
        k = (k + step) % n
        out.append(k)
        guard += 1
    return out


def _path_indices(pts: list[list[float]], start: int, end: int, side: str) -> list[int]:
    n = len(pts)
    fwd = _walk(n, start, end, 1)
    rev = _walk(n, start, end, -1)

    def mean_x(idx: list[int]) -> float:
        return sum(pts[i][0] for i in idx) / max(len(idx), 1)

    if side == "left":
        return fwd if mean_x(fwd) <= mean_x(rev) else rev
    return fwd if mean_x(fwd) >= mean_x(rev) else rev


def extract_side_indices(pts: list[list[float]]) -> dict[str, list[int]] | None:
    if len(pts) < 8:
        return None
    ys = [p[1] for p in pts]
    miny, maxy = min(ys), max(ys)
    h = max(maxy - miny, 1.0)
    hem_l = _band_extreme(pts, miny, miny + 0.12 * h, "left")
    hem_r = _band_extreme(pts, miny, miny + 0.12 * h, "right")
    arm_l = _band_extreme(pts, miny + 0.58 * h, miny + 0.82 * h, "left")
    arm_r = _band_extreme(pts, miny + 0.58 * h, miny + 0.82 * h, "right")
    left = _path_indices(pts, arm_l, hem_l, "left")
    right = _path_indices(pts, arm_r, hem_r, "right")
    if len(left) < 2 or len(right) < 2:
        return None
    return {"left": left, "right": right}


def _center_x(pts: list[list[float]]) -> float:
    xs = [p[0] for p in pts]
    return (min(xs) + max(xs)) / 2.0


def _target_hem(host_arm: list[float], host_hem: list[float], host_cx: float,
                donor_side: list[list[float]], donor_cx: float) -> list[float]:
    """Lock armhole join + hem y; let hem x follow donor flare/taper."""
    donor_arm, donor_hem = donor_side[0], donor_side[-1]
    host_off = host_arm[0] - host_cx
    donor_off = donor_arm[0] - donor_cx
    if abs(donor_off) < 1e-6:
        scale = 1.0
    else:
        scale = host_off / donor_off
        if scale < 0:
            scale = abs(scale)
    hem_x = host_cx + (donor_hem[0] - donor_cx) * scale
    return [hem_x, host_hem[1]]


def _span_step(span: list[int], n: int) -> int:
    if len(span) < 2:
        return 1
    delta = (span[1] - span[0]) % n
    return 1 if delta == 1 or (delta != n - 1 and delta < n / 2) else -1


def _splice_spans(loop: list[list[float]], replacements: list[tuple[list[int], list[list[float]]]]) -> list[list[float]]:
    n = len(loop)
    aligned: list[tuple[int, int, list[list[float]]]] = []
    for span, new_pts in replacements:
        if len(span) < 2 or len(new_pts) < 2:
            continue
        step = _span_step(span, n)
        pts = [p[:] for p in new_pts]
        if step == -1:
            pts = list(reversed(pts))
            span = list(reversed(span))
        aligned.append((span[0], span[-1], pts))
    start_map = {start: (end, pts) for start, end, pts in aligned}
    out: list[list[float]] = []
    i = 0
    seen = 0
    while seen < n:
        if i in start_map:
            end, pts = start_map[i]
            if out and _hypot(out[-1], pts[0]) <= 1e-6:
                out.extend(p[:] for p in pts[1:])
            else:
                out.extend(p[:] for p in pts)
            consumed = (end - i) % n
            seen += consumed + 1
            i = (end + 1) % n
            continue
        out.append(loop[i][:])
        i = (i + 1) % n
        seen += 1
    if out and _hypot(out[0], out[-1]) > 1e-6:
        out.append(out[0][:])
    return out


def _sides_to_morph(role: str) -> tuple[str, ...]:
    if role == "front_left":
        return ("left",)
    if role == "front_right":
        return ("right",)
    return ("left", "right")


def _closed_outlines(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
    for entity in entities:
        role = _piece_role(entity)
        if role not in BODY_SIDE_ROLES:
            continue
        pts = _points(entity)
        if not _is_closed(pts):
            continue
        lr = _line_role(entity) or "pattern_boundary"
        if lr not in OUTLINE_LINE_ROLES and len(pts) < 16:
            continue
        length = sum(_hypot(a, b) for a, b in zip(pts, pts[1:]))
        key = (str(entity.get("piece_id") or entity.get("entity_id")), lr)
        prev = best.get(key)
        if prev is None or length > prev[0]:
            best[key] = (length, entity)
    return [row[1] for row in best.values()]


def _match_donor(host: dict[str, Any], donors: list[dict[str, Any]]) -> dict[str, Any] | None:
    host_role = _piece_role(host)
    host_lr = _line_role(host) or "pattern_boundary"
    host_coarse = _coarse_role(host_role)
    ranked: list[tuple[int, float, dict[str, Any]]] = []
    for donor in donors:
        if _coarse_role(_piece_role(donor)) != host_coarse:
            continue
        lr_match = 1 if (_line_role(donor) or "pattern_boundary") == host_lr else 0
        pts = _points(donor)
        length = sum(_hypot(a, b) for a, b in zip(pts, pts[1:]))
        ranked.append((lr_match, length, donor))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return ranked[0][2]


def morph_body_side_seams(
    host_entities: list[dict[str, Any]],
    donor_entities: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    donors = _closed_outlines(donor_entities)
    changed: dict[str, dict[str, Any]] = {}
    meta_rows: list[dict[str, Any]] = []
    for host in _closed_outlines(host_entities):
        donor = _match_donor(host, donors)
        if not donor:
            continue
        host_loop = _open_loop(_points(host))
        donor_loop = _open_loop(_points(donor))
        host_sides = extract_side_indices(host_loop)
        donor_sides = extract_side_indices(donor_loop)
        if not host_sides or not donor_sides:
            continue
        host_cx = _center_x(host_loop)
        donor_cx = _center_x(donor_loop)
        replacements: list[tuple[list[int], list[list[float]]]] = []
        for side in _sides_to_morph(_piece_role(host)):
            h_idx = host_sides[side]
            d_idx = donor_sides[side]
            host_poly = [host_loop[i] for i in h_idx]
            donor_poly = [donor_loop[i] for i in d_idx]
            target_end = _target_hem(host_poly[0], host_poly[-1], host_cx, donor_poly, donor_cx)
            fitted = _fit_polyline_to_ends(donor_poly, host_poly[0], target_end)
            replacements.append((h_idx, fitted))
        if not replacements:
            continue
        new_pts = _splice_spans(host_loop, replacements)
        eid = str(host.get("entity_id") or "")
        changed[eid] = _set_points(host, new_pts)
        meta_rows.append({
            "entity_id": eid,
            "piece_role": _piece_role(host),
            "sides": list(_sides_to_morph(_piece_role(host))),
        })
    if not changed:
        return host_entities, {"applied": False, "reason": "no_closed_body_side_seams"}
    out = [changed.get(str(entity.get("entity_id")), entity) for entity in host_entities]
    return out, {"applied": True, "mode": "side_seam_morph", "modified": meta_rows}


# Chest/length/neck grade: move structure points, not affine-stretch the piece.
ARMHOLE_LINE_ROLES = {"armhole_front", "armhole_back", "armhole_seam"}
NECK_LINE_ROLES = {"neckline", "front_neckline", "back_neckline"}
BODY_GRADE_ROLES = BODY_SIDE_ROLES | {"front_placket", "back_yoke", "placket", "yoke"}


def _x_origin(role: str, loop: list[list[float]]) -> float:
    xs = [p[0] for p in loop]
    if role == "front_left":
        return max(xs)
    if role == "front_right":
        return min(xs)
    return (min(xs) + max(xs)) / 2.0


def _in_neck_band(x: float, y: float, rec: dict[str, float]) -> bool:
    return y >= rec["maxy"] - 0.22 * rec["h"] and abs(x - rec["origin_x"]) <= 0.32 * rec["w"]


def _map_body_point(
    x: float,
    y: float,
    rec: dict[str, float],
    width_sx: float,
    length_sy: float,
    *,
    skip_width: bool = False,
) -> list[float]:
    chest_y = rec["chest_y"]
    ny = y if y >= chest_y else chest_y + (y - chest_y) * length_sy
    if skip_width or abs(width_sx - 1.0) < 1e-6 or y >= chest_y - 1e-9:
        return [x, ny]
    ramp = max((chest_y - rec["miny"]) * 0.12, 1.0)
    t = min(1.0, (chest_y - y) / ramp)
    nx = rec["origin_x"] + (x - rec["origin_x"]) * (1.0 + (width_sx - 1.0) * t)
    return [nx, ny]


def _outline_params(loop: list[list[float]], role: str) -> dict[str, float] | None:
    sides = extract_side_indices(loop)
    if not sides:
        return None
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    miny, maxy = min(ys), max(ys)
    minx, maxx = min(xs), max(xs)
    origin_x = _x_origin(role, loop)
    rec: dict[str, float] = {
        "chest_y": (loop[sides["left"][0]][1] + loop[sides["right"][0]][1]) / 2.0,
        "miny": miny,
        "maxy": maxy,
        "origin_x": origin_x,
        "h": max(maxy - miny, 1.0),
        "w": max(maxx - minx, 1.0),
        "n": float(len(loop)),
    }
    neck_pts = [p for p in loop if _in_neck_band(p[0], p[1], rec)]
    rec["neck_cx"] = sum(p[0] for p in neck_pts) / len(neck_pts) if neck_pts else origin_x
    rec["neck_cy"] = sum(p[1] for p in neck_pts) / len(neck_pts) if neck_pts else maxy
    return rec


def _params_for(
    entity: dict[str, Any],
    by_piece: dict[str, dict[str, float]],
    by_coarse: dict[str, dict[str, float]],
) -> dict[str, float] | None:
    pid = str(entity.get("piece_id") or entity.get("entity_id") or "")
    if pid in by_piece:
        return by_piece[pid]
    role = _piece_role(entity)
    if role in {"front_placket", "placket"}:
        return by_coarse.get("front")
    if role in {"back_yoke", "yoke"}:
        return by_coarse.get("back")
    if role in BODY_SIDE_ROLES:
        return by_coarse.get(_coarse_role(role))
    return None


def grade_body_structure(
    entities: list[dict[str, Any]],
    *,
    width_sx: float,
    length_sy: float,
    neck_s: float = 1.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Chest → side-seam let-out; length from chest down; neck opening only.

    Armhole / shoulder / neck stay off the chest sx. ponytail: no extra armhole deepen.
    """
    meta: dict[str, Any] = {
        "mode": "body_structure_grade",
        "width_sx": round(width_sx, 5),
        "length_sy": round(length_sy, 5),
        "neck_s": round(neck_s, 5),
    }
    if abs(width_sx - 1.0) < 1e-6 and abs(length_sy - 1.0) < 1e-6 and abs(neck_s - 1.0) < 1e-6:
        meta["applied"] = False
        return entities, meta

    by_piece: dict[str, dict[str, float]] = {}
    by_coarse: dict[str, dict[str, float]] = {}
    for host in _closed_outlines(entities):
        loop = _open_loop(_points(host))
        rec = _outline_params(loop, _piece_role(host))
        if not rec:
            continue
        pid = str(host.get("piece_id") or host.get("entity_id") or "")
        prev = by_piece.get(pid)
        if prev is None or rec["n"] >= prev["n"]:
            by_piece[pid] = rec
            by_coarse[_coarse_role(_piece_role(host))] = rec
    meta["n_outlines"] = len(by_piece)
    if not by_piece:
        meta["applied"] = False
        meta["reason"] = "no_closed_body"
        return entities, meta

    do_neck = abs(neck_s - 1.0) >= 1e-6
    out: list[dict[str, Any]] = []
    n_changed = 0
    for entity in entities:
        role = _piece_role(entity)
        rec = _params_for(entity, by_piece, by_coarse)
        if rec is None or role not in BODY_GRADE_ROLES:
            out.append(entity)
            continue
        lr = _line_role(entity)
        if lr in ARMHOLE_LINE_ROLES:
            out.append(entity)
            continue
        pts = _points(entity)
        if len(pts) < 2:
            out.append(entity)
            continue
        placket = role in {"front_placket", "placket", "back_yoke", "yoke"}
        if lr in NECK_LINE_ROLES and do_neck:
            cx, cy = rec["neck_cx"], rec["neck_cy"]
            new_pts = [[cx + (p[0] - cx) * neck_s, cy + (p[1] - cy) * neck_s] for p in pts]
        else:
            is_outline = _is_closed(pts) and (lr in OUTLINE_LINE_ROLES or len(pts) >= 16)
            work = _open_loop(pts) if is_outline else pts
            new_pts = [
                _map_body_point(
                    p[0], p[1], rec, width_sx, length_sy,
                    skip_width=placket or _in_neck_band(p[0], p[1], rec),
                )
                for p in work
            ]
            if is_outline and do_neck:
                cx, cy = rec["neck_cx"], rec["neck_cy"]
                new_pts = [
                    [cx + (p[0] - cx) * neck_s, cy + (p[1] - cy) * neck_s]
                    if _in_neck_band(work[i][0], work[i][1], rec) else p
                    for i, p in enumerate(new_pts)
                ]
            if is_outline and (not new_pts or _hypot(new_pts[0], new_pts[-1]) > 1e-6):
                new_pts.append(new_pts[0][:])
        out.append(_set_points(entity, new_pts))
        n_changed += 1
    meta["applied"] = n_changed > 0
    meta["n_changed"] = n_changed
    return out, meta

