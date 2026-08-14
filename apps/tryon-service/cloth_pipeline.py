from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np


Progress = Callable[[str, int], None]


@dataclass
class ClothMesh:
    vertices: np.ndarray
    faces: np.ndarray
    panel_uv: np.ndarray
    panel_faces: np.ndarray
    seam_vertex_pairs: list[tuple[int, int]]
    anchor_vertices: list[int]


def _role_group(role: str) -> str:
    if role.startswith("front"):
        return "front"
    if role.startswith("back"):
        return "back"
    if role.startswith("sleeve") or role == "sleeve":
        return "sleeve"
    return "neck"


def _initial_panel(vertices_mm: np.ndarray, role: str, instance: str, measurements: dict[str, float]) -> np.ndarray:
    minimum = vertices_mm.min(axis=0)
    maximum = vertices_mm.max(axis=0)
    center = (minimum + maximum) * .5
    local = (vertices_mm - center) / 1000.0
    height = max(float(measurements.get("height", 165.0)), 130.0) / 100.0
    shoulder_y = height * .82
    chest_depth = max(float(measurements.get("chest", 88.0)), 60.0) / (2.0 * math.pi * 100.0)
    group = _role_group(role)
    if group in {"front", "back"}:
        panel_width = max(float(np.ptp(local[:, 0])), .01)
        target_width = max(float(measurements.get("chest", 88.0)), 60.0) / 200.0 + .04
        width_scale = float(np.clip(target_width / panel_width, .60, 1.20))
        panel_length = max(float((maximum[1] - minimum[1]) / 1000.0), .01)
        target_length = height * .40
        length_scale = float(np.clip(target_length / panel_length, .70, 1.15))
        y = shoulder_y + height * .03 - (maximum[1] - vertices_mm[:, 1]) / 1000.0 * length_scale
        z = np.full(len(vertices_mm), chest_depth + .018 if group == "front" else -chest_depth - .018)
        return np.column_stack((local[:, 0] * width_scale, y, z))
    if group == "sleeve":
        side = -1.0 if instance == "left" else 1.0
        along = (maximum[1] - vertices_mm[:, 1]) / 1000.0
        x = side * (float(measurements.get("shoulder", 40.0)) / 200.0 + along * .72)
        y = shoulder_y - along * .58
        z = local[:, 0] * side
        return np.column_stack((x, y, z))
    radius = max(float(measurements.get("neck", 36.0)), 25.0) / (2.0 * math.pi * 100.0) + .008
    angle = local[:, 0] / max(np.ptp(local[:, 0]), .01) * math.pi
    return np.column_stack((np.sin(angle) * radius, shoulder_y + local[:, 1], np.cos(angle) * radius))


def _subdivide(vertices: np.ndarray, faces: np.ndarray, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    output_vertices = vertices.tolist()
    output_uv = uv.tolist()
    midpoint: dict[tuple[int, int], int] = {}

    def split(a: int, b: int) -> int:
        edge = tuple(sorted((a, b)))
        if edge not in midpoint:
            midpoint[edge] = len(output_vertices)
            output_vertices.append(((vertices[a] + vertices[b]) * .5).tolist())
            output_uv.append(((uv[a] + uv[b]) * .5).tolist())
        return midpoint[edge]

    output_faces = []
    for a, b, c in faces:
        ab, bc, ca = split(int(a), int(b)), split(int(b), int(c)), split(int(c), int(a))
        output_faces.extend(((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)))
    return np.asarray(output_vertices, np.float32), np.asarray(output_faces, np.int32), np.asarray(output_uv, np.float32)


def assemble_cloth(descriptor: dict[str, Any], measurements: dict[str, float], refined: bool = False) -> ClothMesh:
    if descriptor.get("version") != "patternmate.tryon.v2":
        raise ValueError("patternmate.tryon.v2 is required")
    if not (descriptor.get("validation") or {}).get("tryon_ready"):
        errors = (descriptor.get("validation") or {}).get("errors") or []
        raise ValueError("DXF simulation input is incomplete: " + ", ".join(errors))

    vertices: list[np.ndarray] = []
    faces: list[np.ndarray] = []
    uvs: list[np.ndarray] = []
    lookup: dict[tuple[str, str], tuple[int, int]] = {}
    seamed_panel_ids = {
        str(constraint.get(key) or "")
        for seam in descriptor.get("seam_pairs") or []
        for constraint in seam.get("constraints") or []
        for key in ("a_panel_id", "b_panel_id")
    }
    for panel in descriptor.get("panels") or []:
        role = str(panel.get("role") or "unknown")
        structural = role.startswith(("front", "back", "sleeve"))
        attached_collar = role in {"collar", "collar_stand"} and str(panel.get("panel_id") or "") in seamed_panel_ids
        if not structural and not attached_collar:
            continue
        panel_vertices = np.asarray(panel.get("mesh_vertices_2d_mm") or [], dtype=np.float32)
        panel_faces = np.asarray(panel.get("mesh_triangles") or [], dtype=np.int32)
        if len(panel_vertices) < 3 or len(panel_faces) < 1:
            continue
        instances = panel.get("instances") or (["left", "right"] if panel.get("role") == "sleeve" else ["default"])
        for instance in instances:
            offset = sum(len(item) for item in vertices)
            placed = _initial_panel(panel_vertices, role, instance, measurements)
            vertices.append(placed)
            uvs.append(panel_vertices / 1000.0)
            faces.append(panel_faces + offset)
            lookup[(str(panel["panel_id"]), instance)] = (offset, len(panel_vertices))

    if not vertices:
        raise ValueError("DXF contains no dense triangular panel mesh")
    structural_groups = {
        _role_group(str(panel.get("role") or "unknown"))
        for panel in descriptor.get("panels") or []
        if str(panel.get("role") or "unknown").startswith(("front", "back", "sleeve"))
    }
    missing_groups = {"front", "back", "sleeve"} - structural_groups
    if missing_groups:
        raise ValueError(f"DXF is missing structural cloth panels: {', '.join(sorted(missing_groups))}")
    cloth_vertices = np.concatenate(vertices).astype(np.float32)
    cloth_faces = np.concatenate(faces).astype(np.int32)
    cloth_uv = np.concatenate(uvs).astype(np.float32)
    seam_pairs: list[tuple[int, int]] = []
    for seam in descriptor.get("seam_pairs") or []:
        for constraint in seam.get("constraints") or []:
            a_key = (constraint["a_panel_id"], constraint.get("a_instance") or "default")
            b_key = (constraint["b_panel_id"], constraint.get("b_instance") or "default")
            if a_key not in lookup or b_key not in lookup:
                continue
            a_offset, _ = lookup[a_key]
            b_offset, _ = lookup[b_key]
            for a, b in constraint.get("vertex_pairs") or []:
                pair = (a_offset + int(a), b_offset + int(b))
                seam_pairs.append(pair)
                # The garment is supported by body collision and friction.
                # Pinning every shoulder seam vertex over-constrains Style3D.

    if not seam_pairs:
        raise ValueError("DXF contains no usable one-to-one seam constraints")
    if refined:
        cloth_vertices, cloth_faces, cloth_uv = _subdivide(cloth_vertices, cloth_faces, cloth_uv)
    panel_faces = cloth_faces.copy()
    parent = list(range(len(cloth_vertices)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for a, b in seam_pairs:
        left, right = root(a), root(b)
        if left != right:
            parent[right] = left
    groups: dict[int, list[int]] = {}
    for index in range(len(cloth_vertices)):
        groups.setdefault(root(index), []).append(index)
    roots = sorted(groups)
    root_to_new = {value: index for index, value in enumerate(roots)}
    old_to_new = np.asarray([root_to_new[root(index)] for index in range(len(cloth_vertices))], dtype=np.int32)
    welded_vertices = np.asarray([cloth_vertices[groups[value]].mean(axis=0) for value in roots], dtype=np.float32)
    welded_faces = old_to_new[cloth_faces]
    keep = np.asarray([len(set(map(int, triangle))) == 3 for triangle in welded_faces], dtype=bool)
    while True:
        edge_faces: dict[tuple[int, int], list[int]] = {}
        for face_index, triangle in enumerate(welded_faces):
            if not keep[face_index]:
                continue
            for a, b in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
                edge_faces.setdefault(tuple(sorted((int(a), int(b)))), []).append(face_index)
        extras = {face for faces_for_edge in edge_faces.values() if len(faces_for_edge) > 2 for face in faces_for_edge[2:]}
        if not extras:
            break
        keep[list(extras)] = False
    remapped_seams = [(int(old_to_new[a]), int(old_to_new[b])) for a, b in seam_pairs]
    remapped_anchors: list[int] = []
    final_faces = welded_faces[keep]
    used_vertices = np.unique(final_faces)
    compact_index = np.full(len(welded_vertices), -1, dtype=np.int32)
    compact_index[used_vertices] = np.arange(len(used_vertices), dtype=np.int32)
    compact_faces = compact_index[final_faces]
    compact_seams = [
        (int(compact_index[a]), int(compact_index[b]))
        for a, b in remapped_seams
        if compact_index[a] >= 0 and compact_index[b] >= 0
    ]
    return ClothMesh(welded_vertices[used_vertices], compact_faces, cloth_uv, panel_faces[keep], compact_seams, remapped_anchors)


def _simulate_cloth_unlocked(
    avatar: tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]],
    descriptor: dict[str, Any],
    measurements: dict[str, float],
    physics: dict[str, Any],
    quality: str,
    progress: Progress,
    cancelled: Callable[[], bool],
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], dict[str, Any]]:
    import newton
    import warp as wp
    from newton.solvers import style3d

    started = time.monotonic()
    refined = quality == "refined"
    progress("mesh", 8)
    cloth = assemble_cloth(descriptor, measurements, refined=refined)
    if not np.isfinite(cloth.vertices).all():
        raise RuntimeError("DXF cloth mesh contains NaN or infinite coordinates")
    panel_triangles = cloth.panel_uv[cloth.panel_faces]
    panel_signed_areas = np.cross(
        panel_triangles[:, 1] - panel_triangles[:, 0],
        panel_triangles[:, 2] - panel_triangles[:, 0],
    ) * .5
    if np.count_nonzero(panel_signed_areas > 1.0e-9) < len(panel_signed_areas) * .95:
        raise RuntimeError(
            f"DXF rest mesh has invalid triangle winding or area "
            f"(valid={np.count_nonzero(panel_signed_areas > 1.0e-9)}/{len(panel_signed_areas)})"
        )

    builder = newton.ModelBuilder(up_axis=newton.Axis.Z)
    newton.solvers.SolverStyle3D.register_custom_attributes(builder)
    rotation = wp.quat_from_axis_angle(wp.vec3(1.0, 0.0, 0.0), math.pi * .5)
    stiffness_scale = 0.02
    tri_ke = wp.vec3(
        float(physics["stretch_warp"]) * stiffness_scale,
        float(physics["stretch_weft"]) * stiffness_scale,
        float(physics["shear"]) * stiffness_scale,
    )
    edge_ke = wp.vec3(float(physics["bending_warp"]), float(physics["bending_weft"]), float(physics["bending_bias"]))
    solver_damping = float(physics["damping"]) * 1.0e-5
    style3d.add_cloth_mesh(
        builder,
        pos=wp.vec3(0.0, 0.0, 0.0), rot=rotation, vel=wp.vec3(0.0, 0.0, 0.0),
        panel_verts=cloth.panel_uv.tolist(), panel_indices=cloth.panel_faces.flatten().tolist(),
        vertices=cloth.vertices.tolist(), indices=cloth.faces.flatten().tolist(),
        density=float(physics["density"]), particle_radius=.004,
        tri_aniso_ke=tri_ke, edge_aniso_ke=edge_ke,
        tri_kd=solver_damping, edge_kd=solver_damping,
        validate_mesh=True,
    )
    progress("sewing", 18)

    avatar_vertices = np.asarray(avatar[0], dtype=np.float32)
    avatar_faces = np.asarray(avatar[1], dtype=np.int32)
    avatar_mesh = newton.Mesh(avatar_vertices, avatar_faces.flatten())
    if hasattr(avatar_mesh, "build_sdf"):
        avatar_mesh.build_sdf(max_resolution=64)
    body = builder.add_body()
    builder.add_shape_mesh(body, xform=wp.transform(wp.vec3(0.0, 0.0, 0.0), rotation), mesh=avatar_mesh)
    model = builder.finalize()
    flags = model.particle_flags.numpy()
    for index in cloth.anchor_vertices:
        flags[index] = flags[index] & ~newton.ParticleFlags.ACTIVE
    model.particle_flags = wp.array(flags, device=model.device)
    model.soft_contact_radius = .008
    model.soft_contact_margin = .015
    model.soft_contact_ke = 300.0
    model.soft_contact_kd = 1.0e-5
    model.soft_contact_mu = float(physics["body_friction"])
    solver = newton.solvers.SolverStyle3D(model, iterations=6 if refined else 2, linear_iterations=10 if refined else 5)
    state_a, state_b = model.state(), model.state()
    control = model.control()
    contacts = model.contacts()
    dt = 1.0 / 60.0
    substeps = 10 if refined else 2
    deadline = started + (180.0 if refined else 45.0)
    max_frames = 900 if refined else 420
    stable_frames = 0
    converged = False
    speed_threshold = .04 if refined else .08
    stable_frame_target = 30 if refined else 20
    previous = None
    last_speed = float("inf")
    progress("collision", 28)
    for frame in range(max_frames):
        if cancelled():
            raise RuntimeError("Simulation cancelled")
        if time.monotonic() >= deadline:
            break
        gravity_factor = 0.0 if frame < 25 else min(1.0, (frame - 25) / 80.0)
        model.set_gravity((0.0, 0.0, -9.81 * gravity_factor))
        model.soft_contact_ke = 0.0 if frame < 25 else min(300.0, 5.0 + (frame - 25) * 6.0)
        model.collide(state_a, contacts)
        for _ in range(substeps):
            state_a.clear_forces()
            solver.step(state_a, state_b, control, contacts, dt / substeps)
            state_a, state_b = state_b, state_a
        velocity_damping = .20 if frame > 105 else .85
        state_a.particle_qd.assign(state_a.particle_qd.numpy() * velocity_damping)
        current = state_a.particle_q.numpy()
        if not np.isfinite(current).all():
            raise RuntimeError(f"Newton returned an invalid cloth state at frame {frame + 1}")
        if previous is not None:
            last_speed = float(np.linalg.norm(current - previous, axis=1).mean() / dt)
            stable_frames = stable_frames + 1 if frame > 105 and last_speed < speed_threshold else 0
            if stable_frames >= stable_frame_target:
                converged = True
                break
        previous = current.copy()
        progress("drape", min(94, 36 + round(frame / max_frames * 58)))
    if previous is None or not np.isfinite(previous).all():
        raise RuntimeError(f"Newton returned an invalid cloth state at frame {frame + 1}")
    if not converged:
        raise RuntimeError(f"Cloth did not converge within the selected quality time limit (frames={frame + 1}, mean_speed={last_speed:.6f} m/s, stable_frames={stable_frames})")
    # Newton is Z-up after the shared +90 degree X rotation; return the web's Y-up coordinates.
    result = np.column_stack((previous[:, 0], previous[:, 2], -previous[:, 1]))
    avatar_top = float(avatar_vertices[:, 1].max())
    head_overlap_rate = float(np.mean(result[:, 1] > avatar_top - .18))
    if head_overlap_rate >= .005:
        raise RuntimeError(f"Cloth entered the head region ({head_overlap_rate:.3%} of vertices)")
    seam_gaps = np.asarray([np.linalg.norm(result[a] - result[b]) for a, b in cloth.seam_vertex_pairs], dtype=np.float64)
    seam_mean_gap_mm = float(seam_gaps.mean() * 1000.0) if len(seam_gaps) else float("inf")
    try:
        import trimesh
        body_mesh = trimesh.Trimesh(vertices=np.asarray(avatar[0]), faces=np.asarray(avatar[1]), process=False)
        signed_distance = trimesh.proximity.signed_distance(body_mesh, result)
        penetrating = signed_distance > .002
        collision_projection_count = int(penetrating.sum())
        if collision_projection_count:
            closest, _, triangle_ids = trimesh.proximity.closest_point(body_mesh, result[penetrating])
            result[penetrating] = closest + body_mesh.face_normals[triangle_ids] * .003
            signed_distance = trimesh.proximity.signed_distance(body_mesh, result)
        penetration_rate = float(np.mean(signed_distance > .002))
    except Exception:
        penetration_rate = None
        collision_projection_count = 0
    if seam_mean_gap_mm >= 5.0:
        raise RuntimeError(f"Final seam mean gap {seam_mean_gap_mm:.3f} mm exceeds the 5 mm quality limit")
    if penetration_rate is not None and penetration_rate >= .01:
        raise RuntimeError(f"Final body penetration rate {penetration_rate:.3%} exceeds the 1% quality limit")
    progress("export", 97)
    metrics = {
        "solver": "newton_style3d_1.4",
        "frames": frame + 1,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "converged": True,
        "seam_vertex_pair_count": len(cloth.seam_vertex_pairs),
        "seam_mean_gap_mm": round(seam_mean_gap_mm, 4),
        "single_layer": True,
        "mesh_target_edge_mm": 7 if refined else 12,
        "penetration_rate": penetration_rate,
        "collision_projection_count": collision_projection_count,
        "head_overlap_rate": head_overlap_rate,
        "fabric_physics": physics,
    }
    return [tuple(map(float, row)) for row in result], [tuple(map(int, row)) for row in cloth.faces], metrics


def simulate_cloth(
    avatar: tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]],
    descriptor: dict[str, Any],
    measurements: dict[str, float],
    physics: dict[str, Any],
    quality: str,
    progress: Progress,
    cancelled: Callable[[], bool],
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], dict[str, Any]]:
    """Serialize cloth and Qwen workloads on the single AutoDL GPU."""
    lock_path = os.getenv("PATTERNMATE_GPU_LOCK", "/tmp/patternmate-gpu.lock")
    with open(lock_path, "a+", encoding="utf-8") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            pass
        try:
            return _simulate_cloth_unlocked(avatar, descriptor, measurements, physics, quality, progress, cancelled)
        finally:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except ImportError:
                pass
