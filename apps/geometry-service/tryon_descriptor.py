from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np
from scipy.spatial import Delaunay
try:
    import triangle as constrained_delaunay
except ImportError:
    constrained_delaunay = None

from geometry_ops import entity_points, polyline_length, simplify_polyline


ANNOTATION_TOKENS = (
    "construction", "grain", "notch", "pleat", "pocket_position",
    "button", "dart", "fold", "guide", "mark",
)
BOUNDARY_TOKENS = (
    "outline", "boundary", "seam", "hem", "armhole", "neckline", "shoulder",
    "sleeve_cap", "underarm", "cuff_edge", "collar_edge", "cut",
)


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _same_point(a: list[float], b: list[float], tolerance: float = 1.5) -> bool:
    return _distance(a, b) <= tolerance


def _deduplicate(points: list[list[float]], tolerance: float = 0.05) -> list[list[float]]:
    out: list[list[float]] = []
    for point in points:
        rounded = [round(float(point[0]), 3), round(float(point[1]), 3)]
        if not out or not _same_point(out[-1], rounded, tolerance):
            out.append(rounded)
    return out


def _signed_area(points: list[list[float]]) -> float:
    return 0.5 * sum(
        a[0] * b[1] - b[0] * a[1]
        for a, b in zip(points, points[1:] + points[:1])
    )


def _simplify_ring(points: list[list[float]], tolerance: float = 0.8) -> list[list[float]]:
    if len(points) < 5:
        return points
    split = max(range(1, len(points)), key=lambda index: _distance(points[0], points[index]))
    first = simplify_polyline(points[: split + 1], tol=tolerance)
    second = simplify_polyline(points[split:] + [points[0]], tol=tolerance)
    return _deduplicate(first + second[1:-1])


def _closed_boundary(polylines: list[list[list[float]]], tolerance: float = 1.5) -> list[list[float]]:
    remaining = [_deduplicate(line) for line in polylines if len(_deduplicate(line)) >= 2]
    loops: list[list[list[float]]] = []
    while remaining:
        chain = remaining.pop(0)
        changed = True
        while changed and remaining:
            changed = False
            best: tuple[float, int, str] | None = None
            for index, line in enumerate(remaining):
                choices = (
                    (_distance(chain[-1], line[0]), "append"),
                    (_distance(chain[-1], line[-1]), "append_reverse"),
                    (_distance(chain[0], line[-1]), "prepend"),
                    (_distance(chain[0], line[0]), "prepend_reverse"),
                )
                distance, mode = min(choices, key=lambda item: item[0])
                if distance <= tolerance and (best is None or distance < best[0]):
                    best = (distance, index, mode)
            if best is None:
                break
            _, index, mode = best
            line = remaining.pop(index)
            if mode == "append":
                chain.extend(line[1:])
            elif mode == "append_reverse":
                chain.extend(list(reversed(line))[1:])
            elif mode == "prepend":
                chain = line[:-1] + chain
            else:
                chain = list(reversed(line))[:-1] + chain
            changed = True
        if len(chain) >= 4 and _same_point(chain[0], chain[-1], tolerance):
            chain[-1] = chain[0][:]
            loops.append(_deduplicate(chain[:-1]))
    if not loops:
        return []
    return max(loops, key=lambda points: abs(_signed_area(points)))


def _inside_triangle(point: list[float], a: list[float], b: list[float], c: list[float]) -> bool:
    def cross(p: list[float], q: list[float], r: list[float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    ab = cross(a, b, point)
    bc = cross(b, c, point)
    ca = cross(c, a, point)
    return (ab >= -1e-7 and bc >= -1e-7 and ca >= -1e-7) or (
        ab <= 1e-7 and bc <= 1e-7 and ca <= 1e-7
    )


def _triangulate(points: list[list[float]]) -> list[list[int]]:
    if len(points) < 3:
        return []
    order = list(range(len(points)))
    if _signed_area(points) < 0:
        order.reverse()
    triangles: list[list[int]] = []
    guard = len(order) * len(order)
    while len(order) > 3 and guard > 0:
        guard -= 1
        clipped = False
        for position, current in enumerate(order):
            previous = order[position - 1]
            following = order[(position + 1) % len(order)]
            a, b, c = points[previous], points[current], points[following]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if cross <= 1e-7:
                continue
            if any(
                _inside_triangle(points[index], a, b, c)
                for index in order
                if index not in {previous, current, following}
            ):
                continue
            triangles.append([previous, current, following])
            del order[position]
            clipped = True
            break
        if not clipped:
            return []
    if len(order) == 3:
        triangles.append(order)
    return triangles


def _point_in_polygon(point: list[float] | tuple[float, float], polygon: list[list[float]]) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        cross = (x - previous[0]) * (current[1] - previous[1]) - (y - previous[1]) * (current[0] - previous[0])
        if abs(cross) <= 1e-5 and min(previous[0], current[0]) - 1e-5 <= x <= max(previous[0], current[0]) + 1e-5 and min(previous[1], current[1]) - 1e-5 <= y <= max(previous[1], current[1]) + 1e-5:
            return True
        if (current[1] > y) != (previous[1] > y):
            crossing_x = (previous[0] - current[0]) * (y - current[1]) / (previous[1] - current[1]) + current[0]
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _sample_polyline(points: list[list[float]], spacing: float, closed: bool = False) -> list[list[float]]:
    if len(points) < 2:
        return points
    sampled = [[round(points[0][0], 3), round(points[0][1], 3)]]
    remaining = spacing
    path = points + ([points[0]] if closed else [])
    for original_start, end in zip(path, path[1:]):
        start = [float(original_start[0]), float(original_start[1])]
        segment_length = _distance(start, end)
        while segment_length >= remaining and segment_length > 1e-8:
            ratio = remaining / segment_length
            start = [start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio]
            sampled.append([round(start[0], 3), round(start[1], 3)])
            segment_length = _distance(start, end)
            remaining = spacing
        remaining -= segment_length
    if not closed:
        sampled.append([round(points[-1][0], 3), round(points[-1][1], 3)])
    return _deduplicate(sampled)


def _dense_panel_mesh(boundary: list[list[float]], target_edge_mm: float = 12.0) -> tuple[list[list[float]], list[list[int]], list[int]]:
    if len(boundary) < 3:
        return boundary, [], []
    boundary_points = _sample_polyline(boundary, target_edge_mm, closed=True)
    # Bound the noisy legacy outline before the finite scipy Delaunay path.
    # Rejecting large sampled rings here made normal front/back T-shirt panels
    # permanently fail `missing_dense_panel_mesh`.
    if len(boundary_points) > 120:
        perimeter = sum(_distance(start, end) for start, end in zip(boundary, boundary[1:] + boundary[:1]))
        boundary_points = _sample_polyline(boundary, max(target_edge_mm, perimeter / 112.0), closed=True)
    # Use the bounded scipy/fallback path instead of Triangle for service safety.
    # Some valid-looking but noisy DXF rings make Triangle spend unbounded time.
    min_x, max_x = min(point[0] for point in boundary), max(point[0] for point in boundary)
    min_y, max_y = min(point[1] for point in boundary), max(point[1] for point in boundary)
    if max_x - min_x < 1.0 or max_y - min_y < 1.0:
        return boundary_points, [], list(range(len(boundary_points)))
    interior: list[list[float]] = []
    def boundary_distance(point: list[float]) -> float:
        best = float("inf")
        for start, end in zip(boundary, boundary[1:] + boundary[:1]):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length_sq = dx * dx + dy * dy
            ratio = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq))
            best = min(best, _distance(point, [start[0] + ratio * dx, start[1] + ratio * dy]))
        return best
    row = 0
    y = min_y + target_edge_mm
    while y < max_y:
        offset = target_edge_mm * .5 if row % 2 else 0.0
        x = min_x + target_edge_mm + offset
        while x < max_x:
            if _point_in_polygon((x, y), boundary) and boundary_distance([x, y]) >= target_edge_mm * .45:
                interior.append([round(x, 3), round(y, 3)])
            x += target_edge_mm
        y += target_edge_mm * .866
        row += 1
    vertices = boundary_points + interior
    if len(vertices) < 3:
        return vertices, [], list(range(len(boundary_points)))
    try:
        simplices = Delaunay(np.asarray(vertices, dtype=np.float64)).simplices.tolist()
    except Exception:
        return vertices, [], list(range(len(boundary_points)))
    triangles: list[list[int]] = []
    for triangle in simplices:
        points = [vertices[index] for index in triangle]
        area = abs(_signed_area(points))
        if area < 0.25:
            continue
        checks = [
            [sum(point[0] for point in points) / 3, sum(point[1] for point in points) / 3],
            [(points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2],
            [(points[1][0] + points[2][0]) / 2, (points[1][1] + points[2][1]) / 2],
            [(points[2][0] + points[0][0]) / 2, (points[2][1] + points[0][1]) / 2],
        ]
        if all(_point_in_polygon(point, boundary) for point in checks):
            triangles.append([int(index) for index in triangle])
    return vertices, triangles, list(range(len(boundary_points)))


def _nearest_vertex_ids(points: list[list[float]], vertices: list[list[float]], candidates: list[int] | None = None) -> list[int]:
    ids: list[int] = []
    search_ids = candidates or list(range(len(vertices)))
    for point in points:
        nearest = min(search_ids, key=lambda index: _distance(point, vertices[index]))
        if not ids or ids[-1] != nearest:
            ids.append(nearest)
    return ids


def _fold_edge_x(boundary: list[list[float]]) -> float | None:
    if len(boundary) < 3:
        return None
    height = max(point[1] for point in boundary) - min(point[1] for point in boundary)
    for start, end in zip(boundary, boundary[1:] + boundary[:1]):
        if abs(start[0] - end[0]) <= 1.5 and abs(start[1] - end[1]) >= height * .65:
            return round((start[0] + end[0]) / 2, 3)
    return None


def _unfold_mesh(vertices: list[list[float]], triangles: list[list[int]], fold_x: float | None) -> tuple[list[list[float]], list[list[int]], dict[int, int]]:
    if fold_x is None:
        return vertices, triangles, {}
    unfolded = [point[:] for point in vertices]
    mirror: dict[int, int] = {}
    for index, point in enumerate(vertices):
        if abs(point[0] - fold_x) <= 1.5:
            mirror[index] = index
        else:
            mirror[index] = len(unfolded)
            unfolded.append([round(2 * fold_x - point[0], 3), point[1]])
    mirrored_triangles = [[mirror[triangle[0]], mirror[triangle[2]], mirror[triangle[1]]] for triangle in triangles]
    return unfolded, triangles + mirrored_triangles, mirror


def _is_boundary_role(role: str) -> bool:
    lowered = role.lower()
    return not any(token in lowered for token in ANNOTATION_TOKENS) and (
        lowered == "unknown" or any(token in lowered for token in BOUNDARY_TOKENS)
    )


def _seam_kind(role: str) -> str | None:
    lowered = role.lower()
    if "side_seam" in lowered:
        return "side"
    if "shoulder" in lowered:
        return "shoulder"
    if "armhole" in lowered:
        return "armhole"
    if "sleeve_cap" in lowered or "sleeve_head" in lowered:
        return "sleeve_cap"
    if "underarm" in lowered:
        return "underarm"
    if "neckline" in lowered:
        return "neckline"
    if "collar_attach" in lowered or "neck_attach" in lowered:
        return "neck_attach"
    if "cuff_attach" in lowered:
        return "cuff_attach"
    return None


def _role_side(role: str) -> str:
    if role.startswith("front"):
        return "front"
    if role.startswith("back"):
        return "back"
    if role.startswith("sleeve") or role == "sleeve":
        return "sleeve"
    if role.startswith("neck") or role.startswith("collar"):
        return "neck"
    return "other"


def _build_seam_pairs(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for panel in panels:
        side = _role_side(panel["role"])
        panel_xs = [float(point[0]) for point in panel.get("mesh_vertices_2d_mm") or []]
        panel_center_x = (min(panel_xs) + max(panel_xs)) / 2 if panel_xs else 0.0
        for edge in panel["edges"]:
            kind = _seam_kind(edge["line_role"])
            if side == "sleeve" and kind == "armhole":
                kind = "sleeve_cap"
            elif side == "neck" and kind == "neckline":
                kind = "neck_attach"
            if kind:
                points = edge.get("points_2d_mm") or []
                vertex_sets = edge.get("ordered_vertex_id_sets") or [edge.get("ordered_vertex_ids") or []]
                for set_index, vertex_ids in enumerate(vertex_sets):
                    if len(vertex_ids) < 2:
                        continue
                    grouped[(side, kind)].append({
                        "panel_id": panel["panel_id"],
                        "edge_id": edge["edge_id"],
                        "edge_instance": set_index,
                        "vertex_ids": vertex_ids,
                        "length_mm": float(edge.get("length_mm") or 0),
                        "center_x": ((sum(float(point[0]) for point in points) / max(len(points), 1)) - panel_center_x) * (-1 if set_index else 1),
                        "start": points[0] if points else [0.0, 0.0],
                        "end": points[-1] if points else [0.0, 0.0],
                    })

    definitions = (
        ("body_side", ("front", "side"), ("back", "side")),
        ("shoulder", ("front", "shoulder"), ("back", "shoulder")),
        ("armhole_sleeve", ("front", "armhole"), ("sleeve", "sleeve_cap")),
        ("armhole_sleeve", ("back", "armhole"), ("sleeve", "sleeve_cap")),
        ("neckline", ("front", "neckline"), ("neck", "neck_attach")),
        ("neckline", ("back", "neckline"), ("neck", "neck_attach")),
    )
    def constrain(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
        a_ids, b_ids = list(a["vertex_ids"]), list(b["vertex_ids"])
        count = min(len(a_ids), len(b_ids))
        if count < 2:
            return None
        a_sample = [a_ids[round(index * (len(a_ids) - 1) / (count - 1))] for index in range(count)]
        b_sample = [b_ids[round(index * (len(b_ids) - 1) / (count - 1))] for index in range(count)]
        direct = _distance(a["start"], b["start"]) + _distance(a["end"], b["end"])
        reverse = _distance(a["start"], b["end"]) + _distance(a["end"], b["start"])
        orientation = "reverse" if reverse < direct else "forward"
        if orientation == "reverse":
            b_sample.reverse()
        length_a, length_b = float(a["length_mm"]), float(b["length_mm"])
        a_role = _role_side(next(panel["role"] for panel in panels if panel["panel_id"] == a["panel_id"]))
        b_role = _role_side(next(panel["role"] for panel in panels if panel["panel_id"] == b["panel_id"]))
        side_instance = "left" if (b["center_x"] if a_role == "sleeve" else a["center_x"]) < 0 else "right"
        return {
            "a_panel_id": a["panel_id"],
            "b_panel_id": b["panel_id"],
            "a_edge_id": a["edge_id"],
            "b_edge_id": b["edge_id"],
            "a_instance": side_instance if a_role == "sleeve" else "default",
            "b_instance": side_instance if b_role == "sleeve" else "default",
            "orientation": orientation,
            "vertex_pairs": [[a_sample[index], b_sample[index]] for index in range(count)],
            "length_a_mm": round(length_a, 3),
            "length_b_mm": round(length_b, 3),
            "length_mismatch_mm": round(abs(length_a - length_b), 3),
            "length_mismatch_ratio": round(abs(length_a - length_b) / max(length_a, length_b, 1.0), 5),
        }

    pairs = []
    for index, (kind, a_key, b_key) in enumerate(definitions, 1):
        if grouped[a_key] and grouped[b_key]:
            a_edges = sorted(grouped[a_key], key=lambda edge: edge["center_x"])
            b_edges = sorted(grouped[b_key], key=lambda edge: edge["center_x"])
            count = max(len(a_edges), len(b_edges))
            constraints = [
                constraint
                for constraint in (
                    constrain(
                        a_edges[round(position * (len(a_edges) - 1) / max(count - 1, 1))],
                        b_edges[round(position * (len(b_edges) - 1) / max(count - 1, 1))],
                    )
                    for position in range(count)
                )
                if constraint
            ]
            if not constraints:
                continue
            pairs.append({
                "seam_id": f"{kind}:{index}",
                "kind": kind,
                "a": [{key: value for key, value in edge.items() if key not in {"center_x", "start", "end"}} for edge in a_edges],
                "b": [{key: value for key, value in edge.items() if key not in {"center_x", "start", "end"}} for edge in b_edges],
                "constraints": constraints,
            })
    underarm_edges = sorted(grouped[("sleeve", "underarm")], key=lambda edge: edge["center_x"])
    if len(underarm_edges) >= 2:
        constraints = []
        for instance in ("left", "right"):
            constraint = constrain(underarm_edges[0], underarm_edges[-1])
            if constraint:
                constraint["a_instance"] = instance
                constraint["b_instance"] = instance
                constraints.append(constraint)
        if constraints:
            pairs.append({
                "seam_id": "sleeve_underarm:self",
                "kind": "sleeve_underarm",
                "a": [{key: value for key, value in underarm_edges[0].items() if key not in {"center_x", "start", "end"}}],
                "b": [{key: value for key, value in underarm_edges[-1].items() if key not in {"center_x", "start", "end"}}],
                "constraints": constraints,
            })
    return pairs


def build_tryon_descriptor(
    entities: list[dict[str, Any]], recipe_hash: str, family: str
) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        source = str(entity.get("_source_case") or "unknown")
        role = str(entity.get("_piece_role") or "unknown")
        piece_id = str(entity.get("piece_id") or entity.get("entity_id") or "unknown")
        grouped[(source, role, piece_id)].append(entity)

    panels: list[dict[str, Any]] = []
    for (source, role, piece_id), piece_entities in sorted(grouped.items()):
        edge_entities = [entity for entity in piece_entities if _is_boundary_role(str(entity.get("line_role") or "unknown"))]
        semantic_entities = [
            entity for entity in edge_entities
            if str(entity.get("line_role") or "unknown").lower() not in {"unknown", "cut_line"}
        ]
        boundary = _closed_boundary([entity_points(entity) for entity in semantic_entities], tolerance=2.5)
        if not boundary:
            cut_entities = [entity for entity in edge_entities if str(entity.get("line_role") or "").lower() == "cut_line"]
            boundary = _closed_boundary([entity_points(entity) for entity in cut_entities], tolerance=15.0)
        if not boundary:
            boundary = _closed_boundary([entity_points(entity) for entity in edge_entities], tolerance=2.5)
        if boundary:
            boundary = _simplify_ring(boundary)
        origin_x = min((point[0] for point in boundary), default=0.0)
        origin_y = min((point[1] for point in boundary), default=0.0)
        local_boundary = [[round(point[0] - origin_x, 3), round(point[1] - origin_y, 3)] for point in boundary]
        triangles = _triangulate(local_boundary) if len(local_boundary) <= 220 else []
        mesh_vertices, mesh_triangles, boundary_vertex_ids = _dense_panel_mesh(local_boundary, 12.0)
        fold_edges = [entity_points(entity) for entity in edge_entities if "fold" in str(entity.get("line_role") or "").lower()]
        fold_x = round(sum(point[0] - origin_x for edge in fold_edges for point in edge) / sum(len(edge) for edge in fold_edges), 3) if fold_edges else None
        mesh_vertices, mesh_triangles, mirror_ids = _unfold_mesh(mesh_vertices, mesh_triangles, fold_x)
        if mirror_ids:
            boundary_vertex_ids = list(dict.fromkeys(boundary_vertex_ids + [mirror_ids[index] for index in boundary_vertex_ids]))
        panel_id = f"{source}:{role}:{piece_id}"
        edges = []
        for edge_index, entity in enumerate(edge_entities, 1):
            points = entity_points(entity)
            if len(points) < 2:
                continue
            local_points = [[round(point[0] - origin_x, 3), round(point[1] - origin_y, 3)] for point in points]
            sampled_points = _sample_polyline(local_points, 12.0)
            ordered_vertex_ids = _nearest_vertex_ids(sampled_points, mesh_vertices, boundary_vertex_ids) if mesh_vertices else []
            ordered_vertex_id_sets = [ordered_vertex_ids]
            if mirror_ids and not all(mirror_ids.get(index, index) == index for index in ordered_vertex_ids):
                ordered_vertex_id_sets.append([mirror_ids.get(index, index) for index in ordered_vertex_ids])
            edges.append({
                "edge_id": f"{panel_id}:edge:{edge_index}",
                "line_role": str(entity.get("line_role") or "unknown"),
                "points_2d_mm": local_points,
                "length_mm": round(polyline_length(points), 3),
                "ordered_vertex_ids": ordered_vertex_ids,
                "ordered_vertex_id_sets": ordered_vertex_id_sets,
            })
        panels.append({
            "panel_id": panel_id,
            "role": role,
            "instances": ["left", "right"] if role == "sleeve" else ["default"],
            "source_case_id": source,
            "origin_mm": [round(origin_x, 3), round(origin_y, 3)],
            "vertices_2d_mm": local_boundary,
            "triangles": triangles,
            "mesh_vertices_2d_mm": mesh_vertices,
            "mesh_triangles": mesh_triangles,
            "boundary_vertex_ids": boundary_vertex_ids,
            "mesh_target_edge_mm": 12.0,
            "cut_on_fold_x_mm": fold_x,
            "edges": edges,
            "boundary_closed": bool(local_boundary),
            "area_mm2": round(abs(_signed_area(local_boundary)), 3) if local_boundary else 0.0,
        })

    seam_pairs = _build_seam_pairs(panels)
    roles = {panel["role"] for panel in panels if panel["boundary_closed"] and panel["triangles"]}
    errors = []
    for required, label in (({"front_body", "front_left", "front_right"}, "front_body"), ({"back_body", "back_yoke"}, "back_body")):
        if not roles & required:
            errors.append(f"missing_closed_{label}_panel")
    if not roles & {"sleeve", "sleeve_left", "sleeve_right"}:
        errors.append("missing_closed_sleeve_panel")
    seam_kinds = {pair["kind"] for pair in seam_pairs}
    for required in ("body_side", "shoulder", "armhole_sleeve"):
        if required not in seam_kinds:
            errors.append(f"missing_{required}_seam_pair")
    if roles & {"neck_binding", "neck_rib", "collar", "collar_stand"} and "neckline" not in seam_kinds:
        errors.append("missing_neckline_seam_pair")
    if any(panel["boundary_closed"] and not panel["mesh_triangles"] for panel in panels):
        errors.append("missing_dense_panel_mesh")
    if any(not pair.get("constraints") for pair in seam_pairs):
        errors.append("missing_explicit_seam_constraints")
    return {
        "version": "patternmate.tryon.v2",
        "unit": "mm",
        "recipe_hash": recipe_hash,
        "family": family,
        "panels": panels,
        "seam_pairs": seam_pairs,
        "validation": {
            "tryon_ready": not errors,
            "errors": errors,
            "closed_panel_count": sum(1 for panel in panels if panel["boundary_closed"]),
            "triangulated_panel_count": sum(1 for panel in panels if panel["triangles"]),
            "dense_mesh_panel_count": sum(1 for panel in panels if panel["mesh_triangles"]),
            "seam_constraint_count": sum(len(pair.get("constraints") or []) for pair in seam_pairs),
        },
    }
