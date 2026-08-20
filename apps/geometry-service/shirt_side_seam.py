"""Shirt silhouette: morph front/back side-seam curvature, keep armhole join.

line_role=side_seam on remix IR is mostly degenerate, so this edits the closed
body outline (pattern_boundary / cut_line). Armhole junction stays; hem y stays;
hem x may change so A-line can flare and X can indent.
"""
from __future__ import annotations

import math
from typing import Any

from batch_operators import _fit_polyline_to_ends, _points, _set_points

BODY_SIDE_ROLES = {"front_body", "back_body", "front", "back", "front_left", "front_right", "side_panel"}
FRONT_ROLES = {"front_body", "front", "front_left", "front_right"}
OUTLINE_LINE_ROLES = {"cut", "pattern_boundary", "cut_line"}
_NOT_OUTLINE = {"sew", "internal", "grainline", "notch", "net_boundary", "seam_allowance"}
_OUTLINE_RANK = {"cut": 3, "pattern_boundary": 2, "cut_line": 2}


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


def _band_extreme(
    pts: list[list[float]], y0: float, y1: float, side: str, *, toward_y: float | None = None,
) -> int:
    lo, hi = min(y0, y1), max(y0, y1)
    cand = [i for i, p in enumerate(pts) if lo <= p[1] <= hi]
    if not cand:
        cand = list(range(len(pts)))

    def key(i: int) -> tuple[float, float]:
        xkey = pts[i][0] if side == "left" else -pts[i][0]
        ykey = abs(pts[i][1] - toward_y) if toward_y is not None else 0.0
        return (xkey, ykey)

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
    hem_l = _band_extreme(pts, miny, miny + 0.12 * h, "left", toward_y=miny)
    hem_r = _band_extreme(pts, miny, miny + 0.12 * h, "right", toward_y=miny)
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


def _half_outer(role: str) -> str | None:
    if role == "front_left":
        return "right"
    if role == "front_right":
        return "left"
    return None


def _sides_to_morph(role: str) -> tuple[str, ...]:
    outer = _half_outer(role)
    return (outer,) if outer else ("left", "right")


def _poly_flare(poly: list[list[float]]) -> float:
    if len(poly) < 2:
        return 0.0
    return abs(poly[-1][0] - poly[0][0])


def _canonical_side_seams(
    donors: list[dict[str, Any]],
) -> dict[str, tuple[list[list[float]], float]] | None:
    """One left/right side-seam for the whole garment.

    Split fronts contribute only the outer edge (not CF). Front and back
    sew together, so the most flared donor sides are reused on every body.
    """
    found: dict[str, list[tuple[float, list[list[float]], float]]] = {"left": [], "right": []}
    for donor in donors:
        role = _piece_role(donor)
        loop = _open_loop(_points(donor))
        sides = extract_side_indices(loop)
        if not sides:
            continue
        cx = _center_x(loop)
        wanted = _half_outer(role)
        for name in ((wanted,) if wanted else ("left", "right")):
            poly = [loop[i] for i in sides[name]]
            found[name].append((_poly_flare(poly), poly, cx))
    picked: dict[str, tuple[list[list[float]], float]] = {}
    for name, rows in found.items():
        if not rows:
            continue
        rows.sort(key=lambda row: row[0], reverse=True)
        picked[name] = (rows[0][1], rows[0][2])
    if "left" not in picked and "right" in picked:
        picked["left"] = picked["right"]
    if "right" not in picked and "left" in picked:
        picked["right"] = picked["left"]
    if "left" not in picked or "right" not in picked:
        return None
    return picked


def _outline_family(entity: dict[str, Any]) -> str | None:
    lr = _line_role(entity)
    if lr in {"sew", "seam_allowance", "net_boundary"}:
        return "sew"
    if lr in _NOT_OUTLINE:
        return None
    if lr in OUTLINE_LINE_ROLES or lr == "":
        return "cut"
    return None


def _closed_body_candidates(entities: list[dict[str, Any]], *, include_sew: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entity in entities:
        if _piece_role(entity) not in BODY_SIDE_ROLES:
            continue
        pts = _points(entity)
        if not _is_closed(pts):
            continue
        family = _outline_family(entity)
        if family == "cut" or (include_sew and family == "sew"):
            out.append(entity)
        elif family is None and len(pts) >= 16 and include_sew is False:
            continue
    return out


def _closed_outlines(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, tuple[int, float, dict[str, Any]]] = {}
    for entity in _closed_body_candidates(entities):
        pts = _points(entity)
        lr = _line_role(entity) or "pattern_boundary"
        rank = _OUTLINE_RANK.get(lr, 0)
        if rank == 0 and len(pts) < 16:
            continue
        length = sum(_hypot(a, b) for a, b in zip(pts, pts[1:]))
        key = str(entity.get("piece_id") or entity.get("entity_id") or "")
        prev = best.get(key)
        if prev is None or (rank, length) > (prev[0], prev[1]):
            best[key] = (rank, length, entity)
    return [row[2] for row in best.values()]


def drop_extra_closed_outlines(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One cut + one sew closed loop per piece. Extra loops are leftover ghosts."""
    best: dict[tuple[str, str], tuple[int, float, str]] = {}
    closed_ids: set[str] = set()
    for entity in entities:
        family = _outline_family(entity)
        if not family:
            continue
        pts = _points(entity)
        if not _is_closed(pts):
            continue
        eid = str(entity.get("entity_id") or id(entity))
        pid = str(entity.get("piece_id") or eid)
        rank = _OUTLINE_RANK.get(_line_role(entity), 1 if family == "sew" else 0)
        length = sum(_hypot(a, b) for a, b in zip(pts, pts[1:]))
        closed_ids.add(eid)
        key = (pid, family)
        prev = best.get(key)
        if prev is None or (rank, length) > (prev[0], prev[1]):
            best[key] = (rank, length, eid)
    keep = {row[2] for row in best.values()}
    return [
        entity for entity in entities
        if str(entity.get("entity_id") or id(entity)) not in closed_ids
        or str(entity.get("entity_id") or id(entity)) in keep
    ]


def _side_replacements(
    host_loop: list[list[float]],
    role: str,
    host_cx: float,
    donor_sides: dict[str, tuple[list[list[float]], float]],
) -> list[tuple[list[int], list[list[float]]]] | None:
    host_sides = extract_side_indices(host_loop)
    if not host_sides:
        return None
    replacements: list[tuple[list[int], list[list[float]]]] = []
    for side in _sides_to_morph(role):
        donor = donor_sides.get(side)
        if not donor:
            continue
        donor_poly, donor_cx = donor
        h_idx = host_sides[side]
        host_poly = [host_loop[i] for i in h_idx]
        target_end = _target_hem(host_poly[0], host_poly[-1], host_cx, donor_poly, donor_cx)
        fitted = _fit_polyline_to_ends(donor_poly, host_poly[0], target_end)
        replacements.append((h_idx, fitted))
    return replacements or None


def morph_body_side_seams(
    host_entities: list[dict[str, Any]],
    donor_entities: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    donors = _closed_outlines(donor_entities)
    donor_sides = _canonical_side_seams(donors)
    if not donor_sides:
        return host_entities, {"applied": False, "reason": "no_closed_body_side_seams"}
    changed: dict[str, dict[str, Any]] = {}
    meta_rows: list[dict[str, Any]] = []
    for host in _closed_body_candidates(host_entities, include_sew=True):
        host_loop = _open_loop(_points(host))
        replacements = _side_replacements(
            host_loop, _piece_role(host), _center_x(host_loop), donor_sides,
        )
        if not replacements:
            continue
        eid = str(host.get("entity_id") or "")
        changed[eid] = _set_points(host, _splice_spans(host_loop, replacements))
        meta_rows.append({
            "entity_id": eid,
            "piece_role": _piece_role(host),
            "sides": list(_sides_to_morph(_piece_role(host))),
            "line_role": _line_role(host),
        })
    if not changed:
        return host_entities, {"applied": False, "reason": "no_closed_body_side_seams"}
    touched = {
        str(entity.get("piece_id") or entity.get("entity_id") or "")
        for entity in host_entities
        if str(entity.get("entity_id") or "") in changed
    }
    out: list[dict[str, Any]] = []
    for entity in host_entities:
        eid = str(entity.get("entity_id") or "")
        pid = str(entity.get("piece_id") or eid)
        if eid in changed:
            out.append(changed[eid])
            continue
        if pid in touched and _outline_family(entity) and _is_closed(_points(entity)):
            continue
        out.append(entity)
    return drop_extra_closed_outlines(out), {"applied": True, "mode": "side_seam_morph", "modified": meta_rows}


# Chest/length/neck grade: move structure points, not affine-stretch the piece.
ARMHOLE_LINE_ROLES = {"armhole_front", "armhole_back", "armhole_seam"}
NECK_LINE_ROLES = {"neckline", "front_neckline", "back_neckline"}
BODY_GRADE_ROLES = BODY_SIDE_ROLES | {"front_placket", "front_yoke", "back_yoke", "placket", "yoke"}


def _x_origin(role: str, loop: list[list[float]]) -> float:
    xs = [p[0] for p in loop]
    if role == "front_left":
        return max(xs)
    if role == "front_right":
        return min(xs)
    return (min(xs) + max(xs)) / 2.0


def _in_neck_band(x: float, y: float, rec: dict[str, float]) -> bool:
    return y >= rec["maxy"] - 0.22 * rec["h"] and abs(x - rec["origin_x"]) <= 0.32 * rec["w"]


def _dist_to_seg(p: list[float], a: list[float], b: list[float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 < 1e-12:
        return _hypot(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _run_straight(pts: list[list[float]], i: int, j: int, tol: float) -> bool:
    if j <= i + 1:
        return True
    a, b = pts[i], pts[j]
    return all(_dist_to_seg(pts[k], a, b) <= tol for k in range(i + 1, j))


def _restore_straight(
    src: list[list[float]],
    dst: list[list[float]],
    *,
    tol: float = 1.6,
    min_len: float = 12.0,
    chest_y: float | None = None,
) -> list[list[float]]:
    """Keep source-straight spans straight. Split at chest so hem let-out stays parallel."""
    n = min(len(src), len(dst))
    if n < 3:
        return dst
    out = [p[:] for p in dst]

    def apply(i: int, j: int) -> None:
        if j < i + 2 or _hypot(src[i], src[j]) < min_len:
            return
        if chest_y is not None:
            ys = [src[k][1] - chest_y for k in range(i, j + 1)]
            if min(ys) < -1.0 and max(ys) > 1.0:
                return
        total = sum(_hypot(src[k], src[k + 1]) for k in range(i, j)) or 1.0
        acc = 0.0
        a, b = out[i], out[j]
        for k in range(i + 1, j):
            acc += _hypot(src[k - 1], src[k])
            t = acc / total
            out[k] = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]

    i = 0
    while i < n - 1:
        j = i + 1
        while j + 1 < n and _run_straight(src, i, j + 1, tol):
            j += 1
        if chest_y is not None:
            cuts = [i]
            for k in range(i, j):
                if (src[k][1] - chest_y) * (src[k + 1][1] - chest_y) <= 0:
                    cuts.append(k + 1)
            cuts.append(j)
            for a, b in zip(cuts, cuts[1:]):
                apply(a, b)
        else:
            apply(i, j)
        i = j if j > i else i + 1
    return out


def _map_body_point(
    x: float,
    y: float,
    rec: dict[str, float],
    width_sx: float,
    length_sy: float,
    *,
    shoulder_s: float = 1.0,
    armhole_s: float = 1.0,
    skip_width: bool = False,
) -> list[float]:
    chest_y = rec["chest_y"]
    ny = y if y >= chest_y else chest_y + (y - chest_y) * length_sy
    if skip_width:
        return [x, ny]
    _ = armhole_s
    if y >= chest_y - 1e-9:
        span = max(rec["maxy"] - chest_y, 1.0)
        t = min(1.0, max(0.0, (y - chest_y) / span))
        sx = width_sx + (shoulder_s - width_sx) * t
    else:
        sx = width_sx
    if abs(sx - 1.0) < 1e-6:
        return [x, ny]
    return [rec["origin_x"] + (x - rec["origin_x"]) * sx, ny]


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
    if role in {"front_placket", "placket", "front_yoke"}:
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
    shoulder_s: float = 1.0,
    armhole_s: float = 1.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Measurement grade on structure points: chest, length, shoulder, armhole, neck."""
    meta: dict[str, Any] = {
        "mode": "body_structure_grade",
        "width_sx": round(width_sx, 5),
        "length_sy": round(length_sy, 5),
        "neck_s": round(neck_s, 5),
        "shoulder_s": round(shoulder_s, 5),
        "armhole_s": round(armhole_s, 5),
    }
    if all(abs(value - 1.0) < 1e-6 for value in (width_sx, length_sy, neck_s, shoulder_s, armhole_s)):
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
        pts = _points(entity)
        if len(pts) < 2:
            out.append(entity)
            continue
        placket = role in {"front_placket", "placket"}
        if lr in NECK_LINE_ROLES and do_neck:
            cx, cy = rec["neck_cx"], rec["neck_cy"]
            new_pts = [[cx + (p[0] - cx) * neck_s, cy + (p[1] - cy) * neck_s] for p in pts]
        else:
            is_outline = _is_closed(pts) and (lr in OUTLINE_LINE_ROLES or len(pts) >= 16)
            work = _open_loop(pts) if is_outline else pts
            new_pts = [
                _map_body_point(
                    p[0], p[1], rec, width_sx, length_sy,
                    shoulder_s=shoulder_s, armhole_s=armhole_s,
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
            new_pts = _restore_straight(work, new_pts, chest_y=rec["chest_y"])
            if is_outline and (not new_pts or _hypot(new_pts[0], new_pts[-1]) > 1e-6):
                new_pts.append(new_pts[0][:])
        out.append(_set_points(entity, new_pts))
        n_changed += 1
    meta["applied"] = n_changed > 0
    meta["n_changed"] = n_changed
    return drop_extra_closed_outlines(out), meta


def _bbox_w(entity: dict[str, Any]) -> float:
    xs = [p[0] for p in _points(entity)]
    return max(xs) - min(xs) if len(xs) >= 2 else 0.0


def _chest_width(entity: dict[str, Any]) -> float:
    pts = _open_loop(_points(entity))
    rec = _outline_params(pts, _piece_role(entity))
    if rec:
        band = max(12.0, 0.05 * rec["h"])
        xs = [p[0] for p in pts if abs(p[1] - rec["chest_y"]) <= band]
        if len(xs) >= 2:
            return max(xs) - min(xs)
    return _bbox_w(entity)


def shirt_body_sanity_warnings(
    before: list[dict[str, Any]] | None,
    after: list[dict[str, Any]],
    *,
    width_sx: float = 1.0,
) -> list[str]:
    """Catch split-panel backs and chest-width regression after grade."""
    msgs: list[str] = []
    after_out = _closed_outlines(after)
    backs = [row for row in after_out if _piece_role(row) == "back_body"]
    sides = [row for row in after_out if _piece_role(row) == "side_panel"]
    if backs and sides:
        bw = max(_bbox_w(row) for row in backs)
        sw = sum(_bbox_w(row) for row in sides)
        if sw > 0 and bw < 0.55 * (bw + sw):
            cad = str((backs[0].get("source") or {}).get("cad_name") or "后片")
            short = cad.split(".")[-1].split("_")[0]
            msgs.append(f"后片「{short}」是分片中后片（{bw:.0f}mm），胸宽在侧片上，不是放码把整片后背压窄")
    if before and width_sx >= 1.0 - 1e-9:
        before_w = {
            str(row.get("piece_id") or ""): _chest_width(row)
            for row in _closed_outlines(before)
            if _piece_role(row) in BODY_SIDE_ROLES
        }
        for row in after_out:
            if _piece_role(row) not in BODY_SIDE_ROLES:
                continue
            prev = before_w.get(str(row.get("piece_id") or ""))
            now = _chest_width(row)
            if prev and now and now < prev * 0.97:
                msgs.append(
                    f"{_piece_role(row)}放码后胸宽 {prev:.0f}→{now:.0f}mm（胸围系数 {width_sx:.3f}，应变宽）"
                )
    return msgs

