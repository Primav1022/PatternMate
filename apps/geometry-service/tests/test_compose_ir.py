from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from compose_ir import (  # noqa: E402
    COMPOSE_DIR,
    entities_from_compose,
    load_compose,
    tshirt_case_ids,
    validate,
)
from composition_engine import bounds_of_entities, entity_points, reshape_body_neckline  # noqa: E402
from simple_compose import _annotate  # noqa: E402


def _geom_hash(entity: dict) -> str:
    raw = json.dumps(entity.get("geometry"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ComposeIrTests(unittest.TestCase):
    def test_all_62_have_closed_front_and_back(self) -> None:
        ids = tshirt_case_ids()
        self.assertEqual(62, len(ids))
        missing = []
        for case_id in ids:
            path = COMPOSE_DIR / f"{case_id}.json"
            if not path.exists():
                missing.append(f"{case_id}: missing file")
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            errors = validate(doc)
            roles = [p["piece_role"] for p in doc["pieces"]]
            if "front_body" not in roles or "back_body" not in roles:
                errors.append("need front_body and back_body")
            if not doc.get("source_dxf"):
                errors.append("no source_dxf")
            if errors:
                missing.append(f"{case_id}: {errors}")
        self.assertEqual([], missing)

    def test_no_shared_cut_across_pieces(self) -> None:
        for path in sorted(COMPOSE_DIR.glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            keys = []
            for piece in doc["pieces"]:
                pts = (piece.get("cut") or {}).get("points") or []
                keys.append(tuple((round(p[0] * 100), round(p[1] * 100)) for p in pts[:12]))
            self.assertEqual(len(keys), len(set(keys)), path.stem)

    def test_c2590045_is_five_closed_panels(self) -> None:
        doc = load_compose("C2590045")
        self.assertIsNotNone(doc)
        roles = [p["piece_role"] for p in doc["pieces"]]
        self.assertEqual(roles.count("front_body"), 1)
        self.assertEqual(roles.count("back_body"), 1)
        self.assertEqual(roles.count("side_panel"), 2)
        self.assertGreaterEqual(roles.count("neck_binding"), 1)
        ents = entities_from_compose(doc)
        cuts = [e for e in ents if e.get("line_role") == "cut"]
        self.assertEqual(5, len(cuts))
        self.assertTrue(all((e.get("geometry") or {}).get("closed") for e in cuts))
        from composition_engine import build_index, compose_recipe, pattern_catalog
        index = build_index(ROOT / "data/ir/v1_rule_ready", ROOT / "data/ir/tshirt_v2/pattern_ir")
        catalog = pattern_catalog(ROOT / "packages/catalogs/src/pattern-options.v1.json", index)
        preview, _meta = compose_recipe(
            {
                "family": "tshirt", "sex": "female", "base_case_id": "C2590045",
                "measurements_cm": {"height": 160, "chest": 84, "waist": 68, "shoulder": 39, "neck": 34, "sleeveLength": 58, "upperArm": 28},
                "selections": {}, "base_option_ids": {},
            },
            index,
            catalog,
        )
        preview_cuts = {
            str(e.get("piece_id"))
            for e in preview
            if e.get("line_role") == "cut" and (e.get("source") or {}).get("origin") == "compose_ir"
        }
        self.assertEqual(5, len(preview_cuts))

    def test_c2590734_has_front_back_and_binding(self) -> None:
        doc = load_compose("C2590734")
        roles = {p["piece_role"] for p in doc["pieces"]}
        self.assertTrue({"front_body", "back_body", "neck_binding"} <= roles)

    def test_annotate_prefers_compose_ir(self) -> None:
        ir = json.loads((ROOT / "data/ir/tshirt_v2/pattern_ir/C2390279.pattern-ir.json").read_text(encoding="utf-8"))
        ents = _annotate(ir)
        self.assertTrue(ents)
        self.assertTrue(all((e.get("source") or {}).get("origin") == "compose_ir" for e in ents))
        self.assertTrue(any(e.get("line_role") == "cut" and e.get("_piece_role") == "front_body" for e in ents))
        self.assertTrue(any(e.get("line_role") in {"sew", "internal", "grainline"} for e in ents))

    def test_c2390279_vneck_changes_front_cut_not_sleeve(self) -> None:
        ir = json.loads((ROOT / "data/ir/tshirt_v2/pattern_ir/C2390279.pattern-ir.json").read_text(encoding="utf-8"))
        ents = _annotate(ir)
        sleeve_before = {
            str(e.get("entity_id")): _geom_hash(e)
            for e in ents
            if e.get("_piece_role") == "sleeve"
        }
        front = next(
            e for e in ents
            if e.get("_piece_role") == "front_body" and e.get("line_role") == "cut"
        )
        crew_pts = entity_points(front)
        crew_box = bounds_of_entities([front])
        after, meta = reshape_body_neckline(ents, {**ir, "atomic_entities": ents}, "v-neck")
        self.assertTrue(meta["applied"])
        self.assertEqual("compose_cut_span", meta.get("mode"))
        self.assertTrue(all(row["piece_role"] in {"front_body", "back_body"} for row in meta["chains"]))
        sleeve_after = {
            str(e.get("entity_id")): _geom_hash(e)
            for e in after
            if e.get("_piece_role") == "sleeve"
        }
        self.assertEqual(sleeve_before, sleeve_after)
        front_after = next(
            e for e in after
            if e.get("entity_id") == front.get("entity_id") and e.get("piece_id") == front.get("piece_id")
        )
        v_pts = entity_points(front_after)
        self.assertNotEqual(crew_pts, v_pts)
        cx = (crew_box[0] + crew_box[2]) / 2.0
        band = crew_box[1] + (crew_box[3] - crew_box[1]) * 0.6
        window = (crew_box[2] - crew_box[0]) * 0.12

        def dip(points: list) -> float:
            rows = [p[1] for p in points if abs(p[0] - cx) < window and p[1] >= band]
            return min(rows)

        self.assertLess(dip(v_pts), dip(crew_pts) - 20.0)

    def test_c2490681_split_front_v_and_boat(self) -> None:
        ir = json.loads((ROOT / "data/ir/tshirt_v2/pattern_ir/C2490681.pattern-ir.json").read_text(encoding="utf-8"))
        ents = _annotate(ir)
        fronts = [e for e in ents if e.get("_piece_role") == "front_body" and e.get("line_role") == "cut"]
        self.assertEqual(2, len(fronts))
        back_before = {
            str(e.get("entity_id")): _geom_hash(e)
            for e in ents
            if e.get("_piece_role") == "back_body"
        }
        crew, crew_meta = reshape_body_neckline(ents, ir, "crew")
        self.assertFalse(crew_meta["applied"])
        self.assertEqual(back_before, {
            str(e.get("entity_id")): _geom_hash(e)
            for e in crew
            if e.get("_piece_role") == "back_body"
        })
        v_ents, v_meta = reshape_body_neckline(ents, ir, "v-neck")
        self.assertTrue(v_meta["applied"])
        self.assertEqual(2, len(v_meta["chains"]))
        for front in fronts:
            after = next(
                e for e in v_ents
                if e.get("entity_id") == front.get("entity_id") and e.get("piece_id") == front.get("piece_id")
            )
            self.assertLess(len(entity_points(after)), len(entity_points(front)))
            self.assertNotEqual(entity_points(front), entity_points(after))
        self.assertEqual(back_before, {
            str(e.get("entity_id")): _geom_hash(e)
            for e in v_ents
            if e.get("_piece_role") == "back_body"
        })
        boat_ents, boat_meta = reshape_body_neckline(ents, ir, "boat")
        self.assertTrue(boat_meta["applied"])
        for front in fronts:
            after = next(
                e for e in boat_ents
                if e.get("entity_id") == front.get("entity_id") and e.get("piece_id") == front.get("piece_id")
            )
            pts = entity_points(after)
            self.assertNotEqual(entity_points(front), pts)
            top = max(p[1] for p in pts)
            top_pts = [p for p in pts if abs(p[1] - top) <= 2.0]
            self.assertGreaterEqual(len(top_pts), 2)
            width = max(p[0] for p in pts) - min(p[0] for p in pts)
            self.assertGreater(max(p[0] for p in top_pts) - min(p[0] for p in top_pts), width * 0.35)


if __name__ == "__main__":
    unittest.main()
