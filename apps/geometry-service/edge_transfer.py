"""Small protected-entity helpers for edge-role transfer audits."""
from __future__ import annotations

from typing import Any

from batch_executor import entity_hash


def protected_hashes(entities: list[dict[str, Any]], mutable_entity_ids: set[str]) -> dict[str, str]:
    return {
        str(entity.get("entity_id")): entity_hash(entity)
        for entity in entities
        if entity.get("entity_id") and str(entity.get("entity_id")) not in mutable_entity_ids
    }


__all__ = ["protected_hashes"]
