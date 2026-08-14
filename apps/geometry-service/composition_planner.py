"""Compatibility boundary for the edge-role batch planner.

The implementation lives in batch_planner.py; this module preserves the
name used by the design/plan documents for future maintainers.
"""
from __future__ import annotations

from batch_planner import OPERATION_ORDER, RULES, build_composition_plan

__all__ = ["OPERATION_ORDER", "RULES", "build_composition_plan"]
