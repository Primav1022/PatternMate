"""Compose + grade + AAMA DXF across family / sex / size / parts. No file dumps."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))
sys.path.insert(0, str(ROOT / "apps" / "geometry-service"))

from composition_engine import build_index, compose_recipe, grading_profile, pattern_catalog  # noqa: E402
from dxf_export import write_entities_dxf  # noqa: E402

FEMALE_BASE = {
    "height": 160, "chest": 84, "waist": 68, "shoulder": 38.88,
    "neck": 34, "sleeveLength": 58, "upperArm": 28,
}
MALE_BASE = {
    "height": 175, "chest": 92, "waist": 78, "shoulder": 44,
    "neck": 39, "sleeveLength": 60, "upperArm": 32,
}
DEFAULTS = {
    "tshirt": {"neckline": "tshirt.neckline.crew", "sleeve": "tshirt.sleeve.set-in"},
    "shirt": {
        "silhouette": "shirt.silhouette.regular-fit",
        "collar": "shirt.collar.pointed",
        "placket": "shirt.placket.full",
        "sleeve": "shirt.sleeve.regular",
        "cuff": "shirt.cuff.regular",
    },
}
BASES = {"tshirt": "C2590529", "shirt": "C2530175"}
PARTS = {
    "tshirt": [
        ("default", {}),
        ("neckline-v-neck", {"neckline": "tshirt.neckline.v-neck"}),
        ("sleeve-puff", {"sleeve": "tshirt.sleeve.puff"}),
        ("length-long", {"garment_length": "tshirt.garment-length.long"}),
    ],
    "shirt": [
        ("default", {}),
        ("collar-peter-pan", {"collar": "shirt.collar.peter-pan"}),
        ("sleeve-puff", {"sleeve": "shirt.sleeve.puff"}),
        ("silhouette-fitted-x", {"silhouette": "shirt.silhouette.fitted-x"}),
    ],
}


def measurements(sex: str, height: float, chest: float) -> dict[str, float]:
    base = MALE_BASE if sex == "male_general" else FEMALE_BASE
    dc = chest - base["chest"]
    return {
        **base,
        "height": height,
        "chest": chest,
        "waist": base["waist"] + dc,
        "shoulder": round(base["shoulder"] + 0.32 * dc, 2),
        "neck": round(base["neck"] + 0.25 * dc, 2),
    }


class GradingMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = build_index(
            ROOT / "data" / "ir" / "v1_rule_ready",
            ROOT / "data" / "ir" / "tshirt_v2" / "pattern_ir",
            ROOT / "data" / "ir" / "shirt_v2" / "pattern_ir",
        )
        cls.catalog = pattern_catalog(
            ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json", cls.index
        )

    def recipe(self, family: str, sex: str, height: float, chest: float, overlay: dict | None = None) -> dict:
        selections = {**DEFAULTS[family], **(overlay or {})}
        return {
            "family": family,
            "sex": sex,
            "base_case_id": BASES[family],
            "measurements_cm": measurements(sex, height, chest),
            "ease_cm": 8,
            "fit": "regular",
            "material_id": "",
            "selections": selections,
            "base_option_ids": dict(DEFAULTS[family]),
        }

    def export_dxf(self, entities: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.dxf"
            roles = {
                str(entity.get("piece_id")): str(entity.get("_piece_role") or entity.get("piece_role") or "piece")
                for entity in entities if entity.get("piece_id")
            }
            report = write_entities_dxf(entities, str(path), piece_role_by_id=roles)
            text = path.read_text(encoding="ascii")
        self.assertIn("POLYLINE", text)
        self.assertIn("BLOCK", text)
        self.assertGreaterEqual(report["blocks_written"], 2)
        return report

    def test_female_base_size_is_identity_grade(self) -> None:
        for family in ("tshirt", "shirt"):
            with self.subTest(family=family):
                profile = grading_profile(self.recipe(family, "female", 160, 84))
                self.assertEqual("gbt_1335_2_2008_female", profile["mode"])
                for axis in ("width", "length", "shoulder", "neck", "armhole"):
                    self.assertAlmostEqual(float(profile[axis]), 1.0, places=4, msg=axis)

    def test_size_ladder_grades_up(self) -> None:
        small = grading_profile(self.recipe("tshirt", "female", 155, 80))
        base = grading_profile(self.recipe("tshirt", "female", 160, 84))
        large = grading_profile(self.recipe("tshirt", "female", 170, 92))
        self.assertLess(float(small["width"]), float(base["width"]))
        self.assertLess(float(base["width"]), float(large["width"]))
        self.assertLess(float(small["length"]), float(base["length"]))
        self.assertLess(float(base["length"]), float(large["length"]))

    def test_male_prototype_is_applied_at_male_base(self) -> None:
        profile = grading_profile(self.recipe("tshirt", "male_general", 175, 92))
        self.assertEqual("male_general_trial", profile["mode"])
        self.assertGreater(float(profile["width"]), 1.0)
        self.assertGreater(float(profile["length"]), 1.0)
        self.assertTrue((profile.get("prototype") or {}).get("applied"))

    def test_family_sex_size_parts_compose_and_export(self) -> None:
        jobs = []
        for family, variants in PARTS.items():
            for height, chest in ((155, 80), (160, 84), (170, 92)):
                jobs.append((family, "female", height, chest, "default", {}))
            jobs.append((family, "male_general", 175, 92, "default", {}))
            for slug, overlay in variants:
                if slug == "default":
                    continue
                jobs.append((family, "female", 160, 84, slug, overlay))
        for family, sex, height, chest, slug, overlay in jobs:
            label = f"{family}/{sex}/h{height}-c{chest}/{slug}"
            with self.subTest(label):
                recipe = self.recipe(family, sex, height, chest, overlay)
                entities, meta = compose_recipe(recipe, self.index, self.catalog)
                self.assertTrue(entities, label)
                self.assertTrue(meta["validation"]["trial_ready"], meta["validation"].get("errors"))
                roles = {piece["role"] for piece in meta["pieces"]}
                self.assertTrue(
                    {"front_body", "back_body"} <= roles or {"front_left", "front_right", "back_body"} <= roles,
                    roles,
                )
                self.assertTrue(any(str(role).startswith("sleeve") for role in roles), roles)
                report = self.export_dxf(entities)
                self.assertGreaterEqual(report["blocks_written"], 2)
