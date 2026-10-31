"""Portable offline access to the byte-preserved paper CSV runtime and scorer.

This module is an adapter, not a modification of the frozen scientific code.
It never constructs a network transport or reads API credentials.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import re
import sys

from .storage import file_hash, read_json, write_json

RUNTIME = Path(__file__).with_name("paper_runtime")
CSV = RUNTIME / "work/reconstruction-v1/csv-first-v1"
EVALUATION = RUNTIME / "work/reconstruction-expansion-v1/evaluation-v1"
OLD_EVALUATION = RUNTIME / "work/reconstruction-v1/csv-evaluation-v1"


def verify_sources() -> dict:
    manifest = read_json(RUNTIME / "source-manifest.json")
    for entry in manifest["files"]:
        relative = Path(entry["runtime_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid vendored source path")
        path = RUNTIME / relative
        if not path.resolve().is_relative_to(RUNTIME.resolve()) or path.is_symlink():
            raise ValueError("Vendored source must stay within the paper runtime")
        if file_hash(path) != entry["sha256"]:
            raise ValueError(f"Vendored scientific source changed: {relative}")
    return {
        "verified_source_files": len(manifest["files"]),
        "csv_procedure_sha256": manifest["active_csv_procedure_sha256"],
        "scope": "Byte-preserved study code; no model calls or study data loaded",
    }


@contextmanager
def _imports():
    verify_sources()
    paths = [CSV / "src", CSV / "tests", EVALUATION, OLD_EVALUATION]
    original = sys.path[:]
    try:
        for name in ("ftv_csv_reconstruction", "source_compiler", "structural_binding",
                     "score_population", "accounting_screens", "numeric_format", "paired_analysis"):
            loaded = sys.modules.get(name)
            if loaded and not Path(loaded.__file__).resolve().is_relative_to(RUNTIME.resolve()):
                raise RuntimeError(f"Conflicting scientific module already imported: {name}")
        sys.path[:0] = [str(p) for p in paths]
        yield
    finally:
        sys.path[:] = original


def info() -> dict:
    result = verify_sources()
    with _imports():
        artifacts = importlib.import_module("ftv_csv_reconstruction.artifacts")
        actual = artifacts.procedure_hash()
        if actual != result["csv_procedure_sha256"]:
            raise ValueError("Active CSV procedure fingerprint differs")
    return {**result, "actual_csv_procedure_sha256": actual,
            "generic_json_tool_is_not_paper_scorer": True,
            "data_requirement": "Full-cohort provenance replay needs the separately archived study artifacts"}


@contextmanager
def study_code():
    """Make the verified, byte-preserved study modules available to trusted caller code."""
    with _imports():
        yield


def score(input_path: Path, output_path: Path) -> dict:
    """Score caller-supplied sources/records using the unchanged full50 scientific functions.

    This does not assert original request/response provenance. The original report
    driver retains its stricter, path-bound prospective-freeze and closure gates.
    """
    if output_path.exists():
        raise ValueError("Refusing to overwrite paper score output")
    data = read_json(input_path)
    if data.get("schema") != "FTV-portable-paper-score-input-v1":
        raise ValueError("Expected FTV-portable-paper-score-input-v1")
    with _imports():
        compiler = importlib.import_module("source_compiler")
        compiled = compiler.compile_sources(data["source_populations"], data["physical_map"])
        result = compiler.score_union(data["records"], compiled, data["table_inventory"],
                                      source_anomalies=data.get("source_anomalies"))
    report = {"schema": "FTV-portable-paper-score-output-v1", "synthetic": data.get("synthetic", False),
              "input_sha256": file_hash(input_path), "scientific_sources": verify_sources(),
              "original_model_request_provenance_verified_by_this_adapter": False, "results": result}
    write_json(output_path, report)
    return report


def demo(output: Path) -> dict:
    """Run the actual frozen CSV preparation/extraction/replay on canned synthetic inputs."""
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Paper demo output must be empty or absent")
    output.mkdir(parents=True, exist_ok=True)
    with _imports():
        fixtures = importlib.import_module("test_csv_first")
        prep = importlib.import_module("ftv_csv_reconstruction.preparation")
        pipeline = importlib.import_module("ftv_csv_reconstruction.pipeline")
        binding_fixtures = importlib.import_module("test_structural_binding")
        config = fixtures.fixture(output, selected=(1, 2))
        prepared = prep.prepare_document(config, fixtures.Canned([fixtures.plan_data(), fixtures.generated_prompt]))
        transport = fixtures.Canned([{"tables": [fixtures.table()]}] * 2)
        run = pipeline.extract_document(config, prepared, transport)
        plan = importlib.import_module("ftv_csv_reconstruction.contracts").SchemaPlan.model_validate(fixtures.plan_data())
        replay = pipeline.replay_page(config.output / "pages/p0001", plan,
                                      document_id=config.document_id, page=1)
        target = binding_fixtures.target("demo-revenue", "Revenue", doc=config.document_id,
                                          family="income_statement")
        target.update(raw_value="$ (1,234)", parsed_numeric=True, numeric_value_printed="-1234",
                      rounding_radius_printed="0.5", metadata={}, metadata_verifiability={})
        inventory = binding_fixtures.inventory(doc=config.document_id)
        inventory["pages"][0]["tables"][0]["family"] = "income_statement"
        source = {"demo": {"BSIS": {"targets": [target], "candidates": []}}}
        physical = {"physical_cells": [{"physical_id": "demo-physical", "parsed_numeric": True,
            "canonical_csv_selector": target["csv_selector"], "members": [{"partition": "demo",
            "population": "BSIS", "target_id": target["target_id"], "original_raw_value": target["raw_value"],
            "original_selector": target["csv_selector"]}]}]}
        portable = {"schema": "FTV-portable-paper-score-input-v1", "synthetic": True,
                    "source_populations": source, "physical_map": physical,
                    "records": run["raw_records"], "table_inventory": inventory}
    write_json(output / "portable-score-input.json", portable)
    result = score(output / "portable-score-input.json", output / "portable-score-output.json")
    return {"status": run["status"], "synthetic": True, "paid_requests": 0,
            "canned_extraction_calls": len(transport.requests),
            "replayed_csv_records": len(replay["records"]),
            "numeric_transcription": result["results"]["populations"]["BSIS"]["numeric_transcription"],
            "output": str(output), "note": "Canned fixture, not evidence of model quality"}


def inspect_report(path: Path, expected_sha256: str) -> dict:
    """Read-only checksum and cell-count inspection of the final saved full50 report."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Expected SHA-256 must have 64 lowercase hexadecimal characters")
    if file_hash(path) != expected_sha256:
        raise ValueError("Saved paper report SHA-256 differs")
    report = read_json(path)
    coverage = report["coverage"]
    expected_groups = {(m, c, v) for m in ("gemini", "llama", "mistral")
                       for c in ("independent", "sequential") for v in ("raw", "clean")}
    actual_groups = [(g["model_tag"], g["condition"], g["variant"]) for g in report["groups"]]
    if (coverage["scored_whole_reports"] != 50 or coverage["planned_jobs"] != 300
            or sum(coverage["job_status_counts"].values()) != 300
            or len(actual_groups) != 12 or set(actual_groups) != expected_groups):
        raise ValueError("Expected the complete fixed50/300-slot/12-group paper report")
    groups = []
    for group in report["groups"]:
        metrics = {}
        for name, population in group["populations"].items():
            cells = [c for c in population["cell_results"] if c["numeric_target"]]
            correct = sum(c["printed_numeric_equal"] is True for c in cells)
            saved = population["numeric_transcription"]
            if len(cells) != {"BSIS": 2938, "EPSRE": 508}[name]:
                raise ValueError("Fixed paper reference population differs")
            if (len(cells), correct) != (saved["fixed_denominator"], saved["correct_signed_printed_values"]):
                raise ValueError("Saved numeric cell recount differs")
            metrics[name] = {"correct": correct, "fixed_denominator": len(cells)}
        groups.append({k: group[k] for k in ("model_tag", "condition", "variant")} | {"metrics": metrics})
    return {"report_sha256": expected_sha256, "coverage": report["coverage"]["job_status_counts"],
            "reports": report["coverage"]["scored_whole_reports"], "groups": groups,
            "scope": "Saved-cell recount; not fresh inference or source/PDF provenance replay"}


def run_tests() -> dict:
    """Run portable frozen scientific tests, blocking real network connections."""
    import unittest
    from unittest.mock import patch

    names = ["test_csv_first", "test_independent_adversarial", "test_development_runner",
             "test_structural_binding", "test_accounting_screens", "test_paired_clusters",
             "test_provenance50", "test_report_full50", "test_source_compiler"]
    excluded = "test_source_compiler.CompilerTests.test_actual_source_contract_compiles_counts_without_reading_any_predictions"

    def leaves(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from leaves(item)
            else:
                yield item

    with _imports():
        tests = [case for name in names
                 for case in leaves(unittest.defaultTestLoader.loadTestsFromName(name))
                 if case.id() != excluded]
        with patch("socket.create_connection", side_effect=AssertionError("Offline tests forbid network")):
            result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(tests))
    if not result.wasSuccessful():
        raise RuntimeError(f"Frozen paper tests failed: {len(result.failures)} failures, {len(result.errors)} errors")
    return {"status": "passed", "tests_run": result.testsRun, "skipped": len(result.skipped),
            "excluded_requires_separate_study_archive": [excluded],
            "paid_requests": 0, "source_verification": verify_sources()}
