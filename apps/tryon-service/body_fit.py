from __future__ import annotations

import math
from typing import Any


MEASUREMENT_KEYS = ("height", "chest", "waist", "shoulder", "neck", "sleeveLength", "upperArm")


def _ellipse_circumference(torch: Any, points: Any, axis_a: int, axis_b: int) -> Any:
    diameter_a = points[:, axis_a].amax() - points[:, axis_a].amin()
    diameter_b = points[:, axis_b].amax() - points[:, axis_b].amin()
    a = diameter_a / 2
    b = diameter_b / 2
    return math.pi * (3 * (a + b) - torch.sqrt(torch.clamp((3 * a + b) * (a + 3 * b), min=1e-10)))


def _fixed_slice_indices(torch: Any, vertices: Any) -> dict[str, Any]:
    bottom = vertices[:, 1].amin()
    height = vertices[:, 1].amax() - bottom
    y_ratio = (vertices[:, 1] - bottom) / torch.clamp(height, min=1e-6)
    x_ratio = vertices[:, 0].abs() / torch.clamp(height, min=1e-6)

    def torso_slice(ratio: float, half_band: float, max_x: float) -> Any:
        indices = torch.nonzero((y_ratio - ratio).abs().lt(half_band) & x_ratio.lt(max_x), as_tuple=False).flatten()
        if indices.numel() < 8:
            raise ValueError(f"SMPL measurement slice {ratio:.2f} has too few vertices")
        return indices

    return {
        "chest": torso_slice(.72, .014, .25),
        "waist": torso_slice(.64, .014, .21),
        "neck": torso_slice(.88, .012, .10),
    }


def _measure(torch: Any, vertices: Any, joints: Any, indices: dict[str, Any], target_height_cm: float) -> dict[str, Any]:
    raw_height = vertices[:, 1].amax() - vertices[:, 1].amin()
    scale = (target_height_cm / 100.0) / torch.clamp(raw_height, min=1e-6)
    scaled_vertices = vertices * scale
    scaled_joints = joints * scale

    # SMPL cross-sections and joint centres do not use the same landmarks as
    # garment tape measurements. These factors calibrate the neutral SMPL body
    # to PatternMate's measurement guide before shape optimization.
    chest = _ellipse_circumference(torch, scaled_vertices[indices["chest"]], 0, 2) * 100 * .96
    waist = _ellipse_circumference(torch, scaled_vertices[indices["waist"]], 0, 2) * 100 * .84
    neck = _ellipse_circumference(torch, scaled_vertices[indices["neck"]], 0, 2) * 100 * .78
    shoulder = torch.linalg.vector_norm(scaled_joints[16] - scaled_joints[17]) * 100 * 1.18
    sleeve = (
        torch.linalg.vector_norm(scaled_joints[16] - scaled_joints[18])
        + torch.linalg.vector_norm(scaled_joints[18] - scaled_joints[20])
    ) * 100 * 1.06

    upper_arm_center = (scaled_joints[16, 0] + scaled_joints[18, 0]) / 2
    arm_indices = torch.nonzero(
        (scaled_vertices[:, 0] - upper_arm_center).abs().lt(.012)
        & scaled_vertices[:, 0].gt(0),
        as_tuple=False,
    ).flatten()
    if arm_indices.numel() < 8:
        raise ValueError("SMPL upper-arm measurement slice has too few vertices")
    upper_arm = _ellipse_circumference(torch, scaled_vertices[arm_indices], 1, 2) * 100 * .95
    return {
        "height": torch.as_tensor(target_height_cm, dtype=vertices.dtype, device=vertices.device),
        "chest": chest,
        "waist": waist,
        "shoulder": shoulder,
        "neck": neck,
        "sleeveLength": sleeve,
        "upperArm": upper_arm,
    }


def fit_smpl_avatar(torch: Any, model: Any, measurements: dict[str, float], gender: str, device: str) -> tuple[Any, Any, dict[str, dict[str, float]], list[float]]:
    """Fit SMPL shape coefficients to the measurements used by PatternMate.

    This is an anthropometric approximation based on fixed SMPL cross-sections.
    It replaces the former four-coefficient heuristic and, importantly, reports
    measured residuals instead of claiming zero error.
    """

    target_height = float(measurements.get("height", 170 if gender == "male" else 160))
    betas = torch.zeros((1, 10), dtype=torch.float32, device=device, requires_grad=True)
    with torch.no_grad():
        betas[0, 0] = (float(measurements.get("chest", 92 if gender == "male" else 84)) - (92 if gender == "male" else 84)) / 18
        betas[0, 1] = (float(measurements.get("waist", 78 if gender == "male" else 66)) - (78 if gender == "male" else 66)) / 18
        betas[0, 2] = (float(measurements.get("shoulder", 44 if gender == "male" else 39)) - (44 if gender == "male" else 39)) / 8
        betas[0, 3] = (float(measurements.get("upperArm", 31 if gender == "male" else 27)) - (31 if gender == "male" else 27)) / 8
        betas.clamp_(-3, 3)

    with torch.no_grad():
        neutral = model(betas=torch.zeros_like(betas), return_verts=True)
        indices = _fixed_slice_indices(torch, neutral.vertices[0])

    optimizer = torch.optim.Adam([betas], lr=.045)
    scales_cm = {"chest": 4.0, "waist": 4.0, "shoulder": 2.5, "neck": 3.0, "sleeveLength": 3.0, "upperArm": 3.0}
    active = [key for key in scales_cm if key in measurements and float(measurements[key]) > 0]
    for _ in range(70 if device.startswith("cuda") else 35):
        optimizer.zero_grad(set_to_none=True)
        output = model(betas=betas, return_verts=True)
        predicted = _measure(torch, output.vertices[0], output.joints[0], indices, target_height)
        residuals = [((predicted[key] - float(measurements[key])) / scales_cm[key]).square() for key in active]
        loss = (torch.stack(residuals).mean() if residuals else betas.square().mean() * 0) + .012 * betas.square().mean()
        loss.backward()
        optimizer.step()
        with torch.no_grad():
            betas.clamp_(-3, 3)

    with torch.no_grad():
        fitted_neutral = model(betas=betas, return_verts=True)
        predicted = _measure(torch, fitted_neutral.vertices[0], fitted_neutral.joints[0], indices, target_height)
        metrics: dict[str, dict[str, float]] = {}
        for key in MEASUREMENT_KEYS:
            if key not in measurements:
                continue
            requested = float(measurements[key])
            fitted = float(predicted[key].detach().cpu())
            metrics[key] = {"requested": round(requested, 2), "fitted": round(fitted, 2), "error": round(fitted - requested, 2)}

        body_pose = torch.zeros((1, 69), dtype=torch.float32, device=device)
        body_pose[0, 15 * 3 + 2] = -1.18
        body_pose[0, 16 * 3 + 2] = 1.18
        posed = model(betas=betas, body_pose=body_pose, return_verts=True)
        vertices = posed.vertices[0]
        current_height = vertices[:, 1].amax() - vertices[:, 1].amin()
        vertices = vertices * ((target_height / 100.0) / torch.clamp(current_height, min=1e-6))
        vertices[:, 1] -= vertices[:, 1].amin()
        beta_values = [round(float(value), 5) for value in betas[0].detach().cpu()]
    return vertices, model.faces, metrics, beta_values
