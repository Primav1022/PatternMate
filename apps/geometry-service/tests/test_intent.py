from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from app import IR_INDEX, parse_design_intent, ranked_references, score_semantics, semantic_facets, used_print_asset_ids  # noqa: E402


class IntentAnalysisTests(unittest.TestCase):
    def test_later_message_overrides_only_the_repeated_constraint(self) -> None:
        intent = parse_design_intent("想要休闲T恤，衣长45cm；改成V领，仍然无袖；改成短袖")
        self.assertEqual("tshirt", intent["family"])
        self.assertEqual("short", intent["sleeve"])
        self.assertEqual("v-neck", intent["neckline"])
        self.assertEqual(45.0, intent["target_length_cm"])
        self.assertIn("casual", intent["styles"])

    def test_explicit_negation_removes_previous_sleeve_constraint(self) -> None:
        intent = parse_design_intent("T恤，衣长45cm，无袖，宽松；我要在旅游的时候穿；不要无袖")
        self.assertIsNone(intent["sleeve"])
        self.assertNotIn("无袖", intent["labels"])
        self.assertEqual(45.0, intent["target_length_cm"])
        self.assertEqual("relaxed", intent["fit"])

    def test_travel_scene_adds_style_and_activity_constraints(self) -> None:
        intent = parse_design_intent("我想要旅游穿的衬衫")
        self.assertEqual("shirt", intent["family"])
        self.assertEqual("high", intent["activity"])
        self.assertIn("casual", intent["styles"])
        self.assertIn("outdoor", intent["styles"])
        self.assertIn("高活动性", intent["labels"])

    def test_only_assets_used_by_active_print_modes_are_exported(self) -> None:
        config = {
            "face_modes": {"front": "density", "back": "manual"},
            "density_asset_ids": {"front": "builtin-02", "back": None},
            "placements": [
                {"view": "back", "assetId": "uploaded-a"},
                {"view": "front", "assetId": "unused-manual-placement"},
            ],
        }
        self.assertEqual({"builtin-02", "uploaded-a"}, used_print_asset_ids(config))

    def test_facet_candidates_only_use_values_present_in_ir_json(self) -> None:
        text = "休闲T恤"
        intent = parse_design_intent(text)
        results = []
        annotated_styles = set()
        for ir in IR_INDEX.values():
            semantics = ir.get("design_semantics") or {}
            annotated_styles.update(semantics.get("style_tags") or [])
            score, _ = score_semantics(semantics, text, [], intent)
            results.append({"score": score, "semantics": semantics})
        style_facet = next(item for item in semantic_facets(results) if item["key"] == "style_tags")
        self.assertTrue(style_facet["values"])
        self.assertTrue({item["value"] for item in style_facet["values"]}.issubset(annotated_styles))

    def test_ranked_references_only_keep_selected_family(self) -> None:
        items = ranked_references("衬衫", [], {"family": "shirt", "category": "shirt"})
        self.assertTrue(items)
        catalog = {str(ir.get("case_id")) for ir in IR_INDEX.values() if not ir.get("_donor_only")}
        self.assertEqual({str(row["case_id"]) for row in items}, catalog)
        keep = {"shirt", "blouse"}
        first_other = next((i for i, row in enumerate(items) if str((row["semantics"] or {}).get("category")) not in keep), len(items))
        self.assertGreater(first_other, 0)
        self.assertTrue(all(str((row["semantics"] or {}).get("category")) in keep for row in items[:first_other]))

    def test_ranked_references_only_keep_selected_polo(self) -> None:
        items = ranked_references("Polo", [], {"family": "tshirt", "category": "polo"})
        self.assertTrue(items)
        catalog = {str(ir.get("case_id")) for ir in IR_INDEX.values() if not ir.get("_donor_only")}
        self.assertEqual({str(row["case_id"]) for row in items}, catalog)
        first_other = next((i for i, row in enumerate(items) if str((row["semantics"] or {}).get("category")) != "polo"), len(items))
        self.assertGreater(first_other, 0)
        self.assertTrue(all(str((row["semantics"] or {}).get("category")) == "polo" for row in items[:first_other]))

    def test_assistant_reply_is_clipped_to_100_chars(self) -> None:
        from app import _limit_assistant
        self.assertEqual("已记下衬衫。主要在哪穿？", _limit_assistant("已记下衬衫。主要在哪穿？"))
        long = "已记下" + ("休闲衬衫" * 20)
        clipped = _limit_assistant(long)
        self.assertLessEqual(len(clipped), 100)
        self.assertTrue(clipped.startswith("已记下"))


if __name__ == "__main__":
    unittest.main()
