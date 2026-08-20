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

    def test_c2530029_keeps_source_front_yokes(self) -> None:
        from dxf_closed_cuts import guess_role

        self.assertEqual("front_yoke", guess_role("C2530029.左前上_面料_S", family="shirt"))
        self.assertEqual("front_yoke", guess_role("C2530029.右前上_面料_S", family="shirt"))
        self.assertEqual("front_left", guess_role("C2530029.左前下_面料_S", family="shirt"))
        self.assertEqual("front_right", guess_role("C2530029.右前下_面料_S", family="shirt"))
        doc = load_compose("C2530029")
        names = [p["cad_name"] for p in doc["pieces"] if p["piece_role"] == "front_yoke"]
        self.assertEqual(2, len(names))
        self.assertTrue(any("左前上" in name for name in names))
        self.assertTrue(any("右前上" in name for name in names))
        self.assertTrue(any(p["piece_role"] == "front_left" and "左前下" in p["cad_name"] for p in doc["pieces"]))
        self.assertTrue(any(p["piece_role"] == "front_right" and "右前下" in p["cad_name"] for p in doc["pieces"]))

    def test_cad_names_keep_yokes_and_split_panels(self) -> None:
        from relabel_queue import shirt_yoke_queue

        self.assertEqual(31, len(shirt_yoke_queue()))
        c0093 = load_compose("C2530093")
        self.assertTrue(any(p["piece_role"] == "back_yoke" and "复势" in p["cad_name"] for p in c0093["pieces"]))
        c0423 = load_compose("C2530423")
        self.assertTrue(any(p["piece_role"] == "back_yoke" and "复势" in p["cad_name"] for p in c0423["pieces"]))
        doc = load_compose("C2431055")
        names = [(p["piece_role"], p["cad_name"].split(".")[-1]) for p in doc["pieces"]]
        self.assertTrue(any(role == "front_body" and name.startswith("前片_面") for role, name in names))
        self.assertTrue(any(role == "front_body" and name.startswith("前下片_面") for role, name in names))
        self.assertTrue(any(role == "back_body" and name.startswith("后中_面") for role, name in names))
        self.assertTrue(any(role == "back_body" and name.startswith("后下片_面") for role, name in names))
        self.assertFalse(any("里料" in name for _, name in names))

    def test_short_back_center_promotes_to_yoke_when_lower_exists(self) -> None:
        from compose_ir import _promote_short_back_yokes

        lower = {"piece_role": "back_body", "cad_name": "X.后下片_面A_38", "cut": {"closed": True, "points": [[0, 0], [300, 0], [300, 500], [0, 500], [0, 0]]}}
        short = {"piece_role": "back_body", "cad_name": "X.后中_面A_38", "cut": {"closed": True, "points": [[0, 0], [200, 0], [200, 180], [0, 180], [0, 0]]}}
        tall = {"piece_role": "back_body", "cad_name": "X.后中_面A_38", "cut": {"closed": True, "points": [[0, 0], [200, 0], [200, 450], [0, 450], [0, 0]]}}
        promoted = _promote_short_back_yokes([lower, short])
        self.assertEqual("back_yoke", next(p["piece_role"] for p in promoted if "后中" in p["cad_name"]))
        self.assertEqual("back_body", next(p["piece_role"] for p in promoted if "后下" in p["cad_name"]))
        kept = _promote_short_back_yokes([lower, tall])
        self.assertEqual("back_body", next(p["piece_role"] for p in kept if "后中" in p["cad_name"]))

    def test_c2431055_collar_swap_takes_side_panels(self) -> None:
        from composition_engine import compose_recipe, build_index, pattern_catalog

        index = build_index(ROOT / "data/ir/v1_rule_ready", None, ROOT / "data/ir/shirt_v2/pattern_ir")
        catalog = pattern_catalog(ROOT / "packages/catalogs/src/pattern-options.v1.json", index)
        base = {
            "silhouette": "shirt.silhouette.oversized",
            "collar": "shirt.collar.open-v-pointed",
            "placket": "shirt.placket.full",
            "sleeve": "shirt.sleeve.regular",
            "cuff": "shirt.cuff.regular",
        }
        ents, meta = compose_recipe(
            {
                "family": "shirt", "sex": "female", "base_case_id": "C2431055",
                "measurements_cm": {"height": 160, "chest": 84, "waist": 66, "shoulder": 38, "neck": 34, "sleeveLength": 58},
                "fit": "regular", "ease_cm": 8, "skip_grading": True,
                "selections": {**base, "collar": "shirt.collar.stand"},
                "base_option_ids": base,
            },
            index,
            catalog,
        )
        pieces = meta["pieces"]
        fronts = [p for p in pieces if p["role"] == "front_body"]
        sides = [p for p in pieces if p["role"] == "side_panel"]
        self.assertTrue(fronts)
        donor = fronts[0]["source_case_id"]
        self.assertNotEqual("C2431055", donor)
        self.assertTrue(all(p["source_case_id"] == donor for p in sides))
        self.assertFalse(any(p["role"] == "side_panel" and p["source_case_id"] == "C2431055" for p in pieces))

    def test_c2530029_puff_keeps_host_neckline(self) -> None:
        from composition_engine import compose_recipe, build_index, pattern_catalog
        from simple_compose import _role

        def _cut_dip(ents, role):
            best = 0.0
            for entity in ents:
                if _role(entity) != role or entity.get("line_role") != "cut":
                    continue
                pts = (entity.get("geometry") or {}).get("points") or []
                if len(pts) < 8:
                    continue
                ys = [p[1] for p in pts]
                h = max(ys) - min(ys)
                maxy = max(ys)
                band = [p for p in pts if p[1] >= maxy - 0.18 * max(h, 1)]
                if band:
                    best = max(best, (maxy - min(p[1] for p in band)) / max(h, 1))
            return best

        index = build_index(ROOT / "data/ir/v1_rule_ready", None, ROOT / "data/ir/shirt_v2/pattern_ir")
        catalog = pattern_catalog(ROOT / "packages/catalogs/src/pattern-options.v1.json", index)
        base = {
            "silhouette": "shirt.silhouette.oversized",
            "collar": "shirt.collar.open-v-pointed",
            "placket": "shirt.placket.full",
            "sleeve": "shirt.sleeve.regular",
            "cuff": "shirt.cuff.regular",
        }
        recipe = {
            "family": "shirt", "sex": "female", "base_case_id": "C2530029",
            "measurements_cm": {"height": 160, "chest": 84, "waist": 66, "shoulder": 38, "neck": 34, "sleeveLength": 58},
            "fit": "regular", "ease_cm": 8, "skip_grading": True,
            "selections": {**base, "sleeve": "shirt.sleeve.puff"},
            "base_option_ids": base,
        }
        ents, meta = compose_recipe(recipe, index, catalog)
        roles = {p["role"] for p in meta["pieces"]}
        self.assertIn("back_yoke", roles)
        self.assertTrue(roles & FRONT_LIKE)
        self.assertGreaterEqual(_cut_dip(ents, "front_left"), 0.12)
        self.assertGreaterEqual(_cut_dip(ents, "front_right"), 0.12)
        self.assertEqual("C2530029", next(p["source_case_id"] for p in meta["pieces"] if p["role"] == "front_left"))
        self.assertTrue(any(p["role"] == "sleeve_placket" and "C2530029" in str(p.get("source_case_id") or "") for p in meta["pieces"]))

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
