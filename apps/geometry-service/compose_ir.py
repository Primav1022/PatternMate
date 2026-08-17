"""Compose IR: one piece = one closed cut, plus sew ring and internal lines from the same BLOCK.

View only. Does not change ai4manufacturing.pattern_ir.v1.0 / writeback schema.

Files:
  data/ir/tshirt_v2/pattern_ir_compose/{case_id}.json
  data/ir/shirt_v2/pattern_ir_compose/{case_id}.json

Shirt adds collar / stand / cuff / placket / yoke. Same hard constraints.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

from dxf_closed_cuts import closed_cuts, opening_line_role, original_dxf_path

_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = _ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir_compose"
SHIRT_COMPOSE_DIR = _ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir_compose"
OLD_IR_DIR = _ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir"
SHIRT_IR_DIR = _ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir"
INDEX_PATH = _ROOT / "data" / "ir" / "tshirt_v2" / "index.json"
SHIRT_INDEX_PATH = _ROOT / "data" / "ir" / "shirt_v2" / "index.json"

PIECE_ROLES = frozenset({
    "front_body", "front_left", "front_right", "front_yoke", "front_placket",
    "back_body", "back_yoke",
    "sleeve", "sleeve_left", "sleeve_right", "sleeve_placket",
    "side_panel", "neck_binding",
    "collar", "collar_stand", "collar_interlining",
    "cuff", "scrap",
})
EDGE_ON_ROLE = {
    "front_neckline": frozenset({"front_body", "front_left", "front_right"}),
    "back_neckline": frozenset({"back_body", "back_yoke"}),
    "armhole_front": frozenset({"front_body", "front_left", "front_right", "side_panel"}),
    "armhole_back": frozenset({"back_body", "back_yoke", "side_panel"}),
    "sleeve_cap": frozenset({"sleeve", "sleeve_left", "sleeve_right"}),
    "side_seam": frozenset({"front_body", "front_left", "front_right", "back_body", "side_panel"}),
    "shoulder": frozenset({"front_body", "front_left", "front_right", "back_body", "back_yoke"}),
    "hem": frozenset({"front_body", "front_left", "front_right", "back_body", "side_panel"}),
    "hem_line": frozenset({"front_body", "front_left", "front_right", "back_body", "side_panel"}),
}
SNAP_ROLES = frozenset(EDGE_ON_ROLE)
MIN_CUT_PTS = {
    "neck_binding": 4, "scrap": 4, "collar": 4, "collar_stand": 4,
    "collar_interlining": 4, "cuff": 4, "front_placket": 4, "sleeve_placket": 4,
    "front_yoke": 4, "back_yoke": 4,
}
FRONT_LIKE = frozenset({"front_body", "front_left", "front_right"})
BACK_LIKE = frozenset({"back_body", "back_yoke"})
_NEST_DROP = ("比对版", "半成品", "压裥", "绣花完成", "草图", "无缩版", "模板", "后道", "透明版", "实样", "修样", "纸膜", "定型样")


def compose_path(case_id: str, root: Path | None = None, family: str | None = None) -> Path:
    base = root or _ROOT
    if family == "shirt":
        return base / "data" / "ir" / "shirt_v2" / "pattern_ir_compose" / f"{case_id}.json"
    if family == "tshirt":
        return base / "data" / "ir" / "tshirt_v2" / "pattern_ir_compose" / f"{case_id}.json"
    shirt = base / "data" / "ir" / "shirt_v2" / "pattern_ir_compose" / f"{case_id}.json"
    if shirt.exists():
        return shirt
    return base / "data" / "ir" / "tshirt_v2" / "pattern_ir_compose" / f"{case_id}.json"


def load_compose(case_id: str, root: Path | None = None) -> dict[str, Any] | None:
    path = compose_path(case_id, root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _closed(pts: list[list[float]]) -> list[list[float]]:
    out = [[float(p[0]), float(p[1])] for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(out) >= 2 and out[0] != out[-1]:
        out.append(out[0][:])
    return out


def _cut_key(pts: list[list[float]]) -> tuple[tuple[int, int], ...]:
    ring = pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else pts
    return tuple((round(p[0] * 100), round(p[1] * 100)) for p in ring[:12])


def _bbox_area(pts: list[list[float]]) -> float:
    box = _bbox(pts)
    if not box:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _bbox(pts: list[list[float]]) -> tuple[float, float, float, float] | None:
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _uv(pt: list[float], box: tuple[float, float, float, float]) -> tuple[float, float]:
    w = max(box[2] - box[0], 1e-6)
    h = max(box[3] - box[1], 1e-6)
    return (pt[0] - box[0]) / w, (pt[1] - box[1]) / h


def _nearest_i(pts: list[list[float]], target: tuple[float, float]) -> int:
    best_i, best_d = 0, float("inf")
    for i, p in enumerate(pts):
        d = (p[0] - target[0]) ** 2 + (p[1] - target[1]) ** 2
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def _min_pts(role: str) -> int:
    return MIN_CUT_PTS.get(role, 8)


def validate(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(doc.get("source_dxf") or "").strip():
        errors.append("missing source_dxf")
    pieces = doc.get("pieces") or []
    ids: set[str] = set()
    keys: dict[tuple[tuple[int, int], ...], str] = {}
    for piece in pieces:
        pid = str(piece.get("piece_id") or "")
        role = str(piece.get("piece_role") or "")
        cut = piece.get("cut") or {}
        pts = cut.get("points") or []
        if not pid:
            errors.append("empty piece_id")
            continue
        if pid in ids:
            errors.append(f"duplicate piece_id {pid}")
        ids.add(pid)
        if role not in PIECE_ROLES and role != "unlabeled":
            errors.append(f"{pid}: bad piece_role {role}")
        if not cut.get("closed"):
            errors.append(f"{pid}: cut.closed must be true")
        if len(pts) < _min_pts(role):
            errors.append(f"{pid}: cut needs ≥{_min_pts(role)} points, got {len(pts)}")
        if len(pts) >= 2 and pts[0] != pts[-1]:
            errors.append(f"{pid}: cut is not closed")
        key = _cut_key(pts)
        if key in keys:
            errors.append(f"{pid} shares cut points with {keys[key]}")
        else:
            keys[key] = pid
        n = max(len(pts) - 1, 0)
        for edge in piece.get("edges") or []:
            erole = str(edge.get("role") or "")
            start_i = int(edge.get("start_i") or 0)
            end_i = int(edge.get("end_i") or 0)
            if n and (start_i < 0 or end_i < 0 or start_i >= n or end_i >= n or start_i == end_i):
                errors.append(f"{pid}: edge {erole} indices {start_i},{end_i} not on cut")
            allowed = EDGE_ON_ROLE.get(erole)
            if allowed and role not in allowed:
                errors.append(f"{pid}: {erole} cannot sit on {role}")
    return errors


def entities_from_compose(doc: dict[str, Any]) -> list[dict[str, Any]]:
    case_id = str(doc.get("case_id") or "")
    out: list[dict[str, Any]] = []
    for piece in doc.get("pieces") or []:
        role = str(piece.get("piece_role") or "unlabeled")
        if role in {"scrap", "unlabeled"}:
            continue
        pid = str(piece.get("piece_id") or "")
        pts = _closed((piece.get("cut") or {}).get("points") or [])
        if len(pts) < 2:
            continue
        edges = list(piece.get("edges") or [])
        out.append({
            "entity_id": f"{pid}:cut",
            "piece_id": pid,
            "piece_role": role,
            "_piece_role": role,
            "line_role": "cut",
            "geometry": {"points": deepcopy(pts), "closed": True},
            "source": {"origin": "compose_ir", "cad_name": piece.get("cad_name")},
            "_compose_edges": edges,
            "_source_case": case_id,
        })
        ring = pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else pts
        for i, edge in enumerate(edges):
            erole = str(edge.get("role") or "")
            if erole not in SNAP_ROLES:
                continue
            a, b = int(edge["start_i"]), int(edge["end_i"])
            if a < 0 or b < 0 or a >= len(ring) or b >= len(ring):
                continue
            if a <= b:
                span = ring[a:b + 1]
            else:
                span = ring[a:] + ring[:b + 1]
            if len(span) < 2:
                continue
            out.append({
                "entity_id": f"{pid}:edge:{erole}:{i:02d}",
                "piece_id": pid,
                "piece_role": role,
                "_piece_role": role,
                "line_role": erole,
                "edge_role": erole,
                "geometry": {"points": deepcopy(span)},
                "source": {"origin": "compose_ir", "parent": f"{pid}:cut"},
                "_source_case": case_id,
            })
        sew_pts = ((piece.get("sew") or {}).get("points") or [])
        if len(sew_pts) >= 3:
            sew = _closed(sew_pts)
            out.append({
                "entity_id": f"{pid}:sew",
                "piece_id": pid,
                "piece_role": role,
                "_piece_role": role,
                "line_role": "sew",
                "geometry": {"points": deepcopy(sew), "closed": True},
                "source": {"origin": "compose_ir", "parent": f"{pid}:cut"},
                "_source_case": case_id,
            })
        for i, line in enumerate(piece.get("lines") or []):
            raw = line.get("points") or []
            if len(raw) < 2:
                continue
            closed = bool(line.get("closed"))
            pts = _closed(raw) if closed else [[float(p[0]), float(p[1])] for p in raw if isinstance(p, (list, tuple)) and len(p) >= 2]
            lrole = str(line.get("role") or "internal")
            if lrole == "internal":
                lrole = opening_line_role(pts, ring) or lrole
            out.append({
                "entity_id": f"{pid}:line:{i:02d}",
                "piece_id": pid,
                "piece_role": role,
                "_piece_role": role,
                "line_role": lrole,
                "geometry": {"points": deepcopy(pts), "closed": closed},
                "source": {"origin": "compose_ir", "parent": f"{pid}:cut"},
                "_source_case": case_id,
            })
    return out


def _entity_pts(ir: dict[str, Any], entity_id: str) -> list[list[float]]:
    for raw in ir.get("atomic_entities") or []:
        if str(raw.get("entity_id") or "") == entity_id:
            return [
                [float(p[0]), float(p[1])]
                for p in ((raw.get("geometry") or {}).get("points") or [])
                if isinstance(p, (list, tuple)) and len(p) >= 2
            ]
    return []


def _chain_points(ir: dict[str, Any], chain: dict[str, Any]) -> list[list[float]]:
    pts: list[list[float]] = []
    for eid in chain.get("ordered_entity_ids") or []:
        for p in _entity_pts(ir, str(eid)):
            if not pts or pts[-1] != p:
                pts.append(p)
    return pts


def _top_span(pts: list[list[float]], frac: float = 0.22) -> tuple[int, int] | None:
    ring = pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else pts
    if len(ring) < 6:
        return None
    ys = [p[1] for p in ring]
    min_y, max_y = min(ys), max(ys)
    h = max(max_y - min_y, 1e-6)
    top = [i for i, p in enumerate(ring) if p[1] >= max_y - h * frac]
    if len(top) < 3:
        return None
    left = min(top, key=lambda i: ring[i][0])
    right = max(top, key=lambda i: ring[i][0])
    if left == right:
        return None
    return left, right


def _snap_edges(cuts: list[dict[str, Any]], old_ir: dict[str, Any] | None) -> None:
    if not old_ir:
        return
    piece_box: dict[str, tuple[float, float, float, float]] = {}
    for piece in old_ir.get("piece_instances") or []:
        box = piece.get("bbox") or {}
        try:
            piece_box[str(piece.get("piece_id") or "")] = (
                float(box["min_x"]), float(box["min_y"]), float(box["max_x"]), float(box["max_y"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    used: set[tuple[str, str]] = set()
    for chain in old_ir.get("edge_chains") or []:
        role = str(chain.get("edge_role") or "")
        allowed = EDGE_ON_ROLE.get(role)
        if not allowed:
            continue
        pts = _chain_points(old_ir, chain)
        if len(pts) < 2:
            continue
        host = piece_box.get(str(chain.get("piece_id") or ""))
        if not host:
            continue
        su, sv = _uv(pts[0], host)
        eu, ev = _uv(pts[-1], host)
        best: tuple[float, dict[str, Any], int, int] | None = None
        for cut in cuts:
            if cut["piece_role"] not in allowed:
                continue
            ring = cut["_ring"]
            box = cut["_box"]
            if not box:
                continue
            uv_ring = [_uv(p, box) for p in ring]
            i = _nearest_i(uv_ring, (su, sv))
            j = _nearest_i(uv_ring, (eu, ev))
            if i == j:
                continue
            d = math.hypot(uv_ring[i][0] - su, uv_ring[i][1] - sv) + math.hypot(
                uv_ring[j][0] - eu, uv_ring[j][1] - ev
            )
            if d > 0.55:
                continue
            if best is None or d < best[0]:
                best = (d, cut, i, j)
        if not best:
            continue
        _, cut, i, j = best
        key = (cut["piece_id"], role)
        if key in used:
            continue
        used.add(key)
        cut["edges"].append({"role": role, "start_i": i, "end_i": j})


def _fill_missing_edges(cuts: list[dict[str, Any]]) -> None:
    for cut in cuts:
        have = {e["role"] for e in cut["edges"]}
        role = cut["piece_role"]
        want = None
        if role in FRONT_LIKE and "front_neckline" not in have:
            want = "front_neckline"
        elif role in BACK_LIKE and "back_neckline" not in have:
            want = "back_neckline"
        elif role in {"sleeve", "sleeve_left", "sleeve_right"} and "sleeve_cap" not in have:
            want = "sleeve_cap"
        if not want:
            continue
        span = _top_span(cut["cut"]["points"])
        if span:
            cut["edges"].append({"role": want, "start_i": span[0], "end_i": span[1]})


def _stitch_loop(segments: list[list[list[float]]], tol: float = 3.0) -> list[list[float]] | None:
    if not segments:
        return None
    segs = [seg for seg in segments if len(seg) >= 2]
    if not segs:
        return None
    segs.sort(key=len, reverse=True)
    used = [False] * len(segs)
    ring = [p[:] for p in segs[0]]
    used[0] = True

    def dist(a: list[float], b: list[float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    for _ in range(len(segs)):
        if len(ring) >= 8 and dist(ring[0], ring[-1]) <= tol:
            out = ring + [ring[0][:]]
            return out
        best: tuple[float, int, bool, bool] | None = None
        tail, head = ring[-1], ring[0]
        for i, seg in enumerate(segs):
            if used[i]:
                continue
            for reverse in (False, True):
                pts = list(reversed(seg)) if reverse else seg
                d_tail = dist(tail, pts[0])
                d_head = dist(head, pts[-1])
                if d_tail <= tol and (best is None or d_tail < best[0]):
                    best = (d_tail, i, reverse, True)
                if d_head <= tol and (best is None or d_head < best[0]):
                    best = (d_head, i, reverse, False)
        if not best:
            break
        _d, i, reverse, at_tail = best
        used[i] = True
        pts = list(reversed(segs[i])) if reverse else segs[i]
        if at_tail:
            ring.extend(p[:] for p in pts[1:])
        else:
            ring = [p[:] for p in pts[:-1]] + ring
    if len(ring) >= 8 and dist(ring[0], ring[-1]) <= tol:
        return ring + [ring[0][:]]
    return None


def _stitch_old_ir_cut(old_ir: dict[str, Any] | None, role: str) -> list[list[float]] | None:
    if not old_ir:
        return None
    best_piece = None
    best_area = -1.0
    for piece in old_ir.get("piece_instances") or []:
        if str(piece.get("piece_role") or "") != role:
            continue
        box = piece.get("bbox") or {}
        try:
            area = (float(box["max_x"]) - float(box["min_x"])) * (float(box["max_y"]) - float(box["min_y"]))
        except (KeyError, TypeError, ValueError):
            area = 0.0
        if area > best_area:
            best_area, best_piece = area, piece
    if not best_piece:
        return None
    segs: list[list[list[float]]] = []
    seen: set[tuple] = set()
    for eid in best_piece.get("boundary_entity_ids") or []:
        pts = _entity_pts(old_ir, str(eid))
        if len(pts) < 2:
            continue
        key = (round(pts[0][0], 2), round(pts[0][1], 2), round(pts[-1][0], 2), round(pts[-1][1], 2), len(pts))
        rev = (key[2], key[3], key[0], key[1], key[4])
        if key in seen or rev in seen:
            continue
        seen.add(key)
        segs.append(pts)
    return _stitch_loop(segs)


def _rel_source(path: Path) -> str:
    workspace = _ROOT.parent
    try:
        return str(path.resolve().relative_to(workspace))
    except ValueError:
        return path.name


def _is_face_copy(cad_name: str) -> bool:
    name = cad_name or ""
    if any(token in name for token in _NEST_DROP):
        return False
    if ("衬" in name or "朴" in name or "别布" in name) and not any(token in name for token in ("面料", "面A", "面B", "1号布")):
        return False
    return True


def _shirt_keep(cuts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One production face per role; keep left/right pairs."""
    faces = [row for row in cuts if _is_face_copy(str(row.get("cad_name") or ""))]
    pool = faces or cuts
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in pool:
        grouped.setdefault(str(row.get("piece_role") or "unlabeled"), []).append(row)
    kept: list[dict[str, Any]] = []
    for role, rows in grouped.items():
        rows = sorted(rows, key=lambda item: _bbox_area(item["cut"]["points"]), reverse=True)
        if role == "scrap":
            continue
        kept.append(rows[0])
    return kept


def build_one(case_id: str, old_ir: dict[str, Any] | None = None, family: str = "tshirt") -> dict[str, Any]:
    dxf = original_dxf_path(case_id, family=family)
    if not dxf:
        raise FileNotFoundError(f"no source DXF for {case_id}")
    raw_cuts = closed_cuts(dxf, family=family)
    if not raw_cuts:
        raise ValueError(f"{case_id}: no closed BLOCK cuts in {dxf}")
    counts: dict[str, int] = {}
    pieces: list[dict[str, Any]] = []
    leftovers: list[dict[str, Any]] = []
    for row in raw_cuts:
        role = str(row.get("role") or "unlabeled")
        pts = _closed((row.get("paths") or [[]])[0])
        if len(pts) < _min_pts(role if role in PIECE_ROLES else "scrap"):
            continue
        item = {
            "piece_role": role,
            "cad_name": row.get("cad_name") or row.get("label") or "",
            "cut": {"closed": True, "points": pts},
            "edges": [],
            "_ring": pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else pts,
        }
        if row.get("sew"):
            item["sew"] = row["sew"]
        if row.get("lines"):
            item["lines"] = row["lines"]
        item["_box"] = _bbox(item["_ring"])
        if role in {"unlabeled", ""}:
            leftovers.append(item)
        else:
            pieces.append(item)
    if family == "shirt":
        pieces = _shirt_keep(pieces)
    have = {p["piece_role"] for p in pieces}
    leftovers.sort(key=lambda row: _bbox_area(row["cut"]["points"]), reverse=True)
    need_front = not (have & FRONT_LIKE)
    need_back = not (have & BACK_LIKE)
    for need, missing in (("front_body", need_front), ("back_body", need_back)):
        if not missing:
            continue
        for item in leftovers:
            box = item.get("_box")
            if not box or (box[2] - box[0]) * (box[3] - box[1]) < 200 * 200:
                continue
            leftovers.remove(item)
            item["piece_role"] = need
            pieces.append(item)
            have.add(need)
            break
        if need not in have:
            stitched = _stitch_old_ir_cut(old_ir, need)
            if stitched:
                ring = stitched[:-1] if stitched[0] == stitched[-1] else stitched
                pieces.append({
                    "piece_role": need,
                    "cad_name": f"{need} (stitched)",
                    "cut": {"closed": True, "points": stitched},
                    "edges": [],
                    "_ring": ring,
                    "_box": _bbox(ring),
                })
                have.add(need)
    for item in pieces:
        role = item["piece_role"]
        counts[role] = counts.get(role, 0) + 1
        item["piece_id"] = f"{case_id}:compose:{role}:{counts[role]:02d}"
    _snap_edges(pieces, old_ir)
    _fill_missing_edges(pieces)
    out_pieces = []
    for piece in pieces:
        piece.pop("_ring", None)
        piece.pop("_box", None)
        out_pieces.append(piece)
    doc = {
        "ir_view": "compose_v1",
        "family": family,
        "case_id": case_id,
        "source_dxf": _rel_source(dxf),
        "pieces": out_pieces,
    }
    errors = validate(doc)
    if errors:
        raise ValueError(f"{case_id}: " + "; ".join(errors))
    return doc


def tshirt_case_ids() -> list[str]:
    rows = json.loads(INDEX_PATH.read_text(encoding="utf-8")).get("rows") or []
    return [str(row["case_id"]) for row in rows if row.get("case_id")]


def shirt_case_ids() -> list[str]:
    rows = json.loads(SHIRT_INDEX_PATH.read_text(encoding="utf-8")).get("rows") or []
    return [str(row["case_id"]) for row in rows if row.get("case_id")]


def load_old_ir(case_id: str, family: str = "tshirt") -> dict[str, Any] | None:
    path = (SHIRT_IR_DIR if family == "shirt" else OLD_IR_DIR) / f"{case_id}.pattern-ir.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_one(case_id: str, out_dir: Path | None = None, family: str = "tshirt") -> dict[str, Any]:
    doc = build_one(case_id, load_old_ir(case_id, family), family=family)
    dest = out_dir or (SHIRT_COMPOSE_DIR if family == "shirt" else COMPOSE_DIR)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{case_id}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc
