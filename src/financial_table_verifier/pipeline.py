"""Deterministic orchestration with content-checked resume and partial-run reporting."""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import platform
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, prompts
from .consistency import check_consistency
from .openrouter import (
    HTTPTransport,
    OpenRouterClient,
    Transport,
    byok_cost,
    parse_response,
    request_body,
    unknown_cost_reason,
)
from .render import data_url, page_count, render_page
from .schema import Experiment, Manifest, PageExtraction
from .storage import digest, file_hash, read_json, run_lock, write_json

logger = logging.getLogger(__name__)


def validate_page(page: PageExtraction, number: int, previous: PageExtraction | None) -> None:
    if page.page != number:
        raise ValueError("Response page number differs from requested physical page")
    parents = {f"p{previous.page}-t{t.table_index}": t for t in previous.tables} if previous else {}
    used = set()
    for table in page.tables:
        if table.continuation_of is None:
            continue
        parent = parents.get(table.continuation_of)
        if parent is None or table.continuation_of in used:
            raise ValueError("Invalid or duplicate continuation link")
        used.add(table.continuation_of)
        if (parent.statement, parent.scope) != (table.statement, table.scope):
            raise ValueError("Continuation changes statement type or scope")
        if [(c.label, c.period) for c in parent.columns] != [
            (c.label, c.period) for c in table.columns
        ]:
            raise ValueError("Continuation column labels/periods do not match")


def _extract(
    response: dict, number: int, previous: PageExtraction | None
) -> tuple[PageExtraction | None, dict | None]:
    """The page, or None and why when the model output is unusable. Never repaired."""
    try:
        extraction = parse_response(response)
        validate_page(extraction, number, previous)
    except (RuntimeError, ValueError) as exc:
        return None, {"type": type(exc).__name__, "message": str(exc)}
    return extraction, None


def _snapshot(config: Experiment, manifest: Manifest) -> dict:
    code = {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    docs = []
    for document in manifest.documents:
        count = page_count(document.pdf)
        if document.pages[-1] > count:
            raise ValueError(f"Requested page exceeds PDF length for {document.id}")
        docs.append(
            {
                **document.model_dump(mode="json"),
                "pdf_sha256": file_hash(document.pdf),
                "pdf_page_count": count,
            }
        )
    return {
        "schema_version": 1,
        "version": __version__,
        "code_sha256": digest(code),
        "python": platform.python_version(),
        "dependencies": {
            name: importlib.metadata.version(name) for name in ("pydantic", "pypdfium2", "Pillow")
        },
        "config": config.model_dump(mode="json"),
        "documents": docs,
        "system_prompt": prompts.SYSTEM,
        "response_schema": PageExtraction.model_json_schema(),
    }


def run_experiment(
    config: Experiment,
    manifest: Manifest,
    *,
    resume: bool = False,
    transport: Transport | None = None,
    allow_paid: bool = False,
) -> dict:
    if transport is None and not allow_paid:
        raise RuntimeError("Live runs require explicit allow_paid=True or --allow-paid")
    output = config.output
    snapshot = _snapshot(config, manifest)
    fingerprint = digest(snapshot)
    with run_lock(output):
        run_file = output / "run.json"
        if run_file.exists():
            if not resume:
                raise RuntimeError("Run exists. Use --resume or a new output directory")
            saved = read_json(run_file)
            if saved.get("fingerprint") != fingerprint:
                raise RuntimeError("Inputs, configuration, code, or runtime changed; use a new run")
        else:
            if list(p for p in output.iterdir() if p.name != ".lock"):
                raise RuntimeError("New run output must be empty")
            write_json(
                run_file,
                {
                    **snapshot,
                    "fingerprint": fingerprint,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
        results = {
            "status": "running",
            "fingerprint": fingerprint,
            "synthetic": config.model == "synthetic/offline-fixture",
            "documents": {
                d.id: {
                    "requested_pages": d.pages,
                    "pages": [],
                    "completed_pages": 0,
                    "rejected_pages": [],
                }
                for d in manifest.documents
            },
        }
        client = None
        write_json(output / "results.json", results)
        try:
            for document in manifest.documents:
                previous = None
                pages = []
                for number in document.pages:
                    if previous is not None and previous.page != number - 1:
                        previous = None
                    context = previous if config.context == "sequential" else None
                    previous_json = context.model_dump(mode="json") if context else None
                    text = prompts.page_prompt(number, previous_json)
                    if len(text) > config.max_context_chars:
                        raise RuntimeError(
                            "Previous-page context exceeds limit; no silent truncation"
                        )
                    artifact_key = f"{document.id}/p{number:04d}"
                    directory = output / "pages" / document.id
                    record_file = directory / f"p{number:04d}.json"
                    image_file = directory / f"p{number:04d}.png"
                    png = render_page(document.pdf, number, config.dpi, config.max_image_pixels)
                    png_hash = hashlib.sha256(png).hexdigest()
                    request_info = {
                        "page": number,
                        "system": prompts.SYSTEM,
                        "user_text": text,
                        "image_sha256": png_hash,
                        "previous_page": context.page if context else None,
                    }
                    messages = [
                        {"role": "system", "content": prompts.SYSTEM},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": text},
                                {"type": "image_url", "image_url": {"url": data_url(png)}},
                            ],
                        },
                    ]
                    expected_request_hash = digest(request_body(config, messages))
                    directory.mkdir(parents=True, exist_ok=True)
                    if image_file.exists() and file_hash(image_file) != png_hash:
                        raise RuntimeError("Saved rendered image changed; use a new run")
                    if not image_file.exists():
                        image_file.write_bytes(png)
                    if record_file.exists():
                        record = read_json(record_file)
                        if record["request_info"] != request_info:
                            raise RuntimeError("Saved page provenance changed")
                        event = record["event"]
                        if event["request_sha256"] != expected_request_hash:
                            raise RuntimeError("Saved request differs from reconstructed input")
                        response = read_json(output / event["response_file"])
                        if digest(response) != event["response_sha256"]:
                            raise RuntimeError("Saved response changed")
                        extraction, rejected = _extract(response, number, context)
                        dumped = extraction.model_dump(mode="json") if extraction else None
                        if (dumped, rejected) != (record["extraction"], record.get("rejected")):
                            raise RuntimeError("Saved extraction differs from raw response")
                    else:
                        if client is None:
                            client = OpenRouterClient(
                                config, output, transport or HTTPTransport(config.timeout_seconds)
                            )
                            client.preflight()
                        # Recover a response saved just before a crash, without another paid call.
                        completed = [
                            e
                            for e in client.events
                            if e["artifact_key"] == artifact_key and e["status"] == "response"
                        ]
                        if completed:
                            event = completed[-1]
                            if event["request_sha256"] != expected_request_hash:
                                raise RuntimeError("Recovery request differs from current input")
                            response = read_json(output / event["response_file"])
                            if digest(response) != event["response_sha256"]:
                                raise RuntimeError("Recovery response changed")
                        else:
                            response, event = client.complete(messages, artifact_key)
                        extraction, rejected = _extract(response, number, context)
                        write_json(
                            record_file,
                            {
                                "request_info": request_info,
                                "event": event,
                                "extraction": (
                                    extraction.model_dump(mode="json") if extraction else None
                                ),
                                "rejected": rejected,
                            },
                        )
                    if rejected is not None:
                        # Scored as a missing page; an invalid page is never passed as context.
                        results["documents"][document.id]["rejected_pages"].append(
                            {"page": number, **rejected}
                        )
                        previous = None
                        write_json(output / "results.json", results)
                        logger.warning(
                            "Rejected document=%s page=%s: %s",
                            document.id,
                            number,
                            rejected["message"],
                        )
                        continue
                    pages.append(extraction)
                    previous = extraction
                    results["documents"][document.id].update(
                        pages=[p.model_dump(mode="json") for p in pages],
                        completed_pages=len(pages),
                        consistency=check_consistency(pages, config, document.eps_eligible_tables),
                    )
                    write_json(output / "results.json", results)
                    logger.info(
                        "Completed document=%s page=%s context=%s",
                        document.id,
                        number,
                        config.context,
                    )
            results["status"] = "complete"
        except Exception as exc:
            results["status"] = "incomplete"
            results["error"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            write_json(output / "results.json", results)
            write_report(output)
    return results


def write_report(output: Path) -> None:
    results = read_json(output / "results.json")
    events = read_json(output / "requests.json") if (output / "requests.json").exists() else []
    responses = [e for e in events if e["status"] == "response"]
    cost = sum(float(e.get("cost_usd") or 0) for e in events)
    byok = sum(float(value) for e in events if (value := byok_cost(e)) is not None)
    unresolved = [e for e in events if unknown_cost_reason(e) is not None]
    reserved = [e for e in unresolved if e.get("reserved_usd")]
    missing_byok = sum(byok_cost(e) is None for e in events)
    lines = [
        "# Experiment report",
        "",
        f"Run status: {results['status']}",
        f"Synthetic fixture: {results['synthetic']}",
        "",
        "Accounting pass rates are consistency screens, not extraction accuracy.",
        "",
        "| Document | Pages completed/requested | Passed | Failed | Skipped | Pass rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for doc, item in results["documents"].items():
        check = item.get("consistency", {})
        rate = check.get("pass_rate")
        lines.append(
            f"| {doc} | {item['completed_pages']}/{len(item['requested_pages'])} | "
            f"{check.get('passed', 0)} | {check.get('failed', 0)} | "
            f"{check.get('skipped', 0)} | {f'{rate:.1%}' if rate is not None else 'N/A'} |"
        )
    rejected = [
        (doc, item)
        for doc, d in results["documents"].items()
        for item in d.get("rejected_pages", [])
    ]
    split = f" (OpenRouter ${cost:.6f}; billed to your own provider key ${byok:.6f})"
    lines += [
        "",
        f"Pages rejected for invalid model output (scored as missing): {len(rejected)}",
        *(f"- {doc} p{i['page']}: {i['message'].splitlines()[0]}" for doc, i in rejected),
        "",
        f"Completion attempts (including retries): {len(events)}",
        "Attempts cut off at max_tokens and retried: "
        f"{sum(e['status'] == 'truncated' for e in events)}",
        f"Known reported cost: ${cost + byok:.6f}" + (split if byok else ""),
        f"Attempts with unknown cost: {len(unresolved)} (reservation estimate "
        f"${sum(float(e['reserved_usd']) for e in reserved):.6f}; "
        "not a guaranteed billing bound; reconcile with OpenRouter/provider activity)",
        f"Attempts with unavailable BYOK upstream charge: {missing_byok}",
        "This is not a hard spending cap.",
    ]
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [e.get("usage", {}).get(field) for e in responses]
        known = [v for v in values if isinstance(v, int)]
        lines.append(f"{field}: {sum(known)} (reported on {len(known)}/{len(responses)} responses)")
    if results.get("error"):
        lines += [
            "",
            "Failure: " + results["error"]["type"],
            "Inspect results.json and raw response artifacts before resuming.",
        ]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
