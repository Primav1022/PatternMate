"""Minimal ASCII DXF writer for graded / remixed IR entities."""
from __future__ import annotations

from geometry_ops import entity_points, optimize_entity


LAYER_BY_ROLE = {
    "front_body": "AI4M_FRONT",
    "front_left": "AI4M_FRONT",
    "front_right": "AI4M_FRONT",
    "front_placket": "AI4M_PLACKET",
    "back_body": "AI4M_BACK",
    "back_yoke": "AI4M_BACK",
    "sleeve": "AI4M_SLEEVE",
    "sleeve_left": "AI4M_SLEEVE",
    "sleeve_right": "AI4M_SLEEVE",
    "neck_binding": "AI4M_NECK",
    "collar": "AI4M_NECK",
    "collar_stand": "AI4M_NECK",
    "collar_interlining": "AI4M_NECK",
    "neck_rib": "AI4M_NECK",
    "cuff": "AI4M_CUFF",
    "rib_cuff": "AI4M_CUFF",
    "sleeve_placket": "AI4M_CUFF",
    "sleeve_placket_extension": "AI4M_CUFF",
    "review_retained": "AI4M_REVIEW_RETAINED",
}


def _pairs(code: int, value) -> list[str]:
    return [str(code), str(value)]


def write_entities_dxf(entities: list[dict], path: str, *, piece_role_by_id: dict[str, str] | None = None, optimize: bool = True) -> dict:
    piece_role_by_id = piece_role_by_id or {}
    lines: list[str] = []
    lines += _pairs(0, "SECTION") + _pairs(2, "HEADER")
    lines += _pairs(9, "$ACADVER") + _pairs(1, "AC1009")
    lines += _pairs(0, "ENDSEC")
    lines += _pairs(0, "SECTION") + _pairs(2, "TABLES")
    lines += _pairs(0, "TABLE") + _pairs(2, "LAYER") + _pairs(70, 8)
    for layer in sorted(set(LAYER_BY_ROLE.values()) | {"AI4M_OTHER", "AI4M_REVIEW_RETAINED", "0"}):
        lines += _pairs(0, "LAYER") + _pairs(2, layer) + _pairs(70, 0) + _pairs(62, 7) + _pairs(6, "CONTINUOUS")
    lines += _pairs(0, "ENDTAB") + _pairs(0, "ENDSEC")
    lines += _pairs(0, "SECTION") + _pairs(2, "ENTITIES")

    written = 0
    skipped = 0
    for raw in entities:
        ent = optimize_entity(raw) if optimize else raw
        pts = entity_points(ent)
        if len(pts) < 2:
            skipped += 1
            continue
        role = piece_role_by_id.get(ent.get("piece_id") or "", "") or ent.get("_piece_role") or ""
        layer = str(ent.get("_review_layer") or LAYER_BY_ROLE.get(role, "AI4M_OTHER"))
        # encode provenance in layer comment via 1000 is not in R12; keep layer + handle-like 5
        if len(pts) == 2:
            lines += _pairs(0, "LINE") + _pairs(8, layer)
            lines += _pairs(10, f"{pts[0][0]:.6f}") + _pairs(20, f"{pts[0][1]:.6f}") + _pairs(30, "0.0")
            lines += _pairs(11, f"{pts[1][0]:.6f}") + _pairs(21, f"{pts[1][1]:.6f}") + _pairs(31, "0.0")
        else:
            lines += _pairs(0, "LWPOLYLINE") + _pairs(8, layer) + _pairs(90, len(pts)) + _pairs(70, 0)
            for x, y in pts:
                lines += _pairs(10, f"{x:.6f}") + _pairs(20, f"{y:.6f}")
        written += 1

    lines += _pairs(0, "ENDSEC") + _pairs(0, "EOF")
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"path": path, "entities_written": written, "entities_skipped": skipped, "bytes": len(text.encode("utf-8"))}
