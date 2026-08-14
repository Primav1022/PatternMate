"""Batch composition entrypoint used by the migration plan.

The public compose_recipe dispatcher remains in composition_engine.py for API
compatibility. This wrapper exposes the batch path under the planned module
name without duplicating geometry logic.
"""
from __future__ import annotations

from typing import Any

from composition_engine import compose_recipe


def compose_recipe_batch(recipe: dict[str, Any], index: dict[str, dict[str, Any]], catalog: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return compose_recipe({**recipe, "execution_mode": "batch_preview"}, index, catalog)


__all__ = ["compose_recipe_batch"]
