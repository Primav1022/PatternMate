from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class CatalogScopeTests(unittest.TestCase):
    def test_garment_length_options_exist_for_supported_families(self) -> None:
        catalog = json.loads((ROOT / "packages" / "catalogs" / "src" / "pattern-options.v1.json").read_text(encoding="utf-8"))
        ids = {row["id"] for row in catalog["options"] if row["group"] == "garment_length"}
        self.assertEqual({
            "tshirt.garment-length.short",
            "tshirt.garment-length.regular",
            "tshirt.garment-length.long",
            "shirt.garment-length.short",
            "shirt.garment-length.regular",
            "shirt.garment-length.long",
        }, ids)

    def test_frontend_group_order_matches_finite_annotated_scope(self) -> None:
        source = (ROOT / "apps" / "web" / "src" / "catalogs.ts").read_text(encoding="utf-8")
        self.assertIn("tshirt: ['neckline', 'sleeve', 'garment_length']", source)
        self.assertIn("shirt: ['collar', 'sleeve', 'garment_length', 'cuff']", source)


if __name__ == "__main__":
    unittest.main()
