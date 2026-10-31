"""Operator-injected transport only; replay subcommand has no network factory."""
import argparse
import importlib
import json
from pathlib import Path

from .artifacts import read_json
from .contracts import CSVConfig, SchemaPlan
from .pipeline import extract_document, replay_page
from .preparation import prepare_document
from .auxiliary import extract_html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "extract", "html", "replay-page"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--transport-factory", help="trusted operator module:function; callable receives config")
    parser.add_argument("--primary-run", type=Path)
    parser.add_argument("--page-directory", type=Path)
    parser.add_argument("--schema-plan", type=Path)
    parser.add_argument("--document-id")
    parser.add_argument("--page", type=int)
    args = parser.parse_args()
    if args.stage == "replay-page":
        if args.transport_factory:
            parser.error("Replay is read-only and never accepts a transport factory")
        if not all((args.page_directory, args.schema_plan, args.document_id, args.page)):
            parser.error("Replay requires page-directory, schema-plan, document-id and page")
        result = replay_page(args.page_directory, SchemaPlan.model_validate(read_json(args.schema_plan)),
                             document_id=args.document_id, page=args.page)
        print(json.dumps({k: v for k, v in result.items() if k not in {"records", "next_context"}}, ensure_ascii=False))
        return
    if not args.config or not args.transport_factory:
        parser.error("Model stages require explicit config and caller-owned budgeted transport factory")
    config = CSVConfig.model_validate_json(args.config.read_bytes())
    module, function = args.transport_factory.split(":", 1)
    transport = getattr(importlib.import_module(module), function)(config)
    if args.stage == "prepare":
        result = prepare_document(config, transport)
    elif args.stage == "extract":
        result = extract_document(config, read_json(config.prepared_dir / "prepared.json"), transport)
    else:
        if not args.primary_run:
            parser.error("HTML stage requires primary-run")
        result = extract_html(config, args.primary_run, transport)
    print(json.dumps({"status": result["status"], "stage": args.stage}, ensure_ascii=False))


if __name__ == "__main__":
    main()
