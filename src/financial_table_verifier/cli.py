"""Small command-line interface; paid calls are restricted to the run command."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from pydantic import ValidationError

from .audit import audit_run
from . import __version__
from .cohort import build_cohort
from .config import load_config
from .demo import demo
from .evaluation import compare_runs, evaluate_run
from .local_data import import_index
from .openrouter import HTTPTransport
from .pipeline import _snapshot, run_experiment
from .schema import PageExtraction
from .screen_analysis import analyze_screens
from .storage import read_json, write_json
from .summary import summarize
from .xbrl import compare_facts, read_companyfacts, read_instance
from .xbrl_draft import draft_cohort


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ftv", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name == "run":
            command.add_argument("--resume", action="store_true")
            command.add_argument("--allow-paid", action="store_true",
                                 help="Explicitly allow image uploads and billed API requests")
    command = commands.add_parser("demo")
    command.add_argument("--output", type=Path, default=Path("demo"))
    commands.add_parser("paper-info", help="Verify the byte-preserved study source (offline)")
    commands.add_parser("paper-tests", help="Run portable frozen CSV/scoring tests (offline)")
    command = commands.add_parser("paper-demo", help="Actual CSV runtime with canned synthetic responses")
    command.add_argument("--output", type=Path, default=Path("paper-demo-output"))
    command = commands.add_parser("paper-score", help="Score supplied source references and CSV records offline")
    command.add_argument("--input", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("paper-inspect", help="Checksum and recount a saved full50 report offline")
    command.add_argument("--report", type=Path, required=True)
    command.add_argument("--sha256", required=True)
    command = commands.add_parser("models")
    command.add_argument("--contains", default="")
    command = commands.add_parser("evaluate")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--gold", type=Path, required=True)
    command = commands.add_parser("audit")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--gold", type=Path, required=True)
    command = commands.add_parser("screens")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--relative-tolerances", type=Decimal, nargs="+",
                         default=[Decimal("0"), Decimal("0.0001"), Decimal("0.001"), Decimal("0.005")])
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("xbrl-facts")
    command.add_argument("--instance", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("companyfacts")
    command.add_argument("--input", type=Path, required=True)
    command.add_argument("--accession", required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("xbrl-draft")
    command.add_argument("--cohort", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("xbrl-compare")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--facts", type=Path, required=True)
    command.add_argument("--mapping", type=Path, required=True)
    command = commands.add_parser("import-index")
    command.add_argument("--index", type=Path, required=True)
    command.add_argument("--source-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--limit", type=int, default=3)
    command.add_argument("--window", type=int, default=1)
    command = commands.add_parser("build-cohort")
    command.add_argument("--source-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--per-form", type=int, default=25)
    command.add_argument("--seed", type=int, default=42)
    command = commands.add_parser("compare")
    command.add_argument("--left", type=Path, required=True)
    command.add_argument("--right", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("summarize")
    command.add_argument("--left", type=Path, nargs="+", required=True)
    command.add_argument("--right", type=Path, nargs="+", required=True)
    command.add_argument("--gold", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("annotation-template")
    command.add_argument("--run", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("schema")
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    try:
        if args.command == "demo":
            result = demo(args.output)
        elif args.command.startswith("paper-"):
            from . import paper
            if args.command == "paper-info":
                result = paper.info()
            elif args.command == "paper-tests":
                result = paper.run_tests()
            elif args.command == "paper-demo":
                result = paper.demo(args.output)
            elif args.command == "paper-score":
                scored = paper.score(args.input, args.output)
                result = {"output": str(args.output), "synthetic": scored["synthetic"],
                          "populations": list(scored["results"]["populations"])}
            else:
                result = paper.inspect_report(args.report, args.sha256)
        elif args.command == "models":
            catalog = HTTPTransport(30).request("GET", "models")
            result = [
                {"id": m["id"], "supported_parameters": m.get("supported_parameters", [])}
                for m in catalog.get("data", [])
                if args.contains in m["id"]
                and "image" in m.get("architecture", {}).get("input_modalities", [])
            ]
        elif args.command in {"plan", "run"}:
            if args.command == "run" and not args.allow_paid:
                raise RuntimeError("Live runs require --allow-paid; plan and demo are offline")
            config, manifest = load_config(args.config)
            if args.command == "plan":
                snapshot = _snapshot(config, manifest)
                total = sum(len(d.pages) for d in manifest.documents)
                result = {
                    "documents": snapshot["documents"],
                    "pages": total,
                    "planned_completion_requests": total,
                    "request_cap": config.max_requests,
                    "model": config.model,
                    "context": config.context,
                    "note": "No API calls made. Retries count against the request cap.",
                }
            else:
                result = run_experiment(config, manifest, resume=args.resume, allow_paid=True)
                result = {"status": result["status"], "output": str(config.output)}
        elif args.command == "evaluate":
            result = evaluate_run(args.run.resolve(), args.gold.resolve())
            result = {"metrics": result["metrics"], "output": str(args.run / "evaluation.json")}
        elif args.command == "audit":
            result = audit_run(args.run.resolve(), args.gold.resolve())
        elif args.command == "screens":
            result = analyze_screens(args.run.resolve(), args.relative_tolerances)
            write_json(args.output, result)
        elif args.command == "import-index":
            result = import_index(
                args.index, args.source_root, args.output, args.limit, args.window
            )
        elif args.command == "build-cohort":
            report = build_cohort(args.source_root, args.output, args.per_form, args.seed)
            result = {
                "status": report["status"],
                "selected_counts": report["selected_counts"],
                "eligible_counts": report["eligible_counts"],
                "output": str(args.output),
            }
        elif args.command in {"xbrl-facts", "companyfacts"}:
            facts = (
                read_instance(args.instance)
                if args.command == "xbrl-facts"
                else read_companyfacts(args.input, args.accession)
            )
            write_json(args.output, facts)
            result = {"facts": len(facts["facts"]), "output": str(args.output)}
        elif args.command == "xbrl-draft":
            report = draft_cohort(args.cohort, args.output)
            result = {"filings": len(report["filings"]), "output": str(args.output)}
        elif args.command == "xbrl-compare":
            result = compare_facts(args.run, args.facts, args.mapping)
        elif args.command == "compare":
            result = compare_runs(args.left.resolve(), args.right.resolve())
            write_json(args.output, result)
        elif args.command == "summarize":
            result = summarize(
                [p.resolve() for p in args.left], [p.resolve() for p in args.right], args.gold
            )
            write_json(args.output, result)
            result = {
                "conditions": result["conditions"],
                "paired_filings": result["paired_filings"],
                "paired_filing_f1": result["paired_filing_f1"],
                "warnings": result["warnings"],
                "output": str(args.output),
            }
        elif args.command == "schema":
            write_json(args.output, PageExtraction.model_json_schema())
            result = {"output": str(args.output)}
        else:
            manifest = read_json(args.run / "run.json")
            for document in manifest["documents"]:
                target = args.output / f"{document['id']}.json"
                if target.exists():
                    raise ValueError(f"Refusing to overwrite annotation {target}")
                write_json(
                    target,
                    {
                        "document_id": document["id"],
                        "pdf_sha256": document["pdf_sha256"],
                        "annotator": "",
                        "reviewed": False,
                        "pages": [{"page": n, "tables": []} for n in document["pages"]],
                    },
                )
            result = {
                "output": str(args.output),
                "note": "Blank, unreviewed templates. Annotate from PDF, not predictions.",
            }
        print(json.dumps(result, indent=2, default=str))
        return 0
    except ValidationError as exc:
        # Pydantic's default text includes rejected input values; do not echo config
        # values (which a user may mistakenly fill with credentials) to the terminal.
        print("Error: input validation failed", file=sys.stderr)
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            print(f"  {'.'.join(map(str, error['loc']))}: {error['type']}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
