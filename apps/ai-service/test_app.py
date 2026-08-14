import base64
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import urllib.error
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app


class PrintWorkflowTests(unittest.TestCase):
    def test_print_workflow_has_output(self):
        workflow = app._workflow(app.PrintJobRequest(prompt="blue botanical motif", history=["keep an airy composition"]), 42)
        self.assertEqual(workflow["11"]["inputs"]["batch_size"], 1)
        self.assertEqual(workflow["13"]["inputs"]["seed"], 42)
        self.assertEqual(workflow["15"]["class_type"], "SaveImage")
        self.assertIn("Previous design direction", workflow["9"]["inputs"]["text"])

    def test_print_job_accepts_three_candidates(self):
        request = app.PrintJobRequest(prompt="blue botanical motif", candidate_count=3)
        self.assertEqual(request.candidate_count, 3)

    def test_motif_background_becomes_transparent(self):
        source = Image.new("RGB", (8, 8), "white")
        for x in range(2, 6):
            for y in range(2, 6):
                source.putpixel((x, y), (20, 80, 160))
        raw = io.BytesIO()
        source.save(raw, format="PNG")
        result = Image.open(io.BytesIO(app._transparent_motif(raw.getvalue()))).convert("RGBA")
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertGreater(result.getpixel((3, 3))[3], 240)

    def test_garment_source_combines_previous_preview_and_selected_print(self):
        with tempfile.TemporaryDirectory() as directory:
            result_dir = Path(directory) / "results"
            input_dir = Path(directory) / "inputs"
            result_dir.mkdir(); input_dir.mkdir()
            Image.new("RGB", (80, 120), "blue").save(result_dir / "garment.png")
            Image.new("RGBA", (100, 100), "red").save(result_dir / "print.png")
            request = app.GarmentPrintJobRequest(prompt="red flower", source_preview_url="/results/garment.png", selected_print_url="/results/print.png")
            with patch.object(app, "RESULT_DIR", result_dir), patch.object(app, "_work_image_dir", return_value=input_dir):
                name = app._garment_source(request, "test")
            combined = Image.open(input_dir / name)
            self.assertGreater(combined.width, combined.height)
            self.assertEqual(combined.getpixel((0, 0)), (0, 0, 255))
            self.assertEqual(combined.getpixel((combined.width - 1, 0)), (255, 0, 0))


class ImageApiTests(unittest.TestCase):
    def test_config_reads_image_model_env(self):
        env = {
            "IMAGE_MODEL_BASE_URL": "https://128api.cn/v1",
            "IMAGE_MODEL_NAME": "gpt-image-2",
            "IMAGE_MODEL_API_KEY": "sk-test",
            "MODEL_BASE_URL": "http://127.0.0.1:8791/v1",
            "MODEL_API_KEY": "local-patternmate",
        }
        with patch.dict(os.environ, env, clear=False):
            config = app._image_api_config()
        self.assertEqual(config["base_url"], "https://128api.cn/v1")
        self.assertEqual(config["name"], "gpt-image-2")
        self.assertEqual(config["key"], "sk-test")

    def test_decode_b64_image(self):
        image = Image.new("RGB", (1, 1), "red")
        raw = io.BytesIO()
        image.save(raw, format="PNG")
        payload = {"data": [{"b64_json": base64.b64encode(raw.getvalue()).decode()}]}
        data = app._decode_image_api_body(payload, timeout=1, ssl_context=None)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_multipart_includes_file(self):
        body, content_type = app._multipart({"model": "gpt-image-2", "prompt": "x"}, [("image", "ref.png", b"abc", "image/png")])
        self.assertIn("multipart/form-data", content_type)
        self.assertIn(b'filename="ref.png"', body)
        self.assertIn(b"abc", body)


class DesignPreviewTests(unittest.TestCase):
    def request(self, **overrides):
        values = {
            "case_id": "C100",
            "family": "tshirt",
            "sex": "female",
            "measurements_cm": {"height": "160", "weight": "50", "chest": "85", "waist": "60", "shoulder": "38", "neck": "32", "sleeveLength": "50.5", "upperArm": "25"},
            "selections": {"neckline": "tshirt.neckline.high-mock", "sleeve": "tshirt.sleeve.set-in"},
            "fabric_color": "#b8d3e8",
            "material_label": "棉针织",
            "material_description": "表面平整、自然起褶",
            "process_label": "数码印花",
            "pattern_context": {"recipe_hash": "abc123", "pieces": [{"role": "front_body", "width_mm": 500, "height_mm": 700}], "sizing_profile": {"width": 1.03}, "sources": {"neckline": {"case_id": "C200", "option_id": "tshirt.neckline.high-mock", "mapping_mode": "exact_component"}}},
            "style_key": "style-test",
            "user_instruction": "简洁室内工作室背景",
        }
        values.update(overrides)
        return app.DesignPreviewJobRequest(**values)

    def test_prompt_contains_all_measurements_and_high_neck_constraints(self):
        prompt = app._design_prompt(self.request())
        for expected in ("Height: 160 cm", "Weight: 50 kg", "Chest: 85 cm", "Waist: 60 cm", "Shoulder width: 38 cm", "Neck circumference: 32 cm", "Target sleeve length: 50.5 cm", "Upper-arm circumference: 25 cm"):
            self.assertIn(expected, prompt)
        self.assertIn("rising from the neck base", prompt)
        self.assertIn("low round crew neckline", prompt)
        self.assertIn("the entire garment must remain plain and unbranded", prompt)
        self.assertIn("No side or back view", prompt)
        self.assertIn("简洁室内工作室背景", prompt)

    def test_visual_expectations_always_require_plain_unbranded_surface(self):
        expectations = app._visual_expectations(self.request(selections={}))
        surface = next(item for item in expectations if item["group"] == "surface_decoration")
        self.assertEqual(surface["slug"], "plain-unprinted")
        self.assertIn("any logo", surface["forbidden_confusions"])

    def test_legacy_prompt_is_additive_not_replacement(self):
        prompt = app._design_prompt(self.request(user_instruction="", prompt="legacy addition"))
        self.assertIn("[VALIDATED PATTERN]", prompt)
        self.assertIn("legacy addition", prompt)

    def test_same_slug_uses_group_specific_visual_semantics(self):
        request = self.request(selections={"placket": "shirt.placket.ruffled", "cuff": "shirt.cuff.ruffled"})
        components = {item["group"]: item for item in app._selected_components(request)}
        self.assertIn("center-front placket", components["placket"]["visual_cues"])
        self.assertIn("sleeve cuff", components["cuff"]["visual_cues"])

    def test_reference_image_combines_base_and_component_donor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for case_id, color in (("C100", "blue"), ("C200", "red")):
                target = root / "apps" / "web" / "public" / "reference-images" / "v2" / case_id
                target.mkdir(parents=True)
                Image.new("RGB", (100, 120), color).save(target / "cover.png")
            work = root / "work"; work.mkdir()
            with patch.object(app, "PROJECT_ROOT", root), patch.object(app, "_work_image_dir", return_value=work):
                self.assertEqual([item["case_id"] for item in app._reference_panels(self.request())], ["C100", "C200"])
                name = app._reference_image(self.request(), "job")
            with Image.open(work / name) as image:
                self.assertEqual(image.size, (1536, 768))

    def test_visual_mismatch_triggers_one_corrective_retry(self):
        request = self.request()
        job = app.ImageEditJob(id="job", request=request, kind="design_preview")
        mismatch = {"matches": False, "components": [], "clear_mismatches": [{"group": "neckline", "expected": "high-mock", "observed": "crew", "reason": "round neck"}]}
        match = {"matches": True, "components": [], "clear_mismatches": []}
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory); (work / "ref.png").write_bytes(b"reference")
            with patch.object(app, "_reference_image", return_value="ref.png"), patch.object(app, "_work_image_dir", return_value=work), patch.object(app, "_use_image_api", return_value=True), patch.object(app, "_image_api_call", side_effect=[b"first", b"second"]) as image_call, patch.object(app, "_validate_design_image", side_effect=[mismatch, match]), patch.object(app, "_save_result_bytes", return_value="/results/final.png"):
                app._run_image_edit_job(job)
        self.assertEqual(image_call.call_count, 2)
        self.assertEqual(job.attempt_count, 2)
        self.assertEqual(job.result_urls, ["/results/final.png"])
        self.assertEqual(job.status, "succeeded")

    def test_visual_service_failure_returns_clear_error(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "preview.png"
            Image.new("RGB", (16, 16), "white").save(image_path)
            with patch.object(app, "gpu_lock", return_value=nullcontext()), patch.object(app, "_release_comfy_memory"), patch.object(app, "_json_request", side_effect=urllib.error.HTTPError("http://local", 503, "busy", {}, None)):
                with self.assertRaisesRegex(RuntimeError, "本地视觉校验服务暂时不可用"):
                    app._validate_design_image(image_path, request)

    def test_visual_validation_hands_gpu_memory_between_services(self):
        request = self.request()
        rows = [{"group": group, "expected": slug, "observed": slug, "match": True, "confidence": 0.95, "reason": "visible"} for group, slug in (("neckline", "high-mock"), ("sleeve", "set-in"), ("surface_decoration", "plain-unprinted"))]
        response = {"choices": [{"message": {"content": json.dumps({"matches": True, "components": rows})}}]}
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "preview.png"
            Image.new("RGB", (16, 16), "white").save(image_path)
            with patch.object(app, "gpu_lock", return_value=nullcontext()), patch.object(app, "_release_comfy_memory") as release, patch.object(app, "_json_request", side_effect=[response, {"ok": True}]) as request_json:
                result = app._validate_design_image(image_path, request)
        self.assertTrue(result["matches"])
        release.assert_called_once_with()
        self.assertTrue(request_json.call_args_list[0].args[0].endswith("/chat/completions"))
        self.assertTrue(request_json.call_args_list[1].args[0].endswith("/unload"))


if __name__ == "__main__":
    unittest.main()
