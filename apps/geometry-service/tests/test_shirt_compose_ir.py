from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from compose_ir import (  # noqa: E402
    BACK_LIKE,
    FRONT_LIKE,
    SHIRT_COMPOSE_DIR,
    entities_from_compose,
    load_compose,
    shirt_case_ids,
    validate,
)
from simple_compose import _annotate  # noqa: E402


class ShirtComposeIrTests(unittest.TestCase):
    def test_all_31_have_closed_front_and_back(self) -> None:
        ids = shirt_case_ids()
        self.assertEqual(31, len(ids))
        missing = []
        for case_id in ids:
            path = SHIRT_COMPOSE_DIR / f"{case_id}.json"
            if not path.exists():
                missing.append(f"{case_id}: missing file")
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            errors = validate(doc)
            roles = {p["piece_role"] for p in doc["pieces"]}
            if not (roles & FRONT_LIKE) or not (roles & BACK_LIKE):
                errors.append("need front and back")
            if errors:
                missing.append(f"{case_id}: {errors}")
        self.assertEqual([], missing)

    def test_c2530694_has_collar_stand_and_cuff(self) -> None:
        doc = load_compose("C2530694")
        self.assertIsNotNone(doc)
        roles = {p["piece_role"] for p in doc["pieces"]}
        self.assertTrue(roles & FRONT_LIKE)
        self.assertTrue(roles & BACK_LIKE)
        self.assertIn("collar", roles)
        self.assertIn("collar_stand", roles)
        self.assertIn("cuff", roles)
        ents = entities_from_compose(doc)
        self.assertTrue(all((e.get("geometry") or {}).get("closed") for e in ents if e.get("line_role") == "cut"))
        body = next(p for p in doc["pieces"] if p["piece_role"] in FRONT_LIKE)
        self.assertTrue(body.get("sew") or body.get("lines"))
        self.assertTrue(any(e.get("line_role") in {"sew", "internal", "grainline"} for e in ents))

    def test_annotate_shirt_uses_compose_ir(self) -> None:
        ir = json.loads((ROOT / "data/ir/shirt_v2/pattern_ir/C2530694.pattern-ir.json").read_text(encoding="utf-8"))
        ents = _annotate(ir)
        self.assertTrue(ents)
        self.assertTrue(all((e.get("source") or {}).get("origin") == "compose_ir" for e in ents))

    def test_c2530642_keeps_tie_face_not_facing(self) -> None:
        doc = load_compose("C2530642")
        names = {p["piece_role"]: p["cad_name"] for p in doc["pieces"]}
        self.assertIn("领带_修剪", names["neck_binding"])
        self.assertNotIn("别布", names["neck_binding"])
        cut = next(p for p in doc["pieces"] if p["piece_role"] == "neck_binding")["cut"]["points"]
        xs = [p[0] for p in cut]
        ys = [p[1] for p in cut]
        self.assertGreater(max(xs) - min(xs), 1000)
        self.assertLess(max(ys) - min(ys), 250)

    def test_c2530642_host_puff_sleeve_not_crushed(self) -> None:
        from shirt_compose import compose_shirt
        from simple_compose import _role, _group_by_piece, bounds_of_entities

        ir = json.loads((ROOT / "data/ir/shirt_v2/pattern_ir/C2530642.pattern-ir.json").read_text(encoding="utf-8"))
        recipe = {
            "family": "shirt", "sex": "female", "base_case_id": "C2530642",
            "measurements_cm": {"height": 160, "chest": 84, "waist": 66, "shoulder": 38, "neck": 34, "sleeveLength": 58},
            "fit": "regular", "ease_cm": 8, "selections": {}, "base_option_ids": {},
        }
        ents, meta = compose_shirt(recipe, {"C2530642": ir}, {"options": []})
        self.assertEqual("host_sleeve_kept", (meta.get("sources") or {}).get("sizing", {}).get("sleeve_armhole_fit", {}).get("reason"))
        sleeve = next(rows for pid, rows in _group_by_piece(ents).items() if _role(rows[0]) == "sleeve")
        box = bounds_of_entities(sleeve)
        self.assertGreater(box[3] - box[1], 500)

    def test_c2530900_keeps_front_opening(self) -> None:
        from composition_engine import filter_preview_entities

        doc = load_compose("C2530900")
        out = filter_preview_entities(entities_from_compose(doc))
        front = [e for e in out if e.get("_piece_role") == "front_body"]
        roles = {e.get("line_role") for e in front}
        self.assertTrue(roles & {"center_front", "placket_line"})


if __name__ == "__main__":
    unittest.main()
