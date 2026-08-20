from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from composition_engine import (  # noqa: E402
    CUFF_ROLES,
    _scale_complete_base,
    build_index,
    compose_recipe,
    grading_profile,
    pattern_catalog,
    reshape_body_neckline,
)
from dxf_export import write_entities_dxf  # noqa: E402
from tryon_descriptor import _triangulate  # noqa: E402


class CompositionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = build_index(ROOT / "data" / "ir" / "v1_rule_ready")
        cls.catalog = pattern_catalog(
            ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", cls.index
        )

    def defaults(self, family: str) -> dict[str, str | None]:
        groups = ["neckline", "sleeve", "special"] if family == "tshirt" else ["silhouette", "collar", "placket", "cuff", "sleeve"]
        result: dict[str, str | None] = {}
        for group in groups:
            result[group] = None if group == "special" else next(
                option["id"] for option in self.catalog["options"]
                if option["family"] == family and option["group"] == group
            )
        return result

    def recipe(self, family: str, selections: dict[str, str | None], sex: str = "female") -> dict:
        return {
            "family": family,
            "sex": sex,
            "base_case_id": "C2590529" if family == "tshirt" else "C2430144",
            "measurements_cm": {
                "height": 160, "chest": 84, "waist": 68, "shoulder": 39,
                "neck": 34, "sleeveLength": 58, "upperArm": 28,
            },
            "ease_cm": 8,
            "material_id": "test-material",
            "selections": selections,
        }

    def test_every_pattern_option_has_a_valid_trial_composition(self) -> None:
        failures = []
        for option in self.catalog["options"]:
            family = option["family"]
            selections = self.defaults(family)
            selections[option["group"]] = option["id"]
            try:
                entities, meta = compose_recipe(self.recipe(family, selections), self.index, self.catalog)
            except Exception as exc:  # collect all failures in one report
                failures.append(f"{option['id']}: {exc}")
                continue
            if not entities or not meta["validation"]["trial_ready"]:
                failures.append(f"{option['id']}: {meta['validation']['errors']}")
        self.assertEqual([], failures)

    def test_sizing_modes_and_recipe_hash_are_deterministic(self) -> None:
        selections = self.defaults("tshirt")
        _, female = compose_recipe(self.recipe("tshirt", selections), self.index, self.catalog)
        _, female_again = compose_recipe(self.recipe("tshirt", selections), self.index, self.catalog)
        _, male = compose_recipe(self.recipe("tshirt", selections, "male_general"), self.index, self.catalog)
        self.assertEqual(female["recipe_hash"], female_again["recipe_hash"])
        self.assertEqual("gbt_1335_2_2008_female", female["sizing_profile"]["mode"])
        self.assertEqual("male_general_trial", male["sizing_profile"]["mode"])
        self.assertEqual("mm", female["paper_info"]["unit"])
        self.assertGreater(female["paper_info"]["width_mm"], 0)
        self.assertGreater(female["paper_info"]["height_mm"], 0)
        self.assertTrue(all("width_mm" in piece and "height_mm" in piece for piece in female["pieces"]))
        descriptor = female["tryon_descriptor"]
        self.assertEqual(female["recipe_hash"], descriptor["recipe_hash"])
        self.assertEqual("patternmate.tryon.v2", descriptor["version"])
        self.assertGreaterEqual(descriptor["validation"]["triangulated_panel_count"], 1)
        self.assertTrue(any(panel["role"] == "front_body" for panel in descriptor["panels"]))

    def test_initial_reference_uses_only_body_fitted_base_dxf(self) -> None:
        base_options = {
            "tshirt": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in"},
            "shirt": {"silhouette": "shirt.silhouette.regular-fit", "placket": "shirt.placket.full", "cuff": "shirt.cuff.regular", "sleeve": "shirt.sleeve.regular"},
        }
        for family in ("tshirt", "shirt"):
            selections = {**{group: None for group in self.defaults(family)}, **base_options[family]}
            initial_recipe = self.recipe(family, selections)
            initial_recipe["base_option_ids"] = base_options[family]
            entities, meta = compose_recipe(initial_recipe, self.index, self.catalog)
            self.assertTrue(entities)
            self.assertEqual({"base": initial_recipe["base_case_id"]}, meta["sources"])
            self.assertFalse(any(isinstance(source, dict) for source in meta["sources"].values()))
            if family == "tshirt":
                self.assertTrue(meta["validation"]["trial_ready"], meta["validation"]["errors"])
            else:
                self.assertTrue(meta["validation"]["trial_ready"], meta["validation"]["errors"])
                self.assertIn("缺少衬衫领片", " ".join(meta["validation"]["warnings"]))

        small = self.recipe("tshirt", {**{group: None for group in self.defaults("tshirt")}, **base_options["tshirt"]})
        large = self.recipe("tshirt", {**{group: None for group in self.defaults("tshirt")}, **base_options["tshirt"]})
        small["base_option_ids"] = base_options["tshirt"]
        large["base_option_ids"] = base_options["tshirt"]
        small["measurements_cm"]["chest"] = 78
        large["measurements_cm"]["chest"] = 104
        _, small_meta = compose_recipe(small, self.index, self.catalog)
        _, large_meta = compose_recipe(large, self.index, self.catalog)
        small_front = max(piece["width_mm"] for piece in small_meta["pieces"] if piece["role"].startswith("front"))
        large_front = max(piece["width_mm"] for piece in large_meta["pieces"] if piece["role"].startswith("front"))
        self.assertGreater(large_front, small_front)

    def test_shirt_lapel_swap_passes_without_separate_collar(self) -> None:
        shirt_root = ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir"
        if not (shirt_root / "C2530694.pattern-ir.json").exists():
            self.skipTest("missing shirt v2 C2530694")
        index = build_index(ROOT / "data" / "ir" / "v1_rule_ready", None, shirt_root)
        catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", index)
        recipe = self.recipe("shirt", {
            "silhouette": "shirt.silhouette.fitted-x",
            "collar": "shirt.collar.casual-wide-lapel",
            "placket": "shirt.placket.half",
            "cuff": "shirt.cuff.regular",
            "sleeve": "shirt.sleeve.regular",
        })
        recipe["base_case_id"] = "C2530694"
        recipe["base_option_ids"] = {
            "collar": "shirt.collar.pointed",
            "placket": "shirt.placket.full",
            "silhouette": "shirt.silhouette.regular-fit",
        }
        _, meta = compose_recipe(recipe, index, catalog)
        errors = " ".join(meta["validation"]["errors"])
        self.assertNotIn("缺少衬衫领片", errors)
        self.assertNotIn("缺少可重绘的前后片领圈", errors)
        self.assertTrue(meta["validation"]["valid"], meta["validation"]["errors"])

    def test_shirt_bell_sleeve_is_not_a_strip(self) -> None:
        shirt_root = ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir"
        if not (shirt_root / "C2530694.pattern-ir.json").exists():
            self.skipTest("missing shirt v2 C2530694")
        index = build_index(ROOT / "data" / "ir" / "v1_rule_ready", None, shirt_root)
        catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", index)
        recipe = self.recipe("shirt", {
            "silhouette": "shirt.silhouette.fitted-x",
            "collar": "shirt.collar.bow-tie",
            "placket": "shirt.placket.half",
            "sleeve": "shirt.sleeve.bell",
            "cuff": "shirt.cuff.regular",
        })
        recipe["base_case_id"] = "C2530694"
        recipe["base_option_ids"] = {
            "collar": "shirt.collar.pointed",
            "placket": "shirt.placket.full",
            "silhouette": "shirt.silhouette.regular-fit",
            "sleeve": "shirt.sleeve.regular",
        }
        _, meta = compose_recipe(recipe, index, catalog)
        self.assertEqual("valid", meta["status"])
        self.assertTrue(meta["validation"]["trial_ready"], meta["validation"]["errors"])
        sleeves = [piece for piece in meta["pieces"] if str(piece.get("role") or "").startswith("sleeve")]
        for piece in sleeves:
            w, h = float(piece.get("width_mm") or 0), float(piece.get("height_mm") or 0)
            self.assertGreaterEqual(min(w, h), 80.0, piece)
            self.assertLessEqual(max(w, h) / max(min(w, h), 1.0), 4.2, piece)

    def test_shirt_puff_sleeve_keeps_sleeve_body(self) -> None:
        shirt_root = ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir"
        if not (shirt_root / "C2530694.pattern-ir.json").exists():
            self.skipTest("missing shirt v2 C2530694")
        index = build_index(ROOT / "data" / "ir" / "v1_rule_ready", None, shirt_root)
        catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", index)
        recipe = self.recipe("shirt", {
            "silhouette": "shirt.silhouette.fitted-x",
            "collar": "shirt.collar.pointed",
            "placket": "shirt.placket.half",
            "sleeve": "shirt.sleeve.puff",
            "cuff": "shirt.cuff.regular",
        })
        recipe["base_case_id"] = "C2530694"
        recipe["base_option_ids"] = {
            "collar": "shirt.collar.open-v-pointed",
            "placket": "shirt.placket.half",
            "silhouette": "shirt.silhouette.regular-fit",
            "cuff": "shirt.cuff.regular",
        }
        _, meta = compose_recipe(recipe, index, catalog)
        sleeves = [piece for piece in meta["pieces"] if str(piece.get("role") or "").startswith("sleeve")]
        self.assertTrue(sleeves, meta.get("sources", {}).get("sleeve"))
        for piece in sleeves:
            w, h = float(piece.get("width_mm") or 0), float(piece.get("height_mm") or 0)
            self.assertGreaterEqual(min(w, h), 200.0, piece)
            self.assertLessEqual(max(w, h) / max(min(w, h), 1.0), 2.2, piece)
        self.assertNotEqual("C2431105", (meta.get("sources") or {}).get("sleeve", {}).get("case_id"))
        cuffs = [piece for piece in meta["pieces"] if str(piece.get("role") or "") in {"cuff", "rib_cuff"}]
        self.assertTrue(cuffs, meta.get("sources", {}).get("sleeve"))
        sleeve_src = str((meta.get("sources") or {}).get("sleeve", {}).get("case_id") or "")
        self.assertTrue(
            sleeve_src and any(sleeve_src in str(piece.get("source_case_id") or piece.get("piece_id") or "") for piece in cuffs),
            cuffs,
        )

    def test_shirt_industrial_grade_follows_body_measurements(self) -> None:
        shirt_root = ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir"
        if not (shirt_root / "C2530175.pattern-ir.json").exists():
            self.skipTest("missing shirt v2 C2530175")
        index = build_index(ROOT / "data" / "ir" / "v1_rule_ready", None, shirt_root)
        catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", index)

        def run(chest: float, shoulder: float, upper_arm: float, neck: float) -> dict:
            recipe = self.recipe("shirt", {
                "silhouette": "shirt.silhouette.regular-fit",
                "collar": "shirt.collar.pointed",
                "placket": "shirt.placket.full",
                "sleeve": "shirt.sleeve.regular",
                "cuff": "shirt.cuff.regular",
            })
            recipe["base_case_id"] = "C2530175"
            recipe["base_option_ids"] = {
                "silhouette": "shirt.silhouette.regular-fit",
                "collar": "shirt.collar.pointed",
                "placket": "shirt.placket.full",
                "sleeve": "shirt.sleeve.regular",
                "cuff": "shirt.cuff.regular",
            }
            recipe["measurements_cm"] = {
                "height": 160, "chest": chest, "waist": 68, "shoulder": shoulder,
                "neck": neck, "sleeveLength": 58, "upperArm": upper_arm,
            }
            _, meta = compose_recipe(recipe, index, catalog)
            return meta

        small = run(78, 36, 24, 32)
        large = run(104, 46, 34, 38)
        self.assertGreater(float(large["sizing_profile"]["shoulder"]), float(small["sizing_profile"]["shoulder"]))
        self.assertGreater(float(large["sizing_profile"]["armhole"]), float(small["sizing_profile"]["armhole"]))
        self.assertGreater(float(large["sizing_profile"]["cuff"]), float(small["sizing_profile"]["cuff"]))
        small_front = max(p["width_mm"] for p in small["pieces"] if str(p.get("role") or "").startswith("front"))
        large_front = max(p["width_mm"] for p in large["pieces"] if str(p.get("role") or "").startswith("front"))
        self.assertGreater(large_front, small_front)
        small_sleeve = max((p["width_mm"] for p in small["pieces"] if "sleeve" in str(p.get("role") or "")), default=0)
        large_sleeve = max((p["width_mm"] for p in large["pieces"] if "sleeve" in str(p.get("role") or "")), default=0)
        if small_sleeve and large_sleeve:
            self.assertGreater(large_sleeve, small_sleeve * 0.98)

    def test_shirt_without_sleeve_selection_keeps_sleeveless_base(self) -> None:
        shirt_root = ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir"
        if not (shirt_root / "C2530694.pattern-ir.json").exists():
            self.skipTest("missing shirt v2 C2530694")
        index = build_index(ROOT / "data" / "ir" / "v1_rule_ready", None, shirt_root)
        catalog = pattern_catalog(ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", index)
        recipe = self.recipe("shirt", {
            "silhouette": "shirt.silhouette.fitted-x",
            "collar": "shirt.collar.pointed",
            "placket": "shirt.placket.full",
        })
        recipe["base_case_id"] = "C2530694"
        recipe["base_option_ids"] = {
            "collar": "shirt.collar.pointed",
            "placket": "shirt.placket.full",
            "silhouette": "shirt.silhouette.regular-fit",
        }
        _, meta = compose_recipe(recipe, index, catalog)
        roles = {str(piece.get("role") or "") for piece in meta["pieces"]}
        self.assertFalse(any(role.startswith("sleeve") for role in roles))
        self.assertIn("original", {row["id"] for row in meta.get("versions") or []})

    def test_c2590734_batwing_passes_without_separate_sleeve(self) -> None:
        if "C2590734" not in self.index:
            self.skipTest("missing C2590734")
        recipe = self.recipe("tshirt", {
            "neckline": "tshirt.neckline.boat",
            "sleeve": "tshirt.sleeve.batwing",
            "garment_length": "tshirt.garment-length.regular",
        })
        recipe["base_case_id"] = "C2590734"
        recipe["base_option_ids"] = {
            "neckline": "tshirt.neckline.boat",
            "sleeve": "tshirt.sleeve.batwing",
            "garment_length": "tshirt.garment-length.regular",
        }
        _, meta = compose_recipe(recipe, self.index, self.catalog)
        roles = {str(piece.get("role") or "") for piece in meta["pieces"]}
        self.assertFalse(roles & {"sleeve", "sleeve_left", "sleeve_right"})
        self.assertEqual("body_and_sleeve", meta["sources"].get("sleeve_mode"))
        self.assertTrue(meta["validation"]["trial_ready"], meta["validation"]["errors"])
        self.assertNotIn("缺少袖片", meta["validation"]["errors"])

    def test_tryon_descriptor_triangulates_concave_panel(self) -> None:
        points = [[0, 0], [80, 0], [80, 60], [40, 35], [0, 60]]
        triangles = _triangulate(points)
        self.assertEqual(len(points) - 2, len(triangles))
        self.assertTrue(all(0 <= vertex < len(points) for face in triangles for vertex in face))

    def test_v_neck_redraws_front_body_deeper_than_crew_neck(self) -> None:
        recipe = self.recipe("tshirt", self.defaults("tshirt"))
        base = self.index["C2590529"]
        scaled = _scale_complete_base(base, grading_profile(recipe))
        crew, crew_meta = reshape_body_neckline(scaled, base, "crew")
        v_neck, v_meta = reshape_body_neckline(scaled, base, "v-neck")
        self.assertTrue(crew_meta["applied"])
        self.assertTrue(v_meta["applied"])
        crew_front = next(row for row in crew_meta["chains"] if row["piece_role"] == "front_body")
        v_front = next(row for row in v_meta["chains"] if row["piece_role"] == "front_body")
        crew_back = next(row for row in crew_meta["chains"] if row["piece_role"] == "back_body")
        v_back = next(row for row in v_meta["chains"] if row["piece_role"] == "back_body")
        self.assertGreater(v_front["depth"], crew_front["depth"] * 2)
        self.assertGreater(v_back["depth"], crew_back["depth"] * 1.5)
        front_pieces = {piece["piece_id"] for piece in base["piece_instances"] if piece["piece_role"] == "front_body"}
        front_ids = {
            entity_id
            for chain in base["edge_chains"]
            if chain.get("piece_id") in front_pieces and "neckline" in str(chain.get("edge_role"))
            for entity_id in chain.get("ordered_entity_ids", [])
        }
        crew_geometry = [entity["geometry"]["points"] for entity in crew if entity["entity_id"] in front_ids]
        v_geometry = [entity["geometry"]["points"] for entity in v_neck if entity["entity_id"] in front_ids]
        self.assertNotEqual(crew_geometry, v_geometry)

        crew_selections = self.defaults("tshirt")
        v_selections = self.defaults("tshirt")
        crew_selections["neckline"] = next(option["id"] for option in self.catalog["options"] if option["family"] == "tshirt" and option["slug"] == "crew")
        v_selections["neckline"] = next(option["id"] for option in self.catalog["options"] if option["family"] == "tshirt" and option["slug"] == "v-neck")
        _, composed_crew = compose_recipe(self.recipe("tshirt", crew_selections), self.index, self.catalog)
        _, composed_v = compose_recipe(self.recipe("tshirt", v_selections), self.index, self.catalog)
        crew_front = next(row for row in composed_crew["validation"]["metrics"]["body_neckline"]["chains"] if row["piece_role"] == "front_body")
        v_front = next(row for row in composed_v["validation"]["metrics"]["body_neckline"]["chains"] if row["piece_role"] == "front_body")
        self.assertGreater(v_front["depth"], crew_front["depth"] * 2)
        self.assertGreaterEqual(v_front["duplicate_cut_edges"], 1)
        self.assertEqual(len(composed_v["pieces"]), len({piece["piece_id"] for piece in composed_v["pieces"]}))
        self.assertGreaterEqual(len(composed_v["pieces"]), len({piece["role"] for piece in composed_v["pieces"]}))

    def test_exported_dxf_contains_complete_role_layers(self) -> None:
        entities, meta = compose_recipe(self.recipe("shirt", self.defaults("shirt")), self.index, self.catalog)
        roles = {entity.get("piece_id"): entity.get("_piece_role", "") for entity in entities}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trial.dxf"
            report = write_entities_dxf(entities, str(path), piece_role_by_id=roles)
            text = path.read_text(encoding="utf-8")
        self.assertTrue(meta["validation"]["trial_ready"])
        self.assertGreater(report["entities_written"], 0)
        self.assertTrue(text.startswith("999\nANSI/AAMA\n"))
        self.assertEqual("aama_r12_blocks", report["format"])
        self.assertEqual(report["blocks_written"], report["inserts_written"])
        self.assertGreaterEqual(report["blocks_written"], 5)
        self.assertIn("\n  2\nBLOCKS\n", text)
        self.assertIn("\n  2\nENTITIES\n", text)
        self.assertIn("\nINSERT\n", text)
        self.assertNotIn("LWPOLYLINE", text)
        self.assertIn("POLYLINE", text)
        self.assertIn("VERTEX", text)
        self.assertIn("SEQEND", text)
        self.assertNotIn("AI4M_", text)
        lines = text.splitlines()
        layers = {lines[index + 1] for index in range(0, len(lines) - 1, 2) if lines[index].strip() == "8"}
        self.assertTrue({"1", "8"}.issubset(layers))
        self.assertTrue(layers.issubset({"1", "4", "7", "8", "11"}))
        text.encode("ascii")

    def test_cuff_styles_add_distinct_production_guides(self) -> None:
        guide_counts = {}
        guide_shapes = {}
        for slug in ("regular", "ruffled", "gathered"):
            selections = self.defaults("shirt")
            selections["cuff"] = next(
                option["id"] for option in self.catalog["options"]
                if option["family"] == "shirt" and option["group"] == "cuff" and option["slug"] == slug
            )
            entities, meta = compose_recipe(self.recipe("shirt", selections), self.index, self.catalog)
            guides = [
                entity for entity in entities
                if entity.get("_piece_role") == "cuff" and entity.get("line_role") == "pleat_line"
            ]
            self.assertTrue(meta["validation"]["trial_ready"])
            guide_counts[slug] = len(guides)
            guide_shapes[slug] = [entity["geometry"]["points"] for entity in guides]
        self.assertEqual(0, guide_counts["regular"])
        self.assertEqual(1, guide_counts["ruffled"])
        self.assertEqual(7, guide_counts["gathered"])
        self.assertNotEqual(guide_shapes["ruffled"], guide_shapes["gathered"])

    def test_short_sleeve_intent_shortens_sleeve_and_removes_cuff(self) -> None:
        long_recipe = self.recipe("shirt", self.defaults("shirt"))
        short_recipe = self.recipe("shirt", self.defaults("shirt"))
        short_recipe["intent_constraints"] = {"sleeve": "short", "target_length_cm": 45}
        _, long_meta = compose_recipe(long_recipe, self.index, self.catalog)
        _, short_meta = compose_recipe(short_recipe, self.index, self.catalog)
        long_sleeves = [piece for piece in long_meta["pieces"] if piece["role"].startswith("sleeve")]
        short_sleeves = [piece for piece in short_meta["pieces"] if piece["role"].startswith("sleeve")]
        long_area = sum(piece["width_mm"] * piece["height_mm"] for piece in long_sleeves)
        short_area = sum(piece["width_mm"] * piece["height_mm"] for piece in short_sleeves)
        self.assertLess(short_area, long_area * 0.65)
        self.assertFalse(any(piece["role"] in CUFF_ROLES for piece in short_meta["pieces"]))
        self.assertEqual("short", short_meta["sources"]["intent_sleeve"])
        self.assertTrue(short_meta["validation"]["trial_ready"])

    def test_sleeveless_intent_removes_sleeve_and_cuff_but_keeps_armhole(self) -> None:
        recipe = self.recipe("shirt", self.defaults("shirt"))
        recipe["intent_constraints"] = {"sleeve": "sleeveless"}
        _, meta = compose_recipe(recipe, self.index, self.catalog)
        roles = {piece["role"] for piece in meta["pieces"]}
        self.assertFalse(roles & {"sleeve", "sleeve_left", "sleeve_right"})
        self.assertFalse(roles & CUFF_ROLES)
        self.assertEqual("sleeveless", meta["sources"]["intent_sleeve"])
        self.assertGreater(meta["validation"]["metrics"]["armhole_finish"]["length_mm"], 0)
        self.assertTrue(meta["validation"]["trial_ready"])

    def test_c2390279_vneck_and_boat_pass_validation(self) -> None:
        path = ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir" / "C2390279.pattern-ir.json"
        if not path.exists():
            self.skipTest("missing C2390279 tshirt v2 IR")
        ir = json.loads(path.read_text(encoding="utf-8"))
        ir["_source_format"] = "tshirt_pattern_ir_v2"
        index = {**self.index, "C2390279": ir}
        for option_id, mode in (
            ("tshirt.neckline.v-neck", "neckline_edge_reshape"),
            ("tshirt.neckline.boat", "neckline_edge_reshape"),
        ):
            recipe = self.recipe("tshirt", {**self.defaults("tshirt"), "neckline": option_id})
            recipe["base_case_id"] = "C2390279"
            recipe["base_option_ids"] = {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in"}
            _, meta = compose_recipe(recipe, index, self.catalog)
            self.assertTrue(meta["validation"]["valid"], meta["validation"]["errors"])
            self.assertNotIn("基础纸样缺少可重绘的前后片领圈", "".join(meta["validation"]["errors"]))
            self.assertEqual(mode, (meta["sources"].get("neckline") or {}).get("mode"))

    def test_c2390279_vneck_changes_front_outline_not_sleeve(self) -> None:
        path = ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir" / "C2390279.pattern-ir.json"
        ir = json.loads(path.read_text(encoding="utf-8"))
        ir["_source_format"] = "tshirt_pattern_ir_v2"
        from simple_compose import _annotate, _keep_largest_clusters
        from composition_engine import entity_points, bounds_of_entities, reshape_body_neckline

        ents = _annotate(ir)
        if not any((e.get("source") or {}).get("origin") == "compose_ir" for e in ents):
            ents = _keep_largest_clusters(ents)
        sleeve_before = {
            str(e.get("entity_id")): e.get("geometry")
            for e in ents
            if e.get("_piece_role") == "sleeve"
        }
        front_before = next(
            e for e in ents
            if e.get("_piece_role") == "front_body"
            and (e.get("line_role") == "cut" or len(entity_points(e)) > 80)
        )
        crew_pts = entity_points(front_before)
        crew_box = bounds_of_entities([front_before])
        after, meta = reshape_body_neckline(ents, {**ir, "atomic_entities": ents}, "v-neck")
        self.assertTrue(meta["applied"])
        self.assertTrue(all(row["piece_role"] in {"front_body", "back_body"} for row in meta["chains"]))
        sleeve_after = {
            str(e.get("entity_id")): e.get("geometry")
            for e in after
            if e.get("_piece_role") == "sleeve"
        }
        self.assertEqual(sleeve_before, sleeve_after)
        front_after = next(e for e in after if e.get("entity_id") == front_before.get("entity_id") and e.get("piece_id") == front_before.get("piece_id"))
        v_pts = entity_points(front_after)
        self.assertNotEqual(crew_pts, v_pts)
        cx = (crew_box[0] + crew_box[2]) / 2.0
        band = crew_box[1] + (crew_box[3] - crew_box[1]) * 0.6
        window = (crew_box[2] - crew_box[0]) * 0.12

        def dip(points: list) -> float:
            rows = [p[1] for p in points if abs(p[0] - cx) < window and p[1] >= band]
            return min(rows)

        self.assertLess(dip(v_pts), dip(crew_pts) - 20.0)


if __name__ == "__main__":
    unittest.main()
