"""Offline validation entry point; input files are never overwritten."""

import argparse
import json
import importlib
from pathlib import Path

from .accounting import EPSLedger, RetainedEarningsLedger, check_eps, check_retained_earnings, denominator_ledger
from .cleaning import CleaningEdit, audit_cleaning
from .contracts import LayoutReference, SourceGrid, teds_inputs
from .stages import StageManifest, verify_stages


def main():
    parser = argparse.ArgumentParser(description="Validate a NEW reconstruction artifact offline")
    parser.add_argument("kind", choices=["schemas", "eps", "retained-earnings", "teds-inputs", "stages", "cleaning", "prepare", "extract", "run"])
    parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--transport-factory", help="Explicit module:function taking (config, phase) and returning the caller's budgeted transport")
    args = parser.parse_args()
    if args.kind == "schemas":
        from .pipeline import ReconstructionConfig
        from .model_contracts import GeometryPage, SchemaPlan, GeneratedPrompt
        result = {cls.__name__: cls.model_json_schema() for cls in
                  (SourceGrid, LayoutReference, EPSLedger, RetainedEarningsLedger, StageManifest, CleaningEdit,
                   ReconstructionConfig, GeometryPage, SchemaPlan, GeneratedPrompt)}
    else:
        if args.input is None:
            parser.error("an input JSON file is required")
        data = json.loads(args.input.read_text())
        if args.kind in {"prepare", "extract", "run"}:
            from .pipeline import ReconstructionConfig, prepare_document, extract_document, read_json
            if not args.transport_factory or ":" not in args.transport_factory:
                parser.error("An explicit --transport-factory module:function is required; no default network client exists")
            module_name, function_name = args.transport_factory.split(":", 1)
            factory = getattr(importlib.import_module(module_name), function_name)
            config = ReconstructionConfig.model_validate(data)
            if args.kind in {"prepare", "run"}:
                prepared = prepare_document(config, factory(config, "prepare"))
                result = prepared
            else:
                prepared = read_json(config.prepared_dir / "prepared.json")
            if args.kind in {"extract", "run"}:
                result = extract_document(config, prepared, factory(config, "extract"))
        elif args.kind == "eps":
            result = denominator_ledger([check_eps(EPSLedger.model_validate(x)) for x in data["cases"]])
        elif args.kind == "retained-earnings":
            result = denominator_ledger([check_retained_earnings(RetainedEarningsLedger.model_validate(x))
                                         for x in data["cases"]])
        elif args.kind == "teds-inputs":
            result = teds_inputs(LayoutReference.model_validate(data["reference"]),
                                 SourceGrid.model_validate(data["prediction"]))
        elif args.kind == "stages":
            if args.root is None:
                parser.error("--root is required for stages")
            result = verify_stages(StageManifest.model_validate(data), args.root)
        else:
            result = audit_cleaning(data["before"], data["after"],
                                     [CleaningEdit.model_validate(x) for x in data["edits"]])
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
