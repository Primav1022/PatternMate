from __future__ import annotations

import json
import math
import struct
from typing import Iterable

import numpy as np


def _pad4(data: bytes, fill: bytes = b"\x00") -> bytes:
    return data + fill * ((4 - len(data) % 4) % 4)


def vertex_normals(vertices, faces) -> list[tuple[float, float, float]]:
    points = np.asarray(vertices, dtype=np.float32)
    triangles = np.asarray(faces, dtype=np.int64)
    normals = np.zeros_like(points)
    if len(points) and len(triangles):
        face_normals = np.cross(points[triangles[:, 1]] - points[triangles[:, 0]], points[triangles[:, 2]] - points[triangles[:, 0]])
        for corner in range(3):
            np.add.at(normals, triangles[:, corner], face_normals)
        lengths = np.linalg.norm(normals, axis=1)
        valid = lengths > 1e-12
        normals[valid] /= lengths[valid, None]
        normals[~valid, 1] = 1.0
    return [tuple(map(float, normal)) for normal in normals]


def ellipsoid(center: tuple[float, float, float], scale: tuple[float, float, float], rings: int = 10, segments: int = 16):
    vertices = []
    faces = []
    for ring in range(rings + 1):
        phi = math.pi * ring / rings
        for segment in range(segments):
            theta = 2 * math.pi * segment / segments
            vertices.append((
                center[0] + scale[0] * math.sin(phi) * math.cos(theta),
                center[1] + scale[1] * math.cos(phi),
                center[2] + scale[2] * math.sin(phi) * math.sin(theta),
            ))
    for ring in range(rings):
        for segment in range(segments):
            a = ring * segments + segment
            b = ring * segments + (segment + 1) % segments
            c = (ring + 1) * segments + segment
            d = (ring + 1) * segments + (segment + 1) % segments
            faces.extend(((a, c, b), (b, c, d)))
    return vertices, faces


def combine(parts: Iterable[tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]]):
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for part_vertices, part_faces in parts:
        offset = len(vertices)
        vertices.extend(part_vertices)
        faces.extend((a + offset, b + offset, c + offset) for a, b, c in part_faces)
    return vertices, faces


def garment_shell(top: float, bottom: float, half_width: float, depth: float, refined: bool):
    rings = 18 if refined else 12
    segments = 48 if refined else 32
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for ring in range(rings + 1):
        fraction = ring / rings
        width = half_width * (1 - 0.07 * math.sin(fraction * math.pi))
        for segment in range(segments):
            angle = 2 * math.pi * segment / segments
            shoulder_drop = 0.045 * abs(math.cos(angle)) ** 1.5 * (1 - fraction)
            vertices.append((width * math.cos(angle), top + (bottom - top) * fraction - shoulder_drop, depth * math.sin(angle)))
    for ring in range(rings):
        for segment in range(segments):
            a = ring * segments + segment
            b = ring * segments + (segment + 1) % segments
            c = (ring + 1) * segments + segment
            d = (ring + 1) * segments + (segment + 1) % segments
            faces.extend(((a, c, b), (b, c, d)))
    return vertices, faces


def garment_yoke(top: float, half_width: float, depth: float, neck_width: float, neck_depth: float, refined: bool):
    segments = 48 if refined else 32
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for segment in range(segments):
        angle = 2 * math.pi * segment / segments
        outer_y = top - 0.045 * abs(math.cos(angle)) ** 1.5
        vertices.extend((
            (half_width * math.cos(angle), outer_y, depth * math.sin(angle)),
            (neck_width * math.cos(angle), top + 0.025, neck_depth * math.sin(angle)),
        ))
    for segment in range(segments):
        outer = segment * 2
        inner = outer + 1
        next_outer = ((segment + 1) % segments) * 2
        next_inner = next_outer + 1
        faces.extend(((outer, next_outer, inner), (inner, next_outer, next_inner)))
    return vertices, faces


def garment_sleeve(side: int, shoulder_x: float, top: float, length: float, depth: float, refined: bool):
    rings = 10 if refined else 7
    segments = 28 if refined else 20
    start = (side * shoulder_x * 0.88, top - 0.075, 0.0)
    end = (side * (shoulder_x + length * 0.48), top - length * 0.84, 0.0)
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    dx, dy = end[0] - start[0], end[1] - start[1]
    axis_length = max(math.hypot(dx, dy), 1e-6)
    perpendicular = (-dy / axis_length, dx / axis_length)
    for ring in range(rings + 1):
        fraction = ring / rings
        center_x = start[0] + dx * fraction
        center_y = start[1] + dy * fraction
        radius = (0.105 - 0.032 * fraction)
        for segment in range(segments):
            angle = 2 * math.pi * segment / segments
            vertices.append((
                center_x + perpendicular[0] * radius * math.cos(angle),
                center_y + perpendicular[1] * radius * math.cos(angle),
                depth * (0.78 - 0.12 * fraction) * math.sin(angle),
            ))
    for ring in range(rings):
        for segment in range(segments):
            a = ring * segments + segment
            b = ring * segments + (segment + 1) % segments
            c = (ring + 1) * segments + segment
            d = (ring + 1) * segments + (segment + 1) % segments
            faces.extend(((a, c, b), (b, c, d)))
    return vertices, faces


def avatar_mesh(measurements: dict[str, float]):
    height = max(1.35, min(2.05, float(measurements.get("height", 160)) / 100.0))
    chest = max(0.65, min(1.35, float(measurements.get("chest", 85)) / 100.0))
    waist = max(0.50, min(1.25, float(measurements.get("waist", 60)) / 100.0))
    shoulder = max(0.30, min(0.58, float(measurements.get("shoulder", 38)) / 100.0))
    upper_arm = max(0.18, min(0.48, float(measurements.get("upperArm", 25)) / 100.0))
    torso_y = height * 0.63
    torso_h = height * 0.22
    body = [
        ellipsoid((0, torso_y, 0), (shoulder * 0.52, torso_h, chest * 0.18)),
        ellipsoid((0, height * 0.43, 0), (waist * 0.24, height * 0.20, waist * 0.18)),
        ellipsoid((0, height * 0.91, 0), (height * 0.075, height * 0.095, height * 0.075)),
        ellipsoid((-shoulder * 0.63, height * 0.60, 0), (upper_arm * 0.16, height * 0.25, upper_arm * 0.16), 8, 12),
        ellipsoid((shoulder * 0.63, height * 0.60, 0), (upper_arm * 0.16, height * 0.25, upper_arm * 0.16), 8, 12),
        ellipsoid((-waist * 0.15, height * 0.20, 0), (waist * 0.12, height * 0.25, waist * 0.13), 8, 12),
        ellipsoid((waist * 0.15, height * 0.20, 0), (waist * 0.12, height * 0.25, waist * 0.13), 8, 12),
    ]
    return combine(body)


def garment_mesh(measurements: dict[str, float], recipe: dict, composition: dict | None = None, refined: bool = False):
    height = float(measurements.get("height", 160)) / 100.0
    chest = float(measurements.get("chest", 85)) / 100.0
    shoulder = float(measurements.get("shoulder", 38)) / 100.0
    piece_dimensions = [piece for piece in (composition or {}).get("pieces", []) if piece.get("role") in {"front_body", "front_left", "front_right"}]
    dxf_length_cm = max((float(piece.get("height_mm") or 0) / 10 for piece in piece_dimensions), default=0)
    length = float((recipe.get("intent_constraints") or {}).get("target_length_cm") or dxf_length_cm or 58) / 100.0
    sleeve_value = str((recipe.get("intent_constraints") or {}).get("sleeve") or (recipe.get("selections") or {}).get("sleeve") or "short")
    top = height * 0.84
    bottom = max(height * 0.43, top - min(max(length, 0.42), 0.76))
    half_width = max(shoulder * 0.57, chest * 0.30)
    depth = max(0.17, chest * 0.22)
    explicit_long = "long" in sleeve_value or recipe.get("family") == "shirt"
    neck_half = min(0.085, half_width * 0.38)
    body_half = half_width * 0.88
    if "sleeveless" in sleeve_value:
        sleeve_length = 0.0
    elif explicit_long:
        sleeve_length = min(0.58, height * 0.34)
    else:
        sleeve_length = min(0.24, height * 0.15)
    parts = [
        garment_shell(top, bottom, body_half, depth, refined),
        garment_yoke(top, body_half, depth, neck_half, min(0.065, depth * 0.48), refined),
    ]
    if sleeve_length:
        parts.extend((
            garment_sleeve(-1, body_half, top, sleeve_length, depth, refined),
            garment_sleeve(1, body_half, top, sleeve_length, depth, refined),
        ))
    return combine(parts)


def descriptor_panel_mesh(descriptor: dict, measurements: dict[str, float]):
    """Build an unsimulated inspection mesh from exact composed panel triangles.

    This is intentionally not a try-on result. It exists so the cloth runtime can
    inspect scale, winding and panel roles before sewing and collision solving.
    """
    if descriptor.get("version") not in {"patternmate.tryon.v1", "patternmate.tryon.v2"} or descriptor.get("unit") != "mm":
        raise ValueError("unsupported try-on descriptor")
    panels = descriptor.get("panels") or []
    if not panels:
        raise ValueError("try-on descriptor has no panels")
    height = float(measurements.get("height", 160)) / 100.0
    chest_depth = max(0.16, float(measurements.get("chest", 85)) / 100.0 * 0.20)
    all_vertices: list[tuple[float, float, float]] = []
    all_faces: list[tuple[int, int, int]] = []
    for panel_index, panel in enumerate(panels):
        points = panel.get("mesh_vertices_2d_mm") or panel.get("vertices_2d_mm") or []
        triangles = panel.get("mesh_triangles") or panel.get("triangles") or []
        if len(points) < 3 or not triangles:
            continue
        role = str(panel.get("role") or "unknown")
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        center_x = (min(xs) + max(xs)) / 2.0
        max_y = max(ys)
        side = -1.0 if role.startswith("back") else 1.0
        z = side * chest_depth
        x_shift = 0.0
        if role.startswith("sleeve") or role == "sleeve":
            x_shift = 0.42 if panel_index % 2 else -0.42
            z = 0.0
        elif role.startswith("neck") or role.startswith("collar"):
            z = chest_depth * 1.08
        vertices = [
            ((float(x) - center_x) / 1000.0 + x_shift, height * 0.83 - (max_y - float(y)) / 1000.0, z)
            for x, y in points
        ]
        offset = len(all_vertices)
        all_vertices.extend(vertices)
        for face in triangles:
            if len(face) != 3 or any(int(index) < 0 or int(index) >= len(vertices) for index in face):
                raise ValueError(f"invalid triangle in panel {panel.get('panel_id')}")
            indices = tuple(int(index) + offset for index in face)
            all_faces.append(indices if side > 0 else (indices[0], indices[2], indices[1]))
    if not all_faces:
        raise ValueError("try-on descriptor has no triangulated panels")
    return all_vertices, all_faces


def descriptor_garment_surface_mesh(descriptor: dict, measurements: dict[str, float]):
    """Build an open, single-layer garment surface from DXF panel dimensions.

    Raw panel triangles stay available for inspection, but bending them before
    sewing constraints exist creates overlaps and holes. This preview therefore
    preserves DXF-derived body/sleeve proportions in a stable open surface.
    """
    if descriptor.get("version") not in {"patternmate.tryon.v1", "patternmate.tryon.v2"} or descriptor.get("unit") != "mm":
        raise ValueError("unsupported try-on descriptor")
    panels = descriptor.get("panels") or []
    if not panels:
        raise ValueError("try-on descriptor has no panels")

    height = max(1.35, min(2.05, float(measurements.get("height", 160)) / 100.0))
    chest = max(.65, min(1.35, float(measurements.get("chest", 85)) / 100.0))
    shoulder = max(.30, min(.58, float(measurements.get("shoulder", 38)) / 100.0))
    upper_arm = max(.18, min(.48, float(measurements.get("upperArm", 27)) / 100.0))
    def panel_height(role_prefix: str) -> float:
        heights = []
        for panel in panels:
            if str(panel.get("role") or "").startswith(role_prefix):
                ys = [float(point[1]) for point in (panel.get("mesh_vertices_2d_mm") or panel.get("vertices_2d_mm") or [])]
                if ys:
                    heights.append((max(ys) - min(ys)) / 1000.0)
        return max(heights, default=0.0)

    body_length = min(max(max(panel_height("front"), panel_height("back")), .42), height * .46)
    sleeve_length = min(max(panel_height("sleeve"), .16), height * .37)
    torso_top = height * .83
    half_width = max(shoulder * .55, chest * .28)
    torso_depth = max(.18, chest * .235)

    def body_surface(front: bool):
        rows, columns = 14, 20
        vertices = []
        faces = []
        neck_drop = .105 if front else .045
        for row in range(rows + 1):
            v = row / rows
            width = half_width * (1.0 - .06 * math.sin(v * math.pi))
            for column in range(columns + 1):
                u = column / columns * 2.0 - 1.0
                neckline = neck_drop * max(0.0, 1.0 - abs(u) / .34) ** 1.7
                shoulder_drop = .045 * abs(u) ** 1.5
                top_y = torso_top - neckline - shoulder_drop
                y = top_y + (torso_top - body_length - top_y) * v
                curve = math.sqrt(max(0.0, 1.0 - u * u))
                ease = .018 + .018 * math.sin(v * math.pi)
                z = (torso_depth * curve + ease) * (1.0 if front else -1.0)
                vertices.append((u * width, y, z))
        stride = columns + 1
        for row in range(rows):
            for column in range(columns):
                a = row * stride + column
                b = a + 1
                c = a + stride
                d = c + 1
                faces.extend(((a, c, b), (b, c, d)) if front else ((a, b, c), (b, d, c)))
        return vertices, faces

    def sleeve_surface(side: int):
        rows, columns = 10, 16
        vertices = []
        faces = []
        arm_radius = max(.05, upper_arm / (2 * math.pi) + .014)
        for row in range(rows + 1):
            v = row / rows
            center_x = side * (half_width * .91 + sleeve_length * .34 * v)
            center_y = torso_top - .07 - sleeve_length * .86 * v
            radius = arm_radius * (1.0 - .22 * v)
            for column in range(columns + 1):
                angle = -.86 * math.pi + 1.72 * math.pi * column / columns
                vertices.append((
                    center_x + side * radius * math.sin(angle),
                    center_y,
                    radius * math.cos(angle) + .012,
                ))
        stride = columns + 1
        for row in range(rows):
            for column in range(columns):
                a = row * stride + column
                b = a + 1
                c = a + stride
                d = c + 1
                faces.extend(((a, c, b), (b, c, d)) if side > 0 else ((a, b, c), (b, d, c)))
        return vertices, faces

    return combine((body_surface(True), body_surface(False), sleeve_surface(-1), sleeve_surface(1)))


def make_glb(meshes: list[tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], tuple[float, float, float, float]]]) -> bytes:
    binary = bytearray()
    buffer_views = []
    accessors = []
    primitives = []
    materials = []
    for vertices, faces, color in meshes:
        normals = vertex_normals(vertices, faces)
        position_offset = len(binary)
        for vertex in vertices:
            binary.extend(struct.pack("<3f", *vertex))
        position_length = len(vertices) * 12
        binary.extend(b"\x00" * ((4 - len(binary) % 4) % 4))
        normal_offset = len(binary)
        for normal in normals:
            binary.extend(struct.pack("<3f", *normal))
        normal_length = len(normals) * 12
        binary.extend(b"\x00" * ((4 - len(binary) % 4) % 4))
        index_offset = len(binary)
        for face in faces:
            binary.extend(struct.pack("<3I", *face))
        index_length = len(faces) * 12
        position_view = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": position_offset, "byteLength": position_length, "target": 34962})
        normal_view = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": normal_offset, "byteLength": normal_length, "target": 34962})
        index_view = len(buffer_views)
        buffer_views.append({"buffer": 0, "byteOffset": index_offset, "byteLength": index_length, "target": 34963})
        xs, ys, zs = zip(*vertices)
        position_accessor = len(accessors)
        accessors.append({"bufferView": position_view, "componentType": 5126, "count": len(vertices), "type": "VEC3", "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]})
        normal_accessor = len(accessors)
        accessors.append({"bufferView": normal_view, "componentType": 5126, "count": len(normals), "type": "VEC3"})
        index_accessor = len(accessors)
        accessors.append({"bufferView": index_view, "componentType": 5125, "count": len(faces) * 3, "type": "SCALAR"})
        material = len(materials)
        materials.append({"pbrMetallicRoughness": {"baseColorFactor": list(color), "metallicFactor": 0, "roughnessFactor": 0.75}, "doubleSided": True})
        primitives.append({"attributes": {"POSITION": position_accessor, "NORMAL": normal_accessor}, "indices": index_accessor, "material": material})
    document = {
        "asset": {"version": "2.0", "generator": "PatternMate research try-on"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": primitives}],
        "materials": materials,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    json_chunk = _pad4(json.dumps(document, separators=(",", ":")).encode("utf-8"), b" ")
    bin_chunk = _pad4(bytes(binary))
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I4s", len(json_chunk), b"JSON") + json_chunk + struct.pack("<I4s", len(bin_chunk), b"BIN\x00") + bin_chunk
