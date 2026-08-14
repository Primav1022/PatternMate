from __future__ import annotations

from typing import Any

from composition_contracts import ResolvedEdgeChain

FRONT_ROLES = {"front_body", "front_left", "front_right"}
BACK_ROLES = {"back_body", "back_yoke"}
SLEEVE_ROLES = {"sleeve", "sleeve_left", "sleeve_right"}
CUFF_ROLES = {"cuff", "rib_cuff", "sleeve_placket", "sleeve_placket_extension"}

ROLE_ALIASES = {
    "hem": "garment_hem",
    "hem_line": "garment_hem",
    "bottom_hem": "garment_hem",
    "side": "side_seam",
    "side_seam": "side_seam",
    "sleeve_underarm_seam": "sleeve_underarm",
    "underarm": "sleeve_underarm",
    "sleeve_hem_line": "sleeve_hem",
    "sleeve_hem": "sleeve_hem",
    "cuff_edge": "cuff_outer",
    "cuff_outer": "cuff_outer",
    "cuff_attach_line": "cuff_attach",
    "rib_cuff_attach": "cuff_attach",
    "armhole_front": "armhole_front",
    "front_armhole": "armhole_front",
    "armhole_back": "armhole_back",
    "back_armhole": "armhole_back",
    "sleeve_cap": "sleeve_cap",
    "sleeve_head": "sleeve_cap",
    "sleeve_cap_front": "sleeve_cap_front",
    "sleeve_cap_back": "sleeve_cap_back",
}

# Polluted labels: edge_role says hem but entity line_role is clearly something else.
BAD_GARMENT_HEM_TOKENS = ("armhole", "neck", "sleeve", "shoulder", "cuff", "collar", "placket")
BODY_ROLES = FRONT_ROLES | BACK_ROLES


def _piece_role_by_id(ir: dict[str, Any]) -> dict[str, str]:
    return {
        str(piece.get("piece_id") or ""): str(piece.get("piece_role") or "unknown")
        for piece in ir.get("piece_instances") or []
    }


def _entity_line_roles(ir: dict[str, Any], entity_ids: tuple[str, ...]) -> list[str]:
    wanted = set(entity_ids)
    roles: list[str] = []
    for entity in ir.get("atomic_entities") or []:
        eid = str(entity.get("entity_id") or "")
        if eid not in wanted:
            continue
        roles.append(str(entity.get("line_role") or entity.get("edge_role") or "").lower())
    return roles


def _garment_hem_polluted(piece_role: str, entity_line_roles: list[str]) -> bool:
    if piece_role in SLEEVE_ROLES:
        return False  # handled as sleeve_hem upstream
    if piece_role and piece_role not in BODY_ROLES and piece_role != "unknown":
        return True
    for raw in entity_line_roles:
        if any(token in raw for token in BAD_GARMENT_HEM_TOKENS):
            return True
        if raw in {"armhole_front", "armhole_back", "armhole", "neckline", "shoulder_line", "shoulder_seam"}:
            return True
    return False


def _canonical(raw_role: str, piece_role: str, entity_line_roles: list[str] | None = None) -> tuple[str | None, str, float, str]:
    raw = raw_role.strip().lower()
    if raw in {"", "unknown", "none", "unlabeled"}:
        return None, "ambiguous", 0.0, "unknown_or_empty_role"
    if "neckline" in raw or raw in {"neck", "neck_edge", "collar_neck"}:
        if piece_role in FRONT_ROLES:
            return "front_neckline", "resolved", 0.95, "neckline_with_front_piece_context"
        if piece_role in BACK_ROLES:
            return "back_neckline", "resolved", 0.95, "neckline_with_back_piece_context"
        return None, "ambiguous", 0.3, "neckline_without_body_piece_context"
    if raw in ROLE_ALIASES:
        canonical = ROLE_ALIASES[raw]
        if canonical == "garment_hem" and piece_role in SLEEVE_ROLES:
            return "sleeve_hem", "resolved", 0.88, "hem_with_sleeve_context"
        if canonical == "garment_hem" and _garment_hem_polluted(piece_role, entity_line_roles or []):
            return None, "ambiguous", 0.15, "garment_hem_polluted_by_line_role"
        return canonical, "resolved", 0.9, "direct_alias"
    if raw == "armhole":
        if piece_role in FRONT_ROLES:
            return "armhole_front", "resolved", 0.82, "armhole_with_front_piece_context"
        if piece_role in BACK_ROLES:
            return "armhole_back", "resolved", 0.82, "armhole_with_back_piece_context"
        return None, "ambiguous", 0.25, "armhole_without_body_piece_context"
    if raw in {"cuff", "cuff_attach"} and piece_role in CUFF_ROLES:
        return "cuff_outer" if raw == "cuff" else "cuff_attach", "resolved", 0.75, "cuff_piece_context"
    return None, "ambiguous", 0.0, "unmapped_role_preserved"


def _fallback_chains_from_entities(ir: dict[str, Any], offset: int) -> list[ResolvedEdgeChain]:
    rows: list[ResolvedEdgeChain] = []
    seen: set[tuple[str, str]] = set()
    for index, entity in enumerate(ir.get("atomic_entities") or []):
        entity_id = str(entity.get("entity_id") or "")
        if not entity_id:
            continue
        piece_id = str(entity.get("piece_id") or "")
        piece_role = str(entity.get("_piece_role") or entity.get("piece_role") or "unknown")
        raw_role = str(entity.get("edge_role") or entity.get("line_role") or "unknown")
        line_roles = [str(entity.get("line_role") or "").lower()]
        canonical, status, confidence, reason = _canonical(raw_role, piece_role, line_roles)
        if status != "resolved" or not canonical:
            continue
        key = (canonical, entity_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(ResolvedEdgeChain(
            edge_chain_id=f"fallback:{offset + index}:{entity_id}",
            piece_id=piece_id,
            piece_role=piece_role,
            raw_role=raw_role,
            canonical_role=canonical,
            ordered_entity_ids=(entity_id,),
            direction="forward",
            status="resolved",
            confidence=min(confidence, 0.82),
            provenance={"resolver": "canonical-edge-role.v1", "reason": f"line_role_fallback:{reason}", "review": "required"},
        ))
    return rows


def resolve_edge_chains(ir: dict[str, Any]) -> list[ResolvedEdgeChain]:
    piece_roles = _piece_role_by_id(ir)
    rows: list[ResolvedEdgeChain] = []
    covered_entity_ids: set[str] = set()
    for index, chain in enumerate(ir.get("edge_chains") or []):
        piece_id = str(chain.get("piece_id") or "")
        piece_role = piece_roles.get(piece_id, str(chain.get("piece_role") or "unknown"))
        raw_role = str(chain.get("edge_role") or "unknown")
        entity_ids = tuple(str(value) for value in chain.get("ordered_entity_ids") or ())
        covered_entity_ids.update(entity_ids)
        line_roles = _entity_line_roles(ir, entity_ids)
        canonical, status, confidence, reason = _canonical(raw_role, piece_role, line_roles)
        rows.append(ResolvedEdgeChain(
            edge_chain_id=str(chain.get("edge_chain_id") or f"edge_chain_{index}"),
            piece_id=piece_id,
            piece_role=piece_role,
            raw_role=raw_role,
            canonical_role=canonical,
            ordered_entity_ids=entity_ids,
            direction=str(chain.get("direction") or "forward"),
            status=status,  # type: ignore[arg-type]
            confidence=confidence,
            provenance={"resolver": "canonical-edge-role.v1", "reason": reason, "review": chain.get("review")},
        ))
    fallback_ir = {**ir, "atomic_entities": [
        entity for entity in ir.get("atomic_entities") or []
        if str(entity.get("entity_id") or "") not in covered_entity_ids
    ]}
    rows.extend(_fallback_chains_from_entities(fallback_ir, len(rows)))
    return rows
