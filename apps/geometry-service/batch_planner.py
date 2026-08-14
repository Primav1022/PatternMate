from __future__ import annotations

import hashlib
import json
from typing import Any

from composition_contracts import CompositionPlan, PlanOperation

OPERATION_ORDER = ("neckline", "collar", "sleeve", "cuff", "garment_length")

RULES = {
    "neckline": {
        "host": ("front_neckline", "back_neckline"),
        "donor": ("front_neckline", "back_neckline"),
        "mutable": ("front_neckline", "back_neckline"),
        "dependent": ("shoulder_seam",),
    },
    "collar": {
        "host": ("front_neckline", "back_neckline"),
        "donor": ("front_neckline", "back_neckline"),
        "mutable": ("front_neckline", "back_neckline"),
        "dependent": ("shoulder_seam",),
    },
    "sleeve": {
        "host": ("armhole_front", "armhole_back"),
        "donor": ("sleeve_cap", "sleeve_underarm", "sleeve_hem"),
        "mutable": ("sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "sleeve_underarm", "sleeve_hem"),
        "dependent": (),
    },
    "cuff": {
        "host": ("sleeve_hem",),
        "donor": ("cuff_attach", "cuff_outer"),
        "mutable": ("cuff_attach", "cuff_outer"),
        "dependent": ("sleeve_hem",),
    },
    "garment_length": {
        "host": ("garment_hem", "side_seam"),
        "donor": (),
        "mutable": ("garment_hem",),
        "dependent": ("side_seam",),
    },
}


def _recipe_hash(recipe: dict[str, Any]) -> str:
    canonical = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _protected_entities(ir: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(str(entity.get("entity_id")) for entity in ir.get("atomic_entities") or [] if entity.get("entity_id")))


def build_composition_plan(recipe: dict[str, Any], base_ir: dict[str, Any]) -> CompositionPlan:
    selections = recipe.get("selections") or {}
    base_option_ids = recipe.get("base_option_ids") or {}
    operations: list[PlanOperation] = []
    selected_groups = {group for group, option_id in selections.items() if option_id and option_id != base_option_ids.get(group)}
    for group in OPERATION_ORDER:
        option_id = selections.get(group)
        if not option_id or option_id == base_option_ids.get(group):
            continue
        rule = RULES[group]
        depends_on: tuple[str, ...] = ()
        if group == "cuff" and "sleeve" in selected_groups:
            depends_on = ("op:sleeve",)
        operations.append(PlanOperation(
            operation_id=f"op:{group}",
            group=group,
            option_id=str(option_id),
            host_required_roles=rule["host"],
            donor_required_roles=rule["donor"],
            mutable_roles=rule["mutable"],
            dependent_roles=rule["dependent"],
            depends_on=depends_on,
            max_donors=3,
            max_repair_rounds=3,
        ))
    return CompositionPlan(
        plan_id=f"plan:{_recipe_hash(recipe)}",
        execution_mode=str(recipe.get("execution_mode") or "legacy"),
        operations=tuple(operations),
        protected_entity_ids=_protected_entities(base_ir),
        warnings=(),
    )
