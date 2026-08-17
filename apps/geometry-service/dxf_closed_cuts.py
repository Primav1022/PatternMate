"""Closed cut outlines from ANSI/AAMA R12 pattern DXFs (POLYLINE flag 70 bit 0)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]


def _dxf_score(path: Path, family: str | None = None) -> tuple[int, int]:
    cuts = closed_cuts(path, family=family)
    roles = {str(row.get("role") or "") for row in cuts}
    if family == "shirt":
        has_front = bool(roles & {"front_body", "front_left", "front_right"})
        has_back = bool(roles & {"back_body", "back_yoke"})
    else:
        has_front = "front_body" in roles
        has_back = "back_body" in roles
    return (int(has_front) + int(has_back), len(cuts))


def original_dxf_path(case_id: str, root: Path | None = None, family: str | None = None) -> Path | None:
    cid = str(case_id or "").strip()
    if not cid:
        return None
    base = root or Path(os.getenv("CHI27_ROOT") or _ROOT)
    extra = os.getenv("ORIGINAL_DXF_DIR")
    workspace = base.parent
    ann = workspace / "CHI27_AI4Manufacturing" / "annotation_platform" / "vercel_app"
    candidates = [
        base / "data" / "seed" / "dxf" / "original" / f"{cid}.dxf",
        workspace / "cad纸样源文件" / "正确版" / f"{cid}.dxf",
        workspace / "dxf修改版" / "针织112条(清洗过）" / f"{cid}.dxf",
        base / "data" / "seed" / "dxf" / "v1_annotated" / f"{cid}.annotated.dxf",
        ann / "data" / "source-dxf" / f"{cid}.dxf",
    ]
    originals = ann / "source_cases" / cid / "originals"
    if originals.exists():
        candidates.extend(sorted(originals.glob("*.dxf")))
    if extra:
        candidates.insert(0, Path(extra) / f"{cid}.dxf")
    found = [path for path in candidates if path.exists() and _dxf_score(path, family)[1] > 0]
    if not found:
        return None
    return max(found, key=lambda path: _dxf_score(path, family))


def _decode_cad(name: str) -> str:
    raw = name.encode("latin1")
    for enc in ("gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name.replace("\xad", "")


def _guess_shirt_role(name: str) -> str:
    parts = {part for part in name.replace("_", ".").split(".") if part}
    if any(key in name for key in ("脚朴", "压条", "洗标", "烫样", "烫版", "唛", "贴袋", "绣花", "夹底", "夹圈", "标位置", "滚条", "捆条", "出芽", "内贴", "贴布")):
        if "袖荷叶" in name:
            return "sleeve"
        if "领" in name:
            return "neck_binding"
        return "scrap"
    if any(key in name for key in ("门襟", "门里襟", "里襟", "门巾", "里巾", "门禁")):
        return "front_placket"
    if any(key in name for key in ("领座", "下级领", "领脚")):
        return "collar_stand"
    if "领衬" in name or ("领" in name and ("衬" in name or "朴" in name) and "面料" not in name and "面A" not in name):
        return "collar_interlining"
    if any(key in name for key in ("上领", "领面", "领底", "领边", "领角")):
        return "collar"
    if any(key in name for key in ("领口", "领圈", "领带", "领贴", "领花边")):
        return "neck_binding"
    if "领" in name:
        return "collar"
    if any(key in name for key in ("袖口", "袖克夫", "克夫", "袖英")):
        return "cuff"
    if any(key in name for key in ("袖叉", "袖衩")):
        return "sleeve_placket"
    if "左袖" in name:
        return "sleeve_left"
    if "右袖" in name:
        return "sleeve_right"
    if "袖" in name:
        return "sleeve"
    if any(key in name for key in ("后育克", "后上")):
        return "back_yoke"
    if "前育克" in name:
        return "front_yoke"
    if any(key in name for key in ("左前", "前左", "前片左")):
        return "front_left"
    if any(key in name for key in ("右前", "前右", "前片右")):
        return "front_right"
    if any(key in name for key in ("前片", "前幅", "前身", "前下", "前中")) or parts & {"前"}:
        return "front_body"
    if any(key in name for key in ("后片", "后幅", "后身", "后下", "后中")) or parts & {"后"}:
        return "back_body"
    if any(key in name for key in ("侧片", "侧幅", "侧拼", "前侧", "后侧", "左拼", "右拼")):
        return "side_panel"
    return "unlabeled"


def guess_role(cad_name: str, family: str | None = None) -> str:
    name = cad_name or ""
    if family == "shirt":
        return _guess_shirt_role(name)
    parts = {part for part in name.replace("_", ".").split(".") if part}
    if any(key in name for key in ("前片", "前幅", "前身", "左前", "右前")) or parts & {"前", "前下", "前育克"}:
        return "front_body"
    if any(key in name for key in ("后片", "后幅", "后身", "后上", "后下", "后中", "后育克")) or parts & {"后"}:
        return "back_body"
    if any(key in name for key in ("侧片", "侧幅", "侧拼", "左拼", "右拼")):
        return "side_panel"
    if "袖口" in name:
        return "scrap"
    if "袖" in name:
        return "sleeve"
    if "领" in name:
        return "neck_binding"
    if any(key in name for key in ("脚朴", "压条", "洗标", "烫样", "烫版", "衬", "唛", "贴袋", "绣花", "夹底", "夹圈", "标位置")):
        return "scrap"
    return "unlabeled"


def _short_name(cad_name: str) -> str:
    rest = cad_name.split(".", 1)[-1] if "." in cad_name else cad_name
    return rest.split("_")[0] if rest else cad_name


def _pairs(path: Path):
    rows = [line.strip() for line in path.read_text(encoding="latin1").splitlines()]
    index = 0
    while index + 1 < len(rows):
        yield rows[index], rows[index + 1]
        index += 2


def _bbox_area(pts: list[list[float]]) -> float:
    if len(pts) < 2:
        return 0.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def _bbox(pts: list[list[float]]) -> tuple[float, float, float, float] | None:
    if len(pts) < 2:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _shift(pts: list[list[float]], dx: float, dy: float) -> list[list[float]]:
    return [[p[0] + dx, p[1] + dy] for p in pts]


def _close(pts: list[list[float]]) -> list[list[float]]:
    out = [p[:] for p in pts]
    if len(out) >= 2 and out[0] != out[-1]:
        out.append(out[0][:])
    return out


def _inside(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float], pad: float) -> bool:
    return (
        inner[0] >= outer[0] - pad
        and inner[1] >= outer[1] - pad
        and inner[2] <= outer[2] + pad
        and inner[3] <= outer[3] + pad
    )


def _looks_like_grain(pts: list[list[float]], box: tuple[float, float, float, float]) -> bool:
    if len(pts) > 6:
        return False
    w = max(box[2] - box[0], 1.0)
    h = max(box[3] - box[1], 1.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    return (dy >= 0.35 * h and dx <= 0.12 * w) or (dx >= 0.35 * w and dy <= 0.12 * h)


def opening_line_role(pts: list[list[float]], outer: list[list[float]]) -> str | None:
    """CF slash / half-placket lives as open internals, not on the outer cut."""
    box = _bbox(outer)
    if not box or len(pts) < 2:
        return None
    w = max(box[2] - box[0], 1.0)
    h = max(box[3] - box[1], 1.0)
    mid = (box[0] + box[2]) / 2.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    lw, lh = max(xs) - min(xs), max(ys) - min(ys)
    cx = (min(xs) + max(xs)) / 2.0
    if lh < 0.12 * h or lw > 0.22 * w or abs(cx - mid) > 0.18 * w:
        return None
    if lw <= max(8.0, 0.025 * w) and lh >= 0.45 * h:
        return "center_front"
    return "placket_line"


def _piece_details(
    polys: list[dict[str, Any]],
    outer: list[list[float]],
    dx: float,
    dy: float,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    outer_box = _bbox(outer)
    if not outer_box:
        return None, []
    outer_area = _bbox_area(outer)
    pad = max(12.0, 0.06 * max(outer_box[2] - outer_box[0], outer_box[3] - outer_box[1]))
    sew_candidates: list[tuple[float, list[list[float]]]] = []
    lines: list[dict[str, Any]] = []
    for poly in polys:
        pts = poly.get("points") or []
        if len(pts) < 2 or pts == outer:
            continue
        box = _bbox(pts)
        if not box or not _inside(box, outer_box, pad):
            continue
        closed = bool(poly.get("closed")) and len(pts) >= 3
        area = _bbox_area(pts)
        if closed and 0.45 * outer_area <= area < 0.995 * outer_area:
            sew_candidates.append((area, pts))
            continue
        role = "grainline" if _looks_like_grain(pts, outer_box) else (opening_line_role(pts, outer) or "internal")
        out_pts = _close(pts) if closed else [p[:] for p in pts]
        lines.append({"role": role, "closed": closed, "points": _shift(out_pts, dx, dy)})
    sew = None
    if sew_candidates:
        sew_candidates.sort(key=lambda row: row[0], reverse=True)
        sew = {"closed": True, "points": _shift(_close(sew_candidates[0][1]), dx, dy)}
        for _area, pts in sew_candidates[1:]:
            lines.append({"role": "internal", "closed": True, "points": _shift(_close(pts), dx, dy)})
    return sew, lines


def _piece_row(
    cad: str,
    family: str | None,
    outer: list[list[float]],
    sew: dict[str, Any] | None,
    lines: list[dict[str, Any]],
) -> dict[str, Any]:
    xs = [p[0] for p in outer]
    ys = [p[1] for p in outer]
    return {
        "cad_name": cad,
        "label": _short_name(cad),
        "role": guess_role(cad, family),
        "width_mm": round(max(xs) - min(xs), 1),
        "height_mm": round(max(ys) - min(ys), 1),
        "paths": [outer],
        "closed": True,
        "sew": sew,
        "lines": lines,
    }


def closed_cuts(path: Path, family: str | None = None) -> list[dict[str, Any]]:
    blocks: dict[str, list[dict[str, Any]]] = {}
    cur_block: str | None = None
    entity = ""
    in_poly = False
    closed = False
    pts: list[list[float]] = []
    inserts: list[dict[str, Any]] = []
    x: float | None = None
    line_xy: list[float | None] = [None, None, None, None]

    def flush() -> None:
        nonlocal in_poly, pts
        if in_poly and len(pts) >= 2:
            blocks.setdefault(cur_block or "_ents", []).append(
                {"points": [p[:] for p in pts], "closed": bool(closed and len(pts) >= 3)}
            )
        in_poly = False
        pts = []

    for code, val in _pairs(path):
        if code == "0":
            if in_poly and val != "VERTEX":
                flush()
            entity = val
            if val == "BLOCK":
                cur_block = None
            elif val == "ENDBLK":
                cur_block = None
            elif val in {"POLYLINE", "LWPOLYLINE"}:
                in_poly = True
                closed = False
                pts = []
            elif val == "LINE":
                line_xy = [None, None, None, None]
            elif val == "INSERT":
                inserts.append({"name": None, "x": 0.0, "y": 0.0})
            continue
        if code == "2" and entity == "BLOCK":
            cur_block = val
            blocks.setdefault(cur_block, [])
        elif code == "2" and entity == "INSERT" and inserts:
            inserts[-1]["name"] = val
        elif code == "70" and in_poly:
            try:
                closed = bool(int(float(val)) & 1)
            except ValueError:
                closed = False
        elif entity == "LINE":
            try:
                num = float(val)
            except ValueError:
                continue
            if code == "10":
                line_xy[0] = num
            elif code == "20":
                line_xy[1] = num
            elif code == "11":
                line_xy[2] = num
            elif code == "21":
                line_xy[3] = num
                if all(v is not None for v in line_xy):
                    blocks.setdefault(cur_block or "_ents", []).append({
                        "points": [[line_xy[0], line_xy[1]], [line_xy[2], line_xy[3]]],
                        "closed": False,
                    })
                line_xy = [None, None, None, None]
        elif code == "10":
            try:
                x = float(val)
            except ValueError:
                x = None
            if entity == "INSERT" and inserts and x is not None:
                inserts[-1]["x"] = x
        elif code == "20":
            try:
                y = float(val)
            except ValueError:
                continue
            if entity == "INSERT" and inserts:
                inserts[-1]["y"] = y
            elif in_poly and entity in {"VERTEX", "LWPOLYLINE"} and x is not None:
                pts.append([x, y])
                x = None
    flush()

    def polys_for(name: str) -> list[dict[str, Any]]:
        if name in blocks:
            return blocks[name]
        needle = name.replace("\xad", "")
        for key, value in blocks.items():
            if key.replace("\xad", "") == needle:
                return value
        return []

    def from_polys(name: str, polys: list[dict[str, Any]], dx: float, dy: float) -> dict[str, Any] | None:
        closed_big = [
            poly for poly in polys
            if poly.get("closed") and _bbox_area(poly.get("points") or []) >= 40 * 40
        ]
        if not closed_big:
            return None
        outer_raw = max(closed_big, key=lambda poly: _bbox_area(poly.get("points") or []))
        outer = _close(_shift(outer_raw["points"], dx, dy))
        sew, lines = _piece_details(polys, outer_raw["points"], dx, dy)
        return _piece_row(_decode_cad(name), family, outer, sew, lines)

    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for insert in inserts:
        name = str(insert.get("name") or "")
        if not name or name in used:
            continue
        used.add(name)
        row = from_polys(name, polys_for(name), float(insert["x"]), float(insert["y"]))
        if row:
            rows.append(row)
    if rows:
        return rows
    for name, polys in blocks.items():
        if not name or name == "_ents":
            continue
        row = from_polys(name, polys, 0.0, 0.0)
        if row:
            rows.append(row)
    return rows
