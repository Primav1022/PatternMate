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

from batch_operators import _set_points
from shirt_side_seam import (
    FRONT_ROLES,
    _closed_outlines,
    _line_role,
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
GATHER_SLUGS = {"puff", "flutter"}


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


def _span_armhole(pts: list[list[float]], body_wh: tuple[float, float] | None) -> dict[str, float] | None:
    if len(pts) < 2:
        return None
    arc = sum(_hypot(a, b) for a, b in zip(pts, pts[1:]))
    if arc < 15.0:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    width, depth = max(xs) - min(xs), max(ys) - min(ys)
    if body_wh:
        bw, bh = body_wh
        if bw > 1 and (arc > 0.45 * (bw + bh) or depth > 0.42 * bh or width > 0.38 * bw):
            return None
    return {"width": width, "depth": depth, "arc": arc}


def _labeled_armholes(
    entities: list[dict[str, Any]],
    body_wh: tuple[float, float] | None,
) -> dict[str, list[dict[str, float]]]:
    rows: dict[str, list[dict[str, float]]] = {"front": [], "back": []}
    for entity in entities:
        lr = _line_role(entity)
        if lr not in {"armhole_front", "armhole_back", "armhole"}:
            continue
        hit = _span_armhole(_points(entity), body_wh)
        if not hit:
            continue
        role = _piece_role(entity)
        if lr == "armhole_front" or role in FRONT_ROLES:
            rows["front"].append(hit)
        else:
            rows["back"].append(hit)
    return rows


def infer_armholes(entities: list[dict[str, Any]]) -> dict[str, Any]:
    body = _mean_body_wh([entity for entity in entities if _role(entity) in BODY_ROLES])
    labeled = _labeled_armholes(entities, body)
    rows: dict[str, list[dict[str, float]]] = {"front": [], "back": []}
    source = "inferred"
    if labeled["front"] and labeled["back"]:
        rows, source = labeled, "labeled"
    else:
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
        "source": source,
    }


def _clamp(value: float) -> float:
    return max(SCALE_CLAMP[0], min(SCALE_CLAMP[1], value))


def _looks_like_gather(pts: list[list[float]]) -> bool:
    if len(pts) < 40:
        return False
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    return max(width, height) / max(min(width, height), 1.0) >= 3.2


def _has_gathers(rows: list[dict[str, Any]]) -> bool:
    return any(_looks_like_gather(_points(entity)) for entity in rows)


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
    skipped: list[dict[str, Any]] = []
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
            if min(cw, ch) < 40.0 or max(cw, ch) / min(cw, ch) > 6.0:
                out.extend(piece_rows)
                continue
            if _has_gathers(piece_rows):
                out.extend(piece_rows)
                skipped.append({"piece_id": pid, "reason": "gathered_sleeve", "from": [round(cw, 1), round(ch, 1)]})
                continue
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
    meta = {"applied": bool(pieces), "mode": "armhole_bbox_scale", **ah, "pieces": pieces, "skipped": skipped}
    return out, meta


KNIT_SKIP = {"raglan", "batwing", "flutter"}
KNIT_CAP = {"puff": None, "regular": 0.48, "set-in": 0.48, "bell": 0.48}
KNIT_EASE = {"puff": 1.08, "bell": 1.03, "regular": 1.01, "set-in": 1.01}


def _ellipse_quarter(a: float, b: float) -> float:
    if a <= 1e-6 or b <= 1e-6:
        return 0.0
    h = ((a - b) / (a + b)) ** 2
    return math.pi * (a + b) * (1.0 + 3.0 * h / (10.0 + math.sqrt(max(0.0, 4.0 - 3.0 * h)))) / 4.0


def _half_width_for_arc(arc: float, height: float) -> float:
    height = max(float(height), 1.0)
    if arc <= height:
        return max(arc * 0.35, 8.0)
    lo, hi = 1.0, max(float(arc), 8.0)
    for _ in range(22):
        mid = (lo + hi) / 2.0
        if _ellipse_quarter(mid, height) < arc:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _bicep_y(pts: list[list[float]]) -> float | None:
    if len(pts) < 4:
        return None
    ys = [p[1] for p in pts]
    miny, maxy = min(ys), max(ys)
    h = max(maxy - miny, 1.0)
    lo = miny + 0.18 * h
    hi = maxy - 0.08 * h
    best_w, best_y = -1.0, None
    steps = 16
    for i in range(steps):
        y0 = lo + (hi - lo) * i / steps
        y1 = y0 + max((hi - lo) / steps, 1.0)
        xs = [p[0] for p in pts if y0 <= p[1] <= y1]
        if len(xs) < 2:
            continue
        width = max(xs) - min(xs)
        if width > best_w:
            best_w, best_y = width, (y0 + y1) / 2.0
    return best_y


def _map_sleeve_points(
    pts: list[list[float]],
    *,
    ox: float,
    sx: float,
    bicep_y: float,
    cap0: float,
    cap1: float,
    body0: float,
    body1: float,
) -> list[list[float]]:
    out: list[list[float]] = []
    for x, y in pts:
        nx = ox + (x - ox) * sx
        if y >= bicep_y - 1e-6 and cap0 > 1.0:
            ny = bicep_y + (y - bicep_y) / cap0 * cap1
        elif body0 > 1.0:
            ny = bicep_y - (bicep_y - y) / body0 * body1
        else:
            ny = y
        out.append([nx, ny])
    return out


def fit_knit_sleeves(
    entities: list[dict[str, Any]],
    *,
    sleeve_sy: float = 1.0,
    slug: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """T-shirt set-in: low cap from scye depth, width from front/back arcs, length below bicep."""
    kind = str(slug or "regular").split(".")[-1].strip().lower()
    if kind in KNIT_SKIP:
        return entities, {"applied": False, "reason": "integrated_sleeve", "slug": kind}
    sleeves = [entity for entity in entities if _role(entity) in PURE_SLEEVE_ROLES]
    if not sleeves:
        return entities, {"applied": False, "reason": "no_sleeve_pieces"}
    ah = infer_armholes(entities)
    front_arc = float(ah.get("front_arc") or 0.0)
    back_arc = float(ah.get("back_arc") or 0.0)
    depth = float(ah.get("target_cap_h") or 0.0)
    body = ah.get("body_wh") or (0.0, 0.0)
    bw = float(body[0] or 0.0)
    if front_arc < 8.0 and back_arc < 8.0:
        return entities, {"applied": False, "reason": "armhole_unusable", **ah}
    if front_arc < 8.0:
        front_arc = back_arc
    if back_arc < 8.0:
        back_arc = front_arc
    ease = KNIT_EASE.get(kind, 1.01)
    ratio = KNIT_CAP.get(kind, 0.48)
    cap_limit = 0.85 * min(front_arc, back_arc)
    target_h = 0.0 if ratio is None else max(8.0, min(depth * ratio, cap_limit))
    af, ab = front_arc * ease, back_arc * ease
    cap_for_w = target_h if target_h > 1 else max(depth * 0.48, 8.0)
    target_w = _half_width_for_arc(af, cap_for_w) + _half_width_for_arc(ab, cap_for_w)
    if bw > 1:
        target_w = min(0.88 * bw, max(0.48 * bw, target_w))
    if target_w < 8.0:
        return entities, {"applied": False, "reason": "sleeve_width_unusable", **ah, "target_knit_width": round(target_w, 2)}

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
            pts = [point for entity in cluster for point in _points(entity)]
            box = bounds_of_entities(cluster)
            if not box or len(pts) < 4:
                out.extend(piece_rows)
                continue
            cw, ch = max(box[2] - box[0], 1.0), max(box[3] - box[1], 1.0)
            if min(cw, ch) < 40.0 or max(cw, ch) / min(cw, ch) > 6.0:
                out.extend(piece_rows)
                continue
            bicep = _bicep_y(pts)
            if bicep is None:
                out.extend(piece_rows)
                continue
            peak_y, hem_y = box[3], box[1]
            cap0 = peak_y - bicep
            body0 = bicep - hem_y
            if cap0 < 8.0 or body0 < 8.0:
                out.extend(piece_rows)
                continue
            cap1 = cap0 if target_h < 1.0 else min(cap0, target_h)
            body1 = max(8.0, body0 * max(0.35, min(2.2, float(sleeve_sy) or 1.0)))
            sx = _clamp(target_w / cw)
            ox = (box[0] + box[2]) / 2.0
            mapped = [
                _set_points(entity, _map_sleeve_points(
                    _points(entity), ox=ox, sx=sx, bicep_y=bicep,
                    cap0=cap0, cap1=cap1, body0=body0, body1=body1,
                )) if _points(entity) else entity
                for entity in piece_rows
            ]
            out.extend(mapped)
            pieces.append({
                "piece_id": pid,
                "sx": round(sx, 4),
                "cap_h": [round(cap0, 1), round(cap1, 1)],
                "body_h": [round(body0, 1), round(body1, 1)],
                "from": [round(cw, 1), round(ch, 1)],
            })
    meta = {
        "applied": bool(pieces),
        "mode": "knit_cap_to_armhole",
        "slug": kind,
        "ease": ease,
        "target_cap_h_knit": round(target_h, 2),
        "target_knit_width": round(target_w, 2),
        **ah,
        "pieces": pieces,
    }
    return out, meta


CUFF_FIT_ROLES = {"cuff", "rib_cuff"}


def _hem_width(entities: list[dict[str, Any]]) -> float:
    pts = [point for entity in entities for point in _points(entity)]
    if len(pts) < 2:
        return 0.0
    min_y = min(p[1] for p in pts)
    height = max(p[1] for p in pts) - min_y
    band = [p[0] for p in pts if p[1] <= min_y + max(8.0, 0.08 * max(height, 1.0))]
    if len(band) < 2:
        xs = [p[0] for p in pts]
        return max(xs) - min(xs)
    return max(band) - min(band)


def fit_cuffs_to_sleeves(entities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sleeves = [entity for entity in entities if _role(entity) in PURE_SLEEVE_ROLES]
    cuffs = [entity for entity in entities if _role(entity) in CUFF_FIT_ROLES]
    if not sleeves or not cuffs:
        return entities, {"applied": False, "reason": "missing_sleeve_or_cuff"}
    target = _hem_width(sleeves)
    if target < 20.0:
        return entities, {"applied": False, "reason": "sleeve_hem_unusable", "target_width": round(target, 2)}
    out: list[dict[str, Any]] = []
    pieces: list[dict[str, Any]] = []
    by_role: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        by_role.setdefault(_role(entity), []).append(entity)
    for role, rows in by_role.items():
        if role not in CUFF_FIT_ROLES:
            out.extend(rows)
            continue
        for pid, piece_rows in _group_by_piece(rows).items():
            box = bounds_of_entities(piece_rows)
            if not box:
                out.extend(piece_rows)
                continue
            cw = max(box[2] - box[0], 1.0)
            sx = _clamp(target / cw)
            out.extend(_scale_group(piece_rows, sx=sx, sy=1.0, anchor="center"))
            pieces.append({"piece_id": pid, "sx": round(sx, 4), "from": round(cw, 1), "to": round(cw * sx, 1)})
    return out, {"applied": bool(pieces), "mode": "cuff_to_sleeve_hem", "target_width": round(target, 2), "pieces": pieces}
