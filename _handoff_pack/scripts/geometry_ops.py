"""Geometry helpers for IR-based grading / remix experiments."""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any


Point = list[float]


def polyline_length(points: list[Point]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


def entity_points(entity: dict) -> list[Point]:
    geom = entity.get("geometry") or {}
    pts = geom.get("points") or []
    out = []
    for p in pts:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            out.append([float(p[0]), float(p[1])])
    center = geom.get("center")
    if center and len(center) >= 2 and not out:
        r = float(geom.get("radius") or 0)
        # approximate circle as octagon for length/bbox ops
        cx, cy = float(center[0]), float(center[1])
        for i in range(8):
            ang = 2 * math.pi * i / 8
            out.append([cx + r * math.cos(ang), cy + r * math.sin(ang)])
        if out:
            out.append(out[0][:])
    return out


def entity_length(entity: dict) -> float:
    return polyline_length(entity_points(entity))


def bounds_of_points(points: list[Point]) -> list[float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def bounds_of_entities(entities: list[dict]) -> list[float] | None:
    pts = []
    for e in entities:
        pts.extend(entity_points(e))
    return bounds_of_points(pts)


def transform_point(p: Point, *, sx: float, sy: float, ox: float, oy: float, dx: float, dy: float) -> Point:
    return [ox + (p[0] - ox) * sx + dx, oy + (p[1] - oy) * sy + dy]


def transform_entity(entity: dict, *, sx: float = 1.0, sy: float = 1.0, ox: float = 0.0, oy: float = 0.0, dx: float = 0.0, dy: float = 0.0) -> dict:
    e = deepcopy(entity)
    geom = dict(e.get("geometry") or {})
    if geom.get("points"):
        geom["points"] = [transform_point(list(p), sx=sx, sy=sy, ox=ox, oy=oy, dx=dx, dy=dy) for p in geom["points"]]
    if geom.get("center") is not None and len(geom["center"]) >= 2:
        c = transform_point([float(geom["center"][0]), float(geom["center"][1])], sx=sx, sy=sy, ox=ox, oy=oy, dx=dx, dy=dy)
        geom["center"] = c
        if geom.get("radius") is not None:
            geom["radius"] = float(geom["radius"]) * (abs(sx) + abs(sy)) / 2.0
    e["geometry"] = geom
    return e


def simplify_polyline(points: list[Point], tol: float = 0.35) -> list[Point]:
    """Ramer-Douglas-Peucker-ish sequential simplification for DXF cleanliness."""
    if len(points) <= 2:
        return [p[:] for p in points]

    def _rdp(pts: list[Point], epsilon: float) -> list[Point]:
        if len(pts) <= 2:
            return pts
        ax, ay = pts[0]
        bx, by = pts[-1]
        denom = math.hypot(bx - ax, by - ay) or 1.0
        max_d = -1.0
        idx = 0
        for i in range(1, len(pts) - 1):
            px, py = pts[i]
            d = abs((by - ay) * px - (bx - ax) * py + bx * ay - by * ax) / denom
            if d > max_d:
                max_d = d
                idx = i
        if max_d > epsilon:
            left = _rdp(pts[: idx + 1], epsilon)
            right = _rdp(pts[idx:], epsilon)
            return left[:-1] + right
        return [pts[0], pts[-1]]

    return [p[:] for p in _rdp(points, tol)]


def optimize_entity(entity: dict, tol: float = 0.35) -> dict:
    e = deepcopy(entity)
    geom = dict(e.get("geometry") or {})
    pts = geom.get("points")
    if isinstance(pts, list) and len(pts) >= 3:
        cleaned = simplify_polyline([[float(p[0]), float(p[1])] for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2], tol)
        # drop near-duplicate consecutive points
        dedup = []
        for p in cleaned:
            if not dedup or math.hypot(p[0] - dedup[-1][0], p[1] - dedup[-1][1]) > 1e-4:
                dedup.append(p)
        if len(dedup) >= 2:
            geom["points"] = dedup
    e["geometry"] = geom
    return e


def chain_length(ir: dict, chain_id: str) -> float:
    atoms = {a.get("entity_id"): a for a in ir.get("atomic_entities") or []}
    for chain in ir.get("edge_chains") or []:
        if chain.get("edge_chain_id") != chain_id:
            continue
        total = 0.0
        for eid in chain.get("ordered_entity_ids") or []:
            if eid in atoms:
                total += entity_length(atoms[eid])
        return total
    return 0.0


def role_edge_length(ir: dict, roles: set[str], piece_roles: set[str] | None = None) -> float:
    """Robust interface length: keep plausible chains, drop geometric outliers."""
    piece_ids = None
    role_by_piece = {p["piece_id"]: p.get("piece_role") for p in ir.get("piece_instances") or []}
    if piece_roles is not None:
        piece_ids = {pid for pid, role in role_by_piece.items() if role in piece_roles}

    # per-piece diagonal for outlier rejection
    atoms_by_piece: dict[str, list] = {}
    for atom in ir.get("atomic_entities") or []:
        atoms_by_piece.setdefault(atom.get("piece_id") or "", []).append(atom)
    diag = {}
    for pid, ents in atoms_by_piece.items():
        b = bounds_of_entities(ents)
        if b:
            diag[pid] = math.hypot(b[2] - b[0], b[3] - b[1])

    lengths = []
    for chain in ir.get("edge_chains") or []:
        if chain.get("edge_role") not in roles:
            continue
        pid = chain.get("piece_id")
        if piece_ids is not None and pid not in piece_ids:
            continue
        L = chain_length(ir, chain["edge_chain_id"])
        if L < 8:
            continue
        d = diag.get(pid or "", 0.0)
        if d > 0 and L > 1.25 * d:
            continue  # e.g. mislabeled mega-polyline
        lengths.append(L)

    if lengths:
        lengths.sort()
        med = lengths[len(lengths) // 2]
        kept = [L for L in lengths if 0.35 * med <= L <= 2.8 * med]
        if not kept:
            kept = lengths[-min(4, len(lengths)) :]
        # Prefer sum of top unique seams (front/back armhole etc.), capped
        kept = sorted(kept, reverse=True)[:4]
        return float(sum(kept))

    # fallback: atomic line_role
    total = 0.0
    for atom in ir.get("atomic_entities") or []:
        lr = atom.get("line_role") or ""
        if lr in roles or any(r in lr for r in roles):
            if piece_ids is not None and atom.get("piece_id") not in piece_ids:
                continue
            L = entity_length(atom)
            pid = atom.get("piece_id") or ""
            d = diag.get(pid, 0.0)
            if L >= 8 and (d <= 0 or L <= 1.25 * d):
                total += L
    if total > 0:
        return total

    # geometric proxy from piece bbox
    ents = []
    for atom in ir.get("atomic_entities") or []:
        if piece_ids is not None and atom.get("piece_id") not in piece_ids:
            continue
        ents.append(atom)
    b = bounds_of_entities(ents)
    if not b:
        return 0.0
    h = b[3] - b[1]
    w = b[2] - b[0]
    # armhole/sleeve-cap/neck scale proxies for this corpus
    if roles & {"armhole", "armhole_front", "armhole_back", "armhole_seam"}:
        return 0.62 * h
    if roles & {"sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "sleeve_head"}:
        return 0.95 * max(w, h) * 0.55
    if roles & {"neckline", "front_neckline", "back_neckline", "collar_edge"}:
        return 0.55 * w
    return 0.4 * (w + h)


def piece_entities(ir: dict, piece_roles: set[str]) -> list[dict]:
    wanted = {p["piece_id"] for p in ir.get("piece_instances") or [] if p.get("piece_role") in piece_roles}
    return [deepcopy(a) for a in ir.get("atomic_entities") or [] if a.get("piece_id") in wanted]


def all_piece_entities(ir: dict) -> list[dict]:
    return [deepcopy(a) for a in ir.get("atomic_entities") or [] if a.get("piece_id")]


def layout_groups(groups: list[tuple[str, list[dict]]], gap: float = 80.0) -> list[dict]:
    """Place piece groups in a horizontal strip without overlap."""
    out: list[dict] = []
    cursor_x = 0.0
    for name, ents in groups:
        if not ents:
            continue
        b = bounds_of_entities(ents)
        if not b:
            continue
        dx = cursor_x - b[0]
        dy = -b[1]
        moved = [transform_entity(e, dx=dx, dy=dy) for e in ents]
        for e in moved:
            e["_layout_group"] = name
        out.extend(moved)
        nb = bounds_of_entities(moved)
        cursor_x = (nb[2] if nb else cursor_x) + gap
    return out
