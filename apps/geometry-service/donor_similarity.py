from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from edge_role_resolver import resolve_edge_chains

LABEL_KEY_BY_GROUP = {
    "neckline": "neckline",
    "collar": "collar",
    "sleeve": "sleeve_style",
    "cuff": "cuff",
    "garment_length": "garment_length",
    "placket": "placket",
    "silhouette": "silhouette",
}

ROLE_GROUPS = {
    "neckline": {"front_neckline", "back_neckline"},
    "collar": {"front_neckline", "back_neckline", "collar_attach_line", "collar_roll_line"},
    "sleeve": {"sleeve_cap", "sleeve_cap_front", "sleeve_cap_back", "sleeve_underarm", "sleeve_hem"},
    "cuff": {"cuff_attach", "cuff_outer"},
    "garment_length": {"garment_hem", "side_seam"},
    "silhouette": {"side_seam"},
    "placket": {"placket_line", "center_front"},
}

# Piece roles used when swapping whole pattern pieces (not just edge chains).
PIECE_ROLES_BY_GROUP = {
    "neckline": {"front_body", "front_left", "front_right", "back_body", "back_yoke", "neck_binding", "neck_rib", "collar", "collar_stand", "front_placket"},
    "collar": {"front_body", "front_left", "front_right", "back_body", "collar", "collar_stand", "collar_interlining", "neck_binding"},
    "sleeve": {"sleeve", "sleeve_left", "sleeve_right"},
    "cuff": {"cuff", "rib_cuff", "sleeve_placket"},
    "garment_length": {"front_body", "front_left", "front_right", "back_body"},
    "placket": {"front_body", "front_left", "front_right", "back_body", "front_placket"},
    "silhouette": {"front_body", "front_left", "front_right", "back_body"},
}


@dataclass(frozen=True)
class DonorScore:
    case_id: str
    score: float
    breakdown: dict[str, float]
    reasons: tuple[str, ...]




def part_label_slug(ir: dict[str, Any], group: str) -> str | None:
    labels = ((ir.get("design_semantics_extra") or {}).get("part_labels") or {})
    key = LABEL_KEY_BY_GROUP.get(group, group)
    value = labels.get(key)
    if isinstance(value, dict):
        slug = value.get("slug")
        return str(slug) if slug else None
    if isinstance(value, str):
        return value
    return None


def _slug_from_option_id(option_id: str | None) -> str | None:
    if not option_id:
        return None
    return str(option_id).split(".")[-1]


def _points(entity: dict[str, Any]) -> list[list[float]]:
    return [[float(x), float(y)] for x, y in (entity.get("geometry") or {}).get("points") or []]


def _length(entity: dict[str, Any]) -> float:
    pts = _points(entity)
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _bounds(entities: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    pts = [point for entity in entities for point in _points(entity)]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _ratio_similarity(a: float, b: float) -> float:
    eps = 1e-6
    if a <= eps and b <= eps:
        return 1.0
    if a <= eps or b <= eps:
        return 0.0
    return max(0.0, min(1.0, math.exp(-abs(math.log((a + eps) / (b + eps))))))


def _entity_by_id(ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(entity.get("entity_id")): entity for entity in ir.get("atomic_entities") or [] if entity.get("entity_id")}


def _resolved_ids(ir: dict[str, Any], group: str) -> dict[str, list[str]]:
    wanted = ROLE_GROUPS.get(group, set())
    result: dict[str, list[str]] = {}
    for row in resolve_edge_chains(ir):
        if row.status == "resolved" and row.canonical_role in wanted:
            result.setdefault(row.canonical_role, []).extend(row.ordered_entity_ids)
    return result


def _chain_entities(ir: dict[str, Any], group: str) -> list[dict[str, Any]]:
    entity_by_id = _entity_by_id(ir)
    ids_by_role = _resolved_ids(ir, group)
    return [entity_by_id[entity_id] for ids in ids_by_role.values() for entity_id in ids if entity_id in entity_by_id]


def _topology_score(host_roles: set[str], donor_roles: set[str]) -> float:
    if not host_roles and not donor_roles:
        return 1.0
    union = host_roles | donor_roles
    return len(host_roles & donor_roles) / max(len(union), 1)


def _quality_score(ir: dict[str, Any], group: str) -> float:
    rows = [row for row in resolve_edge_chains(ir) if row.canonical_role in ROLE_GROUPS.get(group, set())]
    if not rows:
        return 0.0
    review_bonus = sum(1.0 if row.provenance.get("review") == "approved" else 0.55 for row in rows) / len(rows)
    ordered_bonus = sum(1.0 if row.ordered_entity_ids else 0.0 for row in rows) / len(rows)
    return max(0.0, min(1.0, review_bonus * 0.65 + ordered_bonus * 0.35))


def _normalize_family(category: str | None) -> str:
    value = str(category or "").lower()
    if value in {"shirt", "blouse"}:
        return "shirt"
    return "tshirt"


def _piece_role_map(ir: dict[str, Any]) -> dict[str, str]:
    return {
        str(piece.get("piece_id") or ""): str(piece.get("piece_role") or "unknown")
        for piece in ir.get("piece_instances") or []
    }


def _entities_by_piece(ir: dict[str, Any], roles: set[str]) -> dict[str, list[dict[str, Any]]]:
    role_map = _piece_role_map(ir)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entity in ir.get("atomic_entities") or []:
        piece_id = str(entity.get("piece_id") or "")
        role = role_map.get(piece_id) or str(entity.get("piece_role") or "unknown")
        if role not in roles:
            continue
        pts = (entity.get("geometry") or {}).get("points") or []
        if len(pts) < 2:
            continue
        grouped.setdefault(f"{role}::{piece_id}", []).append(entity)
    return grouped


def _primary_piece_bounds(ir: dict[str, Any], roles: set[str]) -> tuple[float, float, float, float] | None:
    best = None
    best_area = -1.0
    for rows in _entities_by_piece(ir, roles).values():
        box = _bounds(rows)
        if not box:
            continue
        area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        if area > best_area:
            best_area = area
            best = box
    return best


def _piece_fit_score(host_ir: dict[str, Any], donor_ir: dict[str, Any], group: str) -> float:
    """How well donor cut-pieces match host proportions for this swap group."""
    roles = PIECE_ROLES_BY_GROUP.get(group) or set()
    if not roles:
        return 0.0
    host_roles = {
        str(piece.get("piece_role") or "")
        for piece in host_ir.get("piece_instances") or []
    }
    donor_roles = {
        str(piece.get("piece_role") or "")
        for piece in donor_ir.get("piece_instances") or []
    }
    coverage = len((host_roles & roles) & (donor_roles & roles)) / max(len(host_roles & roles), 1)
    host_box = _primary_piece_bounds(host_ir, roles)
    donor_box = _primary_piece_bounds(donor_ir, roles)
    if not host_box or not donor_box:
        return max(0.0, min(1.0, coverage * 0.55))
    hw = max(host_box[2] - host_box[0], 1e-6)
    hh = max(host_box[3] - host_box[1], 1e-6)
    dw = max(donor_box[2] - donor_box[0], 1e-6)
    dh = max(donor_box[3] - donor_box[1], 1e-6)
    aspect = _ratio_similarity(hw / hh, dw / dh)
    area = _ratio_similarity(hw * hh, dw * dh)
    return max(0.0, min(1.0, coverage * 0.35 + aspect * 0.40 + area * 0.25))


def score_piece_to_host(
    host_entities: list[dict[str, Any]],
    donor_entities: list[dict[str, Any]],
) -> float:
    """Similarity of one donor piece component to the host piece of the same role."""
    host_box = _bounds(host_entities)
    donor_box = _bounds(donor_entities)
    if not host_box or not donor_box:
        return 0.0
    hw = max(host_box[2] - host_box[0], 1e-6)
    hh = max(host_box[3] - host_box[1], 1e-6)
    dw = max(donor_box[2] - donor_box[0], 1e-6)
    dh = max(donor_box[3] - donor_box[1], 1e-6)
    aspect = _ratio_similarity(hw / hh, dw / dh)
    area = _ratio_similarity(hw * hh, dw * dh)
    # Prefer pieces with enough outline entities (not a lone scrap).
    dens = min(1.0, len(donor_entities) / max(len(host_entities), 1))
    return max(0.0, min(1.0, aspect * 0.45 + area * 0.40 + dens * 0.15))


def score_donor(group: str, host_ir: dict[str, Any], donor_ir: dict[str, Any]) -> DonorScore:
    host_ids = _resolved_ids(host_ir, group)
    donor_ids = _resolved_ids(donor_ir, group)
    host_entities = _chain_entities(host_ir, group)
    donor_entities = _chain_entities(donor_ir, group)
    host_length = sum(_length(entity) for entity in host_entities)
    donor_length = sum(_length(entity) for entity in donor_entities)
    interface = _ratio_similarity(host_length, donor_length) if host_entities and donor_entities else 0.0
    topology = _topology_score(set(host_ids), set(donor_ids))
    host_bounds = _bounds(host_entities)
    donor_bounds = _bounds(donor_entities)
    if host_bounds and donor_bounds:
        host_w = max(host_bounds[2] - host_bounds[0], 1e-6)
        host_h = max(host_bounds[3] - host_bounds[1], 1e-6)
        donor_w = max(donor_bounds[2] - donor_bounds[0], 1e-6)
        donor_h = max(donor_bounds[3] - donor_bounds[1], 1e-6)
        proportion = _ratio_similarity(host_w / host_h, donor_w / donor_h)
    else:
        proportion = 0.0
    quality = _quality_score(donor_ir, group)
    piece_fit = _piece_fit_score(host_ir, donor_ir, group)
    host_family = _normalize_family((host_ir.get("design_semantics") or {}).get("category"))
    donor_family = _normalize_family((donor_ir.get("design_semantics") or {}).get("category"))
    family = 1.0 if host_family == donor_family else 0.15
    breakdown = {
        "interface": round(interface, 5),
        "topology": round(topology, 5),
        "proportion": round(proportion, 5),
        "quality": round(quality, 5),
        "piece_fit": round(piece_fit, 5),
        "family": round(family, 5),
    }
    # When edge chains are missing, piece_fit still ranks usable cut-piece donors.
    score = (
        0.28 * interface
        + 0.16 * topology
        + 0.14 * proportion
        + 0.10 * quality
        + 0.24 * piece_fit
        + 0.08 * family
    )
    reasons = (
        f"interface={breakdown['interface']}",
        f"topology={breakdown['topology']}",
        f"proportion={breakdown['proportion']}",
        f"quality={breakdown['quality']}",
        f"piece_fit={breakdown['piece_fit']}",
        f"family={breakdown['family']}",
    )
    return DonorScore(str(donor_ir.get("case_id") or "unknown"), round(score, 5), breakdown, reasons)


def _target_gender(ir: dict[str, Any]) -> str:
    value = str((ir.get("design_semantics") or {}).get("target_gender") or "unknown").lower()
    return value if value else "unknown"


def _gender_compatible(host_ir: dict[str, Any], donor_ir: dict[str, Any]) -> bool:
    host = _target_gender(host_ir)
    donor = _target_gender(donor_ir)
    if host == donor:
        return True
    # unknown / unisex may pair with either; never mix explicit female ↔ male.
    if "unknown" in {host, donor} or "unisex" in {host, donor}:
        return not ({host, donor} == {"female", "male"})
    return False


def _has_swap_pieces(ir: dict[str, Any], group: str) -> bool:
    wanted = PIECE_ROLES_BY_GROUP.get(group) or set()
    roles = {str(piece.get("piece_role") or "") for piece in ir.get("piece_instances") or []}
    return bool(roles & wanted)


def _eligible_donors(
    group: str,
    host_ir: dict[str, Any],
    donor_index: dict[str, dict[str, Any]],
    wanted_slug: str | None,
) -> list[dict[str, Any]]:
    host_family = _normalize_family((host_ir.get("design_semantics") or {}).get("category"))
    out: list[dict[str, Any]] = []
    for donor in donor_index.values():
        if _normalize_family((donor.get("design_semantics") or {}).get("category")) != host_family:
            continue
        if not _gender_compatible(host_ir, donor):
            continue
        if not _has_swap_pieces(donor, group):
            continue
        if wanted_slug:
            slug = part_label_slug(donor, group)
            if slug in {None, "", "unknown", "non_composable"}:
                continue
            if slug != wanted_slug:
                continue
        out.append(donor)
    return out


def rank_donors(group: str, host_ir: dict[str, Any], donor_index: dict[str, dict[str, Any]], max_donors: int = 3, target_option_id: str | None = None, target_slug: str | None = None) -> list[DonorScore]:
    wanted_slug = target_slug or _slug_from_option_id(target_option_id)
    candidates = _eligible_donors(group, host_ir, donor_index, wanted_slug)
    rows = []
    for donor in candidates:
        scored = score_donor(group, host_ir, donor)
        slug = part_label_slug(donor, group)
        breakdown = dict(scored.breakdown)
        breakdown["label_match"] = 1.0 if wanted_slug and slug == wanted_slug else 0.0
        breakdown["gender"] = _target_gender(donor)
        reasons = tuple(scored.reasons) + ((f"label={slug}" if slug else "label=missing"), f"gender={breakdown['gender']}")
        score = scored.score + (0.30 if wanted_slug and slug == wanted_slug else 0.0)
        if score <= 0 and float(breakdown.get("piece_fit") or 0.0) > 0.2:
            score = 0.05 + float(breakdown["piece_fit"]) * 0.2
        rows.append(DonorScore(scored.case_id, round(score, 5), breakdown, reasons))
    rows = [row for row in rows if row.score > 0]
    rows.sort(key=lambda row: (-row.score, row.case_id))
    return rows[:max_donors]
