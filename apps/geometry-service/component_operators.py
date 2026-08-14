"""Compatibility boundary for finite component operators.

Operators are implemented inside batch_executor.py so they can share rollback
state and provenance. This module gives the documented boundary a stable import.
"""
from __future__ import annotations

from batch_executor import execute_batch_preview

__all__ = ["execute_batch_preview"]
