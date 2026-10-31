from __future__ import annotations

import copy
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from financial_table_verifier.audit import audit_document
from financial_table_verifier.cohort import build_cohort
from financial_table_verifier.consistency import check_consistency
from financial_table_verifier.demo import fixture_pages, make_pdf
from financial_table_verifier.local_data import import_index
from financial_table_verifier.schema import Experiment, PageExtraction
from financial_table_verifier.storage import write_json
from financial_table_verifier.xbrl import read_companyfacts, read_instance
from financial_table_verifier.xbrl_draft import _binding, _current_facts, _locate


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.pages = [PageExtraction.model_validate(p) for p in fixture_pages()]
        self.config = Experiment(model="fixture/model", manifest=Path("m"), output=Path("r"))

    def test_false_pass_detected_against_reference(self):
        wrong = copy.deepcopy(self.pages)
        for page in wrong:
            for row in page.tables[0].rows:
                row.cells[0].raw_value = str(int(row.cells[0].raw_value) * 2)
        check = check_consistency(wrong, self.config, [])
        report = audit_document(wrong, self.pages, check["checks"])
        self.assertEqual(report["counts"]["incorrect_pass"], 1)
        self.assertEqual(report["false_pass_fraction_among_passed"], 1)

    def test_correct_table_passes_audit(self):
        check = check_consistency(self.pages, self.config, [])
        report = audit_document(self.pages, self.pages, check["checks"])
        self.assertEqual(report["counts"]["correct_pass"], 1)

    def test_partial_annotation_does_not_claim_audit(self):
        check = check_consistency(self.pages, self.config, [])
        report = audit_document(self.pages, self.pages[:1], check["checks"])
        self.assertEqual(report["audited_checks"], 0)
        self.assertEqual(report["unaudited_checks"], 1)
        self.assertEqual(report["table_level"]["audited"], 0)

    def test_table_audit_deduplicates_checks_and_flags_any_failure(self):
        check = check_consistency(self.pages, self.config, [])["checks"][0]
        failed = {**check, "check": "another_identity", "status": "fail"}
        report = audit_document(self.pages, self.pages, [check, failed])
        self.assertEqual(report["audited_checks"], 2)
        self.assertEqual(report["table_level"]["audited"], 1)
        self.assertEqual(report["table_level"]["counts"]["correct_fail"], 1)
        self.assertEqual(report["false_flag_fraction_among_failed"], 1)

    def test_no_applicable_checks_are_not_a_table_success(self):
        check = check_consistency(self.pages, self.config, [])["checks"][0]
        report = audit_document(self.pages, self.pages, [{**check, "status": "skipped"}])
        self.assertEqual(report["table_level"]["audited"], 0)
        self.assertEqual(report["table_level"]["unaudited_tables"], 1)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_companyfacts_selects_exact_accession(self):
        path = self.root / "companyfacts.json"
        write_json(
            path,
            {
                "cik": 123,
                "facts": {
                    "us-gaap": {
                        "Assets": {
                            "units": {
                                "USD": [
                                    {
                                        "accn": "a",
                                        "end": "2023-12-31",
                                        "val": 100,
                                        "fy": 2023,
                                        "form": "10-K",
                                    },
                                    {
                                        "accn": "b",
                                        "end": "2023-12-31",
                                        "val": 200,
                                        "fy": 2023,
                                        "form": "10-K",
                                    },
                                ]
                            }
                        }
                    }
                },
            },
        )
        result = read_companyfacts(path, "a")
        self.assertEqual(len(result["facts"]), 1)
        self.assertEqual(result["facts"][0]["value"], "100")
        with self.assertRaises(ValueError):
            read_companyfacts(path, "missing")

    def test_xbrl_instance_context_and_unit(self):
        path = self.root / "facts.xml"
        path.write_text("""<xbrl xmlns="http://www.xbrl.org/2003/instance"
          xmlns:g="http://example.org/gaap"><context id="c"><entity><identifier
          scheme="example">123</identifier></entity><period><instant>2023-12-31</instant>
          </period></context><unit id="u"><measure>iso4217:USD</measure></unit>
          <g:Assets contextRef="c" unitRef="u" decimals="-3">100000</g:Assets></xbrl>""")
        result = read_instance(path)
        self.assertEqual(result["facts"][0]["value"], "100000")
        self.assertEqual(result["facts"][0]["context"]["period"]["instant"], "2023-12-31")

    def test_xml_entity_declaration_rejected(self):
        path = self.root / "facts.xml"
        path.write_text('<!DOCTYPE x [<!ENTITY e "bad">]><x/>')
        with self.assertRaises(ValueError):
            read_instance(path)

    def test_import_zero_to_one_based_and_does_not_copy_gold(self):
        pdf = self.root / "source.pdf"
        make_pdf(pdf)
        index = self.root / "index.json"
        write_json(
            index,
            {
                "filings": [
                    {
                        "doc_id": "one",
                        "pdf": "source.pdf",
                        "pages": {"income": [0, 1]},
                        "gold": {"net_income": 99},
                    }
                ]
            },
        )
        report = import_index(index, self.root, self.root / "out.json", limit=1, window=0)
        self.assertEqual(report["selected_documents"][0]["physical_pages"], [1, 2])
        from financial_table_verifier.storage import read_json

        self.assertNotIn("gold", read_json(self.root / "out.json")["documents"][0])

    def test_cohort_join_fiscal_year_and_deduplication(self):
        sec = self.root / "data/sec"
        sec.mkdir(parents=True)
        rows = []
        for number, form in enumerate(["10-K", "10-Q"], 1):
            accession = f"000000000{number}-24-000001"
            pdf = sec / f"{number}.pdf"
            make_pdf(pdf)
            pdf.write_bytes(pdf.read_bytes() + f"% source {number}\n".encode())
            rows.append(
                {
                    "doc_id": f"doc-{number}",
                    "cik": number,
                    "company": f"Company {number}",
                    "form": form,
                    "accession": accession,
                    "pdf": f"data/sec/{number}.pdf",
                    "report_date": "2023-12-31",
                    "pages": {"income": [0]},
                }
            )
            write_json(
                self.root / f"data/xbrl/companyfacts/CIK{number:010d}.json",
                {
                    "cik": number,
                    "facts": {
                        "us-gaap": {
                            "Assets": {
                                "units": {
                                    "USD": [
                                        {
                                            "accn": accession,
                                            "end": "2023-12-31",
                                            "val": 100,
                                            "fy": 2023,
                                            "form": form,
                                        }
                                    ]
                                }
                            }
                        }
                    },
                },
            )
            write_json(
                self.root / f"data/xbrl/submissions_CIK{number:010d}.json",
                {
                    "cik": number,
                    "sic": "1234",
                    "filings": {
                        "recent": {
                            "accessionNumber": [accession],
                            "form": [form],
                            "reportDate": ["2023-12-31"],
                            "filingDate": ["2024-02-01"],
                            "primaryDocument": ["report.htm"],
                        }
                    },
                },
            )
        write_json(sec / "index.json", {"filings": rows})
        write_json(sec / "index.old.json", {"filings": rows})
        report = build_cohort(self.root, self.root / "cohort", per_form=1)
        self.assertEqual(report["selected_counts"], {"10-K": 1, "10-Q": 1})
        self.assertEqual(report["eligible_counts"], {"10-K": 1, "10-Q": 1})
        self.assertFalse(report["original_experiment_reproduced"])



class XbrlDraftTests(unittest.TestCase):
    def test_locate_scales_and_ambiguity(self):
        pages = [{Decimal("15803")}, {Decimal("31451.7")}, {Decimal("6.29")}, {Decimal("12")}]
        self.assertEqual(_locate(Decimal("15803000000"), False, pages), {1: 1000000})
        self.assertEqual(_locate(Decimal("31451700000"), False, pages), {2: 1000000})
        self.assertEqual(_locate(Decimal("6.29"), True, pages), {3: 1})
        self.assertEqual(_locate(Decimal("12000"), False, pages), {})  # 12 thousand: too small

    def test_current_facts_keep_quarter_for_10q_and_year_for_10k(self):
        def fact(start, value):
            period = {"startDate": start, "endDate": "2023-06-30"}
            return {"concept": "us-gaap:NetIncomeLoss", "value": value,
                    "context": {"period": period}}

        facts = [fact("2023-01-01", "20"), fact("2023-04-01", "10"), fact("2022-07-01", "40")]
        for form, expected in (("10-Q", "10"), ("10-K", "40")):
            selected = _current_facts(facts, "2023-06-30", form)
            self.assertEqual(selected["NetIncomeLoss"]["value"], expected)

    def test_binding_flags_prior_year_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "facts.json"
            entries = [
                {"end": "2023-01-28", "val": 7907000000, "accn": "a-23", "form": "10-K",
                 "fy": 2022, "fp": "FY", "filed": "2023-03-27"},
                {"end": "2024-02-03", "val": 6868000000, "accn": "a-24", "form": "10-K",
                 "fy": 2023, "fp": "FY", "filed": "2024-03-25"},
            ]
            write_json(path, {"facts": {"us-gaap": {"Assets": {"units": {"USD": entries}}}}})
            row = {"companyfacts": str(path), "report_date": "2024-02-03", "form": "10-K"}
            old_pdf = [{Decimal("7907")}]
            result = _binding(row, old_pdf)
            self.assertEqual(result["status"], "mismatch")
            self.assertEqual(result["earliest_filing_reporting_it"]["accn"], "a-23")
            self.assertEqual(_binding(row, [{Decimal("6868")}])["status"], "consistent")


if __name__ == "__main__":
    unittest.main()
