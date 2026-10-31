import copy
import json
from pathlib import Path
import tempfile
import unittest

from ftv_reconstruction.model_contracts import GeometryPage, SchemaPlan, generated_extraction_schema
from ftv_reconstruction.normalization import normalize_geometry
from ftv_reconstruction.pipeline import procedure_hash
import test_pipeline as canned
from test_pipeline import CannedTransport, geometry_page, schema_plan


def prospective_wire(canonical):
    """Remove ONLY the five prospectively omitted metadata fields from a fixture.

    This is not reclassification/recovery of an old response. Old DEV remains
    rejected under its original contract. Cell/row content is never changed.
    """
    wire = copy.deepcopy(canonical)
    for table in wire["tables"]:
        table.pop("table_index", None)
        for key in ("row_count", "column_count", "row_sections"):
            table["grid"].pop(key, None)
        for cell in table["grid"]["cells"]:
            cell.pop("kind", None)
    return wire


class ModelNormalizationTests(unittest.TestCase):
    def two_column_wire(self):
        wire = prospective_wire(geometry_page(2))
        table = wire["tables"][0]
        header = copy.deepcopy(table["grid"]["cells"][1])
        header.update(source_cell_id="h2", column=2, text="2022")
        table["grid"]["cells"].insert(2, header)
        amount = copy.deepcopy(table["grid"]["cells"][-1])
        amount.update(source_cell_id="value2", column=2, text="90")
        table["grid"]["cells"].append(amount)
        table["columns"].append({"column_index": 1, "grid_column": 2, "label": "2022", "period": "2022-12-31"})
        return wire

    def test_v6_logical_column_ordinals_derive_only_from_array_order(self):
        wire = self.two_column_wire()
        # V5's supplied one-based redundant ordinals remain contradictory, not
        # repaired. The prospective V6 wire omits them entirely.
        for i, column in enumerate(wire["tables"][0]["columns"], 1): column["column_index"] = i
        with self.assertRaisesRegex(ValueError, "contradicts columns-array"):
            GeometryPage.model_validate(wire)
        for column in wire["tables"][0]["columns"]: column.pop("column_index")
        original = copy.deepcopy(wire)
        canonical, audit = normalize_geometry(wire)
        parsed = GeometryPage.model_validate(wire)
        self.assertEqual([c.column_index for c in parsed.tables[0].columns], [0, 1])
        self.assertEqual([c.grid_column for c in parsed.tables[0].columns], [1, 2])
        self.assertEqual([{k: v for k, v in c.items() if k != "column_index"} for c in canonical["tables"][0]["columns"]], original["tables"][0]["columns"])
        self.assertEqual(wire, original)
        self.assertTrue(audit["physical_columns_labels_periods_unchanged"])
        self.assertEqual(audit["derived_metadata"][0]["numeric_column_ordinals"], [
            {"array_position": 0, "derived_column_index": 0, "unchanged_grid_column": 1},
            {"array_position": 1, "derived_column_index": 1, "unchanged_grid_column": 2}])
        schema = generated_extraction_schema(SchemaPlan.model_validate(schema_plan()))
        self.assertNotIn("column_index", schema["$defs"]["NumericColumn"]["properties"])
        self.assertNotIn("column_index", schema["$defs"]["NumericColumn"]["required"])

    def test_v6_never_repairs_bad_or_duplicate_physical_numeric_columns(self):
        for bad in ("outside", "duplicate"):
            wire = self.two_column_wire()
            for column in wire["tables"][0]["columns"]: column.pop("column_index")
            wire["tables"][0]["columns"][1]["grid_column"] = 99 if bad == "outside" else 1
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "distinct existing physical columns"):
                GeometryPage.model_validate(wire)

    def test_wire_schema_omits_only_redundant_fields_and_preserves_cells_spans_mapping(self):
        schema = generated_extraction_schema(SchemaPlan.model_validate(schema_plan()))
        for model, omitted in (("PhysicalGrid", ["row_count", "column_count", "row_sections"]),
                               ("FinancialCell", ["kind"]), ("GeometryTable", ["table_index"])):
            self.assertTrue(all(key not in schema["$defs"][model]["properties"] for key in omitted))
            self.assertTrue(all(key not in schema["$defs"][model]["required"] for key in omitted))
        self.assertIn("grid_column", schema["$defs"]["NumericColumn"]["properties"])
        self.assertIn("rowspan", schema["$defs"]["FinancialCell"]["properties"])

    def test_real_gemini_fixtures_become_valid_only_under_prospective_derived_contract(self):
        for page in (3, 4):
            old = json.loads((Path(__file__).parent / "fixtures" / f"excluded-v4-gemini-p{page}.json").read_text())
            with self.subTest(page=page):
                with self.assertRaises(ValueError):
                    GeometryPage.model_validate(old)  # Do not silently repair old contradictory metadata.
                wire = prospective_wire(old)
                original = copy.deepcopy(wire)
                canonical, audit = normalize_geometry(wire)
                parsed = GeometryPage.model_validate(wire)
                self.assertEqual(wire, original)
                self.assertEqual(parsed.model_dump(mode="json"), GeometryPage.model_validate(canonical).model_dump(mode="json"))
                self.assertTrue(audit["cell_text_coordinates_spans_units_unchanged"])
                self.assertTrue(audit["body_metadata_unchanged"])
                self.assertEqual(audit["cells_added"], 0)
                self.assertFalse(audit["used_source_or_gold"])
                if page == 3:
                    self.assertEqual(parsed.tables[0].grid.row_count, 37)
                else:
                    self.assertEqual(audit["derived_metadata"][0]["default_header_semantic_rows"], [0, 1])

    def test_real_llama_holes_and_wrong_column_mapping_still_fail(self):
        old = json.loads((Path(__file__).parent / "fixtures/excluded-v4-llama-p3.json").read_text())
        with self.assertRaisesRegex(ValueError, "Uncovered physical slots"):
            GeometryPage.model_validate(prospective_wire(old))
        wire = prospective_wire(geometry_page(2))
        wire["tables"][0]["columns"][0]["grid_column"] = 99
        with self.assertRaisesRegex(ValueError, "existing physical columns"):
            GeometryPage.model_validate(wire)

    def test_missing_body_rows_overlap_missing_cell_duplicate_ids_not_repaired(self):
        for problem in ("body", "overlap", "cell", "id"):
            wire = prospective_wire(geometry_page(2))
            table = wire["tables"][0]
            if problem == "body": table["rows"].pop()
            if problem == "overlap": table["grid"]["cells"][2]["colspan"] = 2
            if problem == "cell": table["grid"]["cells"].pop()
            if problem == "id": table["grid"]["cells"][-1]["source_cell_id"] = "label"
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                GeometryPage.model_validate(wire)

    def test_missing_required_shape_is_a_schema_rejection_without_invention(self):
        for field in ("grid", "rows"):
            wire = prospective_wire(geometry_page(2))
            wire["tables"][0].pop(field)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "Malformed required"):
                GeometryPage.model_validate(wire)

    def test_header_metadata_optional_numeric_label_reference_rejected(self):
        wire = prospective_wire(geometry_page(2))
        table = wire["tables"][0]
        table["rows"] = table["rows"][1:]
        parsed = GeometryPage.model_validate(wire)
        self.assertEqual(parsed.tables[0].rows[0].role, "header")
        table["rows"][0]["label_cell_ids"] = ["value"]
        with self.assertRaisesRegex(ValueError, "numeric data cell"):
            GeometryPage.model_validate(wire)

    def test_actual_canned_pipeline_saves_raw_bound_normalization_audit(self):
        class WireTransport(CannedTransport):
            def request(self, method, path, payload=None):
                result = super().request(method, path, payload)
                if payload["response_format"]["json_schema"]["name"] == "financial_geometry_page":
                    model = prospective_wire(json.loads(result["choices"][0]["message"]["content"]))
                    model["tables"][0]["rows"] = model["tables"][0]["rows"][1:]
                    result["choices"][0]["message"]["content"] = json.dumps(model)
                return result
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper = canned.PipelineTests(); config = helper.config(root)
            result = helper.run_pipeline(config, WireTransport())
            self.assertEqual(result["accepted_pages"], [2, 3])
            audit = json.loads((config.output / "pages/p0002/model-normalization-audit.json").read_text())
            self.assertEqual(audit["canonical_validation"], "pass")
            self.assertTrue(audit["cell_text_coordinates_spans_units_unchanged"])
            self.assertIn("raw_response_file_sha256", audit)
            self.assertEqual(audit["derived_metadata"][0]["default_header_semantic_rows"], [0])


if __name__ == "__main__":
    unittest.main()
