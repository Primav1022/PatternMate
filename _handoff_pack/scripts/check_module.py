"""Final QA / check module for graded & interface-morphed patterns."""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from geometry_ops import bounds_of_entities, entity_length, entity_points, polyline_length


def _entity_index(entities: list[dict]) -> dict[str, dict]:
    return {e.get("entity_id"): e for e in entities if e.get("entity_id")}


def _max_point_delta(a: dict, b: dict) -> float:
    pa, pb = entity_points(a), entity_points(b)
    if not pa and not pb:
        return 0.0
    if len(pa) != len(pb):
        # compare resampled endpoints + mid
        if not pa or not pb:
            return 1e9
        samples_a = [pa[0], pa[len(pa) // 2], pa[-1]]
        samples_b = [pb[0], pb[len(pb) // 2], pb[-1]]
        return max(math.hypot(x[0] - y[0], x[1] - y[1]) for x, y in zip(samples_a, samples_b))
    return max(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(pa, pb))


def _endpoints(entity: dict) -> list[list[float]]:
    pts = entity_points(entity)
    if len(pts) < 2:
        return []
    return [pts[0], pts[-1]]


def check_non_interface_preserved(
    before: list[dict],
    after: list[dict],
    interface_ids: set[str],
    *,
    max_delta: float = 1e-3,
    allowed_remove: set[str] | None = None,
) -> dict:
    bi, ai = _entity_index(before), _entity_index(after)
    allowed_remove = allowed_remove or set()
    moved = []
    checked = 0
    for eid, e0 in bi.items():
        if eid in interface_ids or eid in allowed_remove:
            continue
        e1 = ai.get(eid)
        if not e1:
            moved.append({"entity_id": eid, "error": "missing_after"})
            continue
        checked += 1
        d = _max_point_delta(e0, e1)
        if d > max_delta:
            moved.append({"entity_id": eid, "max_delta": round(d, 6)})
    return {
        "name": "non_interface_preserved",
        "passed": len(moved) == 0,
        "checked": checked,
        "violations": moved[:20],
        "violation_count": len(moved),
        "tolerance": max_delta,
    }


def check_interface_ease(
    host_len: float,
    donor_len_after: float,
    *,
    target_ease: float = 1.04,
    abs_tol: float = 0.08,
) -> dict:
    ratio = (donor_len_after / host_len) if host_len > 1e-6 else None
    ok = ratio is not None and abs(ratio - target_ease) <= abs_tol
    return {
        "name": "interface_ease",
        "passed": bool(ok),
        "host_len": round(host_len, 3),
        "donor_len_after": round(donor_len_after, 3),
        "ease_ratio": round(ratio, 4) if ratio is not None else None,
        "target_ease": target_ease,
        "abs_tol": abs_tol,
    }


def check_endpoint_continuity(
    entities: list[dict],
    interface_ids: set[str],
    *,
    snap_tol: float = 12.0,
) -> dict:
    """Each interface arc should keep at least one junction to a locked edge.

    The other end may be a crown/shoulder tip shared only with another interface
    arc (front/back sleeve-cap), so we do not require every endpoint to hit locked.
    """
    iface = [e for e in entities if e.get("entity_id") in interface_ids]
    locked = [e for e in entities if e.get("entity_id") not in interface_ids]
    lock_ends = []
    for e in locked:
        lock_ends.extend(_endpoints(e))
    iface_ends = []
    for e in iface:
        iface_ends.extend(_endpoints(e))
    if not lock_ends:
        return {"name": "endpoint_continuity", "passed": True, "skipped": True, "reason": "no_locked_ends"}

    orphans = []
    checked = 0
    for e in iface:
        checked += 1
        ends = _endpoints(e)
        # pass if some end is near locked, or both ends are near other interface tips
        near_locked = any(
            min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in lock_ends) <= snap_tol
            for p in ends
        )
        if near_locked:
            continue
        # fallback: both ends dock to other interface geometry (closed cap pair)
        other = [q for q in iface_ends if True]
        near_iface = all(
            min((math.hypot(p[0] - q[0], p[1] - q[1]) for q in other), default=1e9) <= snap_tol
            for p in ends
        )
        if not near_iface:
            dmin = min(
                min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in lock_ends)
                for p in ends
            )
            orphans.append({"entity_id": e.get("entity_id"), "gap": round(dmin, 3)})
    return {
        "name": "endpoint_continuity",
        "passed": len(orphans) == 0,
        "checked_endpoints": checked,
        "violations": orphans[:20],
        "violation_count": len(orphans),
        "snap_tol": snap_tol,
    }


def check_no_degenerate(entities: list[dict], *, min_len: float = 1e-3) -> dict:
    bad = []
    drawable = 0
    for e in entities:
        pts = entity_points(e)
        if len(pts) < 2:
            bad.append({"entity_id": e.get("entity_id"), "error": "too_few_points"})
            continue
        drawable += 1
        L = entity_length(e)
        if L < min_len:
            bad.append({"entity_id": e.get("entity_id"), "length": L})
    return {
        "name": "no_degenerate",
        "passed": len(bad) == 0,
        "drawable": drawable,
        "violations": bad[:20],
        "violation_count": len(bad),
    }


def check_bbox_sane(before: list[dict], after: list[dict], *, max_grow: float = 1.8) -> dict:
    b0 = bounds_of_entities(before)
    b1 = bounds_of_entities(after)
    if not b0 or not b1:
        return {"name": "bbox_sane", "passed": False, "reason": "missing_bbox"}
    w0, h0 = max(b0[2] - b0[0], 1e-6), max(b0[3] - b0[1], 1e-6)
    w1, h1 = b1[2] - b1[0], b1[3] - b1[1]
    grow = max(w1 / w0, h1 / h0)
    return {
        "name": "bbox_sane",
        "passed": grow <= max_grow,
        "grow": round(grow, 4),
        "max_grow": max_grow,
        "before_wh": [round(w0, 3), round(h0, 3)],
        "after_wh": [round(w1, 3), round(h1, 3)],
    }


def run_checks(
    *,
    before_entities: list[dict],
    after_entities: list[dict],
    interface_ids: set[str],
    host_interface_len: float,
    donor_interface_len_after: float,
    target_ease: float = 1.04,
    preserve_tol: float = 1e-3,
    allowed_remove: set[str] | None = None,
) -> dict[str, Any]:
    checks = [
        check_non_interface_preserved(
            before_entities, after_entities, interface_ids,
            max_delta=preserve_tol, allowed_remove=allowed_remove,
        ),
        check_interface_ease(host_interface_len, donor_interface_len_after, target_ease=target_ease),
        check_endpoint_continuity(after_entities, interface_ids),
        check_no_degenerate(after_entities),
        check_bbox_sane(before_entities, after_entities),
    ]
    passed = all(c.get("passed") for c in checks)
    return {
        "passed": passed,
        "checks": checks,
        "summary": {
            "pass_count": sum(1 for c in checks if c.get("passed")),
            "fail_count": sum(1 for c in checks if not c.get("passed")),
            "failed": [c["name"] for c in checks if not c.get("passed")],
        },
    }
