"""Front/back armhole ↔ sleeve-cap matching with cap-height constraint.

Avoids the old "inflate bulge until perimeter matches" spikes.
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from geometry_ops import (
    bounds_of_entities,
    entity_length,
    entity_points,
    polyline_length,
)


Point = list[float]

FRONT_ARM = {"armhole_front", "armhole"}
BACK_ARM = {"armhole_back"}
BODY_ROLES = {
    "front_body", "back_body", "front_left", "front_right", "back_yoke", "front_placket",
}
SLEEVE_ROLES = {
    "sleeve", "sleeve_left", "sleeve_right",
}
SLEEVE_ROLES_ALL = SLEEVE_ROLES | {
    "cuff", "sleeve_placket", "sleeve_placket_extension", "rib_cuff",
}


def _piece_ids(ir: dict, roles: set[str]) -> set[str]:
    return {p["piece_id"] for p in ir.get("piece_instances") or [] if p.get("piece_role") in roles}


def _chain_len(ir: dict, chain: dict) -> float:
    """Length of the longest endpoint-connected component (ignore nested L/R copies)."""
    ents = _entities_for_chains(ir, [chain])
    comps = _connected_components(ents)
    if not comps:
        return 0.0
    return float(sum(entity_length(e) for e in comps[0]))


def _chains_for(ir: dict, roles: set[str], piece_ids: set[str] | None = None) -> list[dict]:
    out = []
    for chain in ir.get("edge_chains") or []:
        if (chain.get("edge_role") or "") not in roles:
            continue
        if piece_ids is not None and chain.get("piece_id") not in piece_ids:
            continue
        if _chain_len(ir, chain) >= 8:
            out.append(chain)
    return out


def _dedupe_lengths(lengths: list[float], *, rel: float = 0.02) -> list[float]:
    """Drop near-duplicate lengths (nested / mirrored copies)."""
    kept: list[float] = []
    for L in sorted(lengths, reverse=True):
        if any(abs(L - k) <= rel * max(k, 1.0) for k in kept):
            continue
        kept.append(L)
    return kept


def measure_armhole_fb(body_ir: dict) -> dict[str, Any]:
    """Measure front/back armhole lengths from body (+yoke) pieces."""
    body_ids = _piece_ids(body_ir, BODY_ROLES)
    front_chains = _chains_for(body_ir, {"armhole_front"}, body_ids)
    back_chains = _chains_for(body_ir, {"armhole_back"}, body_ids)
    if not front_chains:
        front_chains = _chains_for(body_ir, FRONT_ARM, body_ids)
    if not back_chains:
        back_chains = _chains_for(body_ir, BACK_ARM | {"armhole"}, body_ids)

    def side_len(chains: list[dict], *, sum_pieces: bool) -> float:
        """One physical armhole side: longest CC per piece; optionally sum yoke+back."""
        if not chains:
            return 0.0
        by_piece: dict[str, list[float]] = {}
        for c in chains:
            by_piece.setdefault(c.get("piece_id") or "?", []).append(_chain_len(body_ir, c))
        per_piece = []
        for lens in by_piece.values():
            uniq = _dedupe_lengths(lens)
            if uniq:
                per_piece.append(uniq[0])
        if not per_piece:
            return 0.0
        if sum_pieces:
            return float(sum(per_piece))
        return float(max(per_piece))

    af = side_len(front_chains, sum_pieces=False)
    ab = side_len(back_chains, sum_pieces=True)

    body_ents = [a for a in body_ir.get("atomic_entities") or [] if a.get("piece_id") in body_ids]
    # depth from a single front/back piece bbox if possible
    depth = 0.0
    piece_boxes = []
    for pid in body_ids:
        ents = [a for a in body_ents if a.get("piece_id") == pid]
        b = bounds_of_entities(ents)
        if b:
            piece_boxes.append(b[3] - b[1])
    if piece_boxes:
        depth = 0.35 * sorted(piece_boxes)[len(piece_boxes) // 2]
    else:
        b = bounds_of_entities(body_ents)
        if b:
            depth = 0.22 * (b[3] - b[1])

    if af <= 1e-6 and ab <= 1e-6:
        from geometry_ops import role_edge_length
        total = role_edge_length(body_ir, {"armhole", "armhole_front", "armhole_back"}, BODY_ROLES)
        af, ab = total * 0.45, total * 0.55
    elif af <= 1e-6:
        af = ab * 0.85
    elif ab <= 1e-6:
        ab = af * 1.15

    return {
        "Af": af,
        "Ab": ab,
        "A": af + ab,
        "depth": depth,
        "front_chains": len(front_chains),
        "back_chains": len(back_chains),
    }


def _entities_for_chains(ir: dict, chains: list[dict], live_entities: list[dict] | None = None) -> list[dict]:
    ids = []
    seen = set()
    for c in chains:
        for eid in c.get("ordered_entity_ids") or []:
            if eid and eid not in seen:
                seen.add(eid)
                ids.append(eid)
    src = {e.get("entity_id"): e for e in (live_entities or ir.get("atomic_entities") or [])}
    return [src[i] for i in ids if i in src]


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _connected_components(ents: list[dict], *, snap: float = 8.0) -> list[list[dict]]:
    """Group entities into endpoint-connected components; keep longest first."""
    if not ents:
        return []
    remaining = list(ents)
    comps: list[list[dict]] = []
    while remaining:
        comp = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            ends = []
            for e in comp:
                pts = entity_points(e)
                if len(pts) >= 2:
                    ends.extend([pts[0], pts[-1]])
            nxt = []
            for e in remaining:
                pts = entity_points(e)
                if len(pts) < 2:
                    nxt.append(e)
                    continue
                if any(_dist(pts[0], p) <= snap or _dist(pts[-1], p) <= snap for p in ends):
                    comp.append(e)
                    changed = True
                else:
                    nxt.append(e)
            remaining = nxt
        comps.append(comp)
    comps.sort(key=lambda c: sum(entity_length(e) for e in c), reverse=True)
    return comps


def _polyline_from_entities(ents: list[dict]) -> list[Point]:
    """Walk entities into one polyline; only the longest connected component."""
    comps = _connected_components(ents)
    if not comps:
        return []
    ents = comps[0]
    pts: list[Point] = []
    unused = list(ents)
    # seed with longest entity
    unused.sort(key=entity_length, reverse=True)
    cur = unused.pop(0)
    pts = [p[:] for p in entity_points(cur)]
    while unused:
        best_i, best_rev, best_d, at_end = -1, False, 1e18, True
        for i, e in enumerate(unused):
            ep = entity_points(e)
            if len(ep) < 2:
                continue
            for rev in (False, True):
                seq = list(reversed(ep)) if rev else ep
                d0 = _dist(seq[0], pts[0])
                d1 = _dist(seq[0], pts[-1])
                if d1 < best_d:
                    best_d, best_i, best_rev, at_end = d1, i, rev, True
                if d0 < best_d:
                    best_d, best_i, best_rev, at_end = d0, i, rev, False
        if best_i < 0 or best_d > 12.0:
            break
        e = unused.pop(best_i)
        ep = entity_points(e)
        seq = list(reversed(ep)) if best_rev else ep
        if at_end:
            pts.extend(p[:] for p in seq[1:])
        else:
            pts = [p[:] for p in seq[:-1]][::-1] + pts
            # seq[-1] should match pts start; prepend seq[:-1] reversed... already did
    return pts


def _cap_chains_on_piece(sleeve_ir: dict, piece_id: str) -> tuple[list[dict], list[dict]]:
    front = [c for c in _chains_for(sleeve_ir, {"sleeve_cap_front"}, {piece_id})]
    back = [c for c in _chains_for(sleeve_ir, {"sleeve_cap_back"}, {piece_id})]
    generic = [c for c in _chains_for(sleeve_ir, {"sleeve_cap", "sleeve_head"}, {piece_id})]
    if front or back:
        return front, back
    return generic, []


def _geometric_cap_entities(piece_ents: list[dict]) -> list[dict]:
    """Fallback when sleeve_cap roles are missing (e.g. C2530714)."""
    b = bounds_of_entities(piece_ents)
    if not b:
        return []
    w, h = b[2] - b[0], b[3] - b[1]
    # horizontal shirt sleeve → cap along the higher-Y long edge
    use_top = w >= h * 0.85
    cands = []
    for e in piece_ents:
        pts = entity_points(e)
        if len(pts) < 6:
            continue
        L = entity_length(e)
        if L < 60:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        sx, sy = max(xs) - min(xs), max(ys) - min(ys)
        if sy > sx * 2.2:
            continue  # underarm-like
        avg_y = sum(ys) / len(ys)
        avg_x = sum(xs) / len(xs)
        if use_top:
            if avg_y < b[3] - 0.35 * h:
                continue
            score = L * (1.0 + 0.002 * len(pts))
        else:
            # vertical sleeve: cap near max-Y end
            if avg_y < b[3] - 0.4 * h:
                continue
            score = L * (1.0 + 0.002 * len(pts))
        cands.append((score, L, e))
    cands.sort(key=lambda t: t[0], reverse=True)
    # dedupe near-identical geometry (seam allowance pairs)
    picked: list[dict] = []
    for _, L, e in cands:
        pts = entity_points(e)
        mid = pts[len(pts) // 2]
        if any(
            _dist(mid, entity_points(p)[len(entity_points(p)) // 2]) < 8.0
            and abs(L - entity_length(p)) < 0.05 * L
            for p in picked
        ):
            continue
        picked.append(e)
        if len(picked) >= 6:
            break
    # keep longest connected component among candidates
    comps = _connected_components(picked, snap=10.0)
    return comps[0] if comps else []


def _smooth_keep_ends(points: list[Point], passes: int = 2) -> list[Point]:
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


def _oriented_normal(
    p0: Point, p1: Point, prefer_side: Point | None
) -> tuple[float, float, float, float, float]:
    chord = [p1[0] - p0[0], p1[1] - p0[1]]
    clen = math.hypot(chord[0], chord[1]) or 1.0
    ux, uy = chord[0] / clen, chord[1] / clen
    nx, ny = -uy, ux
    if prefer_side is not None:
        mid = [(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2]
        vx, vy = prefer_side[0] - mid[0], prefer_side[1] - mid[1]
        if vx * nx + vy * ny < 0:
            nx, ny = -nx, -ny
    return ux, uy, nx, ny, clen


def build_smooth_cap_arc(
    p0: Point,
    p1: Point,
    *,
    target_len: float,
    max_height: float,
    prefer_side: Point | None = None,
    samples: int = 28,
) -> list[Point]:
    """Full sleeve-cap: chord + sin(πt)·h·n (peak at mid, zero at both ends)."""
    ux, uy, nx, ny, clen = _oriented_normal(p0, p1, prefer_side)

    def curve(h: float) -> list[Point]:
        out = []
        for i in range(samples + 1):
            t = i / samples
            s = math.sin(math.pi * t)
            out.append([p0[0] + ux * clen * t + nx * h * s, p0[1] + uy * clen * t + ny * h * s])
        return out

    return _search_height_curve(curve, target_len, max_height, clen)


def build_half_cap_to_crown(
    p_underarm: Point,
    p_crown: Point,
    *,
    target_len: float,
    max_height: float,
    prefer_side: Point | None = None,
    samples: int = 24,
) -> list[Point]:
    """Half sleeve-cap: height 0 at underarm, max at crown (no valley at shoulder)."""
    # bulge toward prefer_side, or toward a point slightly off the chord mid
    if prefer_side is None:
        prefer_side = [
            (p_underarm[0] + p_crown[0]) / 2 + (p_crown[1] - p_underarm[1]) * 0.1,
            (p_underarm[1] + p_crown[1]) / 2 - (p_crown[0] - p_underarm[0]) * 0.1,
        ]
    ux, uy, nx, ny, clen = _oriented_normal(p_underarm, p_crown, prefer_side)

    def curve(h: float) -> list[Point]:
        out = []
        for i in range(samples + 1):
            t = i / samples
            # sin(πt/2): 0 at underarm → 1 at crown
            s = math.sin(0.5 * math.pi * t)
            out.append([
                p_underarm[0] + ux * clen * t + nx * h * s,
                p_underarm[1] + uy * clen * t + ny * h * s,
            ])
        return out

    return _search_height_curve(curve, target_len, max_height, clen)


def _search_height_curve(curve_fn, target_len: float, max_height: float, clen: float) -> list[Point]:
    target_len = max(target_len, clen * 1.001)
    lo, hi = 0.0, max(1.0, max_height)
    if polyline_length(curve_fn(hi)) < target_len:
        return _smooth_keep_ends(curve_fn(hi), passes=1)
    for _ in range(40):
        mid_h = (lo + hi) / 2.0
        if polyline_length(curve_fn(mid_h)) < target_len:
            lo = mid_h
        else:
            hi = mid_h
    return _smooth_keep_ends(curve_fn((lo + hi) / 2.0), passes=1)


def _orient_half_ends(poly: list[Point], crown: Point) -> tuple[Point, Point]:
    """Return (underarm, crown) given a half-cap polyline."""
    if _dist(poly[0], crown) <= _dist(poly[-1], crown):
        return poly[-1][:], poly[0][:]
    return poly[0][:], poly[-1][:]


def _replace_entities_with_polyline(entities: list[dict], template: dict, points: list[Point]) -> dict:
    e = deepcopy(template)
    e["geometry"] = {"points": [p[:] for p in points]}
    e["_morphed_interface"] = True
    e["_morphed_fb"] = True
    return e


def _primary_sleeve_piece_ids(sleeve_ir: dict) -> list[str]:
    """Prefer sleeve_left/right; else top sleeves by bbox area (max 2)."""
    scored = []
    for p in sleeve_ir.get("piece_instances") or []:
        role = p.get("piece_role") or ""
        if role not in SLEEVE_ROLES:
            continue
        pid = p["piece_id"]
        ents = [a for a in sleeve_ir.get("atomic_entities") or [] if a.get("piece_id") == pid]
        b = bounds_of_entities(ents)
        area = (b[2] - b[0]) * (b[3] - b[1]) if b else 0.0
        # prefer named left/right
        bonus = 1e9 if role in {"sleeve_left", "sleeve_right"} else 0.0
        scored.append((bonus + area, pid, role))
    scored.sort(reverse=True)
    # keep at most one left + one right, or two largest
    out = []
    seen_role = set()
    for _, pid, role in scored:
        if role in {"sleeve_left", "sleeve_right"}:
            if role in seen_role:
                continue
            seen_role.add(role)
            out.append(pid)
        elif len(out) < 2:
            out.append(pid)
        if len(out) >= 2 and ({"sleeve_left", "sleeve_right"} <= seen_role or "sleeve" in seen_role or True):
            if len(out) >= 2:
                break
    return out[:2] if out else []


def match_sleeve_front_back(
    sleeve_entities: list[dict],
    sleeve_ir: dict,
    body_ir: dict,
    *,
    ease_front: float = 0.01,
    ease_back: float = 0.03,
    height_k: float = 0.9,
) -> tuple[list[dict], dict]:
    """Morph only sleeve-cap arcs using Af/Ab + capped sleeve-cap height."""
    arm = measure_armhole_fb(body_ir)
    Af, Ab, depth = arm["Af"], arm["Ab"], arm["depth"]
    Tf = Af * (1.0 + ease_front)
    Tb = Ab * (1.0 + ease_back)
    Hmax = max(depth * height_k, (Af + Ab) / 8.0)

    sleeve_piece_ids = _primary_sleeve_piece_ids(sleeve_ir)
    if not sleeve_piece_ids:
        sleeve_piece_ids = list(_piece_ids(sleeve_ir, SLEEVE_ROLES))[:2]

    live = {e.get("entity_id"): deepcopy(e) for e in sleeve_entities if e.get("entity_id")}
    removed: set[str] = set()
    piece_reports = []

    for pid in sleeve_piece_ids:
        front_chains, back_chains = _cap_chains_on_piece(sleeve_ir, pid)
        front_ents = _entities_for_chains(sleeve_ir, front_chains, list(live.values()))
        back_ents = _entities_for_chains(sleeve_ir, back_chains, list(live.values()))
        mode = "tagged"

        # geometric fallback
        if not front_ents and not back_ents:
            piece_live = [e for e in live.values() if e.get("piece_id") == pid]
            geo = _geometric_cap_entities(piece_live)
            if geo:
                front_ents = geo
                mode = "geometric"
            else:
                continue

        def apply_poly(ents: list[dict], new_poly: list[Point]) -> None:
            if not ents or len(new_poly) < 2:
                return
            # only rewrite entities in the longest connected component
            comps = _connected_components(ents)
            use = comps[0] if comps else ents
            for e in use[1:]:
                eid = e.get("entity_id")
                removed.add(eid)
                live.pop(eid, None)
            # drop other components (copies) from interface rewrite but keep geometry locked
            kept = _replace_entities_with_polyline(use, use[0], new_poly)
            removed.discard(use[0].get("entity_id"))
            live[use[0].get("entity_id")] = kept

        # Separate front/back tags: one shared crown tip; each half underarm→tip
        if front_ents and back_ents and mode == "tagged":
            fpoly = _polyline_from_entities(front_ents)
            bpoly = _polyline_from_entities(back_ents)
            if len(fpoly) < 2 or len(bpoly) < 2:
                continue
            best = (1e18, fpoly[-1], bpoly[0])
            for pf in (fpoly[0], fpoly[-1]):
                for pb in (bpoly[0], bpoly[-1]):
                    d = _dist(pf, pb)
                    if d < best[0]:
                        best = (d, pf, pb)
            crown = [(best[1][0] + best[2][0]) / 2, (best[1][1] + best[2][1]) / 2]
            f_mid = fpoly[len(fpoly) // 2]
            b_mid = bpoly[len(bpoly) // 2]
            fu, _ = _orient_half_ends(fpoly, crown)
            bu, _ = _orient_half_ends(bpoly, crown)
            # One smooth full cap fu→bu (single peak); split at peak for front/back entities
            prefer = [
                (f_mid[0] + b_mid[0]) / 2,
                (f_mid[1] + b_mid[1]) / 2,
            ]
            full = build_smooth_cap_arc(
                fu, bu,
                target_len=Tf + Tb,
                max_height=Hmax,
                prefer_side=prefer if prefer else crown,
            )
            # split near original crown parameter / closest point
            split_i = min(range(len(full)), key=lambda i: _dist(full[i], crown))
            split_i = min(max(split_i, 1), len(full) - 2)
            # assign longer target side more points by arc-length ratio Tf:Tb
            total_tgt = Tf + Tb
            need_f = Tf / total_tgt if total_tgt > 1e-9 else 0.5
            acc = 0.0
            full_len = polyline_length(full) or 1.0
            split_i = 1
            for i in range(1, len(full)):
                acc += _dist(full[i - 1], full[i])
                if acc / full_len >= need_f:
                    split_i = i
                    break
            new_f = full[: split_i + 1]
            new_b = full[split_i:]
            # keep orientation matching original underarm ends
            if _dist(new_f[0], fu) > _dist(new_f[-1], fu):
                new_f = list(reversed(new_f))
            if _dist(new_b[0], bu) > _dist(new_b[-1], bu):
                new_b = list(reversed(new_b))
            # ensure shared tip
            tip = full[split_i]
            if _dist(new_f[-1], tip) <= _dist(new_f[0], tip):
                new_f[-1] = tip[:]
            else:
                new_f[0] = tip[:]
            if _dist(new_b[-1], tip) <= _dist(new_b[0], tip):
                new_b[-1] = tip[:]
            else:
                new_b[0] = tip[:]
            apply_poly(front_ents, new_f)
            apply_poly(back_ents, new_b)
            piece_reports.append({
                "piece_id": pid,
                "mode": "fb_full_split",
                "Cf_before": round(polyline_length(fpoly), 3),
                "Cb_before": round(polyline_length(bpoly), 3),
                "Cf_after": round(polyline_length(new_f), 3),
                "Cb_after": round(polyline_length(new_b), 3),
                "Tf": round(Tf, 3),
                "Tb": round(Tb, 3),
                "Hmax": round(Hmax, 3),
            })
            continue

        # Generic / geometric: prefer ONE full smooth cap (single peak), length ≈ Tf+Tb
        all_ents = front_ents + back_ents
        poly = _polyline_from_entities(all_ents)
        if len(poly) < 4:
            continue
        mid = poly[len(poly) // 2]
        # original crown hint for normal orientation
        a, b = poly[0], poly[-1]
        chord = [b[0] - a[0], b[1] - a[1]]
        clen = math.hypot(*chord) or 1.0
        ux, uy = chord[0] / clen, chord[1] / clen
        nx, ny = -uy, ux
        best_i, best_d = 1, -1.0
        for i, p in enumerate(poly):
            d = (p[0] - a[0]) * nx + (p[1] - a[1]) * ny
            if abs(d) > abs(best_d):
                best_d = d
                best_i = i
        crown_hint = poly[min(max(best_i, 1), len(poly) - 2)]
        new_full = build_smooth_cap_arc(
            poly[0], poly[-1],
            target_len=Tf + Tb,
            max_height=Hmax,
            prefer_side=crown_hint,
        )
        apply_poly(all_ents, new_full)
        # report approximate front/back split at peak for diagnostics
        peak_i = max(range(len(new_full)), key=lambda i: abs(
            (new_full[i][0] - new_full[0][0]) * nx + (new_full[i][1] - new_full[0][1]) * ny
        )) if new_full else 0
        piece_reports.append({
            "piece_id": pid,
            "mode": "full_cap" if mode != "geometric" else "geometric_full",
            "Cf_before": round(polyline_length(poly[: peak_i + 1]) if peak_i else 0.0, 3),
            "Cb_before": round(polyline_length(poly[peak_i:]) if peak_i else polyline_length(poly), 3),
            "Cf_after": round(polyline_length(new_full[: peak_i + 1]), 3),
            "Cb_after": round(polyline_length(new_full[peak_i:]), 3),
            "C_after": round(polyline_length(new_full), 3),
            "Tf": round(Tf, 3),
            "Tb": round(Tb, 3),
            "Hmax": round(Hmax, 3),
        })

    out = []
    for e in sleeve_entities:
        eid = e.get("entity_id")
        if eid in removed:
            continue
        if eid in live:
            out.append(live[eid])
        else:
            out.append(deepcopy(e))

    iface_ids = {e.get("entity_id") for e in out if e.get("_morphed_interface")}
    meta: dict[str, Any] = {
        "method": "front_back_cap_height_constrained",
        "armhole": {k: round(v, 3) if isinstance(v, float) else v for k, v in arm.items()},
        "targets": {"Tf": round(Tf, 3), "Tb": round(Tb, 3), "Hmax": round(Hmax, 3)},
        "ease": {"front": ease_front, "back": ease_back, "height_k": height_k},
        "pieces": piece_reports,
        "interface_ids": [i for i in iface_ids if i],
        "applied": bool(piece_reports),
        "locked_entity_count": sum(1 for e in out if not e.get("_morphed_interface")),
        "interface_entity_count": sum(1 for e in out if e.get("_morphed_interface")),
    }
    if piece_reports:
        # one sleeve worth of length (mean if L+R both morphed)
        totals_after = [
            (p.get("Cf_after") or 0) + (p.get("Cb_after") or 0) for p in piece_reports
        ]
        totals_before = [
            (p.get("Cf_before") or 0) + (p.get("Cb_before") or 0) for p in piece_reports
        ]
        meta["length_after"] = round(sum(totals_after) / len(totals_after), 3)
        meta["length_before"] = round(sum(totals_before) / len(totals_before), 3)
        meta["target_length"] = round(Tf + Tb, 3)
        meta["length_error_ratio"] = round(
            (meta["length_after"] - meta["target_length"]) / meta["target_length"], 4
        ) if meta["target_length"] else None
    return out, meta
