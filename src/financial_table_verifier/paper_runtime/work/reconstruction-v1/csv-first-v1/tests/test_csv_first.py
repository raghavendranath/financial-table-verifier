import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import shutil
import unittest

from ftv_csv_reconstruction import prompts
from ftv_csv_reconstruction.artifacts import (SavedCallFailure, call, digest, file_hash, json_bytes,
    read_json, replay_call, write_bytes, write_json)
from ftv_csv_reconstruction.auxiliary import extract_html
from ftv_csv_reconstruction.cleaning import clean_page
from ftv_csv_reconstruction.contracts import (CSVConfig, PageEnvelope, SchemaPlan, GeneratedPrompt, description_diagnostics,
                                             strict_schema)
from ftv_csv_reconstruction.csv_io import (ContextTooLarge, context_for, page_context, parse_csv,
                                          parse_page, serialize)
from ftv_csv_reconstruction.pipeline import extract_document, replay_page
from ftv_csv_reconstruction.preparation import (_archive_prompts, import_source_stages, prepare_document)


def plan_data():
    definitions = [
        ("quantity", "printed_value", "string", "Literal source amount string"),
        ("Description", "row_label", "string", "Visible row label"),
        ("Parents", "ancestor_path", "json_array", "Visible ancestor label array"),
        ("Role", "row_role", "enum_string", "Header Data Total or empty"),
        ("Reporting period", "period", "string", "Explicit source period or empty"),
        ("Measure", "quantity_kind", "enum_string", "Measurement dimension, not a numeric value"),
        ("Multiplier", "scale", "integer_or_blank", "Explicit multiplicative factor"),
        ("Currency code", "currency", "string", "Source ISO currency or empty"),
        ("Source column", "source_column_ordinal", "integer_or_blank", "Actual numeric column ordinal"),
        ("Column heading", "source_column_label", "string", "Printed column header"),
    ]
    return {"archetypes": [{"schema_id": "generated-other", "statement": "other",
            "columns": [{"name": n, "role": r, "data_type": t, "description": d} for n, r, t, d in definitions],
            "source_number_format_rules": "Preserve printed signs and punctuation; do not repair values",
            "hierarchy_rules": "Visible section labels only"}], "contract_version": "csv-roles-v1", "no_numeric_repair": True}


def table(value="$ (1,234)", label="Revenue", scale="1000", period="2023-01-01/2023-12-31", extra=""):
    header = [c["name"] for c in plan_data()["archetypes"][0]["columns"]]
    fields = [value, label, '["Income"]', "", period, "money", scale, "USD", "0", "Year ended December 31, 2023"]
    return {"schema_id": "generated-other", "title": "Income statement", "scope": None,
            "csv": serialize([header, fields]) + extra}


def response(content, finish="stop"):
    return {"id": "synthetic-id", "choices": [{"finish_reason": finish, "message": {
                "content": json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content}}],
            "usage": {"cost": 0}, "model": "synthetic-model"}


class Canned:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def request(self, method, path, payload):
        self.requests.append(deepcopy(payload))
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value(payload)
        return value if isinstance(value, dict) and "choices" in value else response(value)


def generated_prompt(request):
    task = json.loads(request["messages"][1]["content"][0]["text"])
    return {"instructions": "Transcribe visible literal CSV. Never repair amounts.",
            "schema_plan_sha256": task["schema_plan_sha256"], "source_only": True,
            "changes_numbers_allowed": False, "contract_version": "csv-roles-v1"}


def fixture(root, selected=(1, 2, 4), condition="sequential"):
    source = root / "old-prepared"
    source.mkdir()
    pdf = root / "synthetic.pdf"
    pdf.write_bytes(b"synthetic PDF fixture, never submitted to a network")
    pdf_hash = file_hash(pdf)
    snapshot = {"procedure_sha256": "f255fad79eccfc9f941457a9e1821ff0e326ad8b890cb2319a7ac6c80513be7b",
        "document_id": "synthetic-document", "issuer_id": "synthetic-issuer", "pdf_sha256": pdf_hash,
        "pdf_page_count": 4, "dpi": 300, "preparation_model": "google/gemini-2.5-pro",
        "preparation_provider": "google-ai-studio", "preparation_reasoning_max_tokens": 256,
        "temperature": 0.0, "seed": 42, "limits": [3500, 32768, 6000, 6000]}
    images = []
    selected_images = {}
    for page in range(1, 5):
        path = source / "images" / f"p{page}.png"
        write_bytes(path, f"synthetic-source-page-{page}".encode())
        image = {"page": page, "image_file": str(path), "image_sha256": file_hash(path), "pdf_sha256": pdf_hash, "dpi": 300}
        images.append(image)
        selected_images[page] = {"path": path, "sha256": image["image_sha256"]}
    chunk = {"pages": [{"page": p, "table_titles": [], "statement_types": [], "hierarchy_notes": "", "continuation_notes": ""} for p in range(1, 5)], "document_notes": "synthetic"}
    survey = {"covered_pages": [1, 2, 3, 4], "full_document_page_count": 4, "chunks": [chunk], "image_provenance": images}
    structure = {"document_summary": "synthetic", "reviewed_pages": [1, 2, 3, 4], "statement_types": [],
                 "table_locations": [], "hierarchy_guidance": "source only", "unit_and_eps_complexity_cautions": "unknown"}
    old = _archive_prompts()
    def save_stage(directory, name, parsed, task, stage_images=()):
        content = [{"type": "text", "text": json.dumps(task, ensure_ascii=False)}]
        for page, path in stage_images:
            content.extend([{"type": "text", "text": f"Physical source PDF page {page}"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,"+base64.b64encode(path.read_bytes()).decode()}}])
        request = {"model": snapshot["preparation_model"], "provider": {"order": ["google-ai-studio"], "allow_fallbacks": False, "require_parameters": True},
            "temperature": 0.0, "seed": 42, "max_tokens": 3500 if name == "document_survey_chunk" else 32768,
            "reasoning": {"max_tokens": 256}, "messages": [
                {"role": "system", "content": old["SOURCE_ONLY"]+old["SURVEY" if name == "document_survey_chunk" else "STRUCTURE"]},
                {"role": "user", "content": content}], "response_format": {"json_schema": {"name": name}}}
        raw = response(parsed)
        write_json(directory / "request.json", request)
        write_json(directory / "response.json", raw)
        write_json(directory / "parsed.json", parsed)
        write_json(directory / "call.json", {"request_file_sha256": file_hash(directory / "request.json"),
            "request_body_sha256": digest(request), "response_file_sha256": file_hash(directory / "response.json"), "response_sha256": digest(raw)})
    save_stage(source / "survey/chunk-0001", "document_survey_chunk", chunk,
               {"physical_pages": [1, 2, 3, 4], "document_id": "synthetic-document"},
               [(p, Path(images[p-1]["image_file"])) for p in range(1, 5)])
    save_stage(source / "structure", "document_structure", structure, survey)
    for name, value in (("snapshot", snapshot), ("survey", survey), ("structure", structure)):
        write_json(source / f"{name}.json", value)
    return CSVConfig(experiment_id="synthetic", document_id="synthetic-document", issuer_id="synthetic-issuer",
        pdf=pdf, pdf_sha256=pdf_hash, selected_pages=list(selected), selected_images=selected_images,
        illustrative_pages=[1], condition=condition, model="google/gemini-2.5-pro", provider="google-ai-studio",
        source_prepared_dir=source, source_snapshot_sha256=file_hash(source / "snapshot.json"),
        source_survey_sha256=file_hash(source / "survey.json"), source_structure_sha256=file_hash(source / "structure.json"),
        output=root / "primary", prepared_dir=root / "csv-prepared", protocol_sha256="a"*64, selection_sha256="b"*64)


class ContractAndParserTests(unittest.TestCase):
    def setUp(self):
        self.plan = SchemaPlan.model_validate(plan_data())

    def test_generated_amount_named_quantity_is_not_measurement_kind(self):
        result = parse_page({"tables": [table()]}, self.plan, document_id="doc", page=1)
        record = result["records"][0]
        self.assertEqual(record["roles"]["printed_value"], "$ (1,234)")
        self.assertEqual(record["quantity"], "money")
        self.assertEqual(record["raw_fields"][0], "$ (1,234)")
        self.assertEqual(record["statement"], "other")

    def test_duplicate_or_missing_machine_roles_rejected(self):
        value = plan_data()
        value["archetypes"][0]["columns"][5]["role"] = "printed_value"
        with self.assertRaises(ValueError): SchemaPlan.model_validate(value)

    def test_model_version_labels_preserved_without_becoming_role_authority(self):
        value = plan_data()
        value["contract_version"] = "1.0"
        plan = SchemaPlan.model_validate(value)
        self.assertEqual(plan.model_dump(mode="json")["contract_version"], "1.0")
        prompt = GeneratedPrompt(instructions="Transcribe CSV only", schema_plan_sha256=digest(value),
            source_only=True, changes_numbers_allowed=False, contract_version="provider-label 2026")
        self.assertEqual(prompt.model_dump(mode="json")["contract_version"], "provider-label 2026")
        value["no_numeric_repair"] = False
        self.assertIs(SchemaPlan.model_validate(value).no_numeric_repair, False)
        value["no_numeric_repair"] = True
        value["archetypes"][0]["columns"][5]["role"] = "printed_value"
        with self.assertRaises(ValueError): SchemaPlan.model_validate(value)

    def test_compatible_types_are_required(self):
        value = plan_data()
        value["archetypes"][0]["columns"][0]["data_type"] = "enum_string"
        with self.assertRaises(ValueError): SchemaPlan.model_validate(value)

    def test_currency_and_scale_enum_strings_preserved_with_identical_projection(self):
        value = plan_data()
        for column in value["archetypes"][0]["columns"]:
            if column["role"] in {"currency", "scale"}:
                column["data_type"] = "enum_string"
        enum_plan = SchemaPlan.model_validate(value)
        self.assertEqual([c.data_type for c in enum_plan.archetypes[0].columns if c.role in {"currency", "scale"}],
                         ["enum_string", "enum_string"])
        for scale in ("1000", "3", ""):
            envelope = {"tables": [table(value="$ (1,234)", scale=scale)]}
            before = parse_page(envelope, self.plan, document_id="doc", page=1)
            after = parse_page(envelope, enum_plan, document_id="doc", page=1)
            self.assertEqual(before, after)
            self.assertEqual(after["records"][0]["roles"]["scale"], scale)
            if scale == "3":
                self.assertIsNone(after["records"][0]["scale"])
                self.assertIn("invalid_scale_no_conversion", after["records"][0]["metadata_issues"])

    def test_enum_currency_scale_does_not_relax_other_role_or_type_gates(self):
        value = plan_data()
        for column in value["archetypes"][0]["columns"]:
            if column["role"] in {"currency", "scale"}:
                column["data_type"] = "enum_string"
        value["archetypes"][0]["columns"][0]["data_type"] = "enum_string"
        with self.assertRaises(ValueError): SchemaPlan.model_validate(value)
        value["archetypes"][0]["columns"][0]["data_type"] = "string"
        value["archetypes"][0]["columns"][5]["role"] = "printed_value"
        with self.assertRaises(ValueError): SchemaPlan.model_validate(value)

    def test_description_lint_nonfatal_and_negation(self):
        self.assertEqual(description_diagnostics(self.plan)["warnings"], [])
        value = plan_data()
        value["archetypes"][0]["columns"][5]["description"] = "The numeric value string"
        plan = SchemaPlan.model_validate(value)
        self.assertFalse(description_diagnostics(plan)["warnings"][0]["fatal"])

    def test_rfc_quotes_unicode_commas_and_multiline(self):
        fields = [["label", "amount"], ['Revenue, α "quoted"\ncontinued', "($1,234)"], ["line\u2028separator", "−2"]]
        value = serialize(fields)
        parsed = parse_csv(value)
        self.assertEqual([r["fields"] for r in parsed], fields)
        self.assertEqual("".join(r["raw_record"] for r in parsed), value)

    def test_bad_quoting_is_table_local(self):
        broken = table()
        broken["csv"] = 'label,amount\nwrong"quote,1\n'
        result = parse_page({"tables": [broken, table()]}, self.plan, document_id="doc", page=1)
        self.assertFalse(result["tables"][0]["context_eligible"])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(len(page_context(result)["tables"]), 1)

    def test_ragged_fields_never_padded_and_remain_context(self):
        raw = table(extra="a,b,c\n\n")
        result = parse_page({"tables": [raw]}, self.plan, document_id="doc", page=1)
        self.assertEqual(result["tables"][0]["records"][-2]["fields"], ["a", "b", "c"])
        self.assertEqual(result["tables"][0]["records"][-1]["fields"], [])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(page_context(result)["tables"][0]["csv"], raw["csv"])

    def test_context_excludes_title_scope_and_preserves_exact_csv(self):
        raw = table(extra="ragged,record\n")
        raw["scope"] = "extra context forbidden"
        parsed = parse_page({"tables": [raw]}, self.plan, document_id="doc", page=3)
        context = page_context(parsed)
        self.assertEqual(set(context["tables"][0]), {"schema_id", "csv"})
        self.assertEqual(context_for(context, 4, "sequential"), context)
        self.assertIsNone(context_for(context, 5, "sequential"))
        self.assertIsNone(context_for(context, 4, "independent"))

    def test_context_no_silent_truncation(self):
        oversized = {"page": 1, "tables": [{"schema_id": "x", "csv": "x"*500001}]}
        with self.assertRaises(ContextTooLarge): context_for(oversized, 2, "sequential")

    def test_unknown_and_wrong_metadata_do_not_erase_amount(self):
        raw = table(scale="3", period="")
        raw["scope"] = {"invalid": "metadata"}
        parsed = parse_page({"tables": [raw]}, self.plan, document_id="doc", page=1)
        record = parsed["records"][0]
        self.assertEqual(record["raw_value"], "$ (1,234)")
        self.assertIsNone(record["scale"])
        self.assertEqual(record["roles"]["scale"], "3")
        self.assertIn("invalid_scale_no_conversion", record["metadata_issues"])
        self.assertIsNone(record["scope"])
        self.assertTrue(parsed["has_context_csv"])

    def test_unknown_schema_header_still_context_not_guessed_roles(self):
        raw = table()
        raw["schema_id"] = "unseen"
        parsed = parse_page({"tables": [raw]}, self.plan, document_id="doc", page=1)
        self.assertTrue(parsed["has_context_csv"])
        self.assertEqual(parsed["records"], [])
        self.assertEqual(parsed["tables"][0]["records"][1]["fields"][0], "$ (1,234)")

    def test_cleaner_only_role_field_changes(self):
        envelope = {"tables": [table(label="Total assets", value="(€1,234.50)", scale="3", period="December 31, 2023", extra="ragged,tail\n")]}
        original = deepcopy(envelope)
        cleaned, edits, audit = clean_page(envelope, self.plan, document_id="doc", page=1)
        before = parse_csv(envelope["tables"][0]["csv"])
        after = parse_csv(cleaned["tables"][0]["csv"])
        self.assertEqual(envelope, original)
        self.assertEqual(before[1]["fields"][:3], after[1]["fields"][:3])
        self.assertEqual(before[1]["fields"][4:], after[1]["fields"][4:])
        self.assertEqual(after[1]["fields"][3], "Total")
        self.assertEqual(before[-1]["raw_record"], after[-1]["raw_record"])
        self.assertEqual(len(edits), 1)
        self.assertTrue(audit["protected_fields_unchanged"])

    def test_empty_section_row_is_retained_and_classified(self):
        cleaned, _, _ = clean_page({"tables": [table(value="", label="Current assets")]}, self.plan, document_id="doc", page=1)
        result = parse_page(cleaned, self.plan, document_id="doc", page=1)
        self.assertEqual(result["records"][0]["raw_value"], "")
        self.assertEqual(result["records"][0]["roles"]["row_role"], "Header")

    def test_wire_schema_has_no_geometry_or_redundant_indices(self):
        text = json.dumps(strict_schema(PageEnvelope))
        for forbidden in ("rowspan", "row_count", "table_index", "page_index", "grid_column"):
            self.assertNotIn(forbidden, text)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = fixture(self.root)

    def tearDown(self): self.temp.cleanup()

    def prepared(self):
        return prepare_document(self.config, Canned([plan_data(), generated_prompt]))

    def test_source_stage_import_pure_complete_and_no_obsolete_stage_needed(self):
        imported = import_source_stages(self.config)
        self.assertEqual(imported["structure"]["reviewed_pages"], [1, 2, 3, 4])
        self.assertFalse((self.config.source_prepared_dir / "prepared.json").exists())
        self.assertFalse((self.config.source_prepared_dir / "schema").exists())

    def test_source_stage_tamper_fails(self):
        path = self.config.source_prepared_dir / "survey/chunk-0001/response.json"
        response_json = read_json(path)
        response_json["choices"][0]["message"]["content"] = "{}"
        write_json(path, response_json)
        with self.assertRaises(ValueError): import_source_stages(self.config)

    def test_accepted_local_attempt_precedes_preserved_invalid_import_origin(self):
        stage = self.config.source_prepared_dir / "survey/chunk-0001"
        valid = stage / "attempt-02"
        valid.mkdir()
        for name in ("request.json", "response.json", "parsed.json", "call.json"):
            shutil.move(str(stage / name), valid / name)
        invalid = self.root / "invalid-old-origin"
        write_json(invalid / "request.json", {"invalid": "prior request"})
        write_json(invalid / "response.json", {"invalid": "prior response"})
        write_json(stage / "import-origin.json", {"status": "known_invalid_model_stage", "request": str(invalid / "request.json"),
            "files": {str(invalid / name): file_hash(invalid / name) for name in ("request.json", "response.json")}})
        imported = import_source_stages(self.config)
        self.assertIn(str(valid / "response.json"), imported["files_sha256"])
        self.assertIn(str(invalid / "response.json"), imported["files_sha256"])

    def test_source_generator_settings_mismatch_fails(self):
        config = self.config.model_copy(update={"seed": 43})
        with self.assertRaises(ValueError): import_source_stages(config)

    def test_actual_new_schema_prompt_stages_are_used_and_saved(self):
        transport = Canned([plan_data(), generated_prompt])
        prepared = prepare_document(self.config, transport)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(prepared["status"], "complete")
        self.assertIn("generated_csv_schema", json.dumps(transport.requests[0]))
        self.assertIn("quantity", json.dumps(transport.requests[1]))
        again = prepare_document(self.config, Canned([]))
        self.assertEqual(again, prepared)

    def test_exact_previous_csv_gap_reset_and_no_current_csv_leak(self):
        prepared = self.prepared()
        first = table(extra="ragged,tail\n")
        transport = Canned([{"tables": [first]}, {"tables": [table(value="42")]}, {"tables": [table(value="99")]}])
        result = extract_document(self.config, prepared, transport)
        self.assertEqual(result["status"], "complete")
        tasks = [json.loads(p["messages"][1]["content"][0]["text"]) for p in transport.requests]
        self.assertIsNone(tasks[0]["untrusted_previous_raw_csv"])
        self.assertEqual(tasks[1]["untrusted_previous_raw_csv"], {"page": 1, "tables": [{"schema_id": "generated-other", "csv": first["csv"]}]})
        self.assertIsNone(tasks[2]["untrusted_previous_raw_csv"])
        self.assertEqual((self.config.output / "pages/p0001/tables/t000/raw.csv").read_bytes(), first["csv"].encode())
        replay = replay_page(self.config.output / "pages/p0001", SchemaPlan.model_validate(plan_data()), document_id=self.config.document_id, page=1)
        self.assertEqual(replay["status"], "pass")

    def test_independent_has_no_previous_context(self):
        config = self.config.model_copy(update={"condition": "independent"})
        prepared = self.prepared()
        transport = Canned([{"tables": [table()]}]*3)
        extract_document(config, prepared, transport)
        self.assertTrue(all(json.loads(p["messages"][1]["content"][0]["text"])["untrusted_previous_raw_csv"] is None for p in transport.requests))

    def test_bad_table_does_not_erase_valid_neighbor(self):
        prepared = self.prepared()
        transport = Canned([{"tables": [{"csv": 'bad"quote'}, table()]}, {"tables": []}, {"tables": []}])
        result = extract_document(self.config, prepared, transport)
        self.assertEqual(len(result["raw_records"]), 1)
        self.assertEqual(len(json.loads(transport.requests[1]["messages"][1]["content"][0]["text"])["untrusted_previous_raw_csv"]["tables"]), 1)

    def test_truncated_response_preserved_resets_context_without_replay(self):
        prepared = self.prepared()
        transport = Canned([response({"tables": [table()]}, finish="length"), {"tables": []}, {"tables": []}])
        result = extract_document(self.config, prepared, transport)
        self.assertEqual(result["rejected_pages"], [{"page": 1, "status": "rejected_response_envelope"}])
        self.assertTrue((self.config.output / "pages/p0001/call/model-content.utf8").exists())
        self.assertIsNone(json.loads(transport.requests[1]["messages"][1]["content"][0]["text"])["untrusted_previous_raw_csv"])

    def test_oversized_context_blocks_only_dependent_call(self):
        prepared = self.prepared()
        large = table(label="x"*500001)
        transport = Canned([{"tables": [large]}, {"tables": []}])
        result = extract_document(self.config, prepared, transport)
        self.assertEqual(result["blocked_pages"], [{"page": 2, "status": "blocked_context_too_large"}])
        self.assertEqual(len(transport.requests), 2)
        self.assertFalse((self.config.output / "pages/p0002/call/request.json").exists())
        self.assertTrue(result["all_requested_pages_processed"])
        self.assertFalse(result["all_requested_pages_attempted"])
        self.assertEqual(result["processed_pages"], [1, 2, 4])
        self.assertEqual(result["attempted_pages"], [1, 4])
        self.assertEqual(result["model_response_pages"], [1, 4])

    def test_auxiliary_skips_primary_blocked_page_and_keeps_attempted_neighbor(self):
        prepared = self.prepared()
        extract_document(self.config, prepared, Canned([{"tables": [table(label="x"*500001)]}, {"tables": []}]))
        auxiliary = self.config.model_copy(update={"output": self.root / "auxiliary", "selected_pages": [2, 4]})
        transport = Canned([{"tables": []}])
        result = extract_html(auxiliary, self.config.output, transport)
        self.assertEqual(result["blocked_primary_pages"], [2])
        self.assertEqual(result["processed_pages"], [2, 4])
        self.assertEqual(result["attempted_pages"], [4])
        self.assertTrue(result["all_requested_pages_processed"])
        self.assertFalse(result["all_requested_pages_attempted"])
        self.assertEqual(len(transport.requests), 1)

    def test_auxiliary_rejected_primary_envelope_still_binds_original_image(self):
        prepared = self.prepared()
        extract_document(self.config, prepared, Canned([{"tables": []}, response("not JSON"), {"tables": []}]))
        auxiliary = self.config.model_copy(update={"output": self.root / "auxiliary", "selected_pages": [2]})
        transport = Canned([{"tables": []}])
        result = extract_html(auxiliary, self.config.output, transport)
        self.assertEqual(result["attempted_pages"], [2])
        self.assertEqual(len(transport.requests), 1)

    def test_auxiliary_detects_primary_image_receipt_rewrite_against_actual_request(self):
        prepared = self.prepared()
        extract_document(self.config, prepared, Canned([{"tables": []}]*3))
        path = self.config.output / "pages/p0002/page-record.json"
        record = read_json(path)
        record["image"]["image_sha256"] = "f"*64
        write_json(path, record)
        auxiliary = self.config.model_copy(update={"output": self.root / "auxiliary", "selected_pages": [2]})
        transport = Canned([])
        with self.assertRaises(ValueError): extract_html(auxiliary, self.config.output, transport)
        self.assertEqual(transport.requests, [])

    def test_unknown_transport_no_automatic_retry(self):
        prepared = self.prepared()
        transport = Canned([RuntimeError("synthetic unknown")])
        with self.assertRaises(RuntimeError): extract_document(self.config, prepared, transport)
        with self.assertRaises(SavedCallFailure): extract_document(self.config, prepared, Canned([]))
        self.assertEqual(len(transport.requests), 1)

    def test_raw_response_binding_rejects_tampered_csv_and_rehash(self):
        prepared = self.prepared()
        extract_document(self.config, prepared, Canned([{"tables": [table()]}]*3))
        directory = self.config.output / "pages/p0001"
        raw = read_json(directory / "raw-envelope.json")
        raw["tables"][0]["csv"] = raw["tables"][0]["csv"].replace("1,234", "9,999")
        write_json(directory / "raw-envelope.json", raw)
        receipt = read_json(directory / "page-record.json")
        receipt["derived_files_sha256"]["raw-envelope.json"] = file_hash(directory / "raw-envelope.json")
        write_json(directory / "page-record.json", receipt)
        with self.assertRaises(ValueError): replay_page(directory, SchemaPlan.model_validate(plan_data()), document_id=self.config.document_id, page=1)

    def test_actual_content_byte_tamper_rejected(self):
        target = self.root / "call"
        call(target, {"test": True}, Canned([{"tables": []}]))
        write_bytes(target / "model-content.utf8", b"{}")
        with self.assertRaises(ValueError): replay_call(target)

    def test_actual_request_source_image_tamper_rejected(self):
        prepared = self.prepared()
        extract_document(self.config, prepared, Canned([{"tables": [table()]}]*3))
        directory = self.config.output / "pages/p0001"
        write_bytes(self.config.output / "images/p0001.png", b"wrong image")
        with self.assertRaises(ValueError): replay_page(directory, SchemaPlan.model_validate(plan_data()), document_id=self.config.document_id, page=1)

    def test_auxiliary_is_separate_uses_same_preceding_csv_not_current(self):
        prepared = self.prepared()
        first = table(value="111")
        extract_document(self.config, prepared, Canned([{"tables": [first]}, {"tables": [table(value="222")]}, {"tables": []}]))
        primary_before = {str(p): file_hash(p) for p in self.config.output.rglob("*") if p.is_file()}
        aux_config = self.config.model_copy(update={"output": self.root / "auxiliary", "selected_pages": [2]})
        transport = Canned([{"tables": [{"title": "partial", "html": "<html><body><table><tr><td>one</td></tr></table></body></html>"}]}])
        result = extract_html(aux_config, self.config.output, transport)
        self.assertEqual(result["status"], "complete")
        task = json.loads(transport.requests[0]["messages"][1]["content"][0]["text"])
        self.assertEqual(set(task), {"physical_page", "document_id", "untrusted_previous_raw_csv"})
        self.assertEqual(task["untrusted_previous_raw_csv"]["tables"][0]["csv"], first["csv"])
        self.assertNotIn("222", json.dumps(task))
        self.assertEqual(primary_before, {str(p): file_hash(p) for p in self.config.output.rglob("*") if p.is_file()})

    def test_auxiliary_failure_cannot_erase_primary(self):
        prepared = self.prepared()
        extract_document(self.config, prepared, Canned([{"tables": [table()]}]*3))
        prior_hash = file_hash(self.config.output / "results.json")
        aux_config = self.config.model_copy(update={"output": self.root / "auxiliary", "selected_pages": [2]})
        result = extract_html(aux_config, self.config.output, Canned([response("not JSON")]))
        self.assertEqual(result["rejected_pages"], [2])
        self.assertEqual(file_hash(self.config.output / "results.json"), prior_hash)

    def test_reasoning_only_gemini_and_no_secret_loading(self):
        config = self.config.model_copy(update={"model": "meta-llama/llama-4-maverick", "provider": "parasail-fp8"})
        prepared = self.prepared()
        transport = Canned([{"tables": []}]*3)
        extract_document(config, prepared, transport)
        self.assertTrue(all("reasoning" not in request for request in transport.requests))
        self.assertEqual(transport.requests[0]["provider"]["order"], ["parasail-fp8"])


if __name__ == "__main__":
    unittest.main()
