from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

LABEL_KEY_BY_GROUP = {
    "neckline": "neckline",
    "collar": "collar",
    "sleeve": "sleeve_style",
    "cuff": "cuff",
    "garment_length": "garment_length",
    "placket": "placket",
    "silhouette": "silhouette",
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

SEMANTIC_KEYS = ("fit", "silhouette")


@dataclass(frozen=True)
class DonorScore:
    case_id: str
    score: float
    breakdown: dict[str, Any]
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


def _normalize_family(category: str | None) -> str:
    value = str(category or "").lower()
    if value in {"shirt", "blouse"}:
        return "shirt"
    return "tshirt"


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
    dens = min(1.0, len(donor_entities) / max(len(host_entities), 1))
    return max(0.0, min(1.0, aspect * 0.45 + area * 0.40 + dens * 0.15))


def _target_gender(ir: dict[str, Any]) -> str:
    value = str((ir.get("design_semantics") or {}).get("target_gender") or "unknown").lower()
    return value if value else "unknown"


def _gender_compatible(host_ir: dict[str, Any], donor_ir: dict[str, Any]) -> bool:
    host = _target_gender(host_ir)
    donor = _target_gender(donor_ir)
    if host == donor:
        return True
    if "unknown" in {host, donor} or "unisex" in {host, donor}:
        return not ({host, donor} == {"female", "male"})
    return False


def _has_swap_pieces(ir: dict[str, Any], group: str) -> bool:
    wanted = PIECE_ROLES_BY_GROUP.get(group) or set()
    roles = {str(piece.get("piece_role") or "") for piece in ir.get("piece_instances") or []}
    return bool(roles & wanted)


def _usable_slug(value: str | None) -> bool:
    return bool(value) and value not in {"unknown", "non_composable"}


def _label_map(ir: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for group in LABEL_KEY_BY_GROUP:
        slug = part_label_slug(ir, group)
        if _usable_slug(slug):
            out[group] = str(slug)
    return out


def _field_hits(host_ir: dict[str, Any], donor_ir: dict[str, Any], skip_group: str) -> tuple[int, tuple[str, ...]]:
    """How many host fields the donor still shares, excluding the swapped group."""
    hits = 0
    reasons: list[str] = []
    host_sem = host_ir.get("design_semantics") or {}
    donor_sem = donor_ir.get("design_semantics") or {}
    for key in SEMANTIC_KEYS:
        host_val = str(host_sem.get(key) or "")
        donor_val = str(donor_sem.get(key) or "")
        if host_val and host_val not in {"unknown", ""} and host_val == donor_val:
            hits += 1
            reasons.append(f"{key}={host_val}")
    host_labels = _label_map(host_ir)
    donor_labels = _label_map(donor_ir)
    for group, slug in host_labels.items():
        if group == skip_group:
            continue
        if donor_labels.get(group) == slug:
            hits += 1
            reasons.append(f"{group}={slug}")
    return hits, tuple(reasons)


def _eligible_donors(
    group: str,
    host_ir: dict[str, Any],
    donor_index: dict[str, dict[str, Any]],
    wanted_slug: str | None,
    *,
    require_slug: bool,
) -> list[dict[str, Any]]:
    host_family = _normalize_family((host_ir.get("design_semantics") or {}).get("category"))
    host_id = str(host_ir.get("case_id") or "")
    out: list[dict[str, Any]] = []
    for donor in donor_index.values():
        if str(donor.get("case_id") or "") == host_id:
            continue
        if _normalize_family((donor.get("design_semantics") or {}).get("category")) != host_family:
            continue
        if not _gender_compatible(host_ir, donor):
            continue
        if not _has_swap_pieces(donor, group):
            continue
        if require_slug and wanted_slug:
            slug = part_label_slug(donor, group)
            if not _usable_slug(slug) or slug != wanted_slug:
                continue
        out.append(donor)
    return out


def rank_donors(
    group: str,
    host_ir: dict[str, Any],
    donor_index: dict[str, dict[str, Any]],
    max_donors: int = 3,
    target_option_id: str | None = None,
    target_slug: str | None = None,
) -> list[DonorScore]:
    wanted_slug = target_slug or _slug_from_option_id(target_option_id)
    candidates = _eligible_donors(group, host_ir, donor_index, wanted_slug, require_slug=True)
    if not candidates:
        candidates = _eligible_donors(group, host_ir, donor_index, wanted_slug, require_slug=False)
    rows: list[DonorScore] = []
    for donor in candidates:
        slug = part_label_slug(donor, group)
        hits, field_reasons = _field_hits(host_ir, donor, group)
        label_match = 1.0 if wanted_slug and slug == wanted_slug else 0.0
        score = 10.0 * label_match + hits
        breakdown = {
            "label_match": label_match,
            "field_hits": float(hits),
            "gender": _target_gender(donor),
        }
        reasons = (
            f"label={slug or 'missing'}",
            f"gender={breakdown['gender']}",
        ) + field_reasons
        rows.append(DonorScore(str(donor.get("case_id") or "unknown"), round(score, 5), breakdown, reasons))
    rows.sort(key=lambda row: (-row.score, row.case_id))
    return rows[:max_donors]


def score_donor(group: str, host_ir: dict[str, Any], donor_ir: dict[str, Any]) -> DonorScore:
    rows = rank_donors(group, host_ir, {str(donor_ir.get("case_id") or "unknown"): donor_ir}, max_donors=1)
    if rows:
        return rows[0]
    return DonorScore(str(donor_ir.get("case_id") or "unknown"), 0.0, {"label_match": 0.0, "field_hits": 0.0}, ("filtered_out",))
