from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

import app as geometry_app  # noqa: E402
from data_registry import BLOCKED_DONORS, case_id_from_name  # noqa: E402


class V2RegistryTests(unittest.TestCase):
    def test_case_id_matching_ignores_prefix_and_suffix(self) -> None:
        self.assertEqual("C2390077", case_id_from_name(Path("pattern_C2390077.annotated.v2.dxf")))

    def test_authoritative_tshirt_dataset_and_dxf_statuses(self) -> None:
        rows = geometry_app.catalog()["items"]
        tshirts = [
            row for row in rows
            if row["cover_url"].startswith("/reference-images/v2/") and row["category"] == "tshirt"
        ]
        self.assertEqual(62, len(tshirts))
        self.assertEqual(44, sum(row["dxf_available"] for row in tshirts))
        self.assertEqual(18, sum(row["data_status"] == "reference_only" for row in tshirts))
        blocked = next(row for row in tshirts if row["case_id"] in BLOCKED_DONORS)
        self.assertFalse(blocked["donor_allowed"])
        reference = next(row for row in geometry_app.catalog()["items"] if row["case_id"] == "C2590529")
        self.assertEqual("tshirt.neckline.crew", reference["base_option_ids"]["neckline"])
        self.assertEqual("tshirt.sleeve.set-in", reference["base_option_ids"]["sleeve"])

    def test_authoritative_shirt_dataset_is_loaded(self) -> None:
        rows = geometry_app.catalog()["items"]
        shirts = [
            row for row in rows
            if row["cover_url"].startswith("/reference-images/v2/") and row["category"] == "shirt"
        ]
        self.assertEqual(31, len(shirts))

    def test_shirt_component_donor_matches_expert_label(self) -> None:
        collar = next(
            option for option in geometry_app.PATTERN_CATALOG["options"]
            if option["id"] == "shirt.collar.open-v-pointed"
        )
        donor = geometry_app.IR_INDEX[collar["donor_case_id"]]
        labels = donor["design_semantics_extra"]["part_labels"]
        self.assertEqual("open-v-pointed", labels["collar"]["slug"])

    def test_unlabelled_catalog_variant_uses_parametric_component_fallback(self) -> None:
        option = next(
            option for option in geometry_app.PATTERN_CATALOG["options"]
            if option["id"] == "shirt.collar.peter-pan"
        )
        self.assertEqual("auto_validated", option["mapping_status"])
        self.assertIsNotNone(option["donor_case_id"])

    def test_conversation_returns_controlled_clarification(self) -> None:
        os.environ["DESIGN_MODEL_ENABLED"] = "false"
        request = geometry_app.DesignConversationRequest(
            messages=[geometry_app.ConversationMessage(role="user", content="我想设计一件旅游穿的上衣")],
            language="zh",
        )
        response = geometry_app.conversation_response(request)
        self.assertEqual(1, response["intent_version"])
        self.assertEqual("rules", response["analysis_mode"])
        self.assertTrue(response["ui_cards"])
        self.assertEqual("family", response["ui_cards"][0]["field"])
        self.assertIn(response["ui_cards"][0]["type"], {"single_select", "multi_select", "number_input", "text_input"})
        self.assertTrue(any(option["value"] == "_skip" for option in response["ui_cards"][0]["options"]))
        self.assertTrue(response["items"])

    def test_empty_conversation_opens_with_family_card(self) -> None:
        os.environ["DESIGN_MODEL_ENABLED"] = "false"
        response = geometry_app.conversation_response(geometry_app.DesignConversationRequest(messages=[], language="zh"))
        self.assertTrue(response["ui_cards"])
        self.assertEqual("family", response["ui_cards"][0]["field"])
        self.assertIn("品类", response["assistant_message"])

    def test_filled_slots_are_not_asked_again(self) -> None:
        os.environ["DESIGN_MODEL_ENABLED"] = "false"
        response = geometry_app.conversation_response(geometry_app.DesignConversationRequest(
            messages=[geometry_app.ConversationMessage(role="user", content="宽松休闲T恤")],
            language="zh",
        ))
        self.assertTrue(response["ui_cards"])
        field = response["ui_cards"][0]["field"]
        self.assertIn(field, {"usage", "neckline", "sleeve", "activity"})
        self.assertNotIn(field, {"family", "fit", "styles"})

    def test_skip_advances_to_next_slot(self) -> None:
        os.environ["DESIGN_MODEL_ENABLED"] = "false"
        first = geometry_app.conversation_response(geometry_app.DesignConversationRequest(
            messages=[geometry_app.ConversationMessage(role="user", content="先跳过")],
            language="zh",
        ))
        self.assertEqual("family", first["confirmed"]["_skipped"][0])
        self.assertEqual("usage", first["ui_cards"][0]["field"])

    def test_suggestion_chips_stay_in_catalog_allowlist(self) -> None:
        os.environ["DESIGN_MODEL_ENABLED"] = "false"
        response = geometry_app.conversation_response(geometry_app.DesignConversationRequest(
            messages=[geometry_app.ConversationMessage(role="user", content="宽松休闲T恤")],
            language="zh",
        ))
        allowed = {
            "family": set(geometry_app.ALLOWED_FAMILY_VALUES),
            "fit": set(geometry_app.ALLOWED_FIT_VALUES) | {"relaxed", "fitted", "regular"},
            "sleeve": set(geometry_app.ALLOWED_SLEEVE_VALUES),
            "neckline": set(geometry_app.ALLOWED_NECKLINE_VALUES),
            "styles": set(geometry_app.ALLOWED_STYLE_TAGS),
        }
        self.assertTrue(response["suggestion_chips"])
        for group in response["suggestion_chips"]:
            for option in group["options"]:
                self.assertIn(option["value"], allowed[group["field"]])

    def test_model_fail_still_returns_ranked_items(self) -> None:
        os.environ["DESIGN_MODEL_ENABLED"] = "false"
        response = geometry_app.conversation_response(geometry_app.DesignConversationRequest(
            messages=[geometry_app.ConversationMessage(role="user", content="旅游时穿，希望轻松、活动方便")],
            language="zh",
        ))
        self.assertEqual("rules", response["analysis_mode"])
        self.assertGreaterEqual(len(response["items"]), 90)
        self.assertTrue(response["assistant_message"])

    def test_semantics_caption_uses_design_annotations(self) -> None:
        from clip_rank import semantics_caption
        caption = semantics_caption(geometry_app.IR_INDEX["C2590045"])
        self.assertIn("T恤", caption)
        self.assertIn("圆领", caption)
        self.assertIn("宽松", caption)


if __name__ == "__main__":
    unittest.main()
