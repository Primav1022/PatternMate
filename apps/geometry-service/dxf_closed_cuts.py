"""Closed cut outlines from ANSI/AAMA R12 pattern DXFs (POLYLINE flag 70 bit 0)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]


def original_dxf_path(case_id: str, root: Path | None = None) -> Path | None:
    cid = str(case_id or "").strip()
    if not cid:
        return None
    base = root or Path(os.getenv("CHI27_ROOT") or _ROOT)
    extra = os.getenv("ORIGINAL_DXF_DIR")
    workspace = base.parent
    candidates = [
        base / "data" / "seed" / "dxf" / "original" / f"{cid}.dxf",
        workspace / "cad纸样源文件" / "正确版" / f"{cid}.dxf",
        workspace / "dxf修改版" / "针织112条(清洗过）" / f"{cid}.dxf",
        base / "data" / "seed" / "dxf" / "v1_annotated" / f"{cid}.annotated.dxf",
    ]
    if extra:
        candidates.insert(0, Path(extra) / f"{cid}.dxf")
    found = [path for path in candidates if path.exists()]
    if not found:
        return None
    return max(found, key=lambda path: len(closed_cuts(path)))


def _decode_cad(name: str) -> str:
    raw = name.encode("latin1")
    for enc in ("gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name.replace("\xad", "")


def guess_role(cad_name: str) -> str:
    name = cad_name or ""
    if any(key in name for key in ("前片", "前幅", "前身")):
        return "front_body"
    if any(key in name for key in ("后片", "后幅", "后身")):
        return "back_body"
    if "侧片" in name or "侧幅" in name:
        return "side_panel"
    if "袖" in name:
        return "sleeve"
    if "领" in name:
        return "neck_binding"
    if any(key in name for key in ("脚朴", "压条", "洗标", "烫样", "衬", "唛")):
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
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def closed_cuts(path: Path) -> list[dict[str, Any]]:
    blocks: dict[str, list[list[list[float]]]] = {}
    cur_block: str | None = None
    entity = ""
    in_poly = False
    closed = False
    pts: list[list[float]] = []
    inserts: list[dict[str, Any]] = []
    x: float | None = None

    def flush() -> None:
        nonlocal in_poly, pts
        if in_poly and closed and len(pts) >= 3:
            blocks.setdefault(cur_block or "_ents", []).append(pts)
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

    def polys_for(name: str) -> list[list[list[float]]]:
        if name in blocks:
            return blocks[name]
        needle = name.replace("\xad", "")
        for key, value in blocks.items():
            if key.replace("\xad", "") == needle:
                return value
        return []

    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for insert in inserts:
        name = str(insert.get("name") or "")
        if not name or name in used:
            continue
        used.add(name)
        polys = [poly for poly in polys_for(name) if _bbox_area(poly) >= 40 * 40]
        if not polys:
            continue
        outer = max(polys, key=_bbox_area)
        dx, dy = float(insert["x"]), float(insert["y"])
        shifted = [[p[0] + dx, p[1] + dy] for p in outer]
        if shifted[0] != shifted[-1]:
            shifted.append(shifted[0][:])
        xs = [p[0] for p in shifted]
        ys = [p[1] for p in shifted]
        cad = _decode_cad(name)
        rows.append({
            "cad_name": cad,
            "label": _short_name(cad),
            "role": guess_role(cad),
            "width_mm": round(max(xs) - min(xs), 1),
            "height_mm": round(max(ys) - min(ys), 1),
            "paths": [shifted],
            "closed": True,
        })
    return rows
