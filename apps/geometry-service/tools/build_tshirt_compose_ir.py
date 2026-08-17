#!/usr/bin/env python3
"""Build pattern_ir_compose for every tshirt_v2 case from original DXF closed cuts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compose_ir import COMPOSE_DIR, tshirt_case_ids, validate, write_one  # noqa: E402


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else COMPOSE_DIR
    ok, failed, skipped = [], [], []
    for case_id in tshirt_case_ids():
        try:
            doc = write_one(case_id, out_dir)
        except FileNotFoundError as exc:
            skipped.append(f"{case_id}: {exc}")
            continue
        except Exception as exc:
            failed.append(f"{case_id}: {exc}")
            continue
        roles = [p["piece_role"] for p in doc["pieces"]]
        ok.append((case_id, roles, doc["source_dxf"]))
    report = {
        "ok": len(ok),
        "failed": failed,
        "skipped": skipped,
        "cases": [
            {
                "case_id": cid,
                "roles": roles,
                "source_dxf": src,
                "has_front": "front_body" in roles,
                "has_back": "back_body" in roles,
            }
            for cid, roles, src in ok
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    missing_body = [row["case_id"] for row in report["cases"] if not (row["has_front"] and row["has_back"])]
    if failed or skipped or missing_body:
        print("FAILED", failed, file=sys.stderr)
        print("SKIPPED", skipped, file=sys.stderr)
        print("NO_FRONT_BACK", missing_body, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # re-validate written files
    code = main()
    extra = []
    for path in sorted(COMPOSE_DIR.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        errs = validate(doc)
        if errs:
            extra.append(f"{path.stem}: {errs}")
    if extra:
        print("VALIDATE", extra, file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(code)
