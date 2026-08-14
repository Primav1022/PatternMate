"""Component/edge-chain index for conservative component transfer."""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any

from edge_role_resolver import resolve_edge_chains


@dataclass(frozen=True)
class EdgeChainBundle:
    canonical_role: str
    piece_id: str
    piece_role: str
    edge_chain_ids: tuple[str, ...]
    entity_ids: tuple[str, ...]
    entities: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ComponentIndex:
    ir: dict[str, Any]
    entity_by_id: dict[str, dict[str, Any]]
    piece_role_by_id: dict[str, str]
    bundles_by_role: dict[str, tuple[EdgeChainBundle, ...]]


def _piece_role_by_id(ir: dict[str, Any]) -> dict[str, str]:
    return {
        str(piece.get("piece_id")): str(piece.get("piece_role") or "unknown")
        for piece in ir.get("piece_instances") or []
        if piece.get("piece_id")
    }


def build_component_index(ir: dict[str, Any]) -> ComponentIndex:
    entities = {str(entity.get("entity_id")): entity for entity in ir.get("atomic_entities") or [] if entity.get("entity_id")}
    piece_roles = _piece_role_by_id(ir)
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in resolve_edge_chains(ir):
        if row.status != "resolved" or not row.canonical_role:
            continue
        piece_role = row.piece_role or piece_roles.get(row.piece_id, "unknown")
        key = (row.canonical_role, row.piece_id, piece_role)
        bucket = grouped.setdefault(key, {"chain_ids": [], "entity_ids": []})
        bucket["chain_ids"].append(row.edge_chain_id)
        for eid in row.ordered_entity_ids:
            if eid not in bucket["entity_ids"]:
                bucket["entity_ids"].append(eid)
    by_role: dict[str, list[EdgeChainBundle]] = {}
    for (canonical_role, piece_id, piece_role), payload in grouped.items():
        entity_ids = tuple(eid for eid in payload["entity_ids"] if eid in entities)
        bundle = EdgeChainBundle(
            canonical_role=canonical_role,
            piece_id=piece_id,
            piece_role=piece_role,
            edge_chain_ids=tuple(payload["chain_ids"]),
            entity_ids=entity_ids,
            entities=tuple(deepcopy(entities[eid]) for eid in entity_ids),
        )
        by_role.setdefault(canonical_role, []).append(bundle)
    return ComponentIndex(
        ir=ir,
        entity_by_id=entities,
        piece_role_by_id=piece_roles,
        bundles_by_role={role: tuple(rows) for role, rows in by_role.items()},
    )


def extract_edge_chain_bundle(index: ComponentIndex, canonical_role: str, *, piece_role: str | None = None) -> EdgeChainBundle:
    candidates = list(index.bundles_by_role.get(canonical_role) or [])
    if piece_role is not None:
        candidates = [row for row in candidates if row.piece_role == piece_role]
    if not candidates:
        raise KeyError(f"missing edge-chain bundle: {canonical_role}")
    # Prefer the bundle with the richest explicit geometry.
    candidates.sort(key=lambda row: (len(row.entity_ids), len(row.edge_chain_ids)), reverse=True)
    return candidates[0]


__all__ = ["ComponentIndex", "EdgeChainBundle", "build_component_index", "extract_edge_chain_bundle"]
