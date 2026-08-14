"""Scale swapped shirt sleeves to the host front/back armholes.

Remix IR armhole_* labels are mostly degenerate (same as side_seam), so this
reads closed body outlines. No sleeve-cap morph — size only.

Rules:
- width  (袖肥)  ≈ 0.55 × (front armhole arc + back armhole arc)
  clamped to 48–80% of body width. Cap *length* is Af+Ab; do not use that as bbox width.
- length follows the same scale (keep donor proportion), never shorter than
  armhole depth × 1.2 so the cap can still cover the scye
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from shirt_side_seam import (
    FRONT_ROLES,
    _closed_outlines,
    _open_loop,
    _piece_role,
    _points,
    extract_side_indices,
)
from simple_compose import (
    BODY_ROLES,
    PURE_SLEEVE_ROLES,
    _group_by_piece,
    _largest_cluster,
    _mean_body_wh,
    _role,
    _scale_group,
    bounds_of_entities,
)

WIDTH_FROM_ARCS = 0.55
WIDTH_BODY = (0.48, 0.80)
SCALE_CLAMP = (0.25, 2.2)


def _hypot(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _path_len(pts: list[list[float]], idx: list[int]) -> float:
    return sum(_hypot(pts[idx[i]], pts[idx[i + 1]]) for i in range(len(idx) - 1))


def _walk(n: int, start: int, end: int, step: int) -> list[int]:
    out = [start]
    k = start
    guard = 0
    while k != end and guard <= n:
        k = (k + step) % n
        out.append(k)
        guard += 1
    return out


def _sides_for_role(role: str) -> tuple[str, ...]:
    if role == "front_left":
        return ("left",)
    if role == "front_right":
        return ("right",)
    return ("left", "right")


def _side_armhole(pts: list[list[float]], side: str) -> dict[str, float] | None:
    sides = extract_side_indices(pts)
    if not sides:
        return None
    n = len(pts)
    ys = [p[1] for p in pts]
    xs = [p[0] for p in pts]
    miny, maxy = min(ys), max(ys)
    minx, maxx = min(xs), max(xs)
    h = max(maxy - miny, 1.0)
    w = max(maxx - minx, 1.0)
    arm = pts[sides[side][0]]
    if side == "left":
        outer = [i for i, p in enumerate(pts) if p[0] <= minx + 0.32 * w]
    else:
        outer = [i for i, p in enumerate(pts) if p[0] >= maxx - 0.32 * w]
    if not outer:
        return None
    sh_i = max(outer, key=lambda i: pts[i][1])
    sh = pts[sh_i]
    depth = abs(sh[1] - arm[1])
    width = abs(sh[0] - arm[0])
    if not (0.08 * h <= depth <= 0.42 * h):
        return None
    if width > 0.38 * w:
        return None
    arm_i = sides[side][0]

    def score(idx: list[int]) -> float:
        if len(idx) < 2:
            return -1e9
        mean_y = sum(pts[i][1] for i in idx) / len(idx)
        length = _path_len(pts, idx)
        if length > 1.1 * (w + h) or len(idx) > 0.45 * n:
            return -1e9
        return mean_y

    p1, p2 = _walk(n, sh_i, arm_i, 1), _walk(n, sh_i, arm_i, -1)
    arc_idx = p1 if score(p1) >= score(p2) else p2
    arc = _path_len(pts, arc_idx) if score(arc_idx) > -1e8 else math.hypot(width, depth)
    return {"width": width, "depth": depth, "arc": max(arc, math.hypot(width, depth))}


def infer_armholes(entities: list[dict[str, Any]]) -> dict[str, Any]:
    rows: dict[str, list[dict[str, float]]] = {"front": [], "back": []}
    for outline in _closed_outlines(entities):
        role = _piece_role(outline)
        coarse = "front" if role in FRONT_ROLES else "back"
        pts = _open_loop(_points(outline))
        for side in _sides_for_role(role):
            hit = _side_armhole(pts, side)
            if hit:
                rows[coarse].append(hit)

    def _med(group: str, key: str) -> float:
        vals = [row[key] for row in rows[group]]
        return float(statistics.median(vals)) if vals else 0.0

    front_arc, back_arc = _med("front", "arc"), _med("back", "arc")
    front_d, back_d = _med("front", "depth"), _med("back", "depth")
    body = _mean_body_wh([entity for entity in entities if _role(entity) in BODY_ROLES])
    bw, bh = (body or (0.0, 0.0))
    pair = front_arc > 1 and back_arc > 1
    ratio = min(front_arc, back_arc) / max(front_arc, back_arc) if pair else 0.0
    if pair and ratio >= 0.55:
        raw_w = (front_arc + back_arc) * WIDTH_FROM_ARCS
    elif max(front_arc, back_arc) > 1:
        raw_w = max(front_arc, back_arc) * 1.10
    else:
        raw_w = 0.58 * bw if bw > 1 else 0.0
    if bw > 1:
        lo, hi = WIDTH_BODY[0] * bw, WIDTH_BODY[1] * bw
        target_w = min(hi, max(lo, raw_w)) if raw_w > 1 else 0.58 * bw
    else:
        target_w = raw_w
    cap_h = (front_d + back_d) / 2.0 if front_d and back_d else max(front_d, back_d)
    return {
        "front_arc": round(front_arc, 2),
        "back_arc": round(back_arc, 2),
        "front_depth": round(front_d, 2),
        "back_depth": round(back_d, 2),
        "target_width": round(target_w, 2),
        "target_cap_h": round(cap_h, 2),
        "body_wh": None if not body else (round(bw, 2), round(bh, 2)),
        "n_front": len(rows["front"]),
        "n_back": len(rows["back"]),
    }


def _clamp(value: float) -> float:
    return max(SCALE_CLAMP[0], min(SCALE_CLAMP[1], value))


def fit_sleeves_to_armholes(entities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sleeves = [entity for entity in entities if _role(entity) in PURE_SLEEVE_ROLES]
    if not sleeves:
        return entities, {"applied": False, "reason": "no_sleeve_pieces"}
    ah = infer_armholes(entities)
    target_w = float(ah.get("target_width") or 0.0)
    cap_h = float(ah.get("target_cap_h") or 0.0)
    if target_w < 8.0:
        return entities, {"applied": False, "reason": "armhole_unusable", **ah}

    out: list[dict[str, Any]] = []
    pieces: list[dict[str, Any]] = []
    by_role: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_role.setdefault(_role(entity), []).append(entity)
    for role, rows in by_role.items():
        if role not in PURE_SLEEVE_ROLES:
            out.extend(rows)
            continue
        for pid, piece_rows in _group_by_piece(rows).items():
            cluster = _largest_cluster(piece_rows)
            box = bounds_of_entities(cluster)
            if not box:
                out.extend(piece_rows)
                continue
            cw, ch = max(box[2] - box[0], 1.0), max(box[3] - box[1], 1.0)
            sx = _clamp(target_w / cw)
            target_h = ch * sx
            if cap_h > 1:
                target_h = max(target_h, cap_h * 1.2)
            sy = _clamp(target_h / ch)
            out.extend(_scale_group(piece_rows, sx=sx, sy=sy, anchor="top"))
            pieces.append({
                "piece_id": pid,
                "sx": round(sx, 4),
                "sy": round(sy, 4),
                "from": [round(cw, 1), round(ch, 1)],
                "to": [round(cw * sx, 1), round(ch * sy, 1)],
            })
    meta = {"applied": bool(pieces), "mode": "armhole_bbox_scale", **ah, "pieces": pieces}
    return out, meta
