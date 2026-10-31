"""Synthetic unit tests; none of these fixtures is a real-study evaluation."""

import copy
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ftv_reconstruction.accounting import (
    EPSLedger, RetainedEarningsLedger, check_eps, check_retained_earnings, denominator_ledger,
)
from ftv_reconstruction.cleaning import CleaningEdit, audit_cleaning
from ftv_reconstruction.contracts import (
    LayoutReference, SourceGrid, grid_html, historical_json_to_grid, teds_inputs,
)
from ftv_reconstruction.stages import STAGES, StageManifest, verify_stages

H = "a" * 64
EVIDENCE = {"pdf_sha256": H, "page": 1, "quote": "SYNTHETIC source evidence"}
PERIOD = {"start": "2023-01-01", "end": "2023-06-30"}
REVIEW = {"status": "eligible", "reviewer": "synthetic-fixture-reviewer",
          "reason": "SYNTHETIC simple-capital-structure review", "evidence": [EVIDENCE],
          "prediction_blind": True}


def fact(ident, value, *, quantity="money", scale=1, instant=None):
    return {"id": ident, "raw_value": value, "quantity": quantity, "scale": scale,
            "currency": None if quantity == "shares" else "USD", "scope": "consolidated",
            "instant": instant, "period": None if instant else PERIOD,
            "evidence": EVIDENCE}


def eps():
    return {"case_id": "synthetic-basic", "variant": "basic", "eligibility": REVIEW,
            "capital_structure": "simple", "numerator_basis": "basic_available_common",
            "denominator_basis": "weighted_average_basic", "scope": "consolidated",
            "period": PERIOD, "currency": "USD", "absolute_tolerance": "0.005",
            "numerator": fact("numerator", "120", scale=1000000),
            "shares": fact("shares", "60,000", quantity="shares", scale=1000),
            "reported_eps": fact("reported", "2.00", quantity="per_share")}


def retained():
    # Synthetic reproducer for the cited printed 3M bridge, not a reviewed reference.
    quarter = {"start": "2023-04-01", "end": "2023-06-30"}
    scope = "parent_3M_shareholder_retained_earnings"
    ledger = {"case_id": "synthetic-other-movement", "eligibility": REVIEW,
            "scope": scope, "currency": "USD", "period": quarter,
            "beginning": fact("opening", "47,966", instant="2023-03-31"),
            "ending": fact("closing", "40,290", instant="2023-06-30"),
            "movements": [
                {"kind": "net_income", "direction": 1, "fact": fact("income", "(6,841)")},
                {"kind": "dividends", "direction": -1, "fact": fact("dividends", "828")},
                {"kind": "other", "direction": 1, "fact": fact("stock-plan", "(7)")},
            ], "movements_complete": True, "other_movements_complete": True,
            "completeness_evidence": [EVIDENCE], "absolute_tolerance": "0.01"}
    for f in [ledger["beginning"], ledger["ending"], *[m["fact"] for m in ledger["movements"]]]:
        f["scope"] = scope
        if f["instant"] is None:
            f["period"] = quarter
    return ledger


def grid():
    return {"pdf_sha256": H, "page": 1, "image_sha256": H, "dpi": 300,
            "table_id": "p1-t0", "box": {"left": 0, "top": 0, "right": 1, "bottom": 1},
            "row_count": 2, "column_count": 2, "row_sections": ["head", "body"],
            "provenance": "source_image_annotation", "cells": [
                {"source_cell_id": "h", "row": 0, "column": 0, "rowspan": 1,
                 "colspan": 2, "text": "Year <2023>", "kind": "header", "header_scope": "column",
                 "box": {"left": 0, "top": 0, "right": 1, "bottom": .5}},
                {"source_cell_id": "blank", "row": 1, "column": 0, "rowspan": 1,
                 "colspan": 1, "text": "", "kind": "blank", "header_scope": None,
                 "box": {"left": 0, "top": .5, "right": .5, "bottom": 1}},
                {"source_cell_id": "v", "row": 1, "column": 1, "rowspan": 1,
                 "colspan": 1, "text": "(123)", "kind": "data", "header_scope": None,
                 "box": {"left": .5, "top": .5, "right": 1, "bottom": 1}},
            ]}


class GeometryTests(unittest.TestCase):
    def test_observed_span_blank_text_and_header_preserved(self):
        html = grid_html(SourceGrid.model_validate(grid()))
        self.assertIn('<thead><tr><td colspan="2">Year &lt;2023&gt;</td>', html)
        self.assertIn('<tbody><tr><td></td><td>(123)</td>', html)

    def test_overlap_and_missing_blank_rejected(self):
        for change in ("overlap", "missing"):
            g = grid()
            if change == "overlap":
                g["cells"][1]["colspan"] = 2
            else:
                g["cells"].pop(1)
            with self.subTest(change=change), self.assertRaises(ValueError):
                SourceGrid.model_validate(g)

    def test_rowspan_cannot_cross_header_boundary(self):
        g = grid(); g["cells"][0]["rowspan"] = 2
        with self.assertRaisesRegex(ValueError, "head/body"):
            SourceGrid.model_validate(g)

    def test_logically_distinct_cells_cannot_overlap_physical_boxes(self):
        g = grid(); g["cells"][2]["box"]["left"] = .4
        with self.assertRaisesRegex(ValueError, "box interiors"):
            SourceGrid.model_validate(g)

    def test_no_historical_span_invention(self):
        with self.assertRaisesRegex(ValueError, "reannotation"):
            historical_json_to_grid({"rows": []})

    def test_only_reviewed_pixel_reference_pairs_are_scoreable(self):
        source = SourceGrid.model_validate(grid())
        prediction = SourceGrid.model_validate({**grid(), "provenance": "new_model_prediction"})
        r = LayoutReference(grid=source, annotator="synthetic", annotation_instructions_sha256=H)
        with self.assertRaisesRegex(ValueError, "review"):
            teds_inputs(r, prediction)
        r = r.model_copy(update={"reviewed": True, "reviewer": "synthetic", "prediction_blind": True})
        out = teds_inputs(r, prediction)
        self.assertIsNone(out["score"])
        self.assertEqual(out["reference_html"], out["prediction_html"])
        p = prediction.model_copy(update={"image_sha256": "b" * 64})
        with self.assertRaisesRegex(ValueError, "identity"):
            teds_inputs(r, p)


class AccountingTests(unittest.TestCase):
    def test_eps_explicit_different_money_share_scales_normalized(self):
        self.assertEqual(check_eps(EPSLedger.model_validate(eps()))["status"], "pass")

    def test_eps_unknown_units_and_mixed_currency_skip(self):
        for field, value in (("scale", None), ("currency", "EUR"), ("scope", "parent")):
            e = eps(); e["numerator"][field] = value
            with self.subTest(field=field):
                self.assertEqual(check_eps(EPSLedger.model_validate(e))["status"], "skipped")

    def test_eps_complex_or_unreviewed_structure_skips(self):
        for key, value in (("capital_structure", "complex"), ("capital_structure", "unknown")):
            e = eps(); e[key] = value
            self.assertEqual(check_eps(EPSLedger.model_validate(e))["status"], "skipped")
        e = eps(); e["eligibility"] = {**REVIEW, "status": "pending"}
        self.assertEqual(check_eps(EPSLedger.model_validate(e))["status"], "skipped")

    def test_eps_diluted_cannot_substitute_basic_numerator(self):
        e = eps(); e["variant"] = "diluted"; e["denominator_basis"] = "weighted_average_diluted"
        self.assertEqual(check_eps(EPSLedger.model_validate(e))["status"], "skipped")
        e["numerator_basis"] = "diluted_adjusted_available_common"
        self.assertEqual(check_eps(EPSLedger.model_validate(e))["status"], "pass")

    def test_eps_dates_and_unweighted_shares_skip(self):
        e = eps(); e["shares"]["period"] = {"start": "2023-04-01", "end": "2023-06-30"}
        self.assertEqual(check_eps(EPSLedger.model_validate(e))["status"], "skipped")
        e = eps(); e["denominator_basis"] = "ending_shares"
        self.assertEqual(check_eps(EPSLedger.model_validate(e))["status"], "skipped")

    def test_eps_arithmetic_failure_is_not_repaired(self):
        e = eps(); e["reported_eps"]["raw_value"] = "3.00"
        r = check_eps(EPSLedger.model_validate(e))
        self.assertEqual(r["status"], "fail"); self.assertEqual(r["observed"], "3.00")

    def test_retained_full_bridge_includes_other_stock_plan_movement(self):
        r = check_retained_earnings(RetainedEarningsLedger.model_validate(retained()))
        self.assertEqual(r["status"], "pass"); self.assertEqual(r["expected"], "40290")

    def test_retained_incomplete_other_movements_and_missing_dividend_skip(self):
        r = retained(); r["other_movements_complete"] = False; r["movements"].pop()
        self.assertEqual(check_retained_earnings(RetainedEarningsLedger.model_validate(r))["status"], "skipped")
        r = retained(); r["movements"].pop(1)
        self.assertEqual(check_retained_earnings(RetainedEarningsLedger.model_validate(r))["status"], "skipped")

    def test_retained_scope_period_unknown_units_and_currency_skip(self):
        for key, value in (("scale", None), ("currency", "EUR"), ("scope", "parent"),
                           ("period", {"start": "2023-01-01", "end": "2023-06-30"})):
            r = retained(); r["movements"][0]["fact"][key] = value
            with self.subTest(key=key):
                self.assertEqual(check_retained_earnings(RetainedEarningsLedger.model_validate(r))["status"], "skipped")

    def test_retained_opening_date_must_precede_interval(self):
        r = retained(); r["beginning"]["instant"] = "2023-01-01"
        self.assertEqual(check_retained_earnings(RetainedEarningsLedger.model_validate(r))["status"], "skipped")

    def test_dividends_cannot_be_added_to_make_bridge_pass(self):
        r = retained(); r["movements"][1]["direction"] = 1
        self.assertEqual(check_retained_earnings(RetainedEarningsLedger.model_validate(r))["status"], "skipped")

    def test_all_candidates_and_duplicate_case_denominators(self):
        summary = denominator_ledger([{"case_id": "a", "status": "pass"},
                                      {"case_id": "b", "status": "skipped"}])
        self.assertEqual((summary["candidates"], summary["applicable"], summary["skipped"]), (2, 1, 1))
        with self.assertRaisesRegex(ValueError, "Repeated"):
            denominator_ledger([{"case_id": "a", "status": "pass"}] * 2)


def extraction():
    return {"tables": [{"table_id": "t0", "scope": "consolidated", "columns": [
        {"column_index": 0}, {"column_index": 1}], "rows": [
        {"row_id": "r0", "label": "Total assets", "role": "data", "path": [], "cells": [
            {"source_cell_id": "c0", "column_index": 0, "raw_value": "100", "scale": 1,
             "currency": "USD", "period": "2023-12-31"}]}]}]}


class CleaningTests(unittest.TestCase):
    def test_only_declared_role_and_column_changes_accepted(self):
        before = extraction(); after = copy.deepcopy(before)
        row = after["tables"][0]["rows"][0]; row["role"] = "total"; row["cells"][0]["column_index"] = 1
        edits = [CleaningEdit(target="/tables/0/rows/0", field="role", before="data", after="total",
                              reason="SYNTHETIC printed total", evidence=EVIDENCE),
                 CleaningEdit(target="/tables/0/rows/0/cells/0", field="column_index", before=0, after=1,
                              reason="SYNTHETIC source alignment", evidence=EVIDENCE)]
        self.assertEqual(audit_cleaning(before, after, edits)["numbers_changed"], 0)
        with self.assertRaisesRegex(ValueError, "every change"):
            audit_cleaning(before, after, [])

    def test_number_total_scale_period_and_scope_repairs_rejected(self):
        before = extraction()
        for key, value in (("raw_value", "99"), ("scale", 1000), ("period", "2022-12-31")):
            after = copy.deepcopy(before); after["tables"][0]["rows"][0]["cells"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "protected"):
                audit_cleaning(before, after, [])
        after = copy.deepcopy(before); after["tables"][0]["scope"] = "parent"
        with self.assertRaisesRegex(ValueError, "protected"):
            audit_cleaning(before, after, [])

    def test_drop_or_duplicate_cells_rejected(self):
        before = extraction(); after = copy.deepcopy(before); after["tables"][0]["rows"][0]["cells"] = []
        with self.assertRaisesRegex(ValueError, "adds/removes"):
            audit_cleaning(before, after, [])
        before["tables"][0]["rows"][0]["cells"][0].pop("source_cell_id")
        with self.assertRaisesRegex(ValueError, "stable"):
            audit_cleaning(before, before, [])


class StageTests(unittest.TestCase):
    def fixture(self, root):
        artifacts = []; previous = H
        for i, stage in enumerate(STAGES):
            data = f"SYNTHETIC-{stage}".encode(); path = f"{i}.json"; (root / path).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            artifact = {"stage": stage, "path": path, "sha256": digest, "input_sha256": [previous],
                        "procedure_sha256": H}
            if stage != "audited_cleaning":
                artifact.update(model_request_sha256=digest, raw_response_sha256=digest,
                                request_path=path, response_path=path)
            artifacts.append(artifact); previous = digest
        return StageManifest(experiment_id="synthetic", protocol_sha256=H, source_pdf_sha256=H,
                             selection_freeze_sha256=H, development_examples_sha256=[],
                             development_issuer_ids=["development"], evaluation_issuer_id="evaluation",
                             full_document_page_count=2, structure_reviewed_pages=[1, 2],
                             generated_schema_validated=True, generated_prompt_safety_reviewed=True,
                             artifacts=artifacts)

    def test_all_five_stages_hashes_and_lineage_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); m = self.fixture(root)
            self.assertEqual(verify_stages(m, root)["network_calls"], 0)
            (root / "2.json").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "changed"):
                verify_stages(m, root)

    def test_partial_document_or_example_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); m = self.fixture(root)
            for update in ({"structure_reviewed_pages": [1]}, {"evaluation_issuer_id": "development"}):
                with self.subTest(update=update), self.assertRaises(ValueError):
                    verify_stages(m.model_copy(update=update), root)

    def test_skipped_stage_and_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); m = self.fixture(root)
            with self.assertRaisesRegex(ValueError, "five ordered"):
                verify_stages(m.model_copy(update={"artifacts": m.artifacts[1:]}), root)
            artifacts = list(m.artifacts); artifacts[0] = artifacts[0].model_copy(update={"path": "../outside"})
            with self.assertRaisesRegex(ValueError, "inside"):
                verify_stages(m.model_copy(update={"artifacts": artifacts}), root)


if __name__ == "__main__":
    unittest.main()
