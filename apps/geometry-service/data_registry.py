from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


CASE_ID_RE = re.compile(r"C\d+", re.IGNORECASE)
BLOCKED_DONORS = {"C2690430", "C2490257"}  # C2490257: jagged/unusable host DXF


@dataclass(frozen=True)
class DxfMatch:
    case_id: str
    selected: Path | None
    candidates: tuple[Path, ...]

    @property
    def conflict(self) -> bool:
        return len(self.candidates) > 1


def case_id_from_name(path: Path) -> str | None:
    match = CASE_ID_RE.search(path.name)
    return match.group(0).upper() if match else None


def _dxf_priority(path: Path) -> tuple[int, int, float, str]:
    normalized = str(path).replace("\\", "/").lower()
    return (
        0 if "annotated" in path.name.lower() else 1,
        0 if "/data/seed/dxf/" in normalized else 1,
        -path.stat().st_mtime,
        normalized,
    )


def build_dxf_index(roots: Iterable[Path]) -> dict[str, DxfMatch]:
    candidates: dict[str, list[Path]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.dxf"):
            case_id = case_id_from_name(path)
            if case_id:
                candidates.setdefault(case_id, []).append(path.resolve())
    result: dict[str, DxfMatch] = {}
    for case_id, paths in candidates.items():
        ordered = tuple(sorted(set(paths), key=_dxf_priority))
        result[case_id] = DxfMatch(case_id, ordered[0] if ordered else None, ordered)
    return result


def data_status(case_id: str, has_dxf: bool, remix_ready: bool) -> str:
    if not has_dxf:
        return "reference_only"
    if case_id in BLOCKED_DONORS:
        return "preview_ready"
    return "tryon_ready" if remix_ready else "preview_ready"

