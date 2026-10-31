"""Offline accounting-screen denominators and fixed-grid tolerance sensitivity.

These analyses never change extractions, select a winning tolerance, or repair values.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from .consistency import check_consistency
from .schema import Experiment, PageExtraction
from .storage import read_json


def _denominators(documents: dict) -> dict:
    by_check = defaultdict(Counter)
    skips = Counter()
    excluded = 0
    for document in documents.values():
        excluded += len(document["excluded_tables"])
        for check in document["checks"]:
            by_check[check["check"]][check["status"]] += 1
            if check["status"] == "skipped":
                skips[check["reason"]] += 1
    totals = Counter()
    identities = {}
    for name, counts in sorted(by_check.items()):
        totals.update(counts)
        applicable = counts["pass"] + counts["fail"]
        identities[name] = {
            "passed": counts["pass"], "failed": counts["fail"],
            "skipped": counts["skipped"], "applicable": applicable,
            "candidates": sum(counts.values()),
            "pass_rate": counts["pass"] / applicable if applicable else None,
        }
    applicable = totals["pass"] + totals["fail"]
    return {
        "passed": totals["pass"], "failed": totals["fail"],
        "skipped": totals["skipped"], "applicable": applicable,
        "candidate_checks": sum(totals.values()),
        "pass_rate": totals["pass"] / applicable if applicable else None,
        "excluded_table_fragments": excluded,
        "by_identity": identities,
        "skip_reasons": dict(sorted(skips.items())),
    }


def analyze_screens(run: Path, relative_tolerances: list[Decimal]) -> dict:
    if not relative_tolerances or len(set(relative_tolerances)) != len(relative_tolerances):
        raise ValueError("Supply a nonempty grid of distinct relative tolerances")
    if any(not t.is_finite() or not 0 <= t <= 1 for t in relative_tolerances):
        raise ValueError("Relative tolerances must be finite and between 0 and 1")
    snapshot, results = read_json(run / "run.json"), read_json(run / "results.json")
    config = Experiment.model_validate(snapshot["config"])
    documents = {d["id"]: d for d in snapshot["documents"]}
    stored = {doc: item["consistency"] for doc, item in results["documents"].items()
              if "consistency" in item}
    grid = []
    for tolerance in relative_tolerances:
        trial_config = config.model_copy(update={"relative_tolerance": tolerance})
        recomputed = {}
        for doc, item in results["documents"].items():
            pages = [PageExtraction.model_validate(p) for p in item["pages"]]
            recomputed[doc] = check_consistency(
                pages, trial_config, documents[doc].get("eps_eligible_tables", [])
            )
        grid.append({"relative_tolerance": str(tolerance), **_denominators(recomputed)})
    return {
        "run": str(run), "run_fingerprint": snapshot.get("fingerprint"),
        "run_status": results["status"],
        "requested_pages": sum(len(d["pages"]) for d in documents.values()),
        "accepted_pages": sum(len(d["pages"]) for d in results["documents"].values()),
        "rejected_pages": sum(len(d.get("rejected_pages", []))
                              for d in results["documents"].values()),
        "stored_screen_denominators": _denominators(stored),
        "absolute_tolerance_normalized_units": str(config.absolute_tolerance),
        "sensitivity_grid": grid,
        "note": "Accounting consistency only, not extraction correctness. The same fixed "
        "absolute tolerance is held at every relative threshold; this is not a "
        "printed-precision rounding model. Missing concepts and rejected pages can remove "
        "candidate checks, so conditional pass rates must accompany page coverage. "
        "Excluded table fragments are not unique linked tables. Stored and recomputed "
        "screens can differ if the checker implementation changed. The grid is descriptive "
        "and must not be tuned on the held-out evaluation set.",
    }
