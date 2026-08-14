from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_DECISIONS = {"human_accepted", "human_rejected", "human_modified"}


def _safe_name(value: str) -> str:
    safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
    return safe or "unknown"


def ledger_path(root: Path, recipe_hash: str) -> Path:
    return root / ".run" / "review-ledgers" / f"{_safe_name(recipe_hash)}.jsonl"


def append_review_decision(
    root: Path,
    *,
    recipe_hash: str,
    operation_id: str,
    decision: str,
    reviewer: str = "anonymous",
    note: str = "",
    geometry_hash_before: str | None = None,
    geometry_hash_after: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"unsupported review decision: {decision}")
    record = {
        "schema": "chi27.review-decision.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "recipe_hash": recipe_hash,
        "operation_id": operation_id,
        "decision": decision,
        "reviewer": reviewer or "anonymous",
        "note": note or "",
        "geometry_hash_before": geometry_hash_before,
        "geometry_hash_after": geometry_hash_after,
        "extra": extra or {},
    }
    path = ledger_path(root, recipe_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    return record


def read_review_history(root: Path, recipe_hash: str) -> list[dict[str, Any]]:
    path = ledger_path(root, recipe_hash)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records
