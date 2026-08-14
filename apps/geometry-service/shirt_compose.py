"""Shirt compose.

Pipeline id: ``shirt.simple_piece_swap.v1``

Rules:
- collar + placket → one front/back body swap (领口和门襟都在衣身上)
- silhouette      → morph front/back side-seam curvature
- sleeve / cuff   → replace those pieces only
- length/width    → stretch body
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from composition_contracts import ValidationIssue
from composition_engine import (
    _downgrade_review_only_batch_errors,
    _layout_complete,
    _normalize_physical_components,
    _paper_info,
    _piece_summary,
    _validate,
    filter_preview_entities,
    grading_profile,
    normalize_family,
    source_measurements,
)
from shirt_side_seam import morph_body_side_seams
from shirt_sleeve_fit import fit_sleeves_to_armholes
from shirt_strategy import (
    BODY_SWAP_ROLES,
    COLLAR_ROLES,
    CUFF_SWAP_ROLES,
    PURE_SLEEVE_ROLES,
    SLEEVE_SWAP_ROLES,
    public_plan,
    swap_plan,
)
from simple_compose import (
    BODY_ROLES,
    LENGTH_FACTOR,
    _annotate,
    _keep_largest_clusters,
    _pick_donor,
    _replace_roles,
    _result,
    _scale_pieces,
)
from tryon_descriptor import build_tryon_descriptor


PIPELINE_ID = "shirt.simple_piece_swap.v1"


def _swap_group(
    *,
    group: str,
    roles: set[str],
    entities: list[dict[str, Any]],
    host_ref: list[dict[str, Any]],
    base_ir: dict[str, Any],
    donor_index: dict[str, dict[str, Any]],
    option_id: str,
    results: list,
    sources: dict[str, Any],
) -> list[dict[str, Any]]:
    plan = swap_plan(group, option_id)
    donor_ir, candidates = _pick_donor(group, base_ir, donor_index, option_id)
    if not donor_ir:
        results.append(_result(
            f"op:{group}", group, "retained_current", option_id=option_id,
            issue=ValidationIssue(code="donor_unavailable", severity="warning", message=f"no donor for {group}", operation_id=f"op:{group}"),
            extra={"donor_candidates": candidates, "mode": "simple_piece_swap", "strategy": public_plan(plan)},
        ))
        return entities
    before_ids = {str(entity.get("entity_id")) for entity in entities}
    entities, count, piece_match = _replace_roles(entities, donor_ir, roles, host_ref=host_ref)
    if count <= 0:
        results.append(_result(
            f"op:{group}", group, "retained_current", option_id=option_id, donor_case_id=str(donor_ir.get("case_id")),
            issue=ValidationIssue(code="donor_pieces_missing", severity="warning", message=f"donor has no {group} pieces", operation_id=f"op:{group}"),
            extra={"donor_candidates": candidates, "mode": "simple_piece_swap", "strategy": public_plan(plan)},
        ))
        return entities
    modified = tuple(str(entity.get("entity_id")) for entity in entities if str(entity.get("entity_id")) not in before_ids)
    sources[group] = {
        "case_id": donor_ir.get("case_id"),
        "option_id": option_id,
        "mode": "piece_swap",
        "strategy": public_plan(plan),
        "donor_score": candidates[0]["score"] if candidates else None,
        "piece_match": piece_match,
    }
    results.append(_result(
        f"op:{group}", group, "applied", option_id=option_id, donor_case_id=str(donor_ir.get("case_id")),
        modified=modified,
        extra={
            "donor_candidates": candidates,
            "mode": "simple_piece_swap",
            "strategy": public_plan(plan),
            "replaced_roles": sorted(roles),
            "inserted": count,
            "piece_match": piece_match,
        },
    ))
    return entities


def _morph_silhouette(
    *,
    entities: list[dict[str, Any]],
    base_ir: dict[str, Any],
    donor_index: dict[str, dict[str, Any]],
    option_id: str,
    results: list,
    sources: dict[str, Any],
) -> list[dict[str, Any]]:
    plan = swap_plan("silhouette", option_id)
    donor_ir, candidates = _pick_donor("silhouette", base_ir, donor_index, option_id)
    if not donor_ir:
        results.append(_result(
            "op:silhouette", "silhouette", "retained_current", option_id=option_id,
            issue=ValidationIssue(code="donor_unavailable", severity="warning", message="no donor for silhouette", operation_id="op:silhouette"),
            extra={"donor_candidates": candidates, "mode": "side_seam_morph", "strategy": public_plan(plan)},
        ))
        return entities
    before = {str(entity.get("entity_id")): entity.get("geometry") for entity in entities}
    entities, morph_meta = morph_body_side_seams(entities, _annotate(donor_ir))
    if not morph_meta.get("applied"):
        results.append(_result(
            "op:silhouette", "silhouette", "retained_current", option_id=option_id, donor_case_id=str(donor_ir.get("case_id")),
            issue=ValidationIssue(code="side_seam_unavailable", severity="warning", message="host/donor closed side seams missing", operation_id="op:silhouette"),
            extra={"donor_candidates": candidates, "mode": "side_seam_morph", "strategy": public_plan(plan), **morph_meta},
        ))
        return entities
    after = {str(entity.get("entity_id")): entity.get("geometry") for entity in entities}
    modified = tuple(eid for eid, geom in before.items() if eid and after.get(eid) != geom)
    sources["silhouette"] = {
        "case_id": donor_ir.get("case_id"),
        "option_id": option_id,
        "mode": "side_seam_morph",
        "strategy": public_plan(plan),
        "donor_score": candidates[0]["score"] if candidates else None,
        **morph_meta,
    }
    results.append(_result(
        "op:silhouette", "silhouette", "applied", option_id=option_id, donor_case_id=str(donor_ir.get("case_id")),
        modified=modified,
        extra={"donor_candidates": candidates, "mode": "side_seam_morph", "strategy": public_plan(plan), **morph_meta},
    ))
    return entities


def _stretch_body_structure(
    entities: list[dict[str, Any]],
    *,
    width_sx: float,
    length_sy: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """衣宽→横向比例；衣长→纵向比例。只动衣身相关片，袖/领/袖口不动。"""
    meta = {
        "mode": "body_structure_scale",
        "width_sx": round(width_sx, 5),
        "length_sy": round(length_sy, 5),
    }
    if abs(width_sx - 1.0) < 1e-6 and abs(length_sy - 1.0) < 1e-6:
        meta["applied"] = False
        return entities, meta
    # Anchor top so length grows downward (CAD Y-up).
    entities = _scale_pieces(entities, roles=BODY_ROLES | {"back_yoke"}, sx=width_sx, sy=length_sy, anchor="top")
    meta["applied"] = True
    return entities, meta


def compose_shirt(
    recipe: dict[str, Any],
    index: dict[str, dict[str, Any]],
    catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    del catalog  # parity with compose_simple signature
    family = "shirt"
    base_case_id = recipe["base_case_id"]
    base_ir = index.get(base_case_id)
    if not base_ir:
        raise ValueError(f"找不到基础纸样 {base_case_id}")
    actual_family = normalize_family((base_ir.get("design_semantics") or {}).get("category"))
    if actual_family != family:
        raise ValueError(f"基础纸样 {base_case_id} 属于 {actual_family}，不能在 shirt 工作台中组合")

    selections = recipe.get("selections") or {}
    base_option_ids = recipe.get("base_option_ids") or {}
    donor_index = {case_id: ir for case_id, ir in index.items() if case_id != base_case_id}

    entities = _annotate(base_ir)
    entities = _keep_largest_clusters(entities)
    host_ref = list(entities)
    results: list = []
    sources: dict[str, Any] = {"base": base_case_id}
    strategies: dict[str, Any] = {}

    # 1) 领口+门襟 → 一次整换前后片（两者都在衣身上）
    collar_opt = selections.get("collar")
    placket_opt = selections.get("placket")
    collar_changed = bool(collar_opt and collar_opt != base_option_ids.get("collar"))
    placket_changed = bool(placket_opt and placket_opt != base_option_ids.get("placket"))
    if collar_changed or placket_changed:
        pick_group = "collar" if collar_changed else "placket"
        pick_opt = collar_opt if collar_changed else placket_opt
        plan = public_plan(swap_plan(pick_group, pick_opt))
        if collar_changed:
            strategies["collar"] = plan
        if placket_changed:
            strategies["placket"] = plan
        entities = _swap_group(
            group=pick_group, roles=set(BODY_SWAP_ROLES), entities=entities, host_ref=host_ref,
            base_ir=base_ir, donor_index=donor_index, option_id=str(pick_opt),
            results=results, sources=sources,
        )
        if collar_changed and placket_changed and "collar" in sources:
            sources["placket"] = {**sources["collar"], "option_id": placket_opt, "bundled_with": "collar"}

    # 1b) 廓形 → 前后片侧缝弧度（换完衣身后再改线）
    silhouette_opt = selections.get("silhouette")
    if silhouette_opt and silhouette_opt != base_option_ids.get("silhouette"):
        strategies["silhouette"] = public_plan(swap_plan("silhouette", silhouette_opt))
        entities = _morph_silhouette(
            entities=entities, base_ir=base_ir, donor_index=donor_index, option_id=silhouette_opt,
            results=results, sources=sources,
        )

    # 2) Sleeve → 只换袖片；host 没有袖片时按当前选项直接补一只
    sleeve_opt = selections.get("sleeve") or base_option_ids.get("sleeve") or "shirt.sleeve.regular"
    host_has_sleeve = any(
        str(entity.get("_piece_role") or entity.get("piece_role") or "") in PURE_SLEEVE_ROLES
        for entity in entities
    )
    if sleeve_opt and (sleeve_opt != base_option_ids.get("sleeve") or not host_has_sleeve):
        strategies["sleeve"] = public_plan(swap_plan("sleeve", sleeve_opt))
        entities = _swap_group(
            group="sleeve", roles=set(SLEEVE_SWAP_ROLES), entities=entities, host_ref=host_ref,
            base_ir=base_ir, donor_index=donor_index, option_id=sleeve_opt,
            results=results, sources=sources,
        )

    # 3) Cuff → 只换袖口片（有选才换，不做别的变换）
    cuff_opt = selections.get("cuff")
    if cuff_opt and cuff_opt != base_option_ids.get("cuff"):
        strategies["cuff"] = public_plan(swap_plan("cuff", cuff_opt))
        entities = _swap_group(
            group="cuff", roles=set(CUFF_SWAP_ROLES), entities=entities, host_ref=host_ref,
            base_ir=base_ir, donor_index=donor_index, option_id=cuff_opt,
            results=results, sources=sources,
        )

    entities = filter_preview_entities(entities)
    entities = _keep_largest_clusters(entities)
    entities = _normalize_physical_components(entities)

    # 4) 衣宽/衣长/袖长/袖肥/颈围：grading_profile 算出的比例落到对应片上
    profile = grading_profile(recipe)
    length_option = selections.get("garment_length")
    length_slug = str(length_option or "x.regular").split(".")[-1]
    length_factor = LENGTH_FACTOR.get(length_slug, 1.0)
    width_sx = float(profile.get("width") or 1.0)
    length_sy = float(profile.get("length") or 1.0) * length_factor
    sleeve_sx = float(profile.get("sleeve_width") or 1.0)
    sleeve_sy = float(profile.get("sleeve_length") or 1.0)
    neck_s = float(profile.get("neck") or 1.0)
    before = deepcopy(entities)
    entities, stretch_meta = _stretch_body_structure(entities, width_sx=width_sx, length_sy=length_sy)
    sleeve_fit = {"applied": False, "reason": "sleeve_not_swapped"}
    if (sources.get("sleeve") or {}).get("mode") == "piece_swap":
        entities, sleeve_fit = fit_sleeves_to_armholes(entities)
    stretch_meta["sleeve_armhole_fit"] = sleeve_fit
    if sleeve_fit.get("applied"):
        if abs(sleeve_sy - 1.0) >= 1e-6:
            entities = _scale_pieces(entities, roles=PURE_SLEEVE_ROLES, sx=1.0, sy=sleeve_sy, anchor="top")
            stretch_meta["sleeve_sy"] = round(sleeve_sy, 5)
    elif abs(sleeve_sx - 1.0) >= 1e-6 or abs(sleeve_sy - 1.0) >= 1e-6:
        entities = _scale_pieces(entities, roles=PURE_SLEEVE_ROLES, sx=sleeve_sx, sy=sleeve_sy, anchor="top")
        stretch_meta["sleeve_sx"] = round(sleeve_sx, 5)
        stretch_meta["sleeve_sy"] = round(sleeve_sy, 5)
    if abs(neck_s - 1.0) >= 1e-6:
        entities = _scale_pieces(entities, roles=COLLAR_ROLES, sx=neck_s, sy=neck_s, anchor="center")
        stretch_meta["neck_s"] = round(neck_s, 5)
    stretch_meta["applied"] = bool(
        stretch_meta.get("applied")
        or sleeve_fit.get("applied")
        or abs(sleeve_sx - 1.0) >= 1e-6
        or abs(sleeve_sy - 1.0) >= 1e-6
        or abs(neck_s - 1.0) >= 1e-6
    )
    sources["sizing"] = {**stretch_meta, "fit": profile.get("fit")}
    if stretch_meta.get("applied"):
        modified = tuple(
            str(entity.get("entity_id"))
            for entity, prev in zip(entities, before)
            if entity.get("entity_id") and entity.get("geometry") != prev.get("geometry")
        )
        sources["garment_length"] = {
            "option_id": length_option,
            "mode": "body_structure_scale",
            "factor": length_factor,
            **stretch_meta,
        }
        results.append(_result(
            "op:garment_length", "garment_length", "applied" if modified else "retained_current",
            option_id=length_option, modified=modified,
            extra={"mode": "body_structure_scale", "length_factor": length_factor, **stretch_meta},
        ))

    laid_out = _layout_complete(entities, gap=52.0 if recipe.get("compact_layout") else 90.0)
    validation = _validate(family, laid_out, {}, sources)
    _downgrade_review_only_batch_errors(validation, family)
    group_zh = {"collar": "领型", "sleeve": "袖型", "cuff": "袖口", "placket": "前门襟", "silhouette": "廓形"}
    for row in results:
        if row.status != "retained_current":
            continue
        if not any(issue.code == "donor_unavailable" for issue in row.validation_issues):
            continue
        validation.setdefault("warnings", []).append(
            f"语料中没有可用的{group_zh.get(row.group, row.group)}来源，已保留当前版片"
        )
    validation["standard"] = "shirt_simple_piece_swap_trial"
    canonical = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    recipe_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    component_payload = [
        {
            "operation_id": row.operation_id,
            "group": row.group,
            "status": row.status,
            "donor_case_id": row.donor_case_id,
            "option_id": row.option_id,
            "modified_entity_ids": list(row.modified_entity_ids),
            "validation_issues": [
                {"code": issue.code, "severity": issue.severity, "message": issue.message, "operation_id": issue.operation_id}
                for issue in row.validation_issues
            ],
            "review_required": row.review_required,
            "provenance": row.provenance,
        }
        for row in results
    ]
    meta = {
        "recipe_hash": recipe_hash,
        "family": family,
        "pipeline": PIPELINE_ID,
        "execution_mode": "shirt_strategy",
        "strategies": strategies,
        "sizing_profile": profile,
        "source_measurements": source_measurements(base_ir),
        "tryon_descriptor": build_tryon_descriptor(laid_out, recipe_hash, family),
        "sources": sources,
        "pieces": _piece_summary(laid_out),
        "paper_info": _paper_info(laid_out),
        "validation": validation,
        "replacement_candidates": {},
        "status": "valid" if validation.get("valid") else "invalid",
        "batch_plan": {"operations": [{"group": row.group, "option_id": row.option_id} for row in results]},
        "component_results": component_payload,
        "review_required": bool(results),
        "review_ledger": {
            "schema": "chi27.review-ledger.shirt-simple-piece-swap.v1",
            "pipeline": PIPELINE_ID,
            "trial_status": "auto_validated_trial",
            "human_review_required": bool(results),
            "strategies": strategies,
            "operations": component_payload,
        },
    }
    return laid_out, meta
