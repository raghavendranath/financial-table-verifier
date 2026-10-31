"""Measure whether arithmetic screens agree with independent extraction correctness."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .evaluation import score_pages
from .schema import Gold, PageExtraction
from .storage import digest, read_json, write_json


def _rates(counts: dict) -> dict:
    passed = counts["correct_pass"] + counts["incorrect_pass"]
    failed = counts["correct_fail"] + counts["incorrect_fail"]
    incorrect = counts["incorrect_pass"] + counts["incorrect_fail"]
    return {
        "counts": dict(counts),
        "audited": sum(counts.values()),
        "passed": passed,
        "failed": failed,
        "false_pass_fraction_among_passed": counts["incorrect_pass"] / passed
        if passed else None,
        "false_flag_fraction_among_failed": counts["correct_fail"] / failed
        if failed else None,
        "error_detection_recall": counts["incorrect_fail"] / incorrect
        if incorrect else None,
    }


def _groups(pages: list[PageExtraction]) -> dict:
    roots = {}
    result = defaultdict(list)
    for page in pages:
        for table in page.tables:
            key = f"p{page.page}-t{table.table_index}"
            root = roots.get(table.continuation_of, key)
            roots[key] = root
            result[root].append(page.model_copy(update={"tables": [table]}))
    # A page can contribute more than one table fragment; merge without reindexing.
    merged = {}
    for key, entries in result.items():
        by_page = {}
        for entry in entries:
            if entry.page in by_page:
                by_page[entry.page].tables.extend(entry.tables)
            else:
                by_page[entry.page] = entry.model_copy(deep=True)
        merged[key] = list(by_page.values())
    return merged


def audit_document(
    pages: list[PageExtraction], references: list[PageExtraction], checks: list[dict]
) -> dict:
    predicted, gold = _groups(pages), _groups(references)
    annotated_pages = {p.page for p in references}
    counts = Counter(
        {k: 0 for k in ("correct_pass", "incorrect_pass", "correct_fail", "incorrect_fail")}
    )
    outcomes = []
    for check in checks:
        outcome = {
            "table": check["table"],
            "period": check["period"],
            "check": check["check"],
            "screen": check["status"],
            "audited": False,
        }
        group_id = check["table"]
        if check["status"] == "skipped":
            outcome["reason"] = "arithmetic check was not applicable"
        elif group_id not in gold:
            outcome["reason"] = "no reviewed reference for root table"
        elif not {p.page for p in predicted.get(group_id, [])} <= annotated_pages:
            outcome["reason"] = "not all predicted continuation pages are annotated"
        else:
            score = score_pages(predicted.get(group_id, []), gold[group_id])
            if score["numeric_gold_cells"] == 0:
                outcome["reason"] = "no independently readable numeric reference cells"
            else:
                correct = (
                    score["semantic_correct"]
                    == score["numeric_gold_cells"]
                    == score["numeric_predicted_cells"]
                )
                category = ("correct" if correct else "incorrect") + "_" + check["status"]
                counts[category] += 1
                outcome.update(audited=True, table_extraction_correct=correct, category=category)
        outcomes.append(outcome)
    # One table is one audit unit even when it has multiple equations or periods.
    table_counts = Counter({key: 0 for key in counts})
    tables = defaultdict(list)
    for outcome in outcomes:
        tables[outcome["table"]].append(outcome)
    unaudited_tables = 0
    for table_outcomes in tables.values():
        applicable = [o for o in table_outcomes if o["screen"] != "skipped"]
        if not applicable or not all(o["audited"] for o in applicable):
            unaudited_tables += 1
            continue
        correct = all(o["table_extraction_correct"] for o in applicable)
        status = "fail" if any(o["screen"] == "fail" for o in applicable) else "pass"
        table_counts[("correct" if correct else "incorrect") + "_" + status] += 1
    return {
        **_rates(counts),
        "audited_checks": sum(counts.values()),
        "unaudited_checks": sum(not o["audited"] for o in outcomes),
        "table_level": {**_rates(table_counts), "unaudited_tables": unaudited_tables},
        "outcomes": outcomes,
        "note": "Correctness covers all annotated numeric cells of the linked table group, across "
        "periods. Multiple checks on a table are correlated. Skips are not successes.",
    }


def audit_run(run: Path, gold_directory: Path) -> dict:
    manifest = read_json(run / "run.json")
    results = read_json(run / "results.json")
    documents = {d["id"]: d for d in manifest["documents"]}
    reports, fingerprints = {}, {}
    for path in sorted(gold_directory.glob("*.json")):
        gold = Gold.model_validate(read_json(path))
        if gold.document_id in reports:
            raise ValueError("Duplicate reference document")
        document = documents.get(gold.document_id)
        if document is None or document["pdf_sha256"] != gold.pdf_sha256:
            raise ValueError("Reference identity/PDF hash mismatch")
        if not {p.page for p in gold.pages} <= set(document["pages"]):
            raise ValueError("Reference pages were not requested in run")
        item = results["documents"][gold.document_id]
        pages = [PageExtraction.model_validate(p) for p in item["pages"]]
        reports[gold.document_id] = audit_document(
            pages,
            gold.pages,
            item.get("consistency", {}).get("checks", []),
        )
        fingerprints[gold.document_id] = digest(gold.model_dump(mode="json"))
    if not reports:
        raise ValueError("No reviewed reference files found")
    counts = Counter({key: 0 for key in ("correct_pass", "incorrect_pass", "correct_fail", "incorrect_fail")})
    table_counts = counts.copy()
    for document in reports.values():
        counts.update(document["counts"])
        table_counts.update(document["table_level"]["counts"])
    report = {
        "check_level": {
            **_rates(counts),
            "unaudited_checks": sum(d["unaudited_checks"] for d in reports.values()),
        },
        "table_level": {
            **_rates(table_counts),
            "unaudited_tables": sum(d["table_level"]["unaudited_tables"] for d in reports.values()),
        },
        "documents": reports,
        "gold_sha256": fingerprints,
        "unannotated_documents": sorted(set(documents) - set(reports)),
        "note": "A table is flagged when any applicable arithmetic check fails; skipped checks "
        "are excluded. Table-level reporting deduplicates equations and periods, but tables "
        "within a filing remain correlated. Descriptive audit, not a significance test. "
        "A false flag is an arithmetically flagged table whose annotated extraction is correct; "
        "the accounting reason still requires independent adjudication.",
    }
    write_json(run / "validator-audit.json", report)
    return report
