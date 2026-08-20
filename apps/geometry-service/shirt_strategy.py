"""Shirt remix rules.

Pipeline id: ``shirt.simple_piece_swap.v1`` (see SHIRT_PIPELINE.md).

- collar + placket → 一起换整个前后片（领口/门襟都在衣身上）
- silhouette → 只改前后片侧缝弧度
- sleeve → 只换袖片
- cuff   → 只换袖口片
- length / width → 衣身纵向 / 横向比例缩放
"""
from __future__ import annotations

from typing import Any

BODY_ROLES = {"front_body", "back_body", "front", "back", "front_placket", "front_left", "front_right", "front_yoke", "back_yoke", "side_panel"}
PURE_SLEEVE_ROLES = {"sleeve", "sleeve_left", "sleeve_right"}
CUFF_ROLES = {"cuff", "rib_cuff", "sleeve_placket", "sleeve_placket_extension"}
COLLAR_ROLES = {"collar", "collar_stand", "collar_interlining", "neck_binding", "neck_rib"}

# 领口+门襟在衣身上，一次整换前后片（含领附件）
BODY_SWAP_ROLES = BODY_ROLES | COLLAR_ROLES
COLLAR_SWAP_ROLES = BODY_SWAP_ROLES
PLACKET_SWAP_ROLES = BODY_SWAP_ROLES
SLEEVE_SWAP_ROLES = set(PURE_SLEEVE_ROLES)
CUFF_SWAP_ROLES = set(CUFF_ROLES)
SILHOUETTE_BODY_ROLES = {"front_body", "back_body", "front", "back", "front_left", "front_right", "side_panel"}


def option_slug(option_id: str | None) -> str:
    return str(option_id or "").split(".")[-1].strip().lower()


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    out = dict(plan)
    roles = out.get("roles")
    if isinstance(roles, (set, frozenset, tuple)):
        out["roles"] = sorted(str(r) for r in roles)
    elif isinstance(roles, list):
        out["roles"] = [str(r) for r in roles]
    return out


def swap_plan(group: str, option_id: str | None) -> dict[str, Any]:
    slug = option_slug(option_id)
    if group in {"collar", "placket"}:
        return {
            "mode": "piece_swap",
            "roles": BODY_SWAP_ROLES,
            "slug": slug,
            "source": "shirt_simple",
            "reason": "换整个前后片（领口+门襟）",
        }
    if group == "silhouette":
        return {
            "mode": "side_seam_morph",
            "roles": SILHOUETTE_BODY_ROLES,
            "slug": slug,
            "source": "shirt_simple",
            "reason": "只改前后片侧缝弧度",
        }
    if group == "sleeve":
        roles, reason, mode = SLEEVE_SWAP_ROLES, "只换袖片", "piece_swap"
    elif group == "cuff":
        roles, reason, mode = CUFF_SWAP_ROLES, "只换袖口片", "piece_swap"
    else:
        roles, reason, mode = set(), "noop", "noop"
    return {
        "mode": mode,
        "roles": roles,
        "slug": slug,
        "source": "shirt_simple",
        "reason": reason,
    }


def sleeve_plan(option_id: str | None) -> dict[str, Any]:
    return swap_plan("sleeve", option_id)


def collar_plan(option_id: str | None) -> dict[str, Any]:
    return swap_plan("collar", option_id)


def cuff_plan(option_id: str | None) -> dict[str, Any]:
    return swap_plan("cuff", option_id)


SHIRT_SLEEVE_STRATEGY: dict[str, dict[str, Any]] = {
    slug: {"mode": "sleeve_only", "roles": PURE_SLEEVE_ROLES}
    for slug in ("regular", "puff", "bell", "batwing", "flutter")
}
