"""AAMA-style AutoCAD R12 ASCII DXF writer for apparel pattern pieces."""
from __future__ import annotations

import math
import re
from collections import OrderedDict

from geometry_ops import entity_points, optimize_entity


BOUNDARY_ROLES = {"cut", "cut_line", "pattern_boundary", "outer_contour", "net_boundary"}
NOTCH_ROLES = {"notch"}
GRAIN_ROLES = {"grainline"}
STITCH_ROLES = {"seam", "sew", "stitch", "seam_line", "stitch_line"}
POINT_EPSILON = 1e-9


def _pairs(code: int, value: object) -> list[str]:
    return [str(code), str(value)]


def _line_role(entity: dict) -> str:
    return str(entity.get("line_role") or entity.get("edge_role") or "").strip().lower()


def _layer(entity: dict) -> str:
    role = _line_role(entity)
    if role in BOUNDARY_ROLES or role.endswith("_cut"):
        return "1"
    if role in NOTCH_ROLES:
        return "4"
    if role in GRAIN_ROLES:
        return "7"
    if role in STITCH_ROLES or role.endswith(("_seam", "_sew", "_stitch")):
        return "11"
    return "8"


def _safe_points(entity: dict) -> list[list[float]]:
    try:
        points = entity_points(entity)
    except (TypeError, ValueError, OverflowError):
        return []
    if len(points) < 2:
        return []
    normalized: list[list[float]] = []
    for point in points:
        if len(point) < 2:
            return []
        x, y = float(point[0]), float(point[1])
        if not math.isfinite(x) or not math.isfinite(y):
            return []
        if normalized and math.hypot(x - normalized[-1][0], y - normalized[-1][1]) <= POINT_EPSILON:
            continue
        normalized.append([x, y])
    return normalized if len(normalized) >= 2 else []


def _closed(entity: dict, points: list[list[float]]) -> bool:
    if (entity.get("geometry") or {}).get("closed"):
        return True
    return len(points) >= 3 and math.hypot(
        points[0][0] - points[-1][0],
        points[0][1] - points[-1][1],
    ) <= POINT_EPSILON


def _format_number(value: float) -> str:
    return f"{value:.6f}"


def _line_rows(layer: str, points: list[list[float]]) -> list[str]:
    return (
        _pairs(0, "LINE")
        + _pairs(8, layer)
        + _pairs(10, _format_number(points[0][0]))
        + _pairs(20, _format_number(points[0][1]))
        + _pairs(30, "0.0")
        + _pairs(11, _format_number(points[1][0]))
        + _pairs(21, _format_number(points[1][1]))
        + _pairs(31, "0.0")
    )


def _polyline_rows(layer: str, points: list[list[float]], closed: bool) -> list[str]:
    vertices = points
    if closed and len(vertices) >= 2 and math.hypot(
        vertices[0][0] - vertices[-1][0],
        vertices[0][1] - vertices[-1][1],
    ) <= POINT_EPSILON:
        vertices = vertices[:-1]
    flag = 1 if closed and len(vertices) >= 3 else 0
    rows = _pairs(0, "POLYLINE") + _pairs(8, layer) + _pairs(66, 1) + _pairs(70, flag)
    for x, y in vertices:
        rows += (
            _pairs(0, "VERTEX")
            + _pairs(8, layer)
            + _pairs(10, _format_number(x))
            + _pairs(20, _format_number(y))
            + _pairs(30, "0.0")
        )
    rows += _pairs(0, "SEQEND") + _pairs(8, layer)
    return rows


def _geometry_rows(entity: dict, points: list[list[float]], closed: bool) -> list[str]:
    layer = _layer(entity)
    if len(points) == 2 and not closed:
        return _line_rows(layer, points)
    return _polyline_rows(layer, points, closed)


def _block_name(piece_role: str, index: int) -> str:
    safe_role = re.sub(r"[^A-Z0-9_]+", "_", piece_role.upper()).strip("_") or "PIECE"
    return f"PM.{safe_role[:20]}.{index:02d}"


def _piece_name(piece_role: str, index: int) -> str:
    safe_role = re.sub(r"[^A-Z0-9]+", " ", piece_role.upper()).strip() or "PIECE"
    return f"{safe_role[:24]} {index:02d}"


def _piece_name_rows(piece_name: str, bounds: list[float]) -> list[str]:
    min_x, min_y, max_x, max_y = bounds
    return (
        _pairs(0, "TEXT")
        + _pairs(8, 1)
        + _pairs(10, _format_number((min_x + max_x) / 2.0))
        + _pairs(20, _format_number((min_y + max_y) / 2.0))
        + _pairs(30, "0.0")
        + _pairs(40, "10.000000")
        + _pairs(50, "0.000000")
        + _pairs(1, f"PIECE NAME: {piece_name}")
    )


def _block_rows(name: str, geometry_rows: list[str], piece_name_rows: list[str]) -> list[str]:
    return (
        _pairs(0, "BLOCK")
        + _pairs(8, 1)
        + _pairs(2, name)
        + _pairs(70, 0)
        + _pairs(10, "0.0")
        + _pairs(20, "0.0")
        + geometry_rows
        + piece_name_rows
        + _pairs(0, "ENDBLK")
    )


def _insert_rows(name: str) -> list[str]:
    return (
        _pairs(0, "INSERT")
        + _pairs(8, 1)
        + _pairs(2, name)
        + _pairs(10, 0)
        + _pairs(20, 0)
    )


def write_entities_dxf(
    entities: list[dict],
    path: str,
    *,
    piece_role_by_id: dict[str, str] | None = None,
    optimize: bool = True,
) -> dict:
    """Write grouped pattern entities as AAMA-style R12 BLOCK/INSERT DXF."""
    piece_role_by_id = piece_role_by_id or {}
    grouped: OrderedDict[str, dict] = OrderedDict()
    skipped = 0
    ungrouped = 0
    written = 0
    closed_count = 0

    for raw in entities:
        piece_id = str(raw.get("piece_id") or "").strip()
        if not piece_id:
            skipped += 1
            ungrouped += 1
            continue
        try:
            entity = optimize_entity(raw) if optimize else raw
        except (TypeError, ValueError, OverflowError):
            skipped += 1
            continue
        points = _safe_points(entity)
        if len(points) < 2:
            skipped += 1
            continue
        closed = _closed(entity, points)
        if closed:
            closed_count += 1
        group = grouped.setdefault(piece_id, {
            "piece_role": str(
                piece_role_by_id.get(piece_id)
                or entity.get("_piece_role")
                or entity.get("piece_role")
                or "piece"
            ),
            "rows": [],
            "bounds": [math.inf, math.inf, -math.inf, -math.inf],
            "entities_written": 0,
        })
        group["rows"].extend(_geometry_rows(entity, points, closed))
        for x, y in points:
            group["bounds"][0] = min(group["bounds"][0], x)
            group["bounds"][1] = min(group["bounds"][1], y)
            group["bounds"][2] = max(group["bounds"][2], x)
            group["bounds"][3] = max(group["bounds"][3], y)
        group["entities_written"] += 1
        written += 1

    valid_groups = [(piece_id, group) for piece_id, group in grouped.items() if group["rows"]]
    if not valid_groups:
        raise ValueError("no valid pattern pieces to export")

    block_rows: list[str] = []
    insert_rows: list[str] = []
    pieces: list[dict] = []
    for index, (piece_id, group) in enumerate(valid_groups, 1):
        name = _block_name(group["piece_role"], index)
        piece_name = _piece_name(group["piece_role"], index)
        block_rows.extend(_block_rows(name, group["rows"], _piece_name_rows(piece_name, group["bounds"])))
        insert_rows.extend(_insert_rows(name))
        pieces.append({
            "piece_id": piece_id,
            "piece_role": group["piece_role"],
            "piece_name": piece_name,
            "block_name": name,
            "entities_written": group["entities_written"],
        })

    lines = (
        _pairs(999, "ANSI/AAMA")
        + _pairs(0, "SECTION")
        + _pairs(2, "BLOCKS")
        + block_rows
        + _pairs(0, "ENDSEC")
        + _pairs(0, "SECTION")
        + _pairs(2, "ENTITIES")
        + insert_rows
        + _pairs(0, "ENDSEC")
        + _pairs(0, "EOF")
    )
    payload = ("\r\n".join(lines) + "\r\n").encode("ascii")
    with open(path, "wb") as handle:
        handle.write(payload)

    return {
        "path": path,
        "entities_written": written,
        "entities_skipped": skipped,
        "ungrouped_entities_skipped": ungrouped,
        "closed_polylines": closed_count,
        "blocks_written": len(pieces),
        "inserts_written": len(pieces),
        "pieces": pieces,
        "format": "aama_r12_blocks",
        "bytes": len(payload),
    }
