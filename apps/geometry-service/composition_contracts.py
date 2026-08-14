from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ChainStatus = Literal["resolved", "ambiguous", "missing"]
ComponentStatus = Literal["applied", "retained_current", "invalid_base"]


@dataclass(frozen=True)
class ResolvedEdgeChain:
    edge_chain_id: str
    piece_id: str
    piece_role: str
    raw_role: str
    canonical_role: str | None
    ordered_entity_ids: tuple[str, ...]
    direction: str = "forward"
    status: ChainStatus = "resolved"
    confidence: float = 1.0
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanOperation:
    operation_id: str
    group: str
    option_id: str
    host_required_roles: tuple[str, ...] = ()
    donor_required_roles: tuple[str, ...] = ()
    mutable_roles: tuple[str, ...] = ()
    dependent_roles: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    max_donors: int = 3
    max_repair_rounds: int = 3


@dataclass(frozen=True)
class CompositionPlan:
    plan_id: str
    execution_mode: str
    operations: tuple[PlanOperation, ...]
    protected_entity_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: Literal["warning", "error"]
    message: str
    operation_id: str | None = None


@dataclass(frozen=True)
class ComponentResult:
    operation_id: str
    group: str
    status: ComponentStatus
    donor_case_id: str | None = None
    option_id: str | None = None
    modified_entity_ids: tuple[str, ...] = ()
    protected_entity_hashes: dict[str, str] = field(default_factory=dict)
    validation_issues: tuple[ValidationIssue, ...] = ()
    review_required: bool = True
    provenance: dict[str, Any] = field(default_factory=dict)
