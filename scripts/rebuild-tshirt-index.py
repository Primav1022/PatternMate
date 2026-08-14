from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE_RE = re.compile(r"C\d+", re.IGNORECASE)


def priority(path: Path) -> tuple[int, int, float, str]:
    normalized = str(path).replace("\\", "/").lower()
    return (
        0 if "annotated" in path.name.lower() else 1,
        0 if "/data/seed/dxf/" in normalized else 1,
        -path.stat().st_mtime,
        normalized,
    )


def dxf_candidates() -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for path in ROOT.rglob("*.dxf"):
        match = CASE_RE.search(path.name)
        if match:
            result.setdefault(match.group(0).upper(), []).append(path)
    return result


def build_dataset_index(family: str, dxfs: dict[str, list[Path]]) -> None:
    dataset_root = ROOT / "data" / "ir" / f"{family}_v2"
    ir_root = dataset_root / "pattern_ir"
    output = dataset_root / "index.json"
    rows = []
    for path in sorted(ir_root.glob("*.pattern-ir.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        case_id = str(data["case_id"]).upper()
        candidates = sorted(dxfs.get(case_id, []), key=priority)
        blocked = case_id == "C2690430"
        rows.append({
            "case_id": case_id,
            "ir": str(path.relative_to(ROOT)).replace("\\", "/"),
            "dxf": str(candidates[0].relative_to(ROOT)).replace("\\", "/") if candidates else None,
            "dxf_candidates": [str(candidate.relative_to(ROOT)).replace("\\", "/") for candidate in candidates],
            "dxf_conflict": len(candidates) > 1,
            "donor_allowed": bool(candidates) and not blocked,
            "data_status": "reference_only" if not candidates else "preview_ready" if blocked else "tryon_ready",
        })
    payload = {
        "version": f"{family}-v2",
        "count": len(rows),
        "matched": sum(bool(row["dxf"]) for row in rows),
        "missing_case_ids": [row["case_id"] for row in rows if not row["dxf"]],
        "rows": rows,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output.relative_to(ROOT)}: {payload['count']} cases, {payload['matched']} DXF matches")


dxfs = dxf_candidates()
for dataset in ("tshirt", "shirt"):
    build_dataset_index(dataset, dxfs)
