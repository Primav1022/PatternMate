#!/usr/bin/env python3
"""Fuse shirt Pattern-IR labels onto original DXF geometry.

Baseline = full dxf_entities.json geometry (nothing dropped).
IR only stamps piece_role / line_role where a match exists.
Unmatched DXF lines stay line_role=unknown and unlabeled piece_id.
"""
from __future__ import annotations

import json
import math
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path("/Users/primav/Documents/博一/CHI27库淑兰服装")
CASE_DXF = ROOT / "CHI27_AI4Manufacturing/annotation_platform/vercel_app/source_cases"
# Read pre-fuse labels only. Do NOT read locked remix baseline (pattern_ir).
IR_DIR = ROOT / "patternmate/data/ir/shirt_v2/pattern_ir_before_writeback"
BACKUP = ROOT / "patternmate/data/ir/shirt_v2/pattern_ir_before_writeback"
OUT_DIR = ROOT / "patternmate/data/ir/shirt_v2/pattern_ir_fused"
# Locked display/migration foundation: pattern_ir -> pattern_ir_remix_v1 (do not overwrite).
LOCKED_REMIX = ROOT / "patternmate/data/ir/shirt_v2/pattern_ir_remix_v1"
SHIRT_IDS = [
    "C2330115", "C2431027", "C2431055", "C2431105", "C2431239", "C2431245",
    "C2431354", "C2431381", "C2530027", "C2530028", "C2530029", "C2530093",
    "C2530098", "C2530110", "C2530117", "C2530128", "C2530131", "C2530137",
    "C2530175", "C2530423", "C2530429", "C2530534", "C2530581", "C2530633",
    "C2530642", "C2530682", "C2530692", "C2530694", "C2530790", "C2530900",
    "C2531023",
]

_USEFUL_IR_ROLES = {
    "side_seam", "shoulder_seam", "armhole_front", "armhole_back",
    "front_neckline", "back_neckline", "neckline", "collar_attach_line",
    "collar_roll_line", "hem_line", "sleeve_cap", "sleeve_hem",
    "cuff_edge", "cuff_attach_line", "cut_line", "placket_line",
}


def _points(entity: dict[str, Any]) -> list[list[float]]:
    raw = entity.get("points") or (entity.get("geometry") or {}).get("points") or []
    out: list[list[float]] = []
    for point in raw:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            out.append([float(point[0]), float(point[1])])
    if out:
        return out
    # POINT / INSERT-like: keep a tiny stub so source content is not dropped.
    pos = entity.get("position")
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        x, y = float(pos[0]), float(pos[1])
        return [[x, y], [x, y]]
    return []


def _closed(points: list[list[float]], tol: float = 1.0) -> bool:
    if len(points) < 3:
        return False
    return math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1]) <= tol


def _bbox(points: list[list[float]]) -> list[float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _center(box: list[float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _centroid(points: list[list[float]]) -> tuple[float, float]:
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def _load_ir(case_id: str) -> dict[str, Any]:
    for path in (BACKUP / f"{case_id}.pattern-ir.json", IR_DIR / f"{case_id}.pattern-ir.json"):
        if not path.exists():
            continue
        data = json.loads(path.resolve().read_text(encoding="utf-8"))
        if data.get("_fused"):
            continue
        return data
    raise FileNotFoundError(case_id)


def _load_dxf_entities(case_id: str) -> list[dict[str, Any]]:
    path = CASE_DXF / case_id / "dxf_entities.json"
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else list(data.get("entities") or [])


def _inside(points: list[list[float]], box: list[float], pad: float) -> bool:
    if len(points) < 1:
        return False
    cx, cy = _centroid(points)
    return (box[0] - pad) <= cx <= (box[2] + pad) and (box[1] - pad) <= cy <= (box[3] + pad)


def _hint_role_from_layer(layer: str, points: list[list[float]], is_cut: bool) -> str | None:
    """Optional layer hint for matched piece members only. Never invent labels for free DXF lines."""
    if is_cut or (layer in {"1", "01"} and _closed(points)):
        return "pattern_boundary"
    if layer in {"8", "08"} and _closed(points):
        return "seam_allowance"
    if layer in {"7", "07"}:
        return "grainline"
    return None


def fuse_case(case_id: str) -> dict[str, Any]:
    ir = _load_ir(case_id)
    dxf_entities = _load_dxf_entities(case_id)

    # 1) Baseline: every DXF entity that can carry geometry.
    fused_by_id: dict[str, dict[str, Any]] = {}
    for index, entity in enumerate(dxf_entities):
        eid = str(entity.get("id") or entity.get("entity_id") or f"dxf_{index:04d}")
        points = _points(entity)
        if len(points) < 2:
            continue
        fused_by_id[eid] = {
            "entity_id": eid,
            "line_role": "unknown",
            "piece_id": None,
            "geometry": {"points": points},
            "source": {
                "dxf_handle": entity.get("handle"),
                "layer": entity.get("layer"),
                "entity_type": entity.get("entity_type"),
                "parent_handle": entity.get("owner") or entity.get("parent_handle"),
                "fused_from": "dxf_entities.json",
                "raw_semantic": entity.get("semantic"),
            },
        }

    if not fused_by_id:
        raise RuntimeError(f"{case_id}: no DXF geometry entities")

    # 2) Match IR pieces → closed layer-1 DXF cuts by area.
    cuts = []
    for eid, row in fused_by_id.items():
        layer = str((row.get("source") or {}).get("layer") or "")
        points = _points(row)
        if layer in {"1", "01"} and _closed(points):
            cuts.append((eid, row, _area(_bbox(points))))

    pieces: list[tuple[dict[str, Any], list[float], float]] = []
    seen_roles: set[str] = set()
    for piece in ir.get("piece_instances") or []:
        role = str(piece.get("piece_role") or "")
        if not role or role in {"unknown", "none", "unlabeled"} or role in seen_roles:
            continue
        bbox = piece.get("bbox") or {}
        try:
            box = [
                float(bbox["min_x"]),
                float(bbox["min_y"]),
                float(bbox["max_x"]),
                float(bbox["max_y"]),
            ]
        except Exception:
            continue
        seen_roles.add(role)
        pieces.append((piece, box, _area(box)))

    used_cut_ids: set[str] = set()
    fused_pieces: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []

    for piece, ir_box, piece_area in sorted(pieces, key=lambda item: -item[2]):
        role = str(piece.get("piece_role"))
        piece_id = str(piece.get("piece_id"))
        candidates = [
            item
            for item in cuts
            if item[0] not in used_cut_ids
            and abs(item[2] - piece_area) <= max(1.0, 0.002 * piece_area)
        ]
        if not candidates:
            remain = [item for item in cuts if item[0] not in used_cut_ids]
            candidates = sorted(remain, key=lambda item: abs(item[2] - piece_area))[:1]
        if not candidates:
            continue
        cut_id, cut_row, cut_area = min(candidates, key=lambda item: abs(item[2] - piece_area))
        used_cut_ids.add(cut_id)
        cut_box = _bbox(_points(cut_row))
        pad = max(40.0, 0.05 * math.hypot(cut_box[2] - cut_box[0], cut_box[3] - cut_box[1]))

        # Stamp piece membership on all DXF entities whose centroid falls in cut bbox.
        member_ids: list[str] = []
        for eid, row in fused_by_id.items():
            points = _points(row)
            if not _inside(points, cut_box, pad=pad):
                continue
            row["piece_id"] = piece_id
            layer = str((row.get("source") or {}).get("layer") or "")
            hint = _hint_role_from_layer(layer, points, is_cut=(eid == cut_id))
            if hint:
                row["line_role"] = hint
            # else keep unknown — do not invent structure labels
            member_ids.append(eid)

        # Transfer IR semantic line_roles onto nearby members (translation IR→DXF).
        ir_c = _center(ir_box)
        dxf_c = _center(cut_box)
        tx, ty = dxf_c[0] - ir_c[0], dxf_c[1] - ir_c[1]
        labeled: list[tuple[str, tuple[float, float]]] = []
        for raw in ir.get("atomic_entities") or []:
            lr = str(raw.get("line_role") or "")
            if lr not in _USEFUL_IR_ROLES:
                continue
            pts = _points(raw)
            if len(pts) < 2:
                continue
            cx, cy = _centroid(pts)
            labeled.append((lr, (cx + tx, cy + ty)))

        diag = math.hypot(cut_box[2] - cut_box[0], cut_box[3] - cut_box[1])
        for eid in member_ids:
            row = fused_by_id[eid]
            if row.get("line_role") not in {"unknown", None, ""}:
                continue
            ecx, ecy = _centroid(_points(row))
            best = None
            best_d = 1e18
            for lr, (lx, ly) in labeled:
                dist = math.hypot(ecx - lx, ecy - ly)
                if dist < best_d:
                    best_d = dist
                    best = lr
            if best and best_d <= max(25.0, 0.08 * diag):
                row["line_role"] = best

        boundary_ids = [
            eid
            for eid in member_ids
            if fused_by_id[eid].get("line_role") in {"pattern_boundary", "seam_allowance", "cut_line"}
            or eid == cut_id
        ]
        internal_ids = [eid for eid in member_ids if eid not in boundary_ids]

        fused_piece = deepcopy(piece)
        fused_piece["boundary_entity_ids"] = boundary_ids or [cut_id]
        fused_piece["internal_entity_ids"] = internal_ids
        fused_piece["bbox"] = {
            "min_x": cut_box[0],
            "min_y": cut_box[1],
            "max_x": cut_box[2],
            "max_y": cut_box[3],
        }
        fused_pieces.append(fused_piece)
        matches.append(
            {
                "role": role,
                "cut_id": cut_id,
                "ir_area": piece_area,
                "dxf_area": cut_area,
                "member_count": len(member_ids),
            }
        )

    if not fused_pieces:
        raise RuntimeError(f"{case_id}: no piece↔cut matches")

    out = deepcopy(ir)
    out["atomic_entities"] = list(fused_by_id.values())
    out["piece_instances"] = fused_pieces
    # Keep original edge_chains only if ids still exist; otherwise clear.
    keep_ids = set(fused_by_id)
    kept_chains = []
    for chain in ir.get("edge_chains") or []:
        ids = [eid for eid in (chain.get("ordered_entity_ids") or []) if eid in keep_ids]
        if ids:
            item = deepcopy(chain)
            item["ordered_entity_ids"] = ids
            kept_chains.append(item)
    out["edge_chains"] = kept_chains
    out["_fused"] = {
        "source": "dxf_entities_baseline+ir_labels",
        "case_id": case_id,
        "dxf_entity_count": len(fused_by_id),
        "labeled_piece_count": len(fused_pieces),
        "matches": matches,
    }
    out["traceability"] = {
        **(out.get("traceability") or {}),
        "fused_from_dxf_entities": True,
        "fused_baseline": "dxf_entities.json",
        "fused_match_count": len(matches),
        "fused_entity_count": len(fused_by_id),
    }
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if OUT_DIR.resolve() == LOCKED_REMIX.resolve():
        print(f"REFUSE: refusing to overwrite locked remix baseline {LOCKED_REMIX}")
        return 2
    ok = fail = 0
    for case_id in SHIRT_IDS:
        try:
            fused = fuse_case(case_id)
            path = OUT_DIR / f"{case_id}.pattern-ir.json"
            path.write_text(json.dumps(fused, ensure_ascii=False) + "\n", encoding="utf-8")
            # Never retarget pattern_ir / remix_v1 from this script.
            # Promote fused → remix_v1 only with an explicit lock/promote step.
            roles = {}
            for entity in fused["atomic_entities"]:
                lr = str(entity.get("line_role") or "unknown")
                roles[lr] = roles.get(lr, 0) + 1
            labeled = sum(1 for e in fused["atomic_entities"] if e.get("piece_id"))
            print(
                f"OK {case_id} dxf_ents={len(fused['atomic_entities'])} "
                f"labeled={labeled} pieces={len(fused['piece_instances'])} roles={roles}"
            )
            ok += 1
        except Exception as exc:
            print(f"FAIL {case_id}: {exc}")
            fail += 1
    print(f"SUMMARY ok={ok} fail={fail} out={OUT_DIR}")
    print(f"NOTE: locked remix baseline unchanged at {LOCKED_REMIX}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
