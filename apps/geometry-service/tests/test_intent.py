from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from app import IR_INDEX, parse_design_intent, score_semantics, semantic_facets, used_print_asset_ids  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
