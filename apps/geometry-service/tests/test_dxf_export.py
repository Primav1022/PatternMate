from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_handoff_pack" / "scripts"))

from dxf_export import write_entities_dxf  # noqa: E402


def read_pairs(path: Path) -> list[tuple[str, str]]:
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) % 2:
        raise AssertionError("DXF must contain complete code/value pairs")
    return [(lines[index].strip(), lines[index + 1].strip()) for index in range(0, len(lines), 2)]


def records(pairs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    result: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for pair in pairs:
        if pair[0] == "0":
            if current:
                result.append(current)
            current = [pair]
        elif current:
            current.append(pair)
    if current:
        result.append(current)
    return result


def value(record: list[tuple[str, str]], code: str) -> str | None:
    return next((item for item_code, item in record if item_code == code), None)


class DxfExportTests(unittest.TestCase):
    def export(self, entities: list[dict]) -> tuple[dict, str, list[tuple[str, str]]]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.dxf"
            report = write_entities_dxf(entities, str(path), optimize=False)
            text = path.read_text(encoding="ascii")
            pairs = read_pairs(path)
        return report, text, pairs

    def test_exports_aama_piece_blocks_with_r12_entities(self) -> None:
        # Regression: the old writer claimed AC1009 but emitted LWPOLYLINE in
        # flat model space, so strict apparel CAD importers rejected the file.
        entities = [
            {
                "entity_id": "front:cut",
                "piece_id": "piece/front",
                "_piece_role": "front_body",
                "line_role": "cut",
                "geometry": {"closed": True, "points": [[0, 0], [100, 0], [100, 80], [0, 80], [0, 0]]},
            },
            {
                "entity_id": "front:grain",
                "piece_id": "piece/front",
                "_piece_role": "front_body",
                "line_role": "grainline",
                "geometry": {"points": [[50, 10], [50, 70]]},
            },
            {
                "entity_id": "front:notch",
                "piece_id": "piece/front",
                "_piece_role": "front_body",
                "line_role": "notch",
                "geometry": {"points": [[0, 30], [4, 30]]},
            },
            {
                "entity_id": "front:stitch",
                "piece_id": "piece/front",
                "_piece_role": "front_body",
                "line_role": "stitch",
                "geometry": {"points": [[5, 5], [95, 5]]},
            },
            {
                "entity_id": "sleeve:cut",
                "piece_id": "piece/sleeve",
                "_piece_role": "sleeve",
                "line_role": "cut",
                "geometry": {"closed": True, "points": [[200, 0], [260, 0], [230, 90], [200, 0]]},
            },
        ]

        report, text, pairs = self.export(entities)
        parsed_records = records(pairs)
        section_names = [
            value(record, "2")
            for record in parsed_records
            if record[0] == ("0", "SECTION")
        ]
        record_types = [record[0][1] for record in parsed_records]
        inserts = [record for record in parsed_records if record[0] == ("0", "INSERT")]
        blocks = [record for record in parsed_records if record[0] == ("0", "BLOCK")]
        piece_names = [record for record in parsed_records if record[0] == ("0", "TEXT")]

        self.assertEqual(("999", "ANSI/AAMA"), pairs[0])
        self.assertEqual(["BLOCKS", "ENTITIES"], section_names)
        self.assertEqual(2, len(blocks))
        self.assertEqual(2, record_types.count("ENDBLK"))
        self.assertEqual(2, len(inserts))
        self.assertEqual(2, len(piece_names))
        self.assertEqual({value(block, "2") for block in blocks}, {value(insert, "2") for insert in inserts})
        self.assertEqual(
            {"FRONT BODY 01", "SLEEVE 02"},
            {value(record, "1") for record in piece_names},
        )
        self.assertNotIn("PIECE NAME:", text)
        self.assertEqual({"1"}, {value(record, "8") for record in piece_names})
        self.assertEqual({"10.000000"}, {value(record, "40") for record in piece_names})
        self.assertEqual({"0"}, {value(insert, "10") for insert in inserts})
        self.assertEqual({"0"}, {value(insert, "20") for insert in inserts})
        self.assertNotIn("LWPOLYLINE", record_types)
        self.assertNotIn("HEADER", text)
        self.assertNotIn("$INSUNITS", text)
        self.assertNotIn("AI4M_", text)
        self.assertTrue({"1", "4", "7", "11"}.issubset({pair_value for code, pair_value in pairs if code == "8"}))
        self.assertEqual(("0", "EOF"), pairs[-1])

        self.assertEqual("aama_r12_blocks", report["format"])
        self.assertEqual(2, report["blocks_written"])
        self.assertEqual(2, report["inserts_written"])
        self.assertEqual(5, report["entities_written"])
        self.assertEqual(2, report["closed_polylines"])
        self.assertEqual({"piece/front", "piece/sleeve"}, {item["piece_id"] for item in report["pieces"]})
        self.assertEqual(
            {"FRONT BODY 01", "SLEEVE 02"},
            {item["piece_name"] for item in report["pieces"]},
        )

    def test_closed_polyline_drops_duplicate_terminal_vertex(self) -> None:
        entities = [{
            "entity_id": "front:cut",
            "piece_id": "front",
            "_piece_role": "front_body",
            "line_role": "cut",
            "geometry": {"closed": True, "points": [[0, 0], [10, 0], [10, 5], [0, 5], [0, 0]]},
        }]

        _, _, pairs = self.export(entities)
        parsed_records = records(pairs)
        polyline_index = next(index for index, record in enumerate(parsed_records) if record[0] == ("0", "POLYLINE"))
        polyline = parsed_records[polyline_index]
        vertices = []
        for record in parsed_records[polyline_index + 1:]:
            if record[0] == ("0", "SEQEND"):
                break
            if record[0] == ("0", "VERTEX"):
                vertices.append((value(record, "10"), value(record, "20")))

        self.assertEqual("1", value(polyline, "70"))
        self.assertEqual(4, len(vertices))
        self.assertEqual(4, len(set(vertices)))

    def test_reports_and_skips_ungrouped_or_non_finite_geometry(self) -> None:
        entities = [
            {
                "entity_id": "valid",
                "piece_id": "front",
                "line_role": "construction",
                "geometry": {"points": [[0, 0], [10, 0]]},
            },
            {
                "entity_id": "ungrouped",
                "line_role": "cut",
                "geometry": {"points": [[0, 0], [5, 0], [0, 0]]},
            },
            {
                "entity_id": "invalid",
                "piece_id": "front",
                "line_role": "cut",
                "geometry": {"points": [[0, 0], [math.nan, 3]]},
            },
        ]

        report, text, _ = self.export(entities)

        self.assertEqual(1, report["entities_written"])
        self.assertEqual(2, report["entities_skipped"])
        self.assertEqual(1, report["ungrouped_entities_skipped"])
        self.assertNotIn("nan", text.lower())
        self.assertIn("\n8\n8\n", text)

    def test_rejects_export_without_a_valid_piece(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.dxf"
            with self.assertRaisesRegex(ValueError, "no valid pattern pieces"):
                write_entities_dxf([
                    {"entity_id": "loose", "geometry": {"points": [[0, 0], [1, 1]]}},
                ], str(path), optimize=False)
            self.assertFalse(path.exists())

    def test_rejects_zero_length_piece_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "degenerate.dxf"
            with self.assertRaisesRegex(ValueError, "no valid pattern pieces"):
                write_entities_dxf([
                    {
                        "entity_id": "zero-length",
                        "piece_id": "front",
                        "geometry": {"points": [[3, 4], [3, 4]]},
                    },
                ], str(path), optimize=False)
            self.assertFalse(path.exists())

    def test_open_curve_with_nearby_endpoints_stays_open_and_keeps_endpoint(self) -> None:
        entities = [{
            "entity_id": "construction-loop",
            "piece_id": "front",
            "line_role": "construction",
            "geometry": {"points": [[0, 0], [10, 0], [0.5, 0.5]]},
        }]

        _, _, pairs = self.export(entities)
        parsed_records = records(pairs)
        polyline_index = next(index for index, record in enumerate(parsed_records) if record[0] == ("0", "POLYLINE"))
        polyline = parsed_records[polyline_index]
        vertices = []
        for record in parsed_records[polyline_index + 1:]:
            if record[0] == ("0", "SEQEND"):
                break
            if record[0] == ("0", "VERTEX"):
                vertices.append((value(record, "10"), value(record, "20")))

        self.assertEqual("0", value(polyline, "70"))
        self.assertEqual(3, len(vertices))
        self.assertEqual(("0.500000", "0.500000"), vertices[-1])

    def test_exact_repeated_endpoint_closes_polyline_without_duplicate_vertex(self) -> None:
        entities = [{
            "entity_id": "closed-construction",
            "piece_id": "front",
            "line_role": "construction",
            "geometry": {"points": [[0, 0], [10, 0], [10, 10], [0, 0]]},
        }]

        _, _, pairs = self.export(entities)
        parsed_records = records(pairs)
        polyline_index = next(index for index, record in enumerate(parsed_records) if record[0] == ("0", "POLYLINE"))
        polyline = parsed_records[polyline_index]
        vertices = []
        for record in parsed_records[polyline_index + 1:]:
            if record[0] == ("0", "SEQEND"):
                break
            if record[0] == ("0", "VERTEX"):
                vertices.append((value(record, "10"), value(record, "20")))

        self.assertEqual("1", value(polyline, "70"))
        self.assertEqual(3, len(vertices))

    def test_production_seam_role_suffix_maps_to_layer_11(self) -> None:
        entities = [{
            "entity_id": "front:side-seam",
            "piece_id": "front",
            "line_role": "side_seam",
            "geometry": {"points": [[0, 0], [0, 50]]},
        }]

        _, _, pairs = self.export(entities)
        parsed_records = records(pairs)
        line = next(record for record in parsed_records if record[0] == ("0", "LINE"))
        self.assertEqual("11", value(line, "8"))

    def test_production_cut_role_suffix_maps_to_layer_1(self) -> None:
        entities = [{
            "entity_id": "back:yoke-cut",
            "piece_id": "back-yoke",
            "line_role": "yoke_cut",
            "geometry": {"points": [[0, 0], [60, 5]]},
        }]

        _, _, pairs = self.export(entities)
        parsed_records = records(pairs)
        line = next(record for record in parsed_records if record[0] == ("0", "LINE"))
        self.assertEqual("1", value(line, "8"))

    def test_cut_line_segment_is_not_closed_from_role_alone(self) -> None:
        entities = [{
            "entity_id": "front:curved-cut-segment",
            "piece_id": "front",
            "line_role": "cut_line",
            "geometry": {"points": [[0, 0], [40, 10], [80, 35], [120, 70]]},
        }]

        _, _, pairs = self.export(entities)
        parsed_records = records(pairs)
        polyline = next(record for record in parsed_records if record[0] == ("0", "POLYLINE"))
        self.assertEqual("1", value(polyline, "8"))
        self.assertEqual("0", value(polyline, "70"))


if __name__ == "__main__":
    unittest.main()
