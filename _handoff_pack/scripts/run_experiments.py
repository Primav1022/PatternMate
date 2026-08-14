#!/usr/bin/env python3
"""Body-type grading + atomic component remix experiments on ir_corpus."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

from dxf_export import write_entities_dxf
from geometry_ops import (
    all_piece_entities,
    bounds_of_entities,
    layout_groups,
    optimize_entity,
    piece_entities,
    role_edge_length,
    transform_entity,
)

ROOT = Path(__file__).resolve().parents[2]
READY = ROOT / "v1_rule_ready"
OUT = ROOT / "experiments" / "grading" / "outputs"
REPORTS = ROOT / "experiments" / "grading" / "reports"

# Body morph targets (relative to base pattern = 1.0)
# Axes: width (chest/shoulder), length (body), sleeve_len, sleeve_width, neck
BODY_TYPES = {
    "slim_short": {
        "label": "偏瘦偏短",
        "width": 0.94,
        "length": 0.92,
        "sleeve_len": 0.94,
        "sleeve_width": 0.93,
        "neck": 0.96,
    },
    "standard": {
        "label": "标准体",
        "width": 1.00,
        "length": 1.00,
        "sleeve_len": 1.00,
        "sleeve_width": 1.00,
        "neck": 1.00,
    },
    "tall_broad": {
        "label": "偏高偏壮",
        "width": 1.08,
        "length": 1.10,
        "sleeve_len": 1.08,
        "sleeve_width": 1.07,
        "neck": 1.04,
    },
}

BODY_ROLES = {
    "front_body", "back_body", "front_left", "front_right", "back_yoke",
    "front_placket", "neck_binding", "collar", "collar_stand", "collar_interlining",
}
SLEEVE_ROLES = {"sleeve", "sleeve_left", "sleeve_right", "cuff", "sleeve_placket", "sleeve_placket_extension", "rib_cuff"}
NECK_ROLES = {"neck_binding", "collar", "collar_stand", "collar_interlining"}

ARMHOLE_ROLES = {"armhole", "armhole_front", "armhole_back", "armhole_seam", "underarm"}
SLEEVE_CAP_ROLES = {"sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "sleeve_head", "armhole", "armhole_front", "armhole_back"}
NECKLINE_ROLES = {"neckline", "front_neckline", "back_neckline", "collar_edge", "neck_binding_line"}


def load_ir(case_id: str) -> dict:
    path = READY / f"{case_id}.rule-ready.json"
    if not path.exists():
        # tolerate alternate naming
        alts = list(READY.glob(f"{case_id}*.json"))
        if not alts:
            raise FileNotFoundError(case_id)
        path = alts[0]
    return json.loads(path.read_text(encoding="utf-8"))


def role_map(ir: dict) -> dict[str, str]:
    return {p["piece_id"]: p.get("piece_role") or "unknown" for p in ir.get("piece_instances") or []}


def scale_piece_group(entities: list[dict], *, sx: float, sy: float) -> list[dict]:
    b = bounds_of_entities(entities)
    if not b:
        return entities
    ox = (b[0] + b[2]) / 2.0
    oy = (b[1] + b[3]) / 2.0
    return [transform_entity(e, sx=sx, sy=sy, ox=ox, oy=oy) for e in entities]


def match_scale_for_interface(donor_len: float, host_len: float, *, min_ease: float = 1.0, max_ease: float = 1.08) -> float:
    """Scale donor so donor_len * scale ≈ host_len * ease (prefer slight ease)."""
    if donor_len <= 1e-6 or host_len <= 1e-6:
        return 1.0
    target = host_len * ((min_ease + max_ease) / 2.0)
    return target / donor_len


def sleeve_length_axis(entities: list[dict], ir: dict | None = None) -> str:
    """Detect which bbox axis is sleeve LENGTH (cap→hem).

    Shirt sleeves in this corpus are often laid horizontally, so length ≈ X.
    Prefer semantic edges: cap vs hem centroids; fallback to longer bbox side.
    """
    from geometry_ops import entity_points

    if ir is not None:
        atoms = {a.get("entity_id"): a for a in entities}
        # map from original ir chains using current entity geometries when possible
        id_set = set(atoms)
        cap_pts, hem_pts = [], []
        for chain in ir.get("edge_chains") or []:
            role = chain.get("edge_role") or ""
            ids = [eid for eid in (chain.get("ordered_entity_ids") or []) if eid in id_set]
            if not ids:
                continue
            pts = []
            for eid in ids:
                pts.extend(entity_points(atoms[eid]))
            if not pts:
                continue
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            if any(k in role for k in ("sleeve_cap", "armhole")):
                cap_pts.append((cx, cy))
            if any(k in role for k in ("sleeve_hem", "cuff_edge", "cuff_attach", "hem")):
                hem_pts.append((cx, cy))
        if cap_pts and hem_pts:
            c = (sum(p[0] for p in cap_pts) / len(cap_pts), sum(p[1] for p in cap_pts) / len(cap_pts))
            h = (sum(p[0] for p in hem_pts) / len(hem_pts), sum(p[1] for p in hem_pts) / len(hem_pts))
            return "x" if abs(h[0] - c[0]) >= abs(h[1] - c[1]) else "y"

    b = bounds_of_entities(entities)
    if not b:
        return "x"
    return "x" if (b[2] - b[0]) >= (b[3] - b[1]) else "y"


def scale_sleeve_anisotropic(entities: list[dict], *, length_scale: float, width_scale: float, ir: dict | None = None) -> list[dict]:
    """Scale sleeves with independent length (cap→hem) and width (armhole girth) factors."""
    if not entities:
        return entities
    axis = sleeve_length_axis(entities, ir)
    if axis == "x":
        sx, sy = length_scale, width_scale
    else:
        sx, sy = width_scale, length_scale
    return scale_piece_group(entities, sx=sx, sy=sy)


def sleeve_span(entities: list[dict], ir: dict | None = None) -> dict:
    b = bounds_of_entities(entities)
    if not b:
        return {"axis": "x", "length": 0.0, "width": 0.0}
    axis = sleeve_length_axis(entities, ir)
    w, h = b[2] - b[0], b[3] - b[1]
    return {
        "axis": axis,
        "length": w if axis == "x" else h,
        "width": h if axis == "x" else w,
        "bbox_w": w,
        "bbox_h": h,
    }


def body_length_span(entities: list[dict]) -> float:
    b = bounds_of_entities(entities)
    if not b:
        return 0.0
    return b[3] - b[1]


def grade_garment(ir: dict, body_key: str) -> tuple[list[dict], dict]:
    bt = BODY_TYPES[body_key]
    roles = role_map(ir)
    stats = {"body_type": body_key, "label": bt["label"], "piece_scales": {}}

    body_ents: list[dict] = []
    sleeve_ents: list[dict] = []
    other_ents: list[dict] = []

    by_piece: dict[str, list[dict]] = {}
    for e in all_piece_entities(ir):
        by_piece.setdefault(e.get("piece_id") or "none", []).append(e)

    # 1) body / neck first
    for pid, ents in by_piece.items():
        role = roles.get(pid, "unknown")
        if role in SLEEVE_ROLES:
            continue
        if role in NECK_ROLES:
            sx = sy = bt["neck"]
            moved = scale_piece_group(ents, sx=sx, sy=sy)
            body_ents.extend(moved)
        elif role in BODY_ROLES:
            sx, sy = bt["width"], bt["length"]
            moved = scale_piece_group(ents, sx=sx, sy=sy)
            body_ents.extend(moved)
        else:
            sx = (bt["width"] + 1.0) / 2.0
            sy = (bt["length"] + 1.0) / 2.0
            moved = scale_piece_group(ents, sx=sx, sy=sy)
            other_ents.extend(moved)
        for e in moved:
            e["_piece_role"] = role
        stats["piece_scales"][pid] = {"role": role, "sx": sx, "sy": sy}

    # 2) sleeves: length follows body length; width follows sleeve_width then armhole sync on WIDTH only
    raw_sleeves = []
    for pid, ents in by_piece.items():
        role = roles.get(pid, "unknown")
        if role in SLEEVE_ROLES:
            raw_sleeves.extend(deepcopy(ents))
            for e in ents:
                e["_piece_role"] = role
            stats["piece_scales"][pid] = {
                "role": role,
                "length_scale": bt["sleeve_len"],
                "width_scale": bt["sleeve_width"],
                "note": "anisotropic_along_sleeve_axis",
            }

    # sleeve length tracks body length; width tracks girth / later armhole sync
    length_scale = 0.45 * bt["sleeve_len"] + 0.55 * bt["length"]
    width_scale = bt["sleeve_width"]
    sleeve_ents = scale_sleeve_anisotropic(raw_sleeves, length_scale=length_scale, width_scale=width_scale, ir=ir)
    for e in sleeve_ents:
        if not e.get("_piece_role"):
            e["_piece_role"] = roles.get(e.get("piece_id") or "", "sleeve")

    before = sleeve_span(sleeve_ents, ir)
    mini_body = {
        "edge_chains": ir.get("edge_chains"),
        "atomic_entities": body_ents,
        "piece_instances": ir.get("piece_instances"),
    }
    mini_sleeve = {
        "edge_chains": ir.get("edge_chains"),
        "atomic_entities": sleeve_ents,
        "piece_instances": ir.get("piece_instances"),
    }
    arm = role_edge_length(mini_body, ARMHOLE_ROLES, BODY_ROLES - NECK_ROLES)
    cap = role_edge_length(mini_sleeve, SLEEVE_CAP_ROLES, SLEEVE_ROLES)
    width_sync = 1.0
    if arm > 1e-6 and cap > 1e-6:
        width_sync = match_scale_for_interface(cap, arm, min_ease=1.02, max_ease=1.06)
        width_sync = max(0.9, min(1.15, width_sync))
        # ONLY scale width axis — keep sleeve length unchanged
        sleeve_ents = scale_sleeve_anisotropic(sleeve_ents, length_scale=1.0, width_scale=width_sync, ir=ir)
        cap *= width_sync
    after = sleeve_span(sleeve_ents, ir)
    stats["sleeve_length_scale"] = round(length_scale, 4)
    stats["sleeve_width_scale"] = round(width_scale, 4)
    stats["sleeve_width_sync"] = round(width_sync, 4)
    stats["sleeve_axis"] = after["axis"]
    stats["sleeve_span"] = {
        "before_sync_length": round(before["length"], 3),
        "before_sync_width": round(before["width"], 3),
        "after_length": round(after["length"], 3),
        "after_width": round(after["width"], 3),
    }
    stats["interface"] = {
        "armhole_len": round(arm, 3),
        "sleeve_cap_len": round(cap, 3),
        "cap_minus_armhole": round(cap - arm, 3) if arm and cap else None,
        "ease_ratio": round(cap / arm, 4) if arm > 1e-6 and cap > 1e-6 else None,
    }
    graded = body_ents + sleeve_ents + other_ents
    return graded, stats


def remix_body_plus_sleeves(body_ir: dict, sleeve_ir: dict, *, tag: str) -> tuple[list[dict], dict]:
    """A 衣身(+领) + B 袖：袖宽贴袖窿，袖长随衣身长关联。"""
    body_ents = piece_entities(body_ir, BODY_ROLES)
    sleeve_ents = piece_entities(sleeve_ir, SLEEVE_ROLES)
    sleeve_body_ents = piece_entities(sleeve_ir, BODY_ROLES - NECK_ROLES)

    arm = role_edge_length(body_ir, ARMHOLE_ROLES, BODY_ROLES - NECK_ROLES)
    if arm <= 1e-6:
        arm = role_edge_length(body_ir, ARMHOLE_ROLES)
    cap = role_edge_length(sleeve_ir, SLEEVE_CAP_ROLES, SLEEVE_ROLES)
    if cap <= 1e-6:
        from geometry_ops import entity_length
        cap = sum(entity_length(e) for e in sleeve_ents) * 0.22

    host_len = body_length_span(piece_entities(body_ir, BODY_ROLES - NECK_ROLES))
    donor_len = body_length_span(sleeve_body_ents) or host_len
    length_scale = (host_len / donor_len) if donor_len > 1e-6 else 1.0
    length_scale = max(0.85, min(1.25, length_scale))

    width_scale = match_scale_for_interface(cap, arm)
    width_scale = max(0.82, min(1.25, width_scale))

    sleeve_scaled = scale_sleeve_anisotropic(
        sleeve_ents, length_scale=length_scale, width_scale=width_scale, ir=sleeve_ir,
    )
    span = sleeve_span(sleeve_scaled, sleeve_ir)

    roles_b = role_map(body_ir)
    roles_s = role_map(sleeve_ir)
    for e in body_ents:
        e["_piece_role"] = roles_b.get(e.get("piece_id") or "", "")
        e["_source_case"] = body_ir.get("case_id")
    for e in sleeve_scaled:
        e["_piece_role"] = roles_s.get(e.get("piece_id") or "", "")
        e["_source_case"] = sleeve_ir.get("case_id")

    laid = layout_groups([("body_from_A", body_ents), ("sleeve_from_B", sleeve_scaled)])
    meta = {
        "tag": tag,
        "body_case": body_ir.get("case_id"),
        "sleeve_case": sleeve_ir.get("case_id"),
        "armhole_len_A": round(arm, 3),
        "sleeve_cap_len_B": round(cap, 3),
        "sleeve_length_scale": round(length_scale, 4),
        "sleeve_width_scale": round(width_scale, 4),
        "sleeve_axis": span["axis"],
        "sleeve_length_after": round(span["length"], 3),
        "sleeve_width_after": round(span["width"], 3),
        "host_body_length": round(host_len, 3),
        "donor_body_length": round(donor_len, 3),
        "matched_sleeve_cap": round(cap * width_scale, 3),
        "ease_ratio_after": round((cap * width_scale) / arm, 4) if arm > 1e-6 else None,
        "body_entities": len(body_ents),
        "sleeve_entities": len(sleeve_scaled),
    }
    return laid, meta


def remix_neck_body_sleeves(neck_ir: dict, body_ir: dict, sleeve_ir: dict, *, tag: str) -> tuple[list[dict], dict]:
    """领(A) + 衣身(B) + 袖(C)：领口缩放 + 袖宽贴袖窿 + 袖长随衣身长。"""
    neck_ents = piece_entities(neck_ir, NECK_ROLES)
    body_ents = piece_entities(body_ir, BODY_ROLES - NECK_ROLES)
    sleeve_ents = piece_entities(sleeve_ir, SLEEVE_ROLES)

    neckline = role_edge_length(body_ir, NECKLINE_ROLES, BODY_ROLES - NECK_ROLES)
    neck_edge = role_edge_length(neck_ir, NECKLINE_ROLES | {"collar_edge", "neck_binding_line"}, NECK_ROLES)
    if neck_edge <= 1e-6:
        from geometry_ops import entity_length
        neck_edge = sum(entity_length(e) for e in neck_ents) or 1.0
    neck_scale = match_scale_for_interface(neck_edge, neckline or neck_edge, min_ease=0.98, max_ease=1.02)
    neck_scale = max(0.85, min(1.18, neck_scale))
    neck_scaled = scale_piece_group(neck_ents, sx=neck_scale, sy=neck_scale)

    arm = role_edge_length(body_ir, ARMHOLE_ROLES, BODY_ROLES - NECK_ROLES) or role_edge_length(body_ir, ARMHOLE_ROLES)
    cap = role_edge_length(sleeve_ir, SLEEVE_CAP_ROLES, SLEEVE_ROLES)
    if cap <= 1e-6:
        from geometry_ops import entity_length
        cap = sum(entity_length(e) for e in sleeve_ents) * 0.22

    host_len = body_length_span(body_ents)
    donor_len = body_length_span(piece_entities(sleeve_ir, BODY_ROLES - NECK_ROLES)) or host_len
    length_scale = max(0.85, min(1.25, host_len / donor_len if donor_len > 1e-6 else 1.0))
    width_scale = max(0.82, min(1.25, match_scale_for_interface(cap, arm or cap)))
    sleeve_scaled = scale_sleeve_anisotropic(
        sleeve_ents, length_scale=length_scale, width_scale=width_scale, ir=sleeve_ir,
    )
    span = sleeve_span(sleeve_scaled, sleeve_ir)

    for e, src in (
        *[(e, neck_ir) for e in neck_scaled],
        *[(e, body_ir) for e in body_ents],
        *[(e, sleeve_ir) for e in sleeve_scaled],
    ):
        e["_source_case"] = src.get("case_id")

    roles = {}
    roles.update(role_map(neck_ir))
    roles.update(role_map(body_ir))
    roles.update(role_map(sleeve_ir))
    for e in neck_scaled + body_ents + sleeve_scaled:
        e["_piece_role"] = roles.get(e.get("piece_id") or "", "")

    laid = layout_groups([
        ("neck_A", neck_scaled),
        ("body_B", body_ents),
        ("sleeve_C", sleeve_scaled),
    ])
    meta = {
        "tag": tag,
        "neck_case": neck_ir.get("case_id"),
        "body_case": body_ir.get("case_id"),
        "sleeve_case": sleeve_ir.get("case_id"),
        "neckline_B": round(neckline, 3),
        "neck_edge_A": round(neck_edge, 3),
        "neck_scale": round(neck_scale, 4),
        "armhole_B": round(arm, 3),
        "sleeve_cap_C": round(cap, 3),
        "sleeve_length_scale": round(length_scale, 4),
        "sleeve_width_scale": round(width_scale, 4),
        "sleeve_axis": span["axis"],
        "sleeve_length_after": round(span["length"], 3),
        "sleeve_width_after": round(span["width"], 3),
        "ease_ratio_sleeve": round((cap * width_scale) / arm, 4) if arm > 1e-6 else None,
    }
    return laid, meta


def qa_entities(entities: list[dict]) -> dict:
    from geometry_ops import entity_length, entity_points
    n = 0
    total_len = 0.0
    degenerates = 0
    for e in entities:
        pts = entity_points(e)
        if len(pts) < 2:
            degenerates += 1
            continue
        n += 1
        total_len += entity_length(e)
    b = bounds_of_entities(entities)
    return {
        "drawable_entities": n,
        "degenerate_entities": degenerates,
        "total_curve_length": round(total_len, 3),
        "bbox": [round(x, 3) for x in b] if b else None,
        "bbox_w": round(b[2] - b[0], 3) if b else None,
        "bbox_h": round(b[3] - b[1], 3) if b else None,
    }


def export_case(name: str, entities: list[dict], ir_for_roles: dict | None, meta: dict) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    # optimize
    ents = [optimize_entity(e) for e in entities]
    roles = role_map(ir_for_roles) if ir_for_roles else {}
    dxf_path = OUT / f"{name}.dxf"
    written = write_entities_dxf(ents, str(dxf_path), piece_role_by_id=roles, optimize=True)
    # also dump entity json sidecar for debugging
    side = {
        "name": name,
        "meta": meta,
        "qa": qa_entities(ents),
        "dxf": written,
    }
    (REPORTS / f"{name}.json").write_text(json.dumps(side, ensure_ascii=False, indent=2), encoding="utf-8")
    return side


def write_svg_preview(name: str, entities: list[dict], meta: dict) -> str:
    from geometry_ops import entity_points, bounds_of_entities
    b = bounds_of_entities(entities)
    if not b:
        return ""
    pad = 40
    w = b[2] - b[0]
    h = b[3] - b[1]
    # flip Y for SVG
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="900" viewBox="{b[0]-pad} {-(b[3]+pad)} {w+2*pad} {h+2*pad}">',
        f'<rect x="{b[0]-pad}" y="{-(b[3]+pad)}" width="{w+2*pad}" height="{h+2*pad}" fill="#f7f4ef"/>',
        f'<text x="{b[0]}" y="{-(b[3]+10)}" font-size="28" fill="#222">{name}</text>',
    ]
    colors = {
        "front_body": "#c45c26", "front_left": "#c45c26", "front_right": "#c45c26",
        "back_body": "#2f6f6a", "back_yoke": "#2f6f6a",
        "sleeve": "#3b5bdb", "sleeve_left": "#3b5bdb", "sleeve_right": "#3b5bdb",
        "neck_binding": "#8b5a2b", "collar": "#8b5a2b", "collar_stand": "#a67c52",
        "cuff": "#5c4b8a",
    }
    for e in entities:
        pts = entity_points(e)
        if len(pts) < 2:
            continue
        role = e.get("_piece_role") or ""
        col = colors.get(role, "#444")
        d = "M " + " L ".join(f"{x:.2f},{-y:.2f}" for x, y in pts)
        parts.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2.2" opacity="0.9"/>')
    parts.append("</svg>")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.svg"
    path.write_text("\n".join(parts), encoding="utf-8")
    return str(path)


def main() -> None:
    # Selected corpus cases
    TSHIRT_A = "C2590529"   # body donor / grade base
    TSHIRT_B = "C2490738"   # sleeve donor
    TSHIRT_C = "C2590218"   # neck donor for 3-way remix
    SHIRT_A = "C2530682"    # shirt with clean armhole/sleeve_cap semantics
    SHIRT_B = "C2530714"    # alternate sleeves (L/R)
    SHIRT_C = "C2530676"    # collar donor

    report = {
        "experiment": "grading_and_atomic_remix_v1",
        "corpus": str(ROOT),
        "body_types": BODY_TYPES,
        "cases": {
            "tshirt_A": TSHIRT_A,
            "tshirt_B": TSHIRT_B,
            "tshirt_C": TSHIRT_C,
            "shirt_A": SHIRT_A,
            "shirt_B": SHIRT_B,
            "shirt_C": SHIRT_C,
        },
        "runs": [],
    }

    tA, tB, tC = load_ir(TSHIRT_A), load_ir(TSHIRT_B), load_ir(TSHIRT_C)
    sA, sB, sC = load_ir(SHIRT_A), load_ir(SHIRT_B), load_ir(SHIRT_C)

    # --- 1) body-type grading ---
    for garment, ir, prefix in (
        ("tshirt", tA, "grade_tshirt"),
        ("shirt", sA, "grade_shirt"),
    ):
        for body_key in BODY_TYPES:
            ents, meta = grade_garment(ir, body_key)
            name = f"{prefix}_{ir['case_id']}_{body_key}"
            meta["garment"] = garment
            side = export_case(name, ents, ir, meta)
            write_svg_preview(name, ents, meta)
            report["runs"].append({"kind": "body_grade", **side})
            print("graded", name, side["qa"])

    # --- 2) body A + sleeve B remix ---
    for garment, body_ir, sleeve_ir in (
        ("tshirt", tA, tB),
        ("shirt", sA, sB),
    ):
        ents, meta = remix_body_plus_sleeves(body_ir, sleeve_ir, tag=f"{garment}_bodyA_sleeveB")
        name = f"remix_{garment}_body-{body_ir['case_id']}_sleeve-{sleeve_ir['case_id']}"
        # roles union
        roles_ir = {
            "piece_instances": (body_ir.get("piece_instances") or []) + (sleeve_ir.get("piece_instances") or []),
            "case_id": name,
        }
        side = export_case(name, ents, roles_ir, meta)
        write_svg_preview(name, ents, meta)
        report["runs"].append({"kind": "remix_body_sleeve", **side})
        print("remix", name, meta)

    # --- 3) neck A + body B + sleeve C ---
    for garment, neck_ir, body_ir, sleeve_ir in (
        ("tshirt", tC, tA, tB),
        ("shirt", sC, sA, sB),
    ):
        ents, meta = remix_neck_body_sleeves(neck_ir, body_ir, sleeve_ir, tag=f"{garment}_neckA_bodyB_sleeveC")
        name = f"remix3_{garment}_neck-{neck_ir['case_id']}_body-{body_ir['case_id']}_sleeve-{sleeve_ir['case_id']}"
        roles_ir = {
            "piece_instances": (
                (neck_ir.get("piece_instances") or [])
                + (body_ir.get("piece_instances") or [])
                + (sleeve_ir.get("piece_instances") or [])
            ),
            "case_id": name,
        }
        side = export_case(name, ents, roles_ir, meta)
        write_svg_preview(name, ents, meta)
        report["runs"].append({"kind": "remix_neck_body_sleeve", **side})
        print("remix3", name, meta)

    # Feasibility summary
    ok_grades = [r for r in report["runs"] if r["kind"] == "body_grade" and r["qa"]["drawable_entities"] > 20]
    ok_remix = [r for r in report["runs"] if r["kind"].startswith("remix") and r["qa"]["drawable_entities"] > 20]
    ease = []
    for r in report["runs"]:
        m = r.get("meta") or {}
        if m.get("ease_ratio_after") is not None:
            ease.append(m["ease_ratio_after"])
        if m.get("interface", {}).get("ease_ratio") is not None:
            ease.append(m["interface"]["ease_ratio"])
        if m.get("ease_ratio_sleeve") is not None:
            ease.append(m["ease_ratio_sleeve"])

    report["feasibility"] = {
        "grade_runs_ok": len(ok_grades),
        "remix_runs_ok": len(ok_remix),
        "total_runs": len(report["runs"]),
        "ease_ratio_samples": ease,
        "ease_within_0_95_1_15": sum(1 for e in ease if e is not None and 0.95 <= e <= 1.15),
        "conclusion": (
            "可行：IR 几何可按体型各向异性放码，并按袖窿/袖山、领口长度对异款组件做关联缩放后导出 DXF。"
            if len(ok_grades) >= 6 and len(ok_remix) >= 3
            else "部分可行：有产出但接口匹配或实体数不足，需补边链语义。"
        ),
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "experiment_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# 放码 / 组件重组实验报告",
        "",
        f"- 语料根：`{ROOT}`",
        f"- 输出 DXF/SVG：`{OUT}`",
        f"- 运行数：{report['feasibility']['total_runs']}（放码 OK {report['feasibility']['grade_runs_ok']}，重组 OK {report['feasibility']['remix_runs_ok']}）",
        f"- 结论：{report['feasibility']['conclusion']}",
        "",
        "## 样本",
        f"- T恤 A/B/C：`{TSHIRT_A}` / `{TSHIRT_B}` / `{TSHIRT_C}`",
        f"- 衬衫 A/B/C：`{SHIRT_A}` / `{SHIRT_B}` / `{SHIRT_C}`",
        "",
        "## 体型",
    ]
    for k, v in BODY_TYPES.items():
        md.append(f"- **{k}**（{v['label']}）：宽 {v['width']} / 长 {v['length']} / 袖长 {v['sleeve_len']} / 袖宽 {v['sleeve_width']} / 领 {v['neck']}")
    md += ["", "## 产出清单", ""]
    for r in report["runs"]:
        q = r["qa"]
        md.append(
            f"- `{r['name']}` · ents={q['drawable_entities']} · "
            f"{q['bbox_w']}×{q['bbox_h']} · {r['dxf']['path']}"
        )
    md += [
        "",
        "## 方法要点",
        "1. 从 rule-ready IR 的 `atomic_entities.geometry` 取点，不依赖 GPU。",
        "2. 放码：按衣片角色对各向异性缩放（衣身宽/长、袖宽/长、领整体）。",
        "3. 重组：A 衣身(+领) + B 袖；或领 A + 衣身 B + 袖 C。",
        "4. 关联变动：用边链角色长度比缩放袖山/领边，使接口接近宿主袖窿/领口，并保留少量松量。",
        "5. 几何优化：折线简化 + 去重点后写 R12 DXF，并导出 SVG 预览。",
        "",
    ]
    (REPORTS / "experiment_summary.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(report["feasibility"], ensure_ascii=False, indent=2))
    print("DONE", OUT)


if __name__ == "__main__":
    main()
