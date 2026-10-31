"""Independent manual-reference scoring with strict physical cell alignment."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

from .numbers import parse_number
from .schema import Gold, PageExtraction
from .storage import digest, read_json, write_json


# PDFs print typographic quotes and dashes; models often emit ASCII. Labels only.
_TYPOGRAPHY = str.maketrans(
    {c: "'" for c in "\u2018\u2019\u201b\u2032"}
    | {c: '"' for c in "\u201c\u201d\u201f\u2033"}
    | {c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}
)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalize_label(value: str) -> str:
    return _normalize(value.translate(_TYPOGRAPHY))


def _cells(pages: list[PageExtraction]) -> dict:
    result = {}
    for page in pages:
        for table in page.tables:
            columns = {c.column_index: c for c in table.columns}
            for row in table.rows:
                for cell in row.cells:
                    key = (page.page, table.table_index, row.row_index, cell.column_index)
                    result[key] = (cell, row, columns[cell.column_index], table)
    return result


def _numeric_match(p, pc, g, gc) -> bool:
    pn, gn = parse_number(p.raw_value), parse_number(g.raw_value)
    return (
        pn is not None
        and gn is not None
        and pn == gn
        and p.scale is not None
        and p.scale == g.scale
        and p.quantity == g.quantity
        and p.currency == g.currency
        and pc.period == gc.period
    )


def _structure_match(prediction, reference) -> bool:
    _, pr, pc, pt = prediction
    _, gr, gc, gt = reference
    return (
        [_normalize_label(s) for s in pr.path] == [_normalize_label(s) for s in gr.path]
        and _normalize_label(pr.label) == _normalize_label(gr.label)
        and pr.role == gr.role
        and pc.period == gc.period
        and pt.statement == gt.statement
        and _normalize(pt.scope) == _normalize(gt.scope)
    )


def _pairs(predicted: list, reference: list) -> list[tuple[int, int]]:
    """Positions paired by the longest matching runs of two key sequences."""
    matcher = SequenceMatcher(None, predicted, reference, autojunk=False)
    return [
        (block.a + offset, block.b + offset)
        for block in matcher.get_matching_blocks()
        for offset in range(block.size)
    ]


def _column_key(column) -> tuple:
    # The period identifies a column; the label only when there is no period.
    return (column.period, None if column.period else _normalize_label(column.label))


def _aligned_cells(predictions: list[PageExtraction], references: list[PageExtraction]) -> dict:
    """Map predicted (page, table, row, column) to the reference cell it is paired with.

    Diagnostic only: tables stay strictly aligned. Rows are paired by normalized label
    sequence and columns by period sequence (label when a column has no period), so one
    inserted or omitted row or column does not shift every later one. Pairing uses
    headings only, never cell values. Unpaired rows and columns contribute nothing.
    """
    gold_tables = {(p.page, t.table_index): t for p in references for t in p.tables}
    mapping = {}
    for page in predictions:
        for table in page.tables:
            reference = gold_tables.get((page.page, table.table_index))
            if reference is None:
                continue
            rows = _pairs(
                [_normalize_label(r.label) for r in table.rows],
                [_normalize_label(r.label) for r in reference.rows],
            )
            columns = dict(
                (table.columns[a].column_index, reference.columns[b].column_index)
                for a, b in _pairs(
                    [_column_key(c) for c in table.columns],
                    [_column_key(c) for c in reference.columns],
                )
            )
            for a, b in rows:
                for cell in table.rows[a].cells:
                    if cell.column_index in columns:
                        key = (page.page, table.table_index, table.rows[a].row_index)
                        mapping[(*key, cell.column_index)] = (
                            page.page,
                            table.table_index,
                            reference.rows[b].row_index,
                            columns[cell.column_index],
                        )
    return mapping


def score_pages(predictions: list[PageExtraction], references: list[PageExtraction]) -> dict:
    annotated = {p.page for p in references}
    predictions = [p for p in predictions if p.page in annotated]
    pred = _cells(predictions)
    gold = _cells(references)
    numeric_correct, text_correct, hierarchy_correct, semantic_correct = 0, 0, 0, 0
    errors = []
    for key in sorted(set(pred) | set(gold)):
        if key not in pred or key not in gold:
            errors.append({"cell": key, "reason": "missing" if key not in pred else "extra"})
            continue
        p, pr, pc, pt = pred[key]
        g, gr, gc, gt = gold[key]
        raw_match = _normalize(p.raw_value) == _normalize(g.raw_value)
        text_correct += raw_match
        structure_match = _structure_match(pred[key], gold[key])
        hierarchy_correct += structure_match
        numeric_match = _numeric_match(p, pc, g, gc)
        numeric_correct += numeric_match
        semantic_correct += numeric_match and structure_match
        if not (raw_match and structure_match and numeric_match):
            errors.append(
                {
                    "cell": key,
                    "reason": "mismatch",
                    "raw_match": raw_match,
                    "hierarchy_match": structure_match,
                    "numeric_match": numeric_match,
                    "prediction": p.model_dump(),
                    "reference": g.model_dump(),
                }
            )

    # Numeric denominators exclude gold missing/unparsed cells. Those still affect raw scoring.
    numeric_gold = sum(parse_number(c.raw_value) is not None for c, _, _, _ in gold.values())
    numeric_pred = sum(parse_number(c.raw_value) is not None for c, _, _, _ in pred.values())
    pairs = _aligned_cells(predictions, references)
    aligned_numeric_correct = 0
    for key, (p, _, pc, _) in pred.items():
        match = gold.get(pairs.get(key))
        if match is not None:
            g, _, gc, _ = match
            aligned_numeric_correct += _numeric_match(p, pc, g, gc)
    return {
        "annotated_pages": len(annotated),
        "gold_cells": len(gold),
        "predicted_cells": len(pred),
        "raw_exact_correct": text_correct,
        "hierarchy_correct": hierarchy_correct,
        "semantic_correct": semantic_correct,
        "numeric_correct": numeric_correct,
        "numeric_gold_cells": numeric_gold,
        "numeric_predicted_cells": numeric_pred,
        "raw_exact_recall": text_correct / len(gold) if gold else None,
        "hierarchy_recall": hierarchy_correct / len(gold) if gold else None,
        "numeric_precision": numeric_correct / numeric_pred if numeric_pred else None,
        "numeric_recall": numeric_correct / numeric_gold if numeric_gold else None,
        "numeric_f1": 2 * numeric_correct / (numeric_pred + numeric_gold)
        if numeric_pred + numeric_gold else None,
        "semantic_precision": semantic_correct / numeric_pred if numeric_pred else None,
        "semantic_recall": semantic_correct / numeric_gold if numeric_gold else None,
        "semantic_f1": 2 * semantic_correct / (numeric_pred + numeric_gold)
        if numeric_pred + numeric_gold else None,
        "aligned_numeric_correct": aligned_numeric_correct,
        "aligned_numeric_recall": aligned_numeric_correct / numeric_gold if numeric_gold else None,
        "errors": errors,
    }


def cell_outcomes(predictions: list[PageExtraction], references: list[PageExtraction]) -> dict:
    """Per reference cell: whether score_pages counts it correct (recall-side, for pairing).

    numeric and aligned are None for reference cells without a parsed value, which the
    numeric denominators exclude.
    """
    annotated = {p.page for p in references}
    pred = _cells([p for p in predictions if p.page in annotated])
    gold = _cells(references)
    pairs = _aligned_cells([p for p in predictions if p.page in annotated], references)
    aligned = set()
    for key, (p, _, pc, _) in pred.items():
        match = gold.get(pairs.get(key))
        if match is not None and _numeric_match(p, pc, match[0], match[2]):
            aligned.add(pairs[key])
    outcomes = {}
    for key, (g, _, gc, _) in gold.items():
        numeric = None
        if parse_number(g.raw_value) is not None:
            numeric = key in pred and _numeric_match(pred[key][0], pred[key][2], g, gc)
        outcomes[key] = {
            "numeric": numeric,
            "semantic": None if numeric is None else numeric and key in pred
            and _structure_match(pred[key], gold[key]),
            "hierarchy": key in pred and _structure_match(pred[key], gold[key]),
            "aligned": None if numeric is None else key in aligned,
        }
    return outcomes


def load_gold(gold_directory: Path) -> tuple[dict[str, Gold], dict[str, str]]:
    references, hashes = {}, {}
    for path in sorted(gold_directory.glob("*.json")):
        reference = Gold.model_validate(read_json(path))
        if reference.document_id in references:
            raise ValueError("Duplicate gold document")
        references[reference.document_id] = reference
        hashes[reference.document_id] = digest(reference.model_dump(mode="json"))
    if not references:
        raise ValueError("No reviewed reference JSON files found")
    return references, hashes


def evaluate_run(run: Path, gold_directory: Path) -> dict:
    manifest = read_json(run / "run.json")
    results = read_json(run / "results.json")
    documents = {d["id"]: d for d in manifest["documents"]}
    per_document = {}
    reference_hashes = {}
    totals: Counter = Counter()
    for path in sorted(gold_directory.glob("*.json")):
        reference = Gold.model_validate(read_json(path))
        reference_hashes[reference.document_id] = digest(reference.model_dump(mode="json"))
        if reference.document_id in per_document:
            raise ValueError("Duplicate gold document")
        document = documents.get(reference.document_id)
        if document is None or document["pdf_sha256"] != reference.pdf_sha256:
            raise ValueError(f"Gold document/PDF hash does not match run: {reference.document_id}")
        if not {p.page for p in reference.pages} <= set(document["pages"]):
            raise ValueError("Gold contains pages not requested in run")
        predictions = [
            PageExtraction.model_validate(p)
            for p in results["documents"][reference.document_id]["pages"]
        ]
        scores = score_pages(predictions, reference.pages)
        per_document[reference.document_id] = scores
        for key in [
            "gold_cells",
            "predicted_cells",
            "raw_exact_correct",
            "hierarchy_correct",
            "numeric_correct",
            "semantic_correct",
            "aligned_numeric_correct",
            "numeric_gold_cells",
            "numeric_predicted_cells",
            "annotated_pages",
        ]:
            totals[key] += scores[key]
    if not per_document:
        raise ValueError("No reviewed reference JSON files found")
    metrics = dict(totals)
    for name, numerator, denominator in [
        ("numeric_precision", "numeric_correct", "numeric_predicted_cells"),
        ("numeric_recall", "numeric_correct", "numeric_gold_cells"),
        ("raw_exact_recall", "raw_exact_correct", "gold_cells"),
        ("hierarchy_recall", "hierarchy_correct", "gold_cells"),
        ("semantic_precision", "semantic_correct", "numeric_predicted_cells"),
        ("semantic_recall", "semantic_correct", "numeric_gold_cells"),
        ("aligned_numeric_precision", "aligned_numeric_correct", "numeric_predicted_cells"),
        ("aligned_numeric_recall", "aligned_numeric_correct", "numeric_gold_cells"),
    ]:
        metrics[name] = totals[numerator] / totals[denominator] if totals[denominator] else None
    metrics["numeric_f1"] = (
        2
        * totals["numeric_correct"]
        / (totals["numeric_predicted_cells"] + totals["numeric_gold_cells"])
        if totals["numeric_predicted_cells"] + totals["numeric_gold_cells"]
        else None
    )
    metrics["semantic_f1"] = (
        2
        * totals["semantic_correct"]
        / (totals["numeric_predicted_cells"] + totals["numeric_gold_cells"])
        if totals["numeric_predicted_cells"] + totals["numeric_gold_cells"]
        else None
    )
    metrics["aligned_numeric_f1"] = (
        2
        * totals["aligned_numeric_correct"]
        / (totals["numeric_predicted_cells"] + totals["numeric_gold_cells"])
        if totals["numeric_predicted_cells"] + totals["numeric_gold_cells"]
        else None
    )
    report = {
        "protocol": "strict page/table/row/column alignment; exact numeric + unit/period match",
        "diagnostic_protocol": "aligned_* metrics pair rows by label sequence and columns by "
        "period (or label) sequence within strictly aligned tables; same numeric criteria; "
        "not the primary endpoint",
        "metrics": metrics,
        "documents": per_document,
        "gold_sha256": reference_hashes,
        "unannotated_documents": sorted(set(documents) - set(per_document)),
        "requested_pages": sum(len(d["pages"]) for d in documents.values()),
        "note": "Hierarchy score is a strict cell-level proxy, NOT TEDS. No significance claim.",
    }
    write_json(run / "evaluation.json", report)
    return report


def compare_runs(left: Path, right: Path) -> dict:
    """Paired per-document differences, not independent samples or a significance test."""
    lm, rm = read_json(left / "run.json"), read_json(right / "run.json")
    if lm["documents"] != rm["documents"]:
        raise ValueError("Comparisons require identical document manifests and PDF hashes")
    a, b = read_json(left / "evaluation.json"), read_json(right / "evaluation.json")
    if set(a["documents"]) != set(b["documents"]):
        raise ValueError("Comparisons require identical annotated documents")
    # Reference fingerprint prevents comparing different human annotation revisions.
    if a.get("gold_sha256") != b.get("gold_sha256"):
        raise ValueError("Comparisons require identical reference annotations")
    rows = []
    for doc in a["documents"]:
        av, bv = a["documents"][doc]["numeric_recall"], b["documents"][doc]["numeric_recall"]
        # Evaluations written before the diagnostic existed lack aligned_numeric_recall.
        aa = a["documents"][doc].get("aligned_numeric_recall")
        ba = b["documents"][doc].get("aligned_numeric_recall")
        rows.append(
            {
                "document": doc,
                "left_numeric_recall": av,
                "right_numeric_recall": bv,
                "right_minus_left": float(Decimal(str(bv)) - Decimal(str(av)))
                if av is not None and bv is not None
                else None,
                "left_aligned_numeric_recall": aa,
                "right_aligned_numeric_recall": ba,
                "aligned_right_minus_left": float(Decimal(str(ba)) - Decimal(str(aa)))
                if aa is not None and ba is not None
                else None,
            }
        )
    return {
        "left": str(left),
        "right": str(right),
        "paired_documents": rows,
        "left_config": lm["config"],
        "right_config": rm["config"],
        "note": "Descriptive paired comparison. Inspect all configuration differences.",
    }
