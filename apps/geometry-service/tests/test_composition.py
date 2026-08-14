from __future__ import annotations

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
    remix_readiness,
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
                self.assertIn("缺少衬衫领片", meta["validation"]["errors"])

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
        self.assertIn("AC1009", text)
        self.assertIn("AI4M_FRONT", text)
        self.assertIn("AI4M_BACK", text)
        self.assertIn("AI4M_SLEEVE", text)
        self.assertIn("AI4M_NECK", text)
        self.assertIn("AI4M_CUFF", text)

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


if __name__ == "__main__":
    unittest.main()
