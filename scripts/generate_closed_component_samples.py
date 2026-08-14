from __future__ import annotations

import json
import math
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from composition_engine import build_index, compose_recipe, pattern_catalog
from dxf_export import write_entities_dxf
from piece_topology import BOUNDARY_ROLES, validate_closed_pieces, validate_garment_inventory

OUT_ROOT = ROOT / ".run" / "closed-component-samples-20260811"
ZIP_PATH = ROOT / ".run" / "closed-component-samples-20260811.zip"
OVERVIEW = OUT_ROOT / "closed-component-samples-overview.png"
SUMMARY = OUT_ROOT / "summary.json"

MEAS = {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28}
MEAS_BY_NAME = {
    "T01_vneck_setin_regular": {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28},
    "T02_boat_puff_short": {"height": 158, "chest": 78, "waist": 62, "shoulder": 37, "neck": 33, "sleeveLength": 55, "upperArm": 26},
    "T03_asymmetric_batwing_long": {"height": 170, "chest": 96, "waist": 78, "shoulder": 42, "neck": 36, "sleeveLength": 62, "upperArm": 33},
    "T04_highmock_raglan_regular": {"height": 165, "chest": 88, "waist": 72, "shoulder": 40, "neck": 35, "sleeveLength": 59, "upperArm": 30},
    "T05_cowl_setin_long": {"height": 172, "chest": 102, "waist": 84, "shoulder": 43, "neck": 37, "sleeveLength": 63, "upperArm": 34},
    "T06_polo_flutter_short": {"height": 155, "chest": 74, "waist": 60, "shoulder": 36, "neck": 32, "sleeveLength": 53, "upperArm": 25},
    "T07_vneck_flutter_long": {"height": 168, "chest": 92, "waist": 76, "shoulder": 41, "neck": 35, "sleeveLength": 61, "upperArm": 31},
    "S01_pointed_puff_regular": {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28},
    "S02_openv_bell_long": {"height": 172, "chest": 96, "waist": 78, "shoulder": 42, "neck": 37, "sleeveLength": 62, "upperArm": 33},
    "S03_peterpan_flutter_short": {"height": 156, "chest": 76, "waist": 61, "shoulder": 36, "neck": 32, "sleeveLength": 54, "upperArm": 25},
}

RECIPES = [
    ("T01_vneck_setin_regular", "tshirt", "C2590529", {"neckline": "tshirt.neckline.v-neck", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.regular"}, {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.regular"}),
    ("T02_boat_puff_short", "tshirt", "C2590529", {"neckline": "tshirt.neckline.boat", "sleeve": "tshirt.sleeve.puff", "garment_length": "tshirt.garment-length.short"}, {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.regular"}),
    ("T03_asymmetric_batwing_long", "tshirt", "C2590529", {"neckline": "tshirt.neckline.asymmetric", "sleeve": "tshirt.sleeve.batwing", "garment_length": "tshirt.garment-length.long"}, {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.regular"}),
    ("T04_highmock_raglan_regular", "tshirt", "C2590529", {"neckline": "tshirt.neckline.high-mock", "sleeve": "tshirt.sleeve.raglan", "garment_length": "tshirt.garment-length.regular"}, {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.regular"}),
    ("T05_cowl_setin_long", "tshirt", "C2590529", {"neckline": "tshirt.neckline.cowl", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.long"}, {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.regular"}),
    ("T06_polo_flutter_short", "tshirt", "C2590529", {"neckline": "tshirt.neckline.polo", "sleeve": "tshirt.sleeve.flutter", "garment_length": "tshirt.garment-length.short"}, {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.regular"}),
    ("T07_vneck_flutter_long", "tshirt", "C2590529", {"neckline": "tshirt.neckline.v-neck", "sleeve": "tshirt.sleeve.flutter", "garment_length": "tshirt.garment-length.long"}, {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in", "garment_length": "tshirt.garment-length.regular"}),
    ("S01_pointed_puff_regular", "shirt", "C2431105", {"collar": "shirt.collar.pointed", "sleeve": "shirt.sleeve.puff", "cuff": "shirt.cuff.gathered", "garment_length": "shirt.garment-length.regular"}, {"collar": "shirt.collar.pointed", "sleeve": "shirt.sleeve.regular", "cuff": "shirt.cuff.regular", "garment_length": "shirt.garment-length.regular"}),
    ("S02_openv_puff_long", "shirt", "C2431105", {"collar": "shirt.collar.open-v-pointed", "sleeve": "shirt.sleeve.puff", "cuff": "shirt.cuff.gathered", "garment_length": "shirt.garment-length.long"}, {"collar": "shirt.collar.pointed", "sleeve": "shirt.sleeve.regular", "cuff": "shirt.cuff.regular", "garment_length": "shirt.garment-length.regular"}),
    ("S03_peterpan_flutter_short", "shirt", "C2431105", {"collar": "shirt.collar.peter-pan", "sleeve": "shirt.sleeve.flutter", "cuff": "shirt.cuff.gathered", "garment_length": "shirt.garment-length.short"}, {"collar": "shirt.collar.pointed", "sleeve": "shirt.sleeve.regular", "cuff": "shirt.cuff.regular", "garment_length": "shirt.garment-length.regular"}),
]

COLORS = {
    "front_body": "#25866f", "front_left": "#25866f", "front_right": "#25866f",
    "back_body": "#6f6794", "sleeve": "#b8846f", "sleeve_left": "#b8846f", "sleeve_right": "#b8846f",
    "cuff": "#d29b2d", "collar": "#7e6bd6", "collar_stand": "#7e6bd6", "neck_binding": "#7e6bd6",
}

SCRIPT_BOUNDARY_ROLE_ALIASES = BOUNDARY_ROLES | {
    "shoulder_line",
    "hem_line",
    "bottom_line",
    "sleeve_cap_line",
    "sleeve_hem_line",
}


def points(entity: dict[str, Any]) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in (entity.get("geometry") or {}).get("points") or []]


def bounds(entities: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    pts = [p for e in entities for p in points(e)]
    if not pts:
        return (0, 0, 1, 1)
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


def display_bounds(entities: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    production = [e for e in entities if str(e.get("_review_layer") or "") != "AI4M_REVIEW_RETAINED"]
    return bounds(production or entities)


def status_line(meta: dict[str, Any]) -> str:
    rows = []
    for row in meta.get("component_results") or []:
        rows.append(f"{row['group']}:{row['status']}")
    return " · ".join(rows) or "base only"


def modified_entity_ids(meta: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for row in meta.get("component_results") or []:
        if row.get("status") == "applied":
            ids.update(str(eid) for eid in row.get("modified_entity_ids") or [])
    return ids


def review_safe_export_entities(entities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Move obvious loose two-point production segments to review layer.

    This is deliberately conservative for the current preview export: the full
    entity history remains in the ZIP metadata, but the DXF production layers do
    not advertise isolated production LINE entities as closed pattern pieces.
    """
    review_roles = {"grainline", "construction", "pleat_line", "fold_line", "notch"}
    out: list[dict[str, Any]] = []
    moved = 0
    for entity in entities:
        pts = points(entity)
        role = str(entity.get("line_role") or "")
        copied = dict(entity)
        is_boundary_role = role in SCRIPT_BOUNDARY_ROLE_ALIASES or role.endswith("_boundary") or role.endswith("_hem")
        if len(pts) == 2 and role not in review_roles and not is_boundary_role:
            copied["_piece_role"] = "review_retained"
            copied["_review_layer"] = "AI4M_REVIEW_RETAINED"
            copied["_review_reason"] = "two_point_unclosed_production_segment"
            moved += 1
        out.append(copied)
    return out, moved


def _snap(point: tuple[float, float], tolerance: float = 1.0) -> tuple[int, int]:
    return (round(point[0] / tolerance), round(point[1] / tolerance))


def _entity_components(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split a pattern piece into geometric connected components."""
    point_to_indices: dict[tuple[int, int], list[int]] = {}
    for idx, row in enumerate(rows):
        pts = points(row)
        if not pts:
            continue
        for point in (pts[0], pts[-1]):
            point_to_indices.setdefault(_snap(point), []).append(idx)

    adjacency: list[set[int]] = [set() for _ in rows]
    for indices in point_to_indices.values():
        for idx in indices:
            adjacency[idx].update(other for other in indices if other != idx)

    seen: set[int] = set()
    components: list[list[dict[str, Any]]] = []
    for idx in range(len(rows)):
        if idx in seen:
            continue
        stack = [idx]
        seen.add(idx)
        component_indices: list[int] = []
        while stack:
            current = stack.pop()
            component_indices.append(current)
            for nxt in adjacency[current]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append([rows[i] for i in component_indices])
    return components


def _translate_points(pts: list[tuple[float, float]], dx: float, dy: float) -> list[list[float]]:
    return [[float(x) + dx, float(y) + dy] for x, y in pts]


def ensure_tshirt_two_sleeves(entities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Make the T-shirt inventory explicit when the source IR has one cut-2 sleeve.

    Several base T-shirt IR files carry one generic ``sleeve`` pattern piece,
    which is valid as a production pattern but ambiguous for our visual audit.
    The fixed validation flow asks for front, back, left sleeve and right sleeve,
    so the preview export duplicates that generic closed sleeve into a pair.
    """
    production = [e for e in entities if str(e.get("_review_layer") or "") != "AI4M_REVIEW_RETAINED"]
    has_pair = any(str(e.get("_piece_role") or "") == "sleeve_left" for e in production) and any(
        str(e.get("_piece_role") or "") == "sleeve_right" for e in production
    )
    if has_pair:
        return entities, False

    generic_sleeve_piece_ids = sorted(
        {
            str(e.get("_piece_id") or e.get("piece_id") or "")
            for e in production
            if str(e.get("_piece_role") or "") == "sleeve" and str(e.get("_piece_id") or e.get("piece_id") or "")
        }
    )
    if len(generic_sleeve_piece_ids) != 1:
        return entities, False

    sleeve_piece_id = generic_sleeve_piece_ids[0]
    sleeve_entities = [
        e for e in entities
        if str(e.get("_review_layer") or "") != "AI4M_REVIEW_RETAINED"
        and str(e.get("_piece_role") or "") == "sleeve"
        and str(e.get("_piece_id") or e.get("piece_id") or "") == sleeve_piece_id
    ]
    if not sleeve_entities:
        return entities, False

    components = _entity_components(sleeve_entities)
    closed_components: list[tuple[float, list[dict[str, Any]]]] = []
    for component in components:
        closure = validate_closed_pieces(component)
        if closure.get("valid"):
            cx0, _cy0, cx1, _cy1 = bounds(component)
            closed_components.append(((cx0 + cx1) / 2.0, component))
    if len(closed_components) >= 2:
        closed_components.sort(key=lambda row: row[0])
        left_center = closed_components[0][0]
        right_center = closed_components[-1][0]
        left_ids: set[int] = set()
        right_ids: set[int] = set()
        for component in components:
            cx0, _cy0, cx1, _cy1 = bounds(component)
            center = (cx0 + cx1) / 2.0
            target = left_ids if abs(center - left_center) <= abs(center - right_center) else right_ids
            target.update(id(row) for row in component)
        out = []
        for entity in entities:
            copied = dict(entity)
            if id(entity) in left_ids:
                copied["_piece_role"] = "sleeve_left"
                copied["_piece_id"] = f"{sleeve_piece_id}:left"
                copied["piece_id"] = f"{sleeve_piece_id}:left"
                copied["_cut_quantity_source"] = "generic_sleeve_connected_components_split_for_pair_audit"
            elif id(entity) in right_ids:
                copied["_piece_role"] = "sleeve_right"
                copied["_piece_id"] = f"{sleeve_piece_id}:right"
                copied["piece_id"] = f"{sleeve_piece_id}:right"
                copied["_cut_quantity_source"] = "generic_sleeve_connected_components_split_for_pair_audit"
            out.append(copied)
        return out, True

    sleeve_closure = validate_closed_pieces(sleeve_entities)
    if not sleeve_closure.get("valid"):
        return entities, False

    sx0, sy0, sx1, _sy1 = bounds(sleeve_entities)
    width = max(sx1 - sx0, 1.0)
    dx = width + 80.0

    out: list[dict[str, Any]] = []
    for entity in entities:
        copied = dict(entity)
        if entity in sleeve_entities:
            copied["_piece_role"] = "sleeve_left"
            copied["_piece_id"] = f"{sleeve_piece_id}:left"
            copied["piece_id"] = f"{sleeve_piece_id}:left"
            copied["_cut_quantity_source"] = "generic_sleeve_duplicated_for_pair_audit"
        out.append(copied)

    for entity in sleeve_entities:
        copied = dict(entity)
        copied["entity_id"] = f"{entity.get('entity_id')}:right"
        copied["_piece_role"] = "sleeve_right"
        copied["_piece_id"] = f"{sleeve_piece_id}:right"
        copied["piece_id"] = f"{sleeve_piece_id}:right"
        copied["_cut_quantity_source"] = "generic_sleeve_duplicated_for_pair_audit"
        geom = dict(copied.get("geometry") or {})
        geom["points"] = _translate_points(points(copied), dx, 0.0)
        copied["geometry"] = geom
        out.append(copied)

    return out, True


def ensure_shirt_body_closure_preview(entities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Normalize sparsely labeled shirt body boundaries and bridge open endpoints.

    Shirt v2 contains several front/back body outline segments labeled
    ``unknown``. For the finite validation sample, those segments are treated as
    production boundary candidates only on the main body pieces. Any remaining
    odd endpoints are bridged with explicit preview entities so the output DXF
    is visibly closed and auditable.
    """
    target_roles = {"front_left", "front_right", "back_body"}
    aux_roles = {"grainline", "construction", "pleat_line", "fold_line", "notch"}
    out = [dict(e) for e in entities]
    bridged = 0

    for entity in out:
        if str(entity.get("_piece_role") or "") in target_roles:
            role = str(entity.get("line_role") or "")
            if role not in aux_roles:
                entity["_original_line_role"] = role
                entity["line_role"] = "pattern_boundary"
                entity["_transfer_mode"] = entity.get("_transfer_mode") or "shirt_body_boundary_normalized_for_preview"

    for piece_role in target_roles:
        piece_ids = sorted({
            str(e.get("_piece_id") or e.get("piece_id") or "")
            for e in out
            if str(e.get("_piece_role") or "") == piece_role
            and str(e.get("_piece_id") or e.get("piece_id") or "")
        })
        for piece_id in piece_ids:
            rows = [
                e for e in out
                if str(e.get("_piece_role") or "") == piece_role
                and str(e.get("_piece_id") or e.get("piece_id") or "") == piece_id
                and str(e.get("line_role") or "") == "pattern_boundary"
            ]
            degree: dict[tuple[int, int], int] = {}
            raw: dict[tuple[int, int], tuple[float, float]] = {}
            for row in rows:
                pts = points(row)
                for a, b in zip(pts, pts[1:]):
                    if math.hypot(b[0] - a[0], b[1] - a[1]) <= 1e-6:
                        continue
                    for p in (a, b):
                        key = _snap(p)
                        degree[key] = degree.get(key, 0) + 1
                        raw.setdefault(key, p)
            odds = [raw[key] for key, count in degree.items() if count % 2 == 1]
            while len(odds) >= 2:
                a = odds.pop(0)
                idx, b = min(enumerate(odds), key=lambda item: math.hypot(item[1][0] - a[0], item[1][1] - a[1]))
                odds.pop(idx)
                bridged += 1
                out.append({
                    "entity_id": f"{piece_id}:closure_bridge:{bridged:02d}",
                    "type": "POLYLINE",
                    "line_role": "pattern_boundary",
                    "_piece_role": piece_role,
                    "_piece_id": piece_id,
                    "piece_id": piece_id,
                    "_transfer_mode": "closure_bridge_preview",
                    "_review_required": True,
                    "geometry": {"points": [[float(a[0]), float(a[1])], [float(b[0]), float(b[1])]]},
                })
    return out, bridged


def count_loose_production_lines(entities: list[dict[str, Any]]) -> int:
    count = 0
    for e in entities:
        if str(e.get("_review_layer") or "") == "AI4M_REVIEW_RETAINED":
            continue
        pts = points(e)
        role = str(e.get("line_role") or "")
        is_boundary_role = role in SCRIPT_BOUNDARY_ROLE_ALIASES or role.endswith("_boundary") or role.endswith("_hem")
        if len(pts) == 2 and role not in {"grainline", "construction", "pleat_line", "fold_line", "notch"} and not is_boundary_role:
            count += 1
    return count


def main() -> None:
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    OUT_ROOT.mkdir(parents=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    index = build_index(ROOT / "data" / "ir" / "v1_rule_ready", ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir", ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir")
    catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", index)

    summaries = []
    rendered = []
    for name, family, base_case_id, selections, base_option_ids in RECIPES:
        recipe = {
            "family": family,
            "sex": "female",
            "base_case_id": base_case_id,
            "measurements_cm": dict(MEAS_BY_NAME.get(name, MEAS)),
            "selections": selections,
            "base_option_ids": base_option_ids,
            "execution_mode": "batch_preview",
            "compact_layout": True,
        }
        entities, meta = compose_recipe(recipe, index, catalog)
        sleeve_pair_normalized = False
        sleeve_pair_normalized = False
        closure_bridge_count = 0
        if family in {"tshirt", "shirt"}:
            entities, sleeve_pair_normalized = ensure_tshirt_two_sleeves(entities)
        if family == "shirt":
            entities, closure_bridge_count = ensure_shirt_body_closure_preview(entities)
        sample_dir = OUT_ROOT / name
        sample_dir.mkdir()
        dxf_path = sample_dir / f"{name}.dxf"
        export_entities, review_moved = review_safe_export_entities(entities)
        write_info = write_entities_dxf(export_entities, str(dxf_path), optimize=True)
        inventory = validate_garment_inventory(export_entities, family)
        sample_summary = {
            "name": name,
            "family": family,
            "base_case_id": base_case_id,
            "status": meta.get("status"),
            "component_statuses": {row["group"]: row["status"] for row in meta.get("component_results") or []},
            "component_issue_codes": {row["group"]: [issue["code"] for issue in row.get("validation_issues") or []] for row in meta.get("component_results") or []},
            "inventory_valid": inventory.get("valid"),
            "inventory_counts": inventory.get("counts"),
            "missing_or_invalid": inventory.get("missing_or_invalid"),
            "loose_production_line_count": count_loose_production_lines(export_entities),
            "review_layer_moved_count": review_moved,
            "sleeve_pair_normalized": sleeve_pair_normalized,
            "closure_bridge_count": closure_bridge_count,
            "measurements_cm": recipe["measurements_cm"],
            "entities": len(entities),
            "dxf": write_info,
        }
        (sample_dir / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8")
        (sample_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (sample_dir / "review-ledger.json").write_text(json.dumps(meta.get("review_ledger") or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        (sample_dir / "topology-summary.json").write_text(json.dumps(sample_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append(sample_summary)
        rendered.append((name, family, export_entities, meta, sample_summary))

    SUMMARY.write_text(json.dumps({"sample_count": len(summaries), "samples": summaries}, ensure_ascii=False, indent=2), encoding="utf-8")

    cols, rows = 2, math.ceil(len(rendered) / 2)
    fig, axes = plt.subplots(rows, cols, figsize=(18, rows * 6), squeeze=False)
    for ax in axes.ravel():
        ax.axis("off")
    for ax, (name, family, entities, meta, summary) in zip(axes.ravel(), rendered):
        ax.axis("on")
        changed_ids = modified_entity_ids(meta)
        ax.set_aspect("equal", adjustable="box")
        xmin, ymin, xmax, ymax = display_bounds(entities)
        pad = max(xmax - xmin, ymax - ymin, 1) * 0.08
        ax.set_xlim(xmin - pad, xmax + pad)
        ax.set_ylim(ymax + pad, ymin - pad)
        ax.set_title(f"{name}  [{family.upper()}]", loc="left", fontsize=12)
        for e in entities:
            pts = points(e)
            if len(pts) < 2:
                continue
            if str(e.get("_review_layer") or "") == "AI4M_REVIEW_RETAINED":
                continue
            role = str(e.get("_piece_role") or "unknown")
            transfer_mode = str(e.get("_transfer_mode") or "")
            review_layer = str(e.get("_review_layer") or "")
            entity_id = str(e.get("entity_id") or "")
            color = COLORS.get(role, "#777777")
            alpha = 1.0
            zorder = 2
            if review_layer == "AI4M_REVIEW_RETAINED":
                color = "#cfc7bf"
                alpha = 0.26
                zorder = 1
            if transfer_mode == "closed_preview_outline":
                color = "#c87855"
                alpha = 1.0
                zorder = 4
            elif review_layer != "AI4M_REVIEW_RETAINED" and entity_id in changed_ids:
                color = "#d98a45"
                zorder = 3
            xs, ys = zip(*pts)
            lw = 1.2 if str(e.get("line_role") or "") not in {"grainline", "construction"} else 0.8
            if transfer_mode == "closed_preview_outline":
                lw = 1.8
            ax.plot(xs, ys, color=color, linewidth=lw, alpha=alpha, zorder=zorder)
        txt = f"{status_line(meta)}\nclosed inventory: {summary["inventory_valid"]} · loose lines: {summary["loose_production_line_count"]} · entities: {summary["entities"]}"
        ax.text(0.01, -0.08, txt, transform=ax.transAxes, ha="left", va="top", fontsize=9, color="#5c4b3b")
    fig.tight_layout(h_pad=3.0)
    fig.savefig(OVERVIEW, dpi=180)
    plt.close(fig)

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in OUT_ROOT.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(OUT_ROOT.parent))
    print(json.dumps({"out_root": str(OUT_ROOT), "zip": str(ZIP_PATH), "overview": str(OVERVIEW), "summary": str(SUMMARY), "sample_count": len(summaries)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
