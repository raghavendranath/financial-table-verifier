"""Repeated paired runs: per-condition spread and exact paired tests, with their caveats."""

from __future__ import annotations

from fractions import Fraction
from math import comb
from pathlib import Path
from statistics import mean, stdev

from .evaluation import cell_outcomes, load_gold, score_pages
from .schema import PageExtraction
from .storage import read_json

MEASURES = ("numeric", "semantic", "hierarchy", "aligned")
# Settings allowed to differ between the paired conditions of one repeat.
CONDITION_KEYS = {"context", "output"}


def exact_two_sided(successes: int, trials: int) -> float | None:
    """Exact two-sided binomial p at 1/2: McNemar on discordant pairs, or a sign test."""
    if not isinstance(successes, int) or not isinstance(trials, int) or not 0 <= successes <= trials:
        raise ValueError("Binomial counts must be integers with 0 <= successes <= trials")
    if trials == 0:
        return None
    tail = sum(comb(trials, k) for k in range(min(successes, trials - successes) + 1))
    return float(min(Fraction(1), Fraction(2 * tail, 2**trials)))


def _run_cells(run: Path, references: dict) -> dict:
    manifest = read_json(run / "run.json")
    results = read_json(run / "results.json")
    documents = {d["id"]: d for d in manifest["documents"]}
    cells = {}
    for doc, reference in references.items():
        document = documents.get(doc)
        if document is None or document["pdf_sha256"] != reference.pdf_sha256:
            raise ValueError(f"Gold document/PDF hash does not match run: {doc}")
        if results["status"] != "complete":
            raise ValueError(f"Run is not complete: {run}")
        if not {p.page for p in reference.pages} <= set(document["pages"]):
            raise ValueError(f"Gold contains pages not requested in run: {doc}")
        predictions = [PageExtraction.model_validate(p) for p in results["documents"][doc]["pages"]]
        pages = set(document["pages"])
        for (page, table, row, column), outcome in cell_outcomes(
            predictions, reference.pages
        ).items():
            # Sequential mode only passes the immediately preceding selected page.
            group = "context" if page - 1 in pages else "same_input"
            cells[(doc, page, table, row, column)] = {**outcome, "group": group}
    return cells


def _recall(cells: dict, measure: str, group: str | None = None) -> float | None:
    values = [
        c[measure] for c in cells.values() if c[measure] is not None and group in {None, c["group"]}
    ]
    return sum(values) / len(values) if values else None


def _providers(run: Path) -> list[str]:
    events = read_json(run / "requests.json") if (run / "requests.json").exists() else []
    return sorted({e["provider"] for e in events if e.get("status") == "response"})


def _document_scores(run: Path, references: dict) -> dict:
    result = read_json(run / "results.json")
    return {
        doc: score_pages(
            [PageExtraction.model_validate(p) for p in result["documents"][doc]["pages"]],
            reference.pages,
        )
        for doc, reference in references.items()
    }


def _paired(left: dict, right: dict, measure: str, group: str | None) -> dict:
    keys = [
        k
        for k in left
        if left[k][measure] is not None and group in {None, left[k]["group"]}
    ]
    only_left = sum(left[k][measure] and not right[k][measure] for k in keys)
    only_right = sum(right[k][measure] and not left[k][measure] for k in keys)
    return {
        "cells": len(keys),
        "only_left_correct": only_left,
        "only_right_correct": only_right,
        "mcnemar_exact_p": exact_two_sided(only_left, only_left + only_right),
    }


def summarize(left: list[Path], right: list[Path], gold_directory: Path) -> dict:
    if not left or len(left) != len(right):
        raise ValueError("Give the same number of left and right runs, paired by repeat")
    if len({p.resolve() for p in left + right}) != len(left + right):
        raise ValueError("Each repeated run must be distinct; a run cannot be counted twice")
    references, hashes = load_gold(gold_directory)
    configs = [read_json(r / "run.json") for r in left + right]
    if any(c["documents"] != configs[0]["documents"] for c in configs):
        raise ValueError("Summaries require identical document manifests and PDF hashes")
    differences = sorted(
        {
            key
            for c in configs
            for key in set(c["config"]) | set(configs[0]["config"])
            if key not in CONDITION_KEYS and c["config"].get(key) != configs[0]["config"].get(key)
        }
    )
    cells = {"left": [], "right": []}
    document_scores = {"left": [], "right": []}
    runs = []
    for side, paths in (("left", left), ("right", right)):
        for path in paths:
            outcome = _run_cells(path, references)
            cells[side].append(outcome)
            scores = _document_scores(path, references)
            document_scores[side].append(scores)
            denominator = sum(s["numeric_predicted_cells"] + s["numeric_gold_cells"]
                              for s in scores.values())
            runs.append(
                {
                    "side": side,
                    "run": str(path),
                    "context": read_json(path / "run.json")["config"]["context"],
                    "providers": _providers(path),
                    **{f"{m}_f1": 2 * sum(s[f"{m}_correct"] for s in scores.values())
                       / denominator if denominator else None for m in ("numeric", "semantic")},
                    **{
                        f"{m}_recall{'' if g is None else '_' + g}": _recall(outcome, m, g)
                        for m in MEASURES
                        for g in (None, "context", "same_input")
                    },
                }
            )
    conditions = {}
    for side in ("left", "right"):
        rows = [r for r in runs if r["side"] == side]
        conditions[side] = {
            key: {
                "mean": mean(r[key] for r in rows),
                "min": min(r[key] for r in rows),
                "max": max(r[key] for r in rows),
                "sample_sd": stdev(r[key] for r in rows) if len(rows) > 1 else None,
            }
            for key in rows[0]
            if key.endswith(("recall", "_context", "_same_input", "_f1"))
            and all(r[key] is not None for r in rows)
        }
    paired = []
    for repeat, (a, b) in enumerate(zip(cells["left"], cells["right"]), 1):
        if set(a) != set(b):
            raise ValueError("Paired runs must have identical reference cell populations")
        paired.append(
            {
                "repeat": repeat,
                "same_provider": _providers(left[repeat - 1]) == _providers(right[repeat - 1]),
                **{
                    f"{m}{'' if g is None else '_' + g}": _paired(a, b, m, g)
                    for m in MEASURES
                    for g in (None, "context", "same_input")
                },
            }
        )
    # Cluster-level test: one paired difference per filing, averaged over repeats.
    filings = {}
    for measure in MEASURES:
        diffs = {}
        for doc in references:
            per_repeat = []
            for a, b in zip(cells["left"], cells["right"]):
                keys = [k for k in a if k[0] == doc and a[k][measure] is not None]
                if keys:
                    per_repeat.append(
                        sum(b[k][measure] for k in keys) / len(keys)
                        - sum(a[k][measure] for k in keys) / len(keys)
                    )
            if per_repeat:
                diffs[doc] = mean(per_repeat)
        positive = sum(d > 0 for d in diffs.values())
        nonzero = sum(d != 0 for d in diffs.values())
        filings[measure] = {
            "right_minus_left": diffs,
            "right_better": positive,
            "left_better": nonzero - positive,
            "sign_test_exact_p": exact_two_sided(positive, nonzero),
        }
    # F1 penalizes extra predictions; recall-only tests cannot detect hallucinated cells.
    per_document_f1 = {"numeric": {}, "semantic": {}}
    for measure in per_document_f1:
        for doc, reference in references.items():
            differences_f1 = []
            for a, b in zip(document_scores["left"], document_scores["right"]):
                left_denominator = a[doc]["numeric_predicted_cells"] + a[doc]["numeric_gold_cells"]
                right_denominator = b[doc]["numeric_predicted_cells"] + b[doc]["numeric_gold_cells"]
                if left_denominator and right_denominator:
                    differences_f1.append(
                        Fraction(2 * b[doc][f"{measure}_correct"], right_denominator)
                        - Fraction(2 * a[doc][f"{measure}_correct"], left_denominator)
                    )
            if differences_f1:
                per_document_f1[measure][doc] = mean(differences_f1)
    paired_f1 = {}
    for measure, diffs in per_document_f1.items():
        positive = sum(d > 0 for d in diffs.values())
        nonzero = sum(d != 0 for d in diffs.values())
        paired_f1[measure] = {
            "right_minus_left": {doc: float(value) for doc, value in diffs.items()},
            "mean_filing_difference": float(mean(diffs.values())) if diffs else None,
            "right_better": positive,
            "left_better": nonzero - positive,
            "sign_test_exact_p": exact_two_sided(positive, nonzero),
        }
    providers = sorted({p for r in runs for p in r["providers"]})
    warnings = []
    if len(providers) > 1:
        warnings.append("Runs used different providers: " + ", ".join(providers))
    if not all(p["same_provider"] for p in paired):
        warnings.append("A repeat's paired runs used different providers")
    if differences:
        warnings.append(f"Configs differ beyond the condition: {differences}")
    for key in ("code_sha256", "system_prompt", "response_schema", "python", "dependencies"):
        if any(c.get(key) != configs[0].get(key) for c in configs):
            warnings.append(f"Frozen run metadata differs: {key}")
    return {
        "left_runs": [str(p) for p in left],
        "right_runs": [str(p) for p in right],
        "gold_sha256": hashes,
        "config_differences_beyond_condition": differences,
        "providers": providers,
        "runs": runs,
        "conditions": conditions,
        "paired_cells_by_repeat": paired,
        "paired_filings": filings,
        "paired_filing_f1": paired_f1,
        "groups": "context: the previous page was also selected, so sequential mode passed it; "
        "same_input: both conditions sent identical requests",
        "note": "Cell-level McNemar treats cells as independent, but cells within a page and "
        "filing are strongly correlated (one inserted row shifts a whole page), so its p-values "
        "are anti-conservative. The filing-level sign test respects clustering; with few "
        "filings it cannot reach significance. Repeats are not pooled.",
        "warnings": warnings,
    }
