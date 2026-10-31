"""Draft XBRL reference facts and candidate statement pages for a cohort. Review required.

Facts come from exact-accession SEC companyfacts for the report date. Candidate pages are
located only by finding those filed values, at a common presentation scale, in the PDF
text layer. Nothing here is a confirmed page pointer or a cell-level mapping.
"""

from __future__ import annotations

import csv
import json
import re
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium

from .storage import file_hash, write_json
from .xbrl import read_companyfacts

# Primary-statement concepts; the first present in each group is the one checked.
STATEMENTS = {
    "balance_sheet": [
        ["Assets"],
        ["AssetsCurrent"],
        ["Liabilities"],
        ["LiabilitiesCurrent"],
        [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ],
        ["LiabilitiesAndStockholdersEquity"],
    ],
    "income_statement": [
        ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
        ["OperatingIncomeLoss"],
        [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",  # noqa: E501
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",  # noqa: E501
        ],
        ["NetIncomeLoss", "ProfitLoss"],
        ["EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted"],
        ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
        ["WeightedAverageNumberOfSharesOutstandingBasic"],
        ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    ],
    "cash_flow": [
        ["NetCashProvidedByUsedInOperatingActivities"],
        ["NetCashProvidedByUsedInInvestingActivities"],
        ["NetCashProvidedByUsedInFinancingActivities"],
        [
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "CashAndCashEquivalentsAtCarryingValue",
        ],
        [
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",  # noqa: E501
            "CashAndCashEquivalentsPeriodIncreaseDecrease",
        ],
    ],
}
TITLES = {
    "balance_sheet": r"balance sheets?|statements? of financial (position|condition)",
    "income_statement": r"statements? of (consolidated )?(operations|income|earnings)|"
    r"income statements?",
    "cash_flow": r"statements? of (consolidated )?cash flows?",
}
SCALES = (1, 1000, 1000000, 1000000000)
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _page_numbers(text: str) -> set[Decimal]:
    return {Decimal(m.replace(",", "")) for m in NUMBER.findall(text)}


def _current_facts(facts: list[dict], report_date: str, form: str) -> dict:
    """Concept -> us-gaap fact for the report date.

    When several durations end there, a 10-K keeps the longest (the fiscal year) and a
    10-Q the shortest (the quarter, usually its first column; year-to-date is not kept).
    """
    selected = {}
    for fact in facts:
        namespace, concept = fact["concept"].split(":", 1)
        period = fact["context"]["period"]
        if namespace != "us-gaap" or report_date not in {
            period.get("instant"),
            period.get("endDate"),
        }:
            continue
        start = period.get("startDate") or ""
        previous = selected.get(concept)
        if previous is not None:
            other = previous["context"]["period"].get("startDate") or ""
            keep_longer = form == "10-K"
            if (start < other) != keep_longer or start == other:
                continue
        selected[concept] = fact
    return selected


def _locate(value: Decimal, per_share: bool, pages: list[set[Decimal]]) -> dict:
    """Pages printing the value at one presentation scale (exact, as SEC tables round)."""
    found = {}
    scales = (1,) if per_share else SCALES
    for scale in scales:
        shown = abs(value) / scale
        # Small values, or more than one decimal place (e.g. "31,451.7" in millions), are
        # too ambiguous to locate a page.
        if not per_share and (shown < 100 or shown != round(shown, 1)):
            continue
        for index, numbers in enumerate(pages, 1):
            if shown in numbers:
                found.setdefault(index, scale)
    return found


def _binding(row: dict, numbers: list[set[Decimal]]) -> dict:
    """Which balance-sheet dates' filed total assets the PDF prints, from any accession.

    A correct PDF prints the report date; one that prints only earlier dates is likely a
    different (e.g. prior-year) filing than the cohort's accession.
    """
    payload = json.loads(Path(row["companyfacts"]).read_text(encoding="utf-8"))
    entries = payload["facts"].get("us-gaap", {}).get("Assets", {}).get("units", {}).get("USD", [])
    printed = {}
    for entry in entries:
        if "start" not in entry and _locate(Decimal(str(entry["val"])), False, numbers):
            printed.setdefault(entry["end"], []).append(entry)
    dates = sorted(printed)
    result = {"total_assets_dates_printed": dates}
    if row["report_date"] in printed:
        result["status"] = "consistent"
    elif dates:
        latest = dates[-1]
        same_form = [e for e in printed[latest] if e.get("form") == row["form"]]
        first = min(same_form or printed[latest], key=lambda e: e.get("filed") or "")
        result |= {
            "status": "mismatch",
            "pdf_latest_date": latest,
            "earliest_filing_reporting_it": {
                k: first.get(k) for k in ("accn", "form", "fy", "fp", "filed")
            },
        }
    else:
        result["status"] = "unknown"
    return result


def draft_filing(row: dict, cohort: Path) -> dict:
    pdf = cohort / row["pdf"]
    facts = read_companyfacts(Path(row["companyfacts"]), row["accession"])
    current = _current_facts(facts["facts"], row["report_date"], row["form"])
    document = pdfium.PdfDocument(pdf)
    texts = [document[i].get_textpage().get_text_range() for i in range(len(document))]
    numbers = [_page_numbers(t) for t in texts]
    statements = {}
    for statement, groups in STATEMENTS.items():
        checked, hits = [], {}
        for group in groups:
            concept = next((c for c in group if c in current), None)
            if concept is None:
                checked.append({"concepts": group, "fact": None})
                continue
            fact = current[concept]
            per_share = fact["unit_id"].endswith("/shares")
            located = _locate(Decimal(fact["value"]), per_share, numbers)
            checked.append(
                {
                    "concept": "us-gaap:" + concept,
                    "period": fact["context"]["period"],
                    "unit": fact["unit_id"],
                    "value": fact["value"],
                    "pages": sorted(located),
                }
            )
            for page, scale in located.items():
                entry = hits.setdefault(page, {"concepts": [], "scales": set()})
                entry["concepts"].append(concept)
                if not per_share:
                    entry["scales"].add(scale)
        candidates = sorted(
            (
                {
                    "page": page,
                    "matched": len(entry["concepts"]),
                    "concepts": entry["concepts"],
                    "scales": sorted(entry["scales"]),
                    "title_on_page": bool(
                        re.search(TITLES[statement], texts[page - 1], re.IGNORECASE)
                    ),
                }
                for page, entry in hits.items()
                if len(entry["concepts"]) >= 2
            ),
            key=lambda c: (-c["matched"], -c["title_on_page"], c["page"]),
        )
        statements[statement] = {"facts": checked, "candidate_pages": candidates[:5]}
    return {
        "id": row["id"],
        "accession": row["accession"],
        "form": row["form"],
        "report_date": row["report_date"],
        "pdf_sha256": file_hash(pdf),
        "pdf_page_count": len(texts),
        "companyfacts_sha256": facts["source_sha256"],
        "binding": _binding(row, numbers),
        "statements": statements,
        "review": "unreviewed",
    }


def draft_cohort(cohort: Path, output: Path) -> dict:
    rows = list(csv.DictReader((cohort / "cohort.csv").open(encoding="utf-8")))
    filings = [draft_filing(row, cohort) for row in rows]
    review = []
    for filing in filings:
        binding = filing["binding"]
        status = binding["status"]
        if "pdf_latest_date" in binding:
            status += f" (PDF {binding['pdf_latest_date']})"
        for statement, item in filing["statements"].items():
            best = item["candidate_pages"][0] if item["candidate_pages"] else None
            facts = [f for f in item["facts"] if f.get("value") is not None]
            review.append(
                {
                    "id": filing["id"],
                    "binding": status,
                    "statement": statement,
                    "best_page": best["page"] if best else "",
                    "matched": f"{best['matched'] if best else 0}/{len(facts)}",
                    "scale": ",".join(map(str, best["scales"])) if best else "",
                    "title_on_page": best["title_on_page"] if best else "",
                    "other_pages": " ".join(
                        str(c["page"]) for c in item["candidate_pages"][1:]
                    ),
                    "confirmed_pages": "",
                    "notes": "",
                }
            )
    report = {
        "status": "draft; every page pointer needs visual confirmation",
        "method": "exact-accession us-gaap companyfacts at the report date; candidate pages "
        "print at least two of a statement's filed values at a scale of 1, 1e3, 1e6 or 1e9",
        "limitations": [
            "companyfacts omits custom and dimensional facts",
            "a 10-Q income-statement value is the quarter; year-to-date columns are not located",
            "values below 100 at every scale are not used to locate pages",
            "a statement continuing onto a page without two filed values is not found",
        ],
        "binding_mismatches": [
            {"id": f["id"], "cohort_accession": f["accession"], **f["binding"]}
            for f in filings
            if f["binding"]["status"] != "consistent"
        ],
        "filings": filings,
    }
    write_json(output, report)
    with (output.parent / (output.stem + "-review.csv")).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(review[0]))
        writer.writeheader()
        writer.writerows(review)
    return report
