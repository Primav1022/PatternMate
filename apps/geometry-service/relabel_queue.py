"""T-shirt sleeve relabel queue: 6 IR-missing-sleeve + 4 sleeve≈body."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from composition_engine import _entity_points, _merged_segment_parent
from dxf_closed_cuts import closed_cuts, original_dxf_path

QUEUE: list[dict[str, str]] = [
    {"case_id": "C2490682", "issue": "no_sleeve", "hint": "原始 DXF 闭合裁片：前幅/后片/领。核对手感是飞袖连裁还是无袖"},
    {"case_id": "C2590045", "issue": "no_sleeve", "hint": "原始 DXF 闭合裁片：前片/后片/左右侧片/领。插肩+侧片，不是正肩 T"},
    {"case_id": "C2590079", "issue": "no_sleeve", "hint": "原始 DXF 闭合裁片：前/后/袖/领。CAD 已有袖片"},
    {"case_id": "C2590205", "issue": "no_sleeve", "hint": "原始 DXF 闭合裁片：前/后/左右袖/领"},
    {"case_id": "C2590551", "issue": "no_sleeve", "hint": "原始 DXF 闭合裁片：前/后/袖/领。后片不是袖"},
    {"case_id": "C2590734", "issue": "no_sleeve", "hint": "闭合裁片：前片/后片/内滚领。样衣像蝙蝠或插肩连裁，没有独立袖片"},
    {"case_id": "C2490320", "issue": "sleeve_body", "hint": "原始 DXF：前/后/袖/领。袖片偏长，核插肩还是连裁"},
    {"case_id": "C2490335", "issue": "sleeve_body", "hint": "原始 DXF：前身/后身/袖都偏窄长，核插肩结构"},
    {"case_id": "C2490340", "issue": "sleeve_body", "hint": "原始 DXF：左右袖 + 前身后身。IR 里那块超大袖是错的"},
    {"case_id": "C2590428", "issue": "sleeve_body", "hint": "原始 DXF：前身/后身/袖长。袖不是衣身复制"},
]

ROLE_OPTIONS = [
    ("front_body", "前片"),
    ("back_body", "后片"),
    ("sleeve", "袖片"),
    ("side_panel", "侧片"),
    ("neck_binding", "领条"),
    ("scrap", "废片/唛架"),
]

SLEEVE_OPTIONS = [
    ("sleeveless", "无袖"),
    ("flutter", "飞袖"),
    ("raglan", "插肩袖"),
    ("batwing", "蝙蝠袖"),
    ("set-in", "正肩袖"),
    ("puff", "泡泡袖"),
    ("unknown", "看不清"),
]

ALLOWED_ROLES = {slug for slug, _ in ROLE_OPTIONS}
ALLOWED_SLEEVES = {slug for slug, _ in SLEEVE_OPTIONS}
SLEEVE_ZH = dict(SLEEVE_OPTIONS)


def _closed(pts: list[list[float]], tol: float = 1.0) -> bool:
    if len(pts) < 3:
        return False
    return abs(pts[0][0] - pts[-1][0]) <= tol and abs(pts[0][1] - pts[-1][1]) <= tol


def _span(pts: list[list[float]]) -> tuple[float, float]:
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


def _bbox(pts: list[list[float]]) -> list[float]:
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _claimed_ids(ir: dict[str, Any]) -> set[str]:
    claimed: set[str] = set()
    for piece in ir.get("piece_instances") or []:
        for raw_id in list(piece.get("boundary_entity_ids") or []) + list(piece.get("internal_entity_ids") or []):
            eid = str(raw_id or "")
            if eid:
                claimed.add(eid)
                claimed.add(eid.split("__seg_")[0])
    return claimed


def _labeled_wh(ir: dict[str, Any]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for piece in ir.get("piece_instances") or []:
        bbox = piece.get("bbox") or {}
        try:
            w = float(bbox.get("max_x", 0) or 0) - float(bbox.get("min_x", 0) or 0)
            h = float(bbox.get("max_y", 0) or 0) - float(bbox.get("min_y", 0) or 0)
        except (TypeError, ValueError):
            continue
        if w > 1 and h > 1:
            out.append((w, h))
    return out


def _similar_wh(w: float, h: float, labeled: list[tuple[float, float]], tol: float = 0.15) -> bool:
    for lw, lh in labeled:
        if abs(w - lw) / max(lw, 1.0) <= tol and abs(h - lh) / max(lh, 1.0) <= tol:
            return True
    return False


def _gap(a: list[float], b: list[float]) -> float:
    dx = 0.0 if a[2] >= b[0] and b[2] >= a[0] else min(abs(a[0] - b[2]), abs(b[0] - a[2]))
    dy = 0.0 if a[3] >= b[1] and b[3] >= a[1] else min(abs(a[1] - b[3]), abs(b[1] - a[3]))
    return (dx * dx + dy * dy) ** 0.5


def unlabeled_clusters(ir: dict[str, Any], *, gap_mm: float = 28.0) -> list[dict[str, Any]]:
    """IR often parks the real body in unlabeled DXF lines and tags scraps as front/back."""
    claimed = _claimed_ids(ir)
    labeled = _labeled_wh(ir)
    items: list[tuple[dict[str, Any], list[float], list[list[float]]]] = []
    for raw in ir.get("atomic_entities") or []:
        eid = str(raw.get("entity_id") or "")
        if not eid or "__seg_" in eid or eid in claimed or eid.split("__seg_")[0] in claimed:
            continue
        if str(raw.get("line_role") or "").lower() in {"text", "drill_hole"}:
            continue
        pts = [[float(p[0]), float(p[1])] for p in _entity_points(raw)]
        if len(pts) < 2:
            continue
        items.append((raw, _bbox(pts), pts))
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _gap(items[i][1], items[j][1]) < gap_mm:
                parent[find(j)] = find(i)
    grouped: dict[int, list[int]] = {}
    for i in range(len(items)):
        grouped.setdefault(find(i), []).append(i)
    rows: list[dict[str, Any]] = []
    for members in grouped.values():
        boxes = [items[i][1] for i in members]
        minx, miny = min(b[0] for b in boxes), min(b[1] for b in boxes)
        maxx, maxy = max(b[2] for b in boxes), max(b[3] for b in boxes)
        w, h = maxx - minx, maxy - miny
        if w * h < 80 * 80 or min(w, h) < 35:
            continue
        if _similar_wh(w, h, labeled):
            continue
        paths = [items[i][2] for i in members]
        paths.sort(key=len, reverse=True)
        rows.append({
            "sort": (minx, miny),
            "entity_ids": [str(items[i][0].get("entity_id")) for i in members],
            "width_mm": round(w, 1),
            "height_mm": round(h, 1),
            "paths": paths[:24],
        })
    rows.sort(key=lambda row: row["sort"])
    out = []
    for index, row in enumerate(rows):
        row.pop("sort")
        out.append({
            "piece_id": f"unlabeled:{index}",
            "role": "unlabeled",
            "width_mm": row["width_mm"],
            "height_mm": row["height_mm"],
            "paths": row["paths"],
            "entity_ids": row["entity_ids"],
        })
    return out


def _spread_paths(rows: list[dict[str, Any]], gap: float = 48.0, max_row_w: float = 2200.0) -> list[dict[str, Any]]:
    """Put each closed cut in its own cell so nested CAD pieces are clickable."""
    x = 0.0
    y = 0.0
    row_h = 0.0
    out: list[dict[str, Any]] = []
    for row in rows:
        pts = [xy for path in (row.get("paths") or []) for xy in path]
        if len(pts) < 2:
            out.append(row)
            continue
        minx = min(p[0] for p in pts)
        miny = min(p[1] for p in pts)
        w = max(p[0] for p in pts) - minx
        h = max(p[1] for p in pts) - miny
        if x > 0 and x + w > max_row_w:
            x = 0.0
            y += row_h + gap
            row_h = 0.0
        shifted = [[[p[0] - minx + x, p[1] - miny + y] for p in path] for path in row["paths"]]
        out.append({**row, "paths": shifted})
        x += w + gap
        row_h = max(row_h, h)
    return out


def _dxf_outlines(case_id: str, family: str | None = None, face_only: bool = False) -> list[dict[str, Any]]:
    path = original_dxf_path(case_id, family=family)
    if not path:
        return []
    rows = []
    for index, cut in enumerate(closed_cuts(path, family=family)):
        cad = str(cut.get("cad_name") or cut.get("label") or "")
        if face_only:
            from compose_ir import _is_face_copy
            if not _is_face_copy(cad):
                continue
        rows.append({
            "piece_id": f"dxf:{index}",
            "role": cut["role"],
            "cad_name": cad,
            "label": cut.get("label") or cad.split(".", 1)[-1],
            "width_mm": cut["width_mm"],
            "height_mm": cut["height_mm"],
            "paths": cut["paths"],
            "closed": True,
        })
    return rows


def piece_outlines(ir: dict[str, Any]) -> list[dict[str, Any]]:
    dxf_rows = _dxf_outlines(str(ir.get("case_id") or ""))
    if dxf_rows:
        return dxf_rows
    by_id = {
        str(raw.get("entity_id") or ""): raw
        for raw in (ir.get("atomic_entities") or [])
        if raw.get("entity_id")
    }
    rows: list[dict[str, Any]] = []
    for piece in ir.get("piece_instances") or []:
        piece_id = str(piece.get("piece_id") or "")
        if not piece_id:
            continue
        role = str(piece.get("piece_role") or "unknown")
        ids = list(piece.get("boundary_entity_ids") or []) + list(piece.get("internal_entity_ids") or [])
        paths: list[list[list[float]]] = []
        seen: set[str] = set()
        for raw_id in ids:
            parent = str(raw_id or "").split("__seg_")[0]
            if not parent or parent in seen:
                continue
            seen.add(parent)
            raw = by_id.get(parent) or _merged_segment_parent(by_id, parent)
            if not raw:
                continue
            pts = [[float(p[0]), float(p[1])] for p in _entity_points(raw)]
            if len(pts) >= 2:
                paths.append(pts)
        if not paths:
            continue
        usable = [p for p in paths if _closed(p) and len(p) >= 8] or paths
        w, h = _span(max(usable, key=len))
        rows.append({
            "piece_id": piece_id,
            "role": role,
            "width_mm": round(w, 1),
            "height_mm": round(h, 1),
            "paths": paths,
        })
    rows.extend(unlabeled_clusters(ir))
    return rows


def svg_payload(pieces: list[dict[str, Any]]) -> dict[str, Any]:
    pts = [xy for piece in pieces for path in piece["paths"] for xy in path]
    if not pts:
        return {"viewBox": "0 0 100 100", "pieces": []}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    out = []
    for piece in pieces:
        flipped = []
        for path in piece["paths"]:
            flipped.append([[round(x - min_x, 2), round(max_y - y, 2)] for x, y in path])
        out.append({**piece, "paths": flipped})
    return {
        "viewBox": f"{-20:.1f} {-20:.1f} {width + 40:.1f} {height + 40:.1f}",
        "pieces": out,
    }


def summarize(ir: dict[str, Any], item: dict[str, str], cover_url: str) -> dict[str, Any]:
    extra = ir.get("design_semantics_extra") or {}
    labels = extra.get("part_labels") or {}
    sleeve = labels.get("sleeve_style") if isinstance(labels, dict) else None
    slug = sleeve.get("slug") if isinstance(sleeve, dict) else None
    roles: dict[str, int] = {}
    for piece in ir.get("piece_instances") or []:
        role = str(piece.get("piece_role") or "unknown")
        roles[role] = roles.get(role, 0) + 1
    return {
        **item,
        "cover_url": cover_url,
        "sleeve_style": slug,
        "roles": roles,
        "reviewed": bool(extra.get("relabel_reviewed")),
    }


def apply_labels(
    ir: dict[str, Any],
    *,
    piece_roles: dict[str, str],
    sleeve_style: str,
    notes: str,
    reviewer: str,
) -> dict[str, Any]:
    if sleeve_style not in ALLOWED_SLEEVES:
        raise ValueError(f"invalid sleeve_style: {sleeve_style}")
    bad = {role for role in piece_roles.values() if role not in ALLOWED_ROLES and role not in {"unlabeled", "unknown"}}
    if bad:
        raise ValueError(f"invalid piece_role: {sorted(bad)}")
    case_id = str(ir.get("case_id") or "case")
    existing = ir.setdefault("piece_instances", [])
    entities = ir.setdefault("atomic_entities", [])
    using_dxf = any(pid.startswith("dxf:") for pid in piece_roles)
    if using_dxf:
        prefix = f"{case_id}:dxf_cut:"
        ir["atomic_entities"] = [
            ent for ent in entities if not str(ent.get("entity_id") or "").startswith(prefix)
        ]
        entities = ir["atomic_entities"]
        existing[:] = [piece for piece in existing if piece.get("source") != "relabel_dxf"]
        for piece in existing:
            piece["piece_role"] = "scrap"
        for ent in entities:
            if ent.get("piece_id"):
                ent["piece_role"] = "scrap"
    for piece in existing:
        pid = str(piece.get("piece_id") or "")
        if pid in piece_roles:
            piece["piece_role"] = piece_roles[pid]
    used = {str(p.get("piece_id") or "") for p in existing}
    seq = 1
    clusters = {row["piece_id"]: row for row in unlabeled_clusters(ir)}
    dxf_rows = {row["piece_id"]: row for row in _dxf_outlines(case_id)}
    by_id = {str(e.get("entity_id") or ""): e for e in entities}

    def _next_piece_id(role: str) -> str:
        nonlocal seq
        while True:
            new_id = f"{case_id}:piece:{role}:{seq:02d}"
            seq += 1
            if new_id not in used:
                used.add(new_id)
                return new_id

    for pid, role in piece_roles.items():
        if pid.startswith("dxf:") and role in ALLOWED_ROLES:
            cut = dxf_rows.get(pid)
            if not cut:
                continue
            new_id = _next_piece_id(role)
            eid = f"{case_id}:dxf_cut:{pid.split(':', 1)[-1]}"
            pts = [list(map(float, xy)) for xy in (cut["paths"][0] if cut.get("paths") else [])]
            entities.append({
                "entity_id": eid,
                "source": {"layer": "1", "entity_type": "POLYLINE", "origin": "original_dxf"},
                "geometry": {"points": pts, "closed": True},
                "line_role": "cut",
                "piece_id": new_id,
                "piece_role": role,
                "review": "human",
            })
            existing.append({
                "piece_id": new_id,
                "piece_role": role,
                "boundary_entity_ids": [eid],
                "internal_entity_ids": [],
                "source": "relabel_dxf",
                "cad_name": cut.get("cad_name") or "",
                "review": "human",
            })
            continue
        if not pid.startswith("unlabeled:") or role not in ALLOWED_ROLES:
            continue
        cluster = clusters.get(pid)
        if not cluster:
            continue
        new_id = _next_piece_id(role)
        ids = list(cluster.get("entity_ids") or [])
        existing.append({
            "piece_id": new_id,
            "piece_role": role,
            "boundary_entity_ids": ids,
            "internal_entity_ids": [],
            "source": "relabel_queue",
            "review": "human",
        })
        for eid in ids:
            ent = by_id.get(eid)
            if not ent:
                continue
            ent["piece_id"] = new_id
            ent["piece_role"] = role
    role_by_id = {str(p.get("piece_id") or ""): str(p.get("piece_role") or "") for p in existing}
    for entity in ir.get("atomic_entities") or []:
        pid = str(entity.get("piece_id") or "")
        if pid in role_by_id:
            entity["piece_role"] = role_by_id[pid]
    extra = dict(ir.get("design_semantics_extra") or {})
    labels = dict(extra.get("part_labels") or {})
    labels["sleeve_style"] = {"slug": sleeve_style, "label_zh": SLEEVE_ZH[sleeve_style]}
    extra["part_labels"] = labels
    extra["relabel_notes"] = notes
    extra["relabel_reviewed"] = True
    extra["relabel_reviewed_at"] = datetime.now(timezone.utc).isoformat()
    extra["relabel_reviewed_by"] = reviewer or "expert"
    ir["design_semantics_extra"] = extra
    ir.pop("_remix_readiness_cache", None)
    return ir


_YOKE_HINT = ("育克", "复势", "后上", "前上", "过肩")


def shirt_yoke_queue() -> list[dict[str, str]]:
    from compose_ir import ROLE_OVERRIDE_PATH, load_compose, shirt_case_ids

    reviewed: set[str] = set()
    if ROLE_OVERRIDE_PATH.exists():
        data = json.loads(ROLE_OVERRIDE_PATH.read_text(encoding="utf-8"))
        reviewed = {cid for cid, entry in data.items() if isinstance(entry, dict) and entry.get("_reviewed")}
    items: list[dict[str, str]] = []
    for case_id in shirt_case_ids():
        doc = load_compose(case_id) or {}
        names = [(p.get("cad_name") or "").split(".", 1)[-1] for p in doc.get("pieces") or []]
        yokeish = [name for name in names if any(key in name for key in _YOKE_HINT)]
        has_by = any(p.get("piece_role") == "back_yoke" for p in doc.get("pieces") or [])
        if yokeish:
            hint = "原版有：" + "、".join(n.split("_")[0] for n in yokeish[:6]) + "。核对后育克"
            issue = "has_yoke_name"
        elif has_by:
            hint = "compose 已有后育克。对照原版裁片确认"
            issue = "has_yoke_name"
        else:
            hint = "原版没有育克/复势字样。若后片上方有短片，标成后育克"
            issue = "no_yoke_name"
        items.append({
            "case_id": case_id,
            "issue": issue,
            "hint": hint,
            "reviewed": "1" if case_id in reviewed else "",
        })
    return items


def shirt_dxf_pieces(case_id: str) -> list[dict[str, Any]]:
    from compose_ir import load_role_overrides

    rows = _dxf_outlines(case_id, family="shirt", face_only=True)
    overrides = load_role_overrides(case_id)
    if overrides:
        patched = []
        for row in rows:
            cad = str(row.get("cad_name") or "")
            role = overrides.get(cad) or overrides.get(cad.split(".", 1)[-1]) or row.get("role")
            patched.append({**row, "role": role})
        rows = patched
    return _spread_paths(rows)


def apply_shirt_compose_labels(case_id: str, piece_roles: dict[str, str]) -> dict[str, Any]:
    from compose_ir import PIECE_ROLES, load_role_overrides, save_role_overrides, write_one

    rows = _dxf_outlines(case_id, family="shirt", face_only=True)
    by_id = {str(row["piece_id"]): row for row in rows}
    overrides = load_role_overrides(case_id)
    for pid, role in piece_roles.items():
        row = by_id.get(str(pid))
        if not row or role not in PIECE_ROLES:
            continue
        cad = str(row.get("cad_name") or "")
        if not cad:
            continue
        guessed = str(row.get("role") or "unlabeled")
        if role == guessed:
            overrides.pop(cad, None)
        else:
            overrides[cad] = role
    save_role_overrides(case_id, overrides, reviewed=True)
    return write_one(case_id, family="shirt")
