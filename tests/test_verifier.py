from __future__ import annotations

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from financial_table_verifier.consistency import check_consistency
from financial_table_verifier.demo import MODEL, FixtureTransport, fixture_pages, make_pdf
from financial_table_verifier.evaluation import (
    cell_outcomes,
    compare_runs,
    evaluate_run,
    score_pages,
)
from financial_table_verifier.numbers import parse_number
from financial_table_verifier.openrouter import (
    APIError,
    ConnectError,
    OpenRouterClient,
    OutcomeUnknown,
    byok_cost,
    parse_response,
    provider_error,
    read_stream,
    request_body,
)
from financial_table_verifier.pipeline import run_experiment, validate_page
from financial_table_verifier.render import render_page
from financial_table_verifier.schema import Experiment, Manifest, PageExtraction
from financial_table_verifier.screen_analysis import analyze_screens
from financial_table_verifier.storage import digest, file_hash, read_json, run_lock, write_json
from financial_table_verifier.summary import exact_two_sided, summarize


class NumericTests(unittest.TestCase):
    def test_accounting_formats(self):
        for raw, expected in [
            ("1,234.50", "1234.50"),
            ("($1,234)", "-1234"),
            ("$ (1,234)", "-1234"),
            ("$ 8,325", "8325"),
            ("-42", "-42"),
            ("0", "0"),
            ("\u221212", "-12"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(parse_number(raw), Decimal(expected))

    def test_ambiguous_numbers_are_not_repaired(self):
        for raw in ["-", "\u2014", "12(a)", "1,23", "1.2m", "10%", "NaN", "Inf", "(-2)", "$$5"]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_number(raw))


class SchemaAndMetricsTests(unittest.TestCase):
    def setUp(self):
        self.raw = fixture_pages()
        self.pages = [PageExtraction.model_validate(p) for p in self.raw]
        self.config = Experiment(model=MODEL, manifest=Path("manifest.json"), output=Path("runs/x"))

    def test_duplicate_cell_rejected(self):
        self.raw[0]["tables"][0]["rows"][0]["cells"] *= 2
        with self.assertRaises(ValidationError):
            PageExtraction.model_validate(self.raw[0])

    def test_duplicate_table_rejected(self):
        self.raw[0]["tables"] *= 2
        with self.assertRaises(ValidationError):
            PageExtraction.model_validate(self.raw[0])

    def test_unknown_fields_rejected(self):
        self.raw[0]["unexpected"] = True
        with self.assertRaises(ValidationError):
            PageExtraction.model_validate(self.raw[0])

    def test_balance_across_continuation(self):
        report = check_consistency(self.pages, self.config, [])
        self.assertEqual((report["passed"], report["applicable"]), (1, 1))

    def test_attributable_net_income_is_not_compared_to_consolidated_pretax(self):
        page = copy.deepcopy(self.pages[0])
        table = page.tables[0]
        table.statement = "income_statement"
        sample = copy.deepcopy(table.rows[0])
        table.rows = []
        for index, (concept, label, value) in enumerate([
            ("net_income", "Net income attributable to 3M", "78"),
            ("income_before_tax", "Income before tax", "60"),
            ("income_tax_expense", "Income tax benefit", "(23)"),
        ]):
            row = copy.deepcopy(sample)
            row.row_index, row.concept, row.label = index, concept, label
            row.cells[0].raw_value = value
            table.rows.append(row)
        checks = check_consistency([page], self.config, [])["checks"]
        net_income = next(c for c in checks if c["check"] == "net_income")
        self.assertEqual(net_income["status"], "skipped")
        self.assertIn("attribution basis", net_income["reason"])
        table.rows[0].label = "Consolidated net income"
        table.rows[0].cells[0].raw_value = "83"
        checks = check_consistency([page], self.config, [])["checks"]
        self.assertEqual(next(c for c in checks if c["check"] == "net_income")["status"], "pass")

    def test_units_normalized(self):
        cell = self.pages[0].tables[0].rows[0].cells[0]
        cell.raw_value, cell.scale = "100,000", 1
        self.assertEqual(check_consistency(self.pages, self.config, [])["passed"], 1)

    def test_unknown_units_skip_not_pass(self):
        self.pages[0].tables[0].rows[0].cells[0].scale = None
        report = check_consistency(self.pages, self.config, [])
        self.assertEqual(report["applicable"], 0)
        self.assertIsNone(report["pass_rate"])

    def test_currency_mismatch_skipped(self):
        self.pages[0].tables[0].rows[0].cells[0].currency = "EUR"
        self.assertEqual(check_consistency(self.pages, self.config, [])["skipped"], 1)

    def test_duplicate_concept_skipped(self):
        row = copy.deepcopy(self.pages[1].tables[0].rows[0])
        row.row_index = 2
        self.pages[1].tables[0].rows.append(row)
        self.assertEqual(check_consistency(self.pages, self.config, [])["applicable"], 0)

    def test_balanced_but_wrong_is_not_accurate(self):
        predictions = copy.deepcopy(self.pages)
        for page in predictions:
            for row in page.tables[0].rows:
                row.cells[0].raw_value = str(int(row.cells[0].raw_value) * 2)
        self.assertEqual(check_consistency(predictions, self.config, [])["pass_rate"], 1)
        self.assertEqual(score_pages(predictions, self.pages)["numeric_recall"], 0)

    def test_missing_page_penalized(self):
        report = score_pages(self.pages[:1], self.pages)
        self.assertEqual((report["numeric_correct"], report["numeric_gold_cells"]), (1, 3))
        self.assertAlmostEqual(report["numeric_recall"], 1 / 3)

    def test_extra_prediction_penalized(self):
        predictions = copy.deepcopy(self.pages)
        row = copy.deepcopy(predictions[1].tables[0].rows[0])
        row.row_index = 2
        predictions[1].tables[0].rows.append(row)
        self.assertEqual(score_pages(predictions, self.pages)["numeric_precision"], 3 / 4)
        self.assertEqual(score_pages(predictions, self.pages)["numeric_recall"], 1)
        self.assertEqual(score_pages(predictions, self.pages)["numeric_f1"], 6 / 7)

    def test_semantic_outcomes_reject_correct_values_on_wrong_labels(self):
        predictions = copy.deepcopy(self.pages)
        predictions[0].tables[0].rows[0].label = "Incorrect label"
        scores = score_pages(predictions, self.pages)
        self.assertEqual(scores["numeric_correct"], 3)
        self.assertEqual(scores["semantic_correct"], 2)
        outcomes = cell_outcomes(predictions, self.pages)
        self.assertEqual(sum(bool(o["semantic"]) for o in outcomes.values()), 2)

    def test_wrong_period_penalized(self):
        predictions = copy.deepcopy(self.pages)
        predictions[0].tables[0].columns[0].period = "2023-12-31"
        self.assertEqual(score_pages(predictions, self.pages)["numeric_correct"], 2)

    def test_hierarchy_independent_of_numeric_value(self):
        predictions = copy.deepcopy(self.pages)
        predictions[0].tables[0].rows[0].path = []
        report = score_pages(predictions, self.pages)
        self.assertEqual(report["numeric_recall"], 1)
        self.assertEqual(report["hierarchy_correct"], 2)

    def test_inserted_row_only_recovered_by_aligned_diagnostic(self):
        predictions = copy.deepcopy(self.pages)
        table = predictions[1].tables[0]
        header = copy.deepcopy(table.rows[0])
        header.label, header.role, header.cells = "Liabilities and equity", "header", []
        table.rows.insert(0, header)
        for index, row in enumerate(table.rows):
            row.row_index = index
        report = score_pages(predictions, self.pages)
        self.assertEqual(report["numeric_correct"], 1)
        self.assertEqual(report["aligned_numeric_correct"], 3)
        self.assertEqual(report["aligned_numeric_recall"], 1)

    def test_aligned_diagnostic_keeps_value_and_period_checks(self):
        predictions = copy.deepcopy(self.pages)
        predictions[1].tables[0].rows[0].cells[0].raw_value = "1"
        predictions[0].tables[0].columns[0].period = "2023-12-31"
        self.assertEqual(score_pages(predictions, self.pages)["aligned_numeric_correct"], 1)

    def test_typographic_label_variants_match_but_raw_values_stay_verbatim(self):
        predictions = copy.deepcopy(self.pages)
        references = copy.deepcopy(self.pages)
        row = references[1].tables[0].rows[0]
        row.label = "Stockholders\u2019 equity \u2013 total"
        predictions[1].tables[0].rows[0].label = "STOCKHOLDERS' EQUITY - total"
        self.assertEqual(
            score_pages(predictions, references)["hierarchy_correct"],
            score_pages(references, references)["hierarchy_correct"],
        )
        references[1].tables[0].rows[0].cells[0].raw_value = "\u2014"
        predictions[1].tables[0].rows[0].cells[0].raw_value = "-"
        self.assertLess(
            score_pages(predictions, references)["raw_exact_correct"],
            score_pages(references, references)["raw_exact_correct"],
        )

    def test_continuation_requires_previous_page(self):
        with self.assertRaises(ValueError):
            validate_page(self.pages[1], 2, None)

    def test_continuation_period_mismatch_rejected(self):
        self.pages[1].tables[0].columns[0].period = "2023"
        with self.assertRaises(ValueError):
            validate_page(self.pages[1], 2, self.pages[0])

    def test_eps_not_implicitly_eligible(self):
        page = copy.deepcopy(self.raw[0])
        table = page["tables"][0]
        table["statement"] = "eps"
        table["rows"] = []
        for index, (concept, raw, quantity, scale) in enumerate(
            [
                ("basic_eps", "2", "per_share", 1),
                ("basic_numerator", "100", "money", 1000),
                ("basic_shares", "50", "shares", 1000),
            ]
        ):
            table["rows"].append(
                {
                    "row_index": index,
                    "label": concept,
                    "path": [],
                    "role": "data",
                    "concept": concept,
                    "cells": [
                        {
                            "column_index": 0,
                            "raw_value": raw,
                            "quantity": quantity,
                            "scale": scale,
                            "currency": "USD" if quantity != "shares" else None,
                        }
                    ],
                }
            )
        parsed = [PageExtraction.model_validate(page)]
        self.assertEqual(check_consistency(parsed, self.config, [])["applicable"], 0)
        self.assertEqual(check_consistency(parsed, self.config, ["p1-t0"])["passed"], 1)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / "synthetic.pdf"
        make_pdf(self.pdf)
        self.config = Experiment(
            model=MODEL, dpi=72, manifest=self.root / "manifest.json", output=self.root / "run"
        )
        self.manifest = Manifest.model_validate(
            {
                "documents": [
                    {
                        "id": "test",
                        "pdf": str(self.pdf),
                        "pages": [1, 2],
                        "split": "pilot",
                    }
                ]
            }
        )

    def gold(self):
        directory = self.root / "gold"
        write_json(
            directory / "test.json",
            {
                "document_id": "test",
                "pdf_sha256": file_hash(self.pdf),
                "annotator": "fixture",
                "reviewed": True,
                "pages": fixture_pages(),
            },
        )
        return directory

    def test_run_resume_and_reference_evaluation(self):
        transport = FixtureTransport()
        run_experiment(self.config, self.manifest, transport=transport)
        self.assertEqual(transport.calls, 2)
        report = evaluate_run(self.config.output, self.gold())
        self.assertEqual(report["metrics"]["numeric_f1"], 1)
        run_experiment(self.config, self.manifest, resume=True, transport=transport)
        self.assertEqual(transport.calls, 2)

    def test_changed_config_cannot_resume(self):
        run_experiment(self.config, self.manifest, transport=FixtureTransport())
        self.config.dpi = 100
        with self.assertRaisesRegex(RuntimeError, "changed"):
            run_experiment(self.config, self.manifest, resume=True, transport=FixtureTransport())

    def test_changed_pdf_cannot_resume(self):
        run_experiment(self.config, self.manifest, transport=FixtureTransport())
        self.pdf.write_bytes(self.pdf.read_bytes() + b"\n% changed\n")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            run_experiment(self.config, self.manifest, resume=True, transport=FixtureTransport())

    def test_changed_response_cannot_resume(self):
        run_experiment(self.config, self.manifest, transport=FixtureTransport())
        path = self.config.output / "responses/000000.json"
        response = read_json(path)
        response["model"] = "tampered"
        write_json(path, response)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            run_experiment(self.config, self.manifest, resume=True, transport=FixtureTransport())

    def test_request_cap_keeps_partial_results(self):
        self.config.max_requests = 1
        with self.assertRaisesRegex(RuntimeError, "limit"):
            run_experiment(self.config, self.manifest, transport=FixtureTransport())
        result = read_json(self.config.output / "results.json")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["documents"]["test"]["completed_pages"], 1)

    def test_invalid_output_preserved_without_retry(self):
        class Invalid(FixtureTransport):
            def request(self, method, path, payload=None):
                response = super().request(method, path, payload)
                if method == "POST":
                    response["choices"][0]["message"]["content"] = "not JSON"
                return response

        transport = Invalid()
        result = run_experiment(self.config, self.manifest, transport=transport)
        self.assertEqual(transport.calls, 2)
        self.assertEqual(result["status"], "complete")
        document = result["documents"]["test"]
        self.assertEqual(document["completed_pages"], 0)
        self.assertEqual([r["page"] for r in document["rejected_pages"]], [1, 2])
        self.assertEqual(document["rejected_pages"][0]["type"], "ValidationError")
        self.assertTrue((self.config.output / "responses/000000.json").exists())
        report_text = (self.config.output / "report.md").read_text()
        self.assertIn("rejected for invalid model output", report_text)
        report = evaluate_run(self.config.output, self.gold())
        self.assertEqual(report["metrics"]["numeric_correct"], 0)
        run_experiment(self.config, self.manifest, resume=True, transport=transport)
        self.assertEqual(transport.calls, 2)

    def test_unsupplied_continuation_link_rejects_only_that_page(self):
        class Linked(FixtureTransport):
            def request(self, method, path, payload=None):
                response = super().request(method, path, payload)
                if method == "POST":
                    page = json.loads(response["choices"][0]["message"]["content"])
                    if page["page"] == 2:
                        page["tables"][0]["continuation_of"] = "p1-t0"
                        response["choices"][0]["message"]["content"] = json.dumps(page)
                return response

        self.config.context = "independent"
        result = run_experiment(self.config, self.manifest, transport=Linked())
        document = result["documents"]["test"]
        self.assertEqual(document["completed_pages"], 1)
        self.assertEqual(document["rejected_pages"][0]["page"], 2)
        self.assertIn("continuation", document["rejected_pages"][0]["message"])

    def test_aligned_diagnostic_pairs_columns_by_period_not_position(self):
        predicted = fixture_pages()
        for page in predicted:
            for table in page["tables"]:
                # A model counting the row-label column: an empty column 0 shifts the rest.
                table["columns"] = [{"column_index": 0, "label": "", "period": None}] + [
                    {**c, "column_index": c["column_index"] + 1} for c in table["columns"]
                ]
                for row in table["rows"]:
                    row["cells"] = [
                        {**c, "column_index": c["column_index"] + 1} for c in row["cells"]
                    ]
        predictions = [PageExtraction.model_validate(p) for p in predicted]
        references = [PageExtraction.model_validate(p) for p in fixture_pages()]
        scores = score_pages(predictions, references)
        self.assertEqual(scores["numeric_correct"], 0)
        self.assertEqual(scores["aligned_numeric_correct"], scores["numeric_gold_cells"])
        outcomes = cell_outcomes(predictions, references).values()
        self.assertEqual(
            sum(bool(o["aligned"]) for o in outcomes), scores["aligned_numeric_correct"]
        )

        wrong_period = copy.deepcopy(predicted)
        for page in wrong_period:
            for table in page["tables"]:
                for column in table["columns"][1:]:
                    column["period"] = "1999-12-31"
        predictions = [PageExtraction.model_validate(p) for p in wrong_period]
        self.assertEqual(score_pages(predictions, references)["aligned_numeric_correct"], 0)

    def test_cell_outcomes_agree_with_score_counts(self):
        predicted = fixture_pages()
        rows = predicted[0]["tables"][0]["rows"]
        rows.insert(0, {**rows[0], "label": "(In millions)", "role": "header", "cells": []})
        for index, row in enumerate(rows):
            row["row_index"] = index
        predictions = [PageExtraction.model_validate(p) for p in predicted]
        references = [PageExtraction.model_validate(p) for p in fixture_pages()]
        scores = score_pages(predictions, references)
        outcomes = cell_outcomes(predictions, references).values()
        for measure, count in (
            ("numeric", "numeric_correct"),
            ("semantic", "semantic_correct"),
            ("hierarchy", "hierarchy_correct"),
            ("aligned", "aligned_numeric_correct"),
        ):
            self.assertEqual(sum(bool(o[measure]) for o in outcomes), scores[count])
        self.assertLess(scores["numeric_correct"], scores["aligned_numeric_correct"])

    def test_exact_paired_tests_and_summary(self):
        self.assertEqual(exact_two_sided(0, 0), None)
        self.assertEqual(exact_two_sided(0, 4), 0.125)
        self.assertEqual(exact_two_sided(5, 10), 1.0)
        self.assertAlmostEqual(exact_two_sided(1, 10), 22 / 1024)
        with self.assertRaises(ValueError):
            exact_two_sided(5, 4)

        left = self.root / "left"
        run_experiment(
            self.config.model_copy(update={"output": left}),
            self.manifest,
            transport=FixtureTransport(),
        )
        right = self.root / "right"
        run_experiment(
            self.config.model_copy(update={"output": right, "context": "independent"}),
            self.manifest,
            transport=FixtureTransport(),
        )
        summary = summarize([left], [right], self.gold())
        self.assertEqual(summary["warnings"], [])
        self.assertEqual(summary["conditions"]["left"]["numeric_recall"]["mean"], 1)
        self.assertEqual(summary["conditions"]["left"]["semantic_recall"]["mean"], 1)
        self.assertEqual(summary["paired_filing_f1"]["semantic"]["mean_filing_difference"], 0)
        pair = summary["paired_cells_by_repeat"][0]
        self.assertEqual(pair["numeric"]["mcnemar_exact_p"], None)
        self.assertGreater(pair["numeric_context"]["cells"], 0)
        with self.assertRaisesRegex(ValueError, "same number"):
            summarize([left], [], self.gold())
        with self.assertRaisesRegex(ValueError, "distinct"):
            summarize([left, left], [right, right], self.gold())
        right_manifest = read_json(right / "run.json")
        right_manifest["system_prompt"] += " Changed scientific protocol."
        write_json(right / "run.json", right_manifest)
        summary = summarize([left], [right], self.gold())
        self.assertIn("Frozen run metadata differs: system_prompt", summary["warnings"])

    def test_screen_sensitivity_retains_denominators_and_does_not_change_run(self):
        run_experiment(self.config, self.manifest, transport=FixtureTransport())
        before = file_hash(self.config.output / "results.json")
        report = analyze_screens(self.config.output, [Decimal("0"), Decimal("0.005")])
        self.assertEqual(report["requested_pages"], 2)
        self.assertEqual(report["accepted_pages"], 2)
        self.assertEqual(report["stored_screen_denominators"]["applicable"], 1)
        self.assertEqual(report["sensitivity_grid"][0]["passed"], 1)
        self.assertEqual(file_hash(self.config.output / "results.json"), before)
        with self.assertRaises(ValueError):
            analyze_screens(self.config.output, [Decimal("NaN")])
        with self.assertRaises(ValueError):
            analyze_screens(self.config.output, [Decimal("0"), Decimal("0")])

    def test_partial_gold_not_silent(self):
        run_experiment(self.config, self.manifest, transport=FixtureTransport())
        directory = self.gold()
        gold = read_json(directory / "test.json")
        gold["pages"] = gold["pages"][:1]
        write_json(directory / "test.json", gold)
        report = evaluate_run(self.config.output, directory)
        self.assertEqual(report["metrics"]["annotated_pages"], 1)
        self.assertEqual(report["requested_pages"], 2)

    def test_gold_hash_mismatch_rejected(self):
        run_experiment(self.config, self.manifest, transport=FixtureTransport())
        directory = self.gold()
        value = read_json(directory / "test.json")
        value["pdf_sha256"] = "0" * 64
        write_json(directory / "test.json", value)
        with self.assertRaisesRegex(ValueError, "hash"):
            evaluate_run(self.config.output, directory)

    def test_gold_revision_mismatch_cannot_be_compared(self):
        run_experiment(self.config, self.manifest, transport=FixtureTransport())
        directory = self.gold()
        evaluate_run(self.config.output, directory)
        other = self.config.model_copy(
            update={"output": self.root / "other", "context": "independent"}
        )
        run_experiment(other, self.manifest, transport=FixtureTransport())
        value = read_json(directory / "test.json")
        value["annotator"] = "another reference revision"
        write_json(directory / "test.json", value)
        evaluate_run(other.output, directory)
        with self.assertRaisesRegex(ValueError, "identical reference"):
            compare_runs(self.config.output, other.output)

    def test_render_pixel_limit(self):
        with self.assertRaisesRegex(ValueError, "pixel"):
            render_page(self.pdf, 1, 300, 100000)

    def test_exclusive_lock(self):
        with run_lock(self.root / "locked"):
            with self.assertRaisesRegex(RuntimeError, "locked"):
                with run_lock(self.root / "locked"):
                    pass

    def test_successful_raw_response_recovered_after_interrupted_commit(self):
        transport = FixtureTransport()
        run_experiment(self.config, self.manifest, transport=transport)
        (self.config.output / "pages/test/p0002.json").unlink()
        run_experiment(self.config, self.manifest, resume=True, transport=transport)
        self.assertEqual(transport.calls, 2)

    def test_missing_byok_charge_stops_next_page_and_report_retains_uncertainty(self):
        class MissingByok(FixtureTransport):
            def request(inner, method, path, payload=None):
                response = super().request(method, path, payload)
                if method == "POST":
                    response["usage"] = {"cost": 0, "is_byok": True}
                return response

        transport = MissingByok()
        with self.assertRaisesRegex(RuntimeError, "BYOK upstream"):
            run_experiment(self.config, self.manifest, transport=transport)
        self.assertEqual(transport.calls, 1)
        self.assertTrue((self.config.output / "pages/test/p0001.json").exists())
        report = (self.config.output / "report.md").read_text()
        self.assertIn("Attempts with unknown cost: 1", report)
        self.assertIn("Attempts with unavailable BYOK upstream charge: 1", report)
        self.assertIn("not a guaranteed billing bound", report)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Experiment(model=MODEL, manifest=Path("manifest.json"), output=self.root)

    def test_retry_429_and_record_each_attempt(self):
        class RateLimited(FixtureTransport):
            def request(self, method, path, payload=None):
                if method == "POST" and self.calls == 0:
                    self.calls += 1
                    raise APIError(429, 0)
                return super().request(method, path, payload)

        client = OpenRouterClient(self.config, self.root, RateLimited(), sleep=lambda _: None)
        client.complete(
            [
                {"role": "system", "content": "fixture"},
                {"role": "user", "content": [{"text": "physical PDF page 1"}]},
            ],
            "d/p1",
        )
        self.assertEqual(len(client.events), 2)
        self.assertEqual([e["status"] for e in client.events], ["http_error", "response"])

    def test_unbilled_provider_error_in_200_body_is_retried(self):
        class UpstreamLimited(FixtureTransport):
            def request(self, method, path, payload=None):
                if method == "POST" and self.calls == 0:
                    self.calls += 1
                    return {
                        "choices": [
                            {
                                "finish_reason": "error",
                                "error": {"code": 429, "message": "rate-limited upstream"},
                                "message": {"content": None},
                            }
                        ],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0},
                    }
                return super().request(method, path, payload)

        delays = []
        client = OpenRouterClient(self.config, self.root, UpstreamLimited(), sleep=delays.append)
        response, event = client.complete(
            [
                {"role": "system", "content": "fixture"},
                {"role": "user", "content": [{"text": "physical PDF page 1"}]},
            ],
            "d/p1",
        )
        self.assertEqual([e["status"] for e in client.events], ["provider_error", "response"])
        self.assertTrue((self.root / client.events[0]["response_file"]).exists())
        self.assertGreaterEqual(delays[0], 10)
        parse_response(response)

    MESSAGES = [
        {"role": "system", "content": "fixture"},
        {"role": "user", "content": [{"text": "physical PDF page 1"}]},
    ]

    def _flaky(self, *errors, generation=None):
        class Flaky(FixtureTransport):
            lookups = 0

            def request(inner, method, path, payload=None):
                if method == "GET" and path.startswith("generation"):
                    inner.lookups += 1
                    if generation is None:
                        raise APIError(404)
                    return {"data": {"total_cost": generation}}
                if method == "POST" and inner.calls < len(errors):
                    inner.calls += 1
                    raise errors[inner.calls - 1]
                return super().request(method, path, payload)

        return Flaky()

    def test_stream_reassembled_with_keepalives_and_header_id(self):
        lines = [
            b": OPENROUTER PROCESSING\n",
            b"\n",
            b'data: {"id":"gen-1","model":"m","provider":"Google","choices":[{"delta":'
            b'{"reasoning":"think"}}]}\n',
            b'data: {"id":"gen-1","choices":[{"delta":{"content":"{\\"pa"}}]}\n',
            b'data: {"id":"gen-1","choices":[{"delta":{"content":"ge\\": 1}"},'
            b'"finish_reason":"stop","native_finish_reason":"STOP"}]}\n',
            b'data: {"id":"gen-1","choices":[{"delta":{},"finish_reason":"stop"}],'
            b'"usage":{"completion_tokens":5,"cost":0.01}}\n',
            b"data: [DONE]\n",
        ]
        response = read_stream(lines, "gen-header")
        self.assertEqual(response["id"], "gen-header")
        self.assertEqual(response["choices"][0]["message"]["content"], '{"page": 1}')
        self.assertEqual(response["choices"][0]["finish_reason"], "stop")
        self.assertEqual(response["usage"]["cost"], 0.01)
        self.assertIsNone(provider_error(response))

    def test_stream_break_reports_generation_id_or_none(self):
        def broken(prefix):
            yield from prefix
            raise TimeoutError

        started = [b'data: {"id":"gen-2","choices":[{"delta":{"content":"{"}}]}\n']
        with self.assertRaises(OutcomeUnknown) as caught:
            read_stream(broken(started))
        self.assertEqual(caught.exception.generation_id, "gen-2")
        with self.assertRaises(OutcomeUnknown) as caught:
            read_stream(broken([]))
        self.assertIsNone(caught.exception.generation_id)

    def test_mid_stream_error_with_string_code_is_provider_error(self):
        lines = [
            b'data: {"id":"cmpl-1","error":{"code":"server_error","message":"x"},'
            b'"choices":[{"index":0,"delta":{"content":""},"finish_reason":"error"}]}\n'
        ]
        self.assertEqual(provider_error(read_stream(lines)), 502)
        timeout = {"choices": [{"finish_reason": "error", "error": {"code": 504}}]}
        self.assertEqual(provider_error(timeout), 504)

    def test_lost_reply_with_id_is_costed_from_generation_record_and_retried(self):
        transport = self._flaky(OutcomeUnknown("lost", "gen-3"), generation=0.12)
        client = OpenRouterClient(self.config, self.root, transport, sleep=lambda _: None)
        client.complete(self.MESSAGES, "d/p1")
        first = client.events[0]
        self.assertEqual([e["status"] for e in client.events], ["unknown", "response"])
        self.assertEqual(first["cost_usd"], "0.12")
        self.assertNotIn("reserved_usd", first)

    def test_unreconciled_lost_reply_stops_without_replay_and_persists_on_reload(self):
        transport = self._flaky(OutcomeUnknown("lost"), OutcomeUnknown("lost"))
        self.config.max_observed_cost_usd = Decimal("1.50")
        client = OpenRouterClient(self.config, self.root, transport, sleep=lambda _: None)
        with self.assertRaises(OutcomeUnknown):
            client.complete(self.MESSAGES, "d/p1")
        self.assertEqual([e["status"] for e in client.events], ["unknown"])
        self.assertEqual(transport.calls, 1)
        # This reservation is retained even below the configured observed-cost cap.
        self.assertEqual(client.events[0]["reserved_usd"], "1.00")
        self.assertIsNone(client.events[0]["cost_usd"])
        self.assertEqual(transport.lookups, 0)
        reloaded = OpenRouterClient(self.config, self.root, transport, sleep=lambda _: None)
        with self.assertRaisesRegex(RuntimeError, "unknown charge"):
            reloaded.complete(self.MESSAGES, "d/p2")
        self.assertEqual(transport.calls, 1)

    def test_generation_lookup_exhaustion_does_not_replay(self):
        transport = self._flaky(OutcomeUnknown("lost", "gen-unsettled"))
        client = OpenRouterClient(self.config, self.root, transport, sleep=lambda _: None)
        with self.assertRaises(OutcomeUnknown):
            client.complete(self.MESSAGES, "d/p1")
        self.assertEqual(transport.calls, 1)
        self.assertEqual(transport.lookups, 8)
        self.assertEqual(client.events[0]["generation_id"], "gen-unsettled")
        with self.assertRaisesRegex(RuntimeError, "unknown charge"):
            client._budget()

    def test_recovered_openrouter_zero_does_not_hide_unknown_byok_provider_charge(self):
        class ByokLookup(FixtureTransport):
            def request(inner, method, path, payload=None):
                if method == "GET" and path.startswith("generation"):
                    return {"data": {"total_cost": 0, "is_byok": True}}
                if method == "POST":
                    inner.calls += 1
                    raise OutcomeUnknown("lost", "gen-byok")
                return super().request(method, path, payload)

        transport = ByokLookup()
        client = OpenRouterClient(self.config, self.root, transport, sleep=lambda _: None)
        with self.assertRaises(OutcomeUnknown):
            client.complete(self.MESSAGES, "d/p1")
        self.assertEqual(client.events[0]["cost_usd"], "0")
        self.assertEqual(client.events[0]["reserved_usd"], "1.00")
        with self.assertRaisesRegex(RuntimeError, "BYOK upstream"):
            client.complete(self.MESSAGES, "d/p2")
        self.assertEqual(transport.calls, 1)

    def test_http_server_error_without_charge_cannot_be_assumed_free(self):
        transport = self._flaky(APIError(502))
        client = OpenRouterClient(self.config, self.root, transport, sleep=lambda _: None)
        with self.assertRaises(APIError):
            client.complete(self.MESSAGES, "d/p1")
        self.assertEqual(transport.calls, 1)
        self.assertEqual(client.events[0]["reserved_usd"], "1.00")
        with self.assertRaisesRegex(RuntimeError, "unknown charge"):
            client.complete(self.MESSAGES, "d/p2")
        self.assertEqual(transport.calls, 1)

    def test_connect_failure_retried_and_unexpected_error_blocks(self):
        client = OpenRouterClient(
            self.config, self.root, self._flaky(ConnectError("x")), sleep=lambda _: None
        )
        client.complete(self.MESSAGES, "d/p1")
        self.assertEqual([e["status"] for e in client.events], ["connect_error", "response"])

        other = self.root / "unexpected"
        other.mkdir()
        client = OpenRouterClient(
            self.config, other, self._flaky(KeyError("bug")), sleep=lambda _: None
        )
        with self.assertRaises(KeyError):
            client.complete(self.MESSAGES, "d/p1")
        with self.assertRaisesRegex(RuntimeError, "unknown outcome"):
            client._budget()

    def test_billed_provider_error_retried_and_cost_counted(self):
        class Billed(FixtureTransport):
            def request(inner, method, path, payload=None):
                if method == "POST" and inner.calls == 0:
                    inner.calls += 1
                    return {
                        "choices": [{"finish_reason": "error", "error": {"code": 504}}],
                        "usage": {"completion_tokens": 280, "cost": 0.05},
                    }
                return super().request(method, path, payload)

        client = OpenRouterClient(self.config, self.root, Billed(), sleep=lambda _: None)
        client.complete(self.MESSAGES, "d/p1")
        self.assertEqual([e["status"] for e in client.events], ["provider_error", "response"])
        self.assertEqual(client.events[0]["cost_usd"], "0.05")

    def test_byok_charge_counts_toward_cost(self):
        event = {
            "cost_usd": "0",
            "usage": {
                "cost": 0,
                "is_byok": True,
                "cost_details": {"upstream_inference_cost": 0.009},
            },
        }
        self.assertEqual(byok_cost(event), Decimal("0.009"))
        self.assertEqual(byok_cost({"usage": {"cost": 0.01}}), 0)
        self.config.max_observed_cost_usd = Decimal("0.01")
        client = OpenRouterClient(self.config, self.root, FixtureTransport())
        client.events = [{"status": "response", **event}, {"status": "response", **event}]
        with self.assertRaisesRegex(RuntimeError, "cost limit"):
            client._budget()

    def test_missing_byok_charge_preserves_output_but_blocks_later_completion(self):
        class MissingByok(FixtureTransport):
            def request(inner, method, path, payload=None):
                response = super().request(method, path, payload)
                if method == "POST":
                    response["usage"] = {"cost": 0, "is_byok": True}
                return response

        transport = MissingByok()
        client = OpenRouterClient(self.config, self.root, transport, sleep=lambda _: None)
        response, event = client.complete(self.MESSAGES, "d/p1")
        parse_response(response)
        self.assertIsNone(byok_cost(event))
        self.assertEqual(event["cost_usd"], "0")
        self.assertEqual(event["reserved_usd"], "1.00")
        self.assertTrue((self.root / event["response_file"]).exists())
        with self.assertRaisesRegex(RuntimeError, "BYOK upstream"):
            client.complete(self.MESSAGES, "d/p2")
        self.assertEqual(transport.calls, 1)
        self.assertIsNone(byok_cost({"usage": {"is_byok": True, "cost_details": {"upstream_inference_cost": "invalid"}}}))
        self.assertEqual(byok_cost({"usage": {"is_byok": True, "cost_details": {"upstream_inference_cost": 0}}}), 0)

    def test_generation_billing_recovery_does_not_mutate_raw_response_fingerprint(self):
        class RecoverBilling(FixtureTransport):
            def request(inner, method, path, payload=None):
                if method == "GET" and path.startswith("generation"):
                    return {"data": {"total_cost": 0, "is_byok": True,
                                     "upstream_inference_cost": 0.02}}
                response = super().request(method, path, payload)
                if method == "POST":
                    response["usage"] = {"is_byok": True}
                return response

        client = OpenRouterClient(self.config, self.root, RecoverBilling(), sleep=lambda _: None)
        response, event = client.complete(self.MESSAGES, "d/p1")
        self.assertEqual(byok_cost(event), Decimal("0.02"))
        self.assertNotIn("cost_details", response["usage"])
        self.assertEqual(read_json(self.root / event["response_file"]), response)
        self.assertEqual(event["response_sha256"], digest(response))
        self.assertNotIn("reserved_usd", event)
        client._budget()

    def test_provider_error_without_recovered_charge_does_not_replay(self):
        class Uncosted(FixtureTransport):
            def request(inner, method, path, payload=None):
                if method == "POST":
                    inner.calls += 1
                    return {"choices": [{"finish_reason": "error", "error": {"code": 504}}]}
                return super().request(method, path, payload)

        transport = Uncosted()
        client = OpenRouterClient(self.config, self.root, transport, sleep=lambda _: None)
        with self.assertRaisesRegex(RuntimeError, "unknown charge"):
            client.complete(self.MESSAGES, "d/p1")
        self.assertEqual(transport.calls, 1)
        self.assertEqual(client.events[0]["status"], "provider_error")
        self.assertEqual(client.events[0]["reserved_usd"], "1.00")

    def test_cut_off_output_retried_and_last_one_kept_for_rejection(self):
        class Looping(FixtureTransport):
            def request(inner, method, path, payload=None):
                if method == "POST" and inner.calls < 2:
                    inner.calls += 1
                    return {
                        "choices": [{"finish_reason": "length", "message": {"content": "{"}}],
                        "usage": {"completion_tokens": 32000, "cost": 0.32},
                    }
                return super().request(method, path, payload)

        client = OpenRouterClient(self.config, self.root, Looping(), sleep=lambda _: None)
        client.complete(self.MESSAGES, "d/p1")
        self.assertEqual(
            [e["status"] for e in client.events], ["truncated", "truncated", "response"]
        )
        self.assertEqual(client.events[0]["cost_usd"], "0.32")
        self.assertTrue((self.root / "responses" / "000000.json").exists())

        other = self.root / "always"
        other.mkdir()
        self.config.max_attempts = 2
        transport = Looping()
        transport.calls = -10
        client = OpenRouterClient(self.config, other, transport, sleep=lambda _: None)
        response, event = client.complete(self.MESSAGES, "d/p1")
        self.assertEqual(event["attempt"], 2)
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            parse_response(response)

    def test_reasoning_budget_sent_only_when_configured(self):
        self.assertNotIn("reasoning", request_body(self.config, self.MESSAGES))
        self.config.reasoning_max_tokens = 16000
        body = request_body(self.config, self.MESSAGES)
        self.assertEqual(body["reasoning"], {"max_tokens": 16000})
        with self.assertRaises(ValueError):
            Experiment.model_validate(
                {**self.config.model_dump(), "reasoning_max_tokens": self.config.max_tokens}
            )

    def test_truncated_response_rejected(self):
        response = {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            parse_response(response)

    def test_unsupported_model_fails_preflight(self):
        self.config.model = "missing/model"
        client = OpenRouterClient(self.config, self.root, FixtureTransport())
        with self.assertRaisesRegex(RuntimeError, "absent"):
            client.preflight()

    def test_unknown_outcome_blocks_next_request(self):
        write_json(self.root / "requests.json", [{"status": "started"}])
        client = OpenRouterClient(self.config, self.root, FixtureTransport())
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            client._budget()


if __name__ == "__main__":
    unittest.main()
