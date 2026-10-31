"""End-to-end staged integration against a canned transport; no model/network call."""

import copy
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from ftv_reconstruction.accounting import EPSLedger, RetainedEarningsLedger, check_eps, check_retained_earnings
from ftv_reconstruction.model_contracts import SchemaPlan, generated_extraction_schema
from ftv_reconstruction.pipeline import (
    ReconstructionConfig, SavedCallFailure, extract_document, prepare_document,
    read_json, run_reconstruction,
)
from test_reconstruction import EVIDENCE, REVIEW, eps, retained
from ftv_reconstruction.prompts import prompt_safety_audit
from ftv_reconstruction.units import VERSION, UNIT_POLICY, SCALE_DESCRIPTION, CURRENCY_DESCRIPTION


def schema_plan():
    types = {"row_label": "string", "ancestor_path": "string_array", "row_role": "string",
             "period": "string", "raw_value": "string", "quantity": "string", "scale": "integer_or_null",
             "currency": "string", "concept": "string"}
    columns = [{"name": k, "data_type": v, "description": f"SYNTHETIC generated description {k}"}
               for k, v in types.items()]
    for column in columns:
        if column["name"] == "scale": column["description"] = SCALE_DESCRIPTION
        if column["name"] == "currency": column["description"] = CURRENCY_DESCRIPTION
    return {"archetypes": [{"statement": kind, "csv_columns": columns,
             "supported_concepts": ["total_assets"] if kind == "balance_sheet" else [],
             "source_number_format_rules": "Retain every source sign and printed decimal",
             "hierarchy_rules": "SYNTHETIC document-specific hierarchy"} for kind in ("balance_sheet", "other")],
             "geometry_contract_acknowledged": True, "no_numeric_repair": True, "unit_contract_version": VERSION}


def geometry_page(number):
    def cell(ident, row, column, text, kind, quantity="unknown", scale=None, currency=None):
        return {"source_cell_id": ident, "row": row, "column": column, "rowspan": 1, "colspan": 1,
            "text": text, "kind": kind, "header_scope": "column" if kind == "header" else None,
            "box": None, "quantity": quantity, "scale": scale, "currency": currency}
    return {"page": number, "tables": [{"table_index": 0, "title": "SYNTHETIC Balance sheet",
        "statement": "balance_sheet", "scope": "consolidated", "continuation_of": None,
        "grid": {"box": None, "row_count": 2, "column_count": 2, "row_sections": ["head", "body"],
            "cells": [cell("h0", 0, 0, "", "blank"), cell("h1", 0, 1, "2023", "header"),
                      cell("label", 1, 0, "Total assets", "data"),
                      cell("value", 1, 1, "100", "data", "money", 1, "USD")]},
        "columns": [{"column_index": 0, "grid_column": 1, "label": "2023", "period": "2023-12-31"}],
        "rows": [{"row_index": 0, "label_cell_ids": [], "role": "header", "path": [], "concept": None},
                 {"row_index": 1, "label_cell_ids": ["label"], "role": "data", "path": [], "concept": "total_assets"}]}]}


class CannedTransport:
    def __init__(self):
        self.calls = []; self.bad_pages = set(); self.fail_extraction = False

    def request(self, method, path, payload=None):
        self.calls.append(copy.deepcopy(payload))
        assert (method, path) == ("POST", "chat/completions")
        name = payload["response_format"]["json_schema"]["name"]
        text = json.loads(payload["messages"][1]["content"][0]["text"])
        def observation(number):
            return {"page": number, "table_titles": [], "statement_types": [],
                    "hierarchy_notes": "SYNTHETIC", "continuation_notes": "SYNTHETIC"}
        if name == "document_survey_chunk":
            value = {"pages": [observation(n) for n in text["physical_pages"]], "document_notes": "SYNTHETIC"}
        elif name == "document_structure":
            value = {"document_summary": "SYNTHETIC full-document review", "reviewed_pages": text["covered_pages"], "statement_types": ["balance_sheet"],
                     "table_locations": [observation(n) for n in text["covered_pages"]],
                     "hierarchy_guidance": "SYNTHETIC", "unit_and_eps_complexity_cautions": "SYNTHETIC"}
        elif name == "document_generated_schema":
            value = schema_plan()
        elif name == "document_generated_prompt":
            value = {"instructions": "SYNTHETIC ACTUALLY GENERATED PROMPT: preserve source geometry and totals",
                     "source_only": True, "changes_numbers_allowed": False,
                     "schema_plan_sha256": text["schema_plan_sha256"], "unit_contract_version": VERSION, "unit_policy": UNIT_POLICY}
        else:
            if self.fail_extraction:
                raise RuntimeError("SYNTHETIC ambiguous transport failure")
            number = text["physical_page"]; value = geometry_page(number)
            if number in self.bad_pages:
                value["tables"][0]["grid"]["cells"].pop()
        return {"id": f"synthetic-{len(self.calls)}", "model": payload["model"], "provider": "synthetic",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                "usage": {"cost": 0, "prompt_tokens": 1, "completion_tokens": 1}}


class PipelineTests(unittest.TestCase):
    def config(self, root, **changes):
        pdf = root / "synthetic.pdf"; pdf.write_bytes(b"SYNTHETIC CANNED PDF")
        base = {"experiment_id": "synthetic", "document_id": "synthetic-document", "issuer_id": "synthetic-issuer",
                "pdf": pdf, "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                "selected_pages": [2, 3], "structural_pages": [2, 3], "preparation_illustrative_pages": [2],
                "model": "synthetic/extractor", "condition": "sequential", "output": root / "run",
                "prepared_dir": root / "prepared", "protocol_sha256": "a" * 64,
                "selection_freeze_sha256": "b" * 64, "survey_chunk_pages": 2}
        return ReconstructionConfig.model_validate({**base, **changes})

    def run_pipeline(self, config, transport):
        from PIL import Image
        def renderer(pdf, page, dpi, pixels):
            stream = io.BytesIO()
            Image.new("RGB", (16, 20), color=(page * 20, 80, 140)).save(stream, format="PNG")
            return stream.getvalue()
        return run_reconstruction(config, transport, renderer=renderer, page_counter=lambda pdf: 4)

    def test_actual_all_stages_full_survey_schema_prompt_cleaner_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root); transport = CannedTransport()
            result = self.run_pipeline(config, transport)
            self.assertEqual(result["status"], "complete"); self.assertEqual(result["accepted_pages"], [2, 3])
            self.assertEqual(len(transport.calls), 7)  # two survey chunks + three prep stages + two pages
            self.assertEqual(read_json(config.prepared_dir / "survey.json")["covered_pages"], [1, 2, 3, 4])
            first = transport.calls[5]; second = transport.calls[6]
            self.assertIn("ACTUALLY GENERATED PROMPT", first["messages"][1]["content"][0]["text"])
            generated = first["response_format"]["json_schema"]["schema"]["$defs"]
            self.assertEqual(generated["GeometryTable"]["properties"]["statement"]["enum"], ["balance_sheet", "other"])
            self.assertIn('"page": 2', second["messages"][1]["content"][0]["text"])
            self.assertEqual(result["raw_records"][0]["role"], "data")
            self.assertEqual(result["clean_records"][0]["role"], "total")
            self.assertEqual(result["raw_records"][0]["raw_value"], result["clean_records"][0]["raw_value"])
            self.assertEqual(read_json(config.output / "pages/p0002/cleaning-audit.json")["numbers_changed"], 0)
            # Same preparation reused across different extractor + context, no survey replay.
            other = self.config(root, model="synthetic/other", condition="independent", output=root / "independent")
            second_result = self.run_pipeline(other, transport)
            self.assertTrue(second_result["preparation_reused"]); self.assertEqual(len(transport.calls), 9)
            self.assertIsNone(json.loads(transport.calls[-1]["messages"][1]["content"][0]["text"])["untrusted_previous_accepted_page"])
            self.run_pipeline(config, transport); self.assertEqual(len(transport.calls), 9)

    def test_schema_rejected_page_remains_missing_and_resets_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root); transport = CannedTransport(); transport.bad_pages = {2}
            result = self.run_pipeline(config, transport)
            self.assertEqual(result["accepted_pages"], [3]); self.assertEqual(result["rejected_pages"][0]["page"], 2)
            self.assertTrue(result["has_rejected_pages"])
            last = json.loads(transport.calls[-1]["messages"][1]["content"][0]["text"])
            self.assertIsNone(last["untrusted_previous_accepted_page"])
            self.assertTrue((config.output / "pages/p0002/response.json").exists())

    def test_nonadjacent_pages_never_get_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root, selected_pages=[1, 3], structural_pages=[1, 3]); transport = CannedTransport()
            self.run_pipeline(config, transport)
            text = json.loads(transport.calls[-1]["messages"][1]["content"][0]["text"])
            self.assertIsNone(text["untrusted_previous_accepted_page"])

    def test_ambiguous_failure_is_saved_and_never_automatically_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root); transport = CannedTransport(); transport.fail_extraction = True
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                self.run_pipeline(config, transport)
            count = len(transport.calls)
            with self.assertRaises(SavedCallFailure):
                self.run_pipeline(config, transport)
            self.assertEqual(len(transport.calls), count)
            self.assertEqual(read_json(config.output / "results.json")["status"], "failed")

    def test_preparation_tamper_and_changed_dpi_reject_without_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root); transport = CannedTransport(); self.run_pipeline(config, transport)
            count = len(transport.calls)
            different = config.model_copy(update={"dpi": 150, "output": root / "other"})
            with self.assertRaisesRegex(ValueError, "cannot reuse"):
                self.run_pipeline(different, transport)
            (config.prepared_dir / "generated-schema-plan.json").write_text("tamper")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                self.run_pipeline(config, transport)
            self.assertEqual(len(transport.calls), count)

    def test_selected_source_png_hash_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = root / "source.png"; image.write_bytes(b"SYNTHETIC-PNG")
            config = self.config(root, selected_images={2: {"path": image, "sha256": "c" * 64}})
            transport = CannedTransport()
            with self.assertRaisesRegex(ValueError, "source image changed"):
                self.run_pipeline(config, transport)
            self.assertEqual(len(transport.calls), 0)

    def test_generated_schema_cannot_drop_lossless_csv_fields(self):
        plan = schema_plan(); plan["archetypes"][0]["csv_columns"].pop()
        with self.assertRaisesRegex(ValueError, "nine"):
            SchemaPlan.model_validate(plan)

    def test_reasoning_is_gemini_only_with_shared_preparation_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); transport = CannedTransport()
            config = self.config(root, model="meta-llama/llama-4-maverick",
                                 preparation_reasoning_max_tokens=1024, extraction_reasoning_max_tokens=1024)
            self.run_pipeline(config, transport)
            self.assertEqual(transport.calls[0]["reasoning"], {"max_tokens": 1024})
            self.assertNotIn("reasoning", transport.calls[-1])

    def test_generated_prompt_lint_rejects_numeric_repair_without_claiming_human_review(self):
        self.assertFalse(prompt_safety_audit("Repair totals to balance assets.")["passed"])
        self.assertFalse(prompt_safety_audit("Never invent numbers; fill missing values from context.")["passed"])
        safe = prompt_safety_audit("Never repair values. Do not infer missing amounts.")
        self.assertTrue(safe["passed"]); self.assertFalse(safe["human_reviewed"])

    def test_jpeg_survey_full_coverage_actual_bytes_hashes_and_png_extraction(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root, survey_image_format="jpeg", survey_jpeg_quality=90)
            transport = CannedTransport(); self.run_pipeline(config, transport)
            survey = read_json(config.prepared_dir / "survey.json")
            self.assertEqual(survey["covered_pages"], [1, 2, 3, 4])
            records = survey["image_provenance"]
            images = [part["image_url"]["url"] for call in transport.calls[:2]
                      for part in call["messages"][1]["content"] if part["type"] == "image_url"]
            for encoded, record in zip(images, records):
                self.assertTrue(encoded.startswith("data:image/jpeg;base64,"))
                sent = base64.b64decode(encoded.split(",", 1)[1])
                self.assertEqual(sent, Path(record["image_file"]).read_bytes())
                self.assertEqual(hashlib.sha256(sent).hexdigest(), record["image_sha256"])
                self.assertEqual(hashlib.sha256(Path(record["source_png_file"]).read_bytes()).hexdigest(), record["source_png_sha256"])
                with Image.open(io.BytesIO(sent)) as image:
                    self.assertEqual((image.format, image.size), ("JPEG", (16, 20)))
                self.assertEqual((record["dpi"], record["jpeg_quality"], record["jpeg_subsampling"]), (300, 90, 2))
            extraction = [part["image_url"]["url"] for part in transport.calls[-1]["messages"][1]["content"]
                          if part["type"] == "image_url"]
            self.assertTrue(all(value.startswith("data:image/png;base64,") for value in extraction))

    def test_png_survey_opt_in_and_changed_encoding_cannot_reuse_jpeg_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root, survey_image_format="png")
            transport = CannedTransport(); self.run_pipeline(config, transport)
            for part in transport.calls[0]["messages"][1]["content"]:
                if part["type"] == "image_url":
                    self.assertTrue(part["image_url"]["url"].startswith("data:image/png;base64,"))
            count = len(transport.calls)
            with self.assertRaisesRegex(ValueError, "cannot reuse"):
                self.run_pipeline(config.model_copy(update={"survey_image_format": "jpeg"}), transport)
            self.assertEqual(len(transport.calls), count)

    def test_generated_exponent_or_compound_currency_definitions_rejected(self):
        for name, description in (("scale", "power of10 exponent e.g3for thousands,6millions"),
                                  ("currency", "Shares for share counts;USDperShare forEPS")):
            plan = schema_plan()
            next(c for c in plan["archetypes"][0]["csv_columns"] if c["name"] == name)["description"] = description
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "immutable"):
                SchemaPlan.model_validate(plan)

    def test_actual_model_cosmetic_definition_prefixes_preserve_full_policy(self):
        from ftv_reconstruction.model_contracts import GeneratedPrompt
        from ftv_reconstruction.units import definition_equivalent, policy_equivalent
        # Actual excluded-development failure: the model omitted only the field
        # name/copula, preserving the entire substantive definition verbatim.
        scale = SCALE_DESCRIPTION.replace("scale is the", "The", 1)
        currency = CURRENCY_DESCRIPTION.replace("currency is an", "An", 1)
        plan = schema_plan()
        for archetype in plan["archetypes"]:
            for column in archetype["csv_columns"]:
                if column["name"] == "scale": column["description"] = scale
                if column["name"] == "currency": column["description"] = currency
        parsed = SchemaPlan.model_validate(plan)
        self.assertEqual(parsed.archetypes[0].csv_columns[6].description, scale)
        policy = scale + "\n" + currency
        prompt = GeneratedPrompt(instructions="Keep visible values unchanged", source_only=True,
            changes_numbers_allowed=False, schema_plan_sha256="a" * 64,
            unit_contract_version=VERSION, unit_policy=policy)
        self.assertEqual(prompt.unit_policy, policy)  # No rewriting of model narrative.
        self.assertTrue(policy_equivalent(UNIT_POLICY))
        self.assertTrue(definition_equivalent("  " + scale.upper() + "  ", SCALE_DESCRIPTION, "scale"))
        self.assertFalse(definition_equivalent(scale.replace("1000", "3", 1), SCALE_DESCRIPTION, "scale"))
        self.assertFalse(policy_equivalent(policy + " Use scale3 for thousands."))

    def test_compiled_unit_description_is_factor_contract_without_integer_enum(self):
        schema = generated_extraction_schema(SchemaPlan.model_validate(schema_plan()))
        cell = schema["$defs"]["FinancialCell"]["properties"]
        self.assertEqual(cell["scale"]["description"], SCALE_DESCRIPTION)
        self.assertNotIn('"enum"', json.dumps(cell["scale"]))
        from ftv_reconstruction.model_contracts import FinancialCell, GeneratedPrompt
        value = geometry_page(2)["tables"][0]["grid"]["cells"][-1]
        for changes in ({"scale": 3}, {"quantity": "shares", "currency": "Shares"},
                        {"quantity": "per_share", "scale": 1000}, {"currency": "USDperShare"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                FinancialCell.model_validate({**value, **changes})
        with self.assertRaisesRegex(ValueError, "contradicts"):
            GeneratedPrompt(instructions="Set scale=3 for thousands", source_only=True, changes_numbers_allowed=False,
                            schema_plan_sha256="a" * 64, unit_contract_version=VERSION, unit_policy=UNIT_POLICY)

    def test_compiled_extraction_forces_unscored_prediction_boxes_null(self):
        schema = generated_extraction_schema(SchemaPlan.model_validate(schema_plan()))
        for model in ("PhysicalGrid", "FinancialCell"):
            self.assertEqual(schema["$defs"][model]["properties"]["box"], {"type": "null"})
        self.assertIn("rowspan", schema["$defs"]["FinancialCell"]["properties"])
        self.assertIn("colspan", schema["$defs"]["FinancialCell"]["properties"])
        self.assertIn("text", schema["$defs"]["FinancialCell"]["properties"])

    def test_reuse_preserved_survey_and_structure_only_regenerates_schema_and_prompt(self):
        from ftv_reconstruction.pipeline import write_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); old = self.config(root, survey_image_format="png"); initial = CannedTransport()
            self.run_pipeline(old, initial)
            # Synthetic fixture recreates V2's flat survey-call layout. Its actual
            # request/response bytes, unchanged stage contracts and hashes remain.
            for chunk in (old.prepared_dir / "survey").iterdir():
                attempt = chunk / "attempt-01"
                for path in list(attempt.iterdir()): path.rename(chunk / path.name)
                attempt.rmdir()
            snapshot = read_json(old.prepared_dir / "snapshot.json")
            snapshot["procedure_sha256"] = "e62953c937b668353ae4d1d29e66a5882b091fb4dba26d94caa1b678275d34b9"
            write_json(old.prepared_dir / "snapshot.json", snapshot)
            source_bytes = {str(p): p.read_bytes() for p in old.prepared_dir.rglob("*") if p.is_file()}
            new = self.config(root, survey_image_format="png", prepared_dir=root / "v3-prepared", output=root / "v3-run",
                              reuse_preparation_from=old.prepared_dir, protocol_sha256="c" * 64)
            transport = CannedTransport(); result = self.run_pipeline(new, transport)
            names = [p["response_format"]["json_schema"]["name"] for p in transport.calls]
            self.assertEqual(names, ["document_generated_schema", "document_generated_prompt", "financial_geometry_page", "financial_geometry_page"])
            for path, raw in source_bytes.items(): self.assertEqual(Path(path).read_bytes(), raw)
            prepared = read_json(new.prepared_dir / "prepared.json")
            self.assertTrue(prepared["imported_artifacts"])
            self.assertEqual(prepared["import_origin"], str(old.prepared_dir))
            self.assertEqual(result["accepted_pages"], [2, 3])

    def test_bounded_survey_validation_retry_retains_bad_response_and_never_changes_payload(self):
        class BadCoverageOnce(CannedTransport):
            def request(self, method, path, payload=None):
                response = super().request(method, path, payload)
                if len(self.calls) == 1:
                    value = json.loads(response["choices"][0]["message"]["content"])
                    value["pages"][0]["page"] = 999
                    response["choices"][0]["message"]["content"] = json.dumps(value)
                return response
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root, survey_validation_attempts=2)
            transport = BadCoverageOnce(); self.run_pipeline(config, transport)
            self.assertEqual(transport.calls[0], transport.calls[1])
            chunk = config.prepared_dir / "survey/chunk-0001"
            self.assertTrue((chunk / "attempt-01/response.json").exists())
            self.assertTrue((chunk / "attempt-01/rejection.json").exists())
            self.assertTrue((chunk / "attempt-02/parsed.json").exists())

    def test_import_gate_rejects_source_settings_drift_before_any_call(self):
        from ftv_reconstruction.pipeline import _preparation_snapshot, write_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); old = self.config(root, survey_image_format="png")
            self.run_pipeline(old, CannedTransport())
            snapshot = read_json(old.prepared_dir / "snapshot.json")
            snapshot["procedure_sha256"] = "e62953c937b668353ae4d1d29e66a5882b091fb4dba26d94caa1b678275d34b9"
            write_json(old.prepared_dir / "snapshot.json", snapshot)
            new = self.config(root, survey_image_format="png", prepared_dir=root / "v3", output=root / "v3-run",
                              reuse_preparation_from=old.prepared_dir, seed=99)
            transport = CannedTransport()
            with self.assertRaisesRegex(ValueError, "settings do not match"):
                self.run_pipeline(new, transport)
            self.assertEqual(transport.calls, [])

    def test_readonly_import_rejects_tampering_without_writing_origin(self):
        from ftv_reconstruction.pipeline import _readonly_call
        from ftv_reconstruction.model_contracts import StructurePlan
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); config = self.config(root); self.run_pipeline(config, CannedTransport())
            directory = config.prepared_dir / "structure"
            payload = read_json(directory / "request.json")
            response = directory / "response.json"; response.write_bytes(response.read_bytes() + b" ")
            before = {str(path): path.read_bytes() for path in directory.iterdir() if path.is_file()}
            with self.assertRaisesRegex(ValueError, "fingerprints"):
                _readonly_call(directory, payload, StructurePlan, lambda x: None)
            for path, raw in before.items(): self.assertEqual(Path(path).read_bytes(), raw)


class NewAccountingGateTests(unittest.TestCase):
    def test_source_verified_absent_dividend_is_not_fabricated_zero(self):
        ledger = retained(); ledger["movements"].pop(1); ledger["ending"]["raw_value"] = "41,118"
        ledger.update(dividend_policy="source_verified_absent", dividend_absence_evidence=[EVIDENCE], dividend_absence_review=REVIEW)
        self.assertEqual(check_retained_earnings(RetainedEarningsLedger.model_validate(ledger))["status"], "pass")
        ledger["dividend_absence_review"] = None
        self.assertEqual(check_retained_earnings(RetainedEarningsLedger.model_validate(ledger))["status"], "skipped")

    def test_printed_precision_intervals_account_for_rounded_numerator(self):
        ledger = eps(); ledger["numerator"]["raw_value"] = "1"; ledger["numerator"]["scale"] = 1
        ledger["shares"]["raw_value"] = "3"; ledger["shares"]["scale"] = 1
        ledger["reported_eps"]["raw_value"] = ".40"  # explicit leading digit needed by conservative parser
        ledger["reported_eps"]["raw_value"] = "0.40"
        result = check_eps(EPSLedger.model_validate(ledger))
        self.assertEqual(result["status"], "pass"); self.assertFalse(result["point_absolute_tolerance_used"])
        ledger["rounding_policy"] = "point_absolute"
        self.assertEqual(check_eps(EPSLedger.model_validate(ledger))["status"], "fail")


if __name__ == "__main__":
    unittest.main()
