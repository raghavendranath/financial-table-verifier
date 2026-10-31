"""Separate HTML-only calls; no physical-tiling or financial semantic gate."""
from pathlib import Path
import base64
import hashlib
import importlib.metadata
import importlib.util
import sys

from . import prompts
from .artifacts import (ModelOutputError, call, digest, file_hash, lock_directory, payload,
                        procedure_hash, read_json, source_image, write_bytes, write_json, default_renderer)
from .contracts import HTMLEnvelope, strict_schema
from .csv_io import ContextTooLarge, context_for
from .pipeline import _validate_envelope, replay_page
from .contracts import SchemaPlan

TEDS_REVISION = "8ffde9024bd331f5a61c3c549fdf30dd3c3e43a5"
TEDS_FILES = {"metric.py": "a9aedd313678b42e55b06c4b0a365d658ef495aaf3abb7fd6012a6e9f334f9f9",
              "parallel.py": "a839481069ebc8a437f812c521e268301c225c6b8d36a1c0c4b3e7c69bad682c",
              "LICENSE-PubTabNet.md": "fd3534b2d5a47a239850b7aa1f6bd68b44d23455f233c586f57e21c19245c668"}


def _official_teds():
    vendor = Path(__file__).resolve().parents[3] / "vendor"
    for name, expected in TEDS_FILES.items():
        if file_hash(vendor / name) != expected:
            raise ValueError("Pinned official TEDS dependency changed")
    previous = sys.modules.get("parallel")
    spec = importlib.util.spec_from_file_location("parallel", vendor / "parallel.py")
    parallel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parallel)
    sys.modules["parallel"] = parallel
    try:
        spec = importlib.util.spec_from_file_location("_csv_official_pubtabnet_metric", vendor / "metric.py")
        metric = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(metric)
    finally:
        if previous is None:
            sys.modules.pop("parallel", None)
        else:
            sys.modules["parallel"] = previous
    return metric.TEDS


def teds_provenance():
    return {"revision": TEDS_REVISION, "files_sha256": TEDS_FILES,
            "dependencies": {n: importlib.metadata.version(n) for n in ("apted", "distance", "lxml", "tqdm")},
            "endpoint": "auxiliary HTML, not primary CSV accuracy"}


def score_html(prediction_html, reference_html):
    """One already-selected pair; never searches references or repairs HTML/cells."""
    if not reference_html:
        raise ValueError("A reference is required")
    if not prediction_html:
        return {"teds": 0.0, "teds_structure": 0.0, "status": "missing_prediction"}
    TEDS = _official_teds()
    try:
        return {"teds": TEDS(structure_only=False, n_jobs=1).evaluate(prediction_html, reference_html),
                "teds_structure": TEDS(structure_only=True, n_jobs=1).evaluate(prediction_html, reference_html),
                "status": "scored"}
    except Exception as exc:
        return {"teds": 0.0, "teds_structure": 0.0, "status": "invalid_prediction", "exception_type": type(exc).__name__}


def _primary_image_identity(origin, source_record, primary):
    """Verify the actual PRIMARY request image, including rejected response pages."""
    request = read_json(origin / "call/request.json")
    receipt = read_json(origin / "call/call.json")
    if (digest(request) != source_record["request_body_sha256"]
            or digest(request) != receipt["request_body_sha256"]
            or file_hash(origin / "call/request.json") != receipt["request_file_sha256"]):
        raise ValueError("Primary actual request provenance changed")
    image = source_record["image"]
    if image["pdf_sha256"] != primary["config"]["pdf_sha256"] or image["dpi"] != primary["config"]["dpi"]:
        raise ValueError("Primary image PDF/DPI provenance differs")
    content = request["messages"][1]["content"]
    if len(content) != 3 or content[1] != {"type": "text", "text": f"Physical source PDF page {source_record['page']}"}:
        raise ValueError("Primary actual image/page ordering differs")
    url = content[2]["image_url"]["url"]
    prefix = "data:image/png;base64,"
    if not url.startswith(prefix):
        raise ValueError("Primary source encoding differs")
    actual = base64.b64decode(url[len(prefix):], validate=True)
    actual_hash = hashlib.sha256(actual).hexdigest()
    if actual_hash != image["image_sha256"] or file_hash(image["image_file"]) != actual_hash:
        raise ValueError("Primary actual request/source-image fingerprint differs")
    asset = primary["config"]["selected_images"].get(str(source_record["page"]))
    if asset and (asset["sha256"] != actual_hash or file_hash(asset["path"]) != actual_hash):
        raise ValueError("Primary image differs from its predeclared source asset")
    return actual_hash


def extract_html(config, primary_run, transport, *, renderer=default_renderer):
    """After primary terminal completion; reuse its exact preceding CSV, never current CSV."""
    primary_run = Path(primary_run)
    if primary_run.resolve() == config.output.resolve():
        raise ValueError("HTML output must be separate from primary CSV artifacts")
    primary = read_json(primary_run / "run.json")
    result = read_json(primary_run / "results.json")
    if result["status"] != "complete" or not result["all_requested_pages_processed"]:
        raise ValueError("Secondary audit must follow terminal processing of primary CSV run")
    for key in ("document_id", "issuer_id", "pdf_sha256", "model", "provider", "condition", "dpi", "seed", "temperature"):
        if primary["config"][key] != config.model_dump(mode="json")[key]:
            raise ValueError("HTML task/primary CSV source or condition mismatch")
    if not set(config.selected_pages) <= set(primary["config"]["selected_pages"]):
        raise ValueError("HTML source pages must be a subset of primary pages")
    plan = SchemaPlan.model_validate(read_json(Path(primary["config"]["prepared_dir"]) / "generated-schema-plan.json"))
    snapshot = {"config": config.model_dump(mode="json"), "primary_run_file_sha256": file_hash(primary_run / "run.json"),
                "primary_results_file_sha256": file_hash(primary_run / "results.json"), "procedure_sha256": procedure_hash()}
    outcome = {"status": "running", "endpoint": "auxiliary HTML only", "pages": [], "rejected_pages": [], "blocked_pages": [],
               "blocked_primary_pages": [], "processed_pages": [], "attempted_pages": [], "model_response_pages": []}
    with lock_directory(config.output):
        path = config.output / "run.json"
        if path.exists() and read_json(path) != snapshot:
            raise ValueError("Secondary HTML inputs changed")
        if not path.exists() and any(p.name != ".lock" for p in config.output.iterdir()):
            raise ValueError("New secondary directory must be empty")
        write_json(path, snapshot)
        try:
            for page in config.selected_pages:
                origin = primary_run / "pages" / f"p{page:04d}"
                source_record = read_json(origin / "page-record.json")
                directory = config.output / "pages" / f"p{page:04d}"
                if source_record["status"] == "blocked_context_too_large":
                    write_json(directory / "page-record.json", {"page": page, "status": "blocked_primary_no_inference",
                        "primary_page_record_sha256": file_hash(origin / "page-record.json"), "automatic_retry": False})
                    outcome["blocked_primary_pages"].append(page)
                    outcome["processed_pages"].append(page)
                    continue
                primary_image_hash = _primary_image_identity(origin, source_record, primary)
                context = source_record["previous_raw_csv"]
                # Rebuild the immediately preceding accepted raw CSV independently.
                previous = None
                predecessor = primary_run / "pages" / f"p{page-1:04d}"
                if config.condition == "sequential" and page-1 in primary["config"]["selected_pages"]:
                    prior_record = read_json(predecessor / "page-record.json")
                    if prior_record["status"] == "response_processed":
                        previous = replay_page(predecessor, plan, document_id=config.document_id, page=page-1)["next_context"]
                expected = previous if config.condition == "sequential" and previous and previous["page"] == page-1 else None
                if context != expected or digest(context) != source_record["previous_raw_csv_sha256"]:
                    raise ValueError("Primary context differs from actual predecessor CSV")
                record = {"page": page, "previous_raw_csv": context,
                          "primary_page_record_sha256": file_hash(origin / "page-record.json"),
                          "previous_raw_csv_sha256": digest(context)}
                try:
                    context_for(context, page, config.condition, config.max_context_chars)
                except ContextTooLarge:
                    record["status"] = "blocked_context_too_large"
                    outcome["blocked_pages"].append(page)
                    outcome["processed_pages"].append(page)
                    write_json(directory / "page-record.json", record)
                    continue
                raw, image = source_image(config, page, config.output / "images", renderer)
                if image["image_sha256"] != primary_image_hash:
                    raise ValueError("Secondary actual source image differs from primary same-page image")
                task = {"physical_page": page, "document_id": config.document_id, "untrusted_previous_raw_csv": context}
                request = payload(config, preparation=False, instruction=prompts.HTML, task=task,
                    schema=strict_schema(HTMLEnvelope), max_tokens=config.html_max_tokens,
                    name="auxiliary_html_page", images=[(page, raw)])
                try:
                    outcome["attempted_pages"].append(page)
                    envelope, receipt = call(directory / "call", request, transport, validate=_validate_envelope)
                except ModelOutputError:
                    record["status"] = "rejected_response_envelope"
                    outcome["rejected_pages"].append(page)
                    outcome["processed_pages"].append(page)
                    if (directory / "call/response.json").exists():
                        outcome["model_response_pages"].append(page)
                    write_json(directory / "page-record.json", record)
                    continue
                outcome["model_response_pages"].append(page)
                tables = []
                for ordinal, emitted in enumerate(envelope["tables"]):
                    item = {"table_ordinal": ordinal, "emitted": emitted, "html_available": False}
                    if isinstance(emitted, dict) and isinstance(emitted.get("html"), str):
                        filename = f"tables/t{ordinal:03d}/raw.html"
                        write_bytes(directory / filename, emitted["html"].encode("utf-8"))
                        item.update(html_available=True, html_file=filename, html_sha256=file_hash(directory / filename))
                    tables.append(item)
                write_json(directory / "raw-envelope.json", envelope)
                record.update(status="response_processed", image=image, call=receipt, tables=tables,
                              raw_envelope_sha256=file_hash(directory / "raw-envelope.json"))
                write_json(directory / "page-record.json", record)
                outcome["pages"].append({"page": page, "table_count": len(tables), "directory": str(directory)})
                outcome["processed_pages"].append(page)
            outcome.update(status="complete", all_requested_pages_processed=set(outcome["processed_pages"]) == set(config.selected_pages),
                           all_requested_pages_attempted=set(outcome["attempted_pages"]) == set(config.selected_pages))
        except Exception as exc:
            outcome.update(status="failed", exception_type=type(exc).__name__, automatic_retry=False,
                           all_requested_pages_processed=False, all_requested_pages_attempted=False)
            write_json(config.output / "results.json", outcome)
            raise
        write_json(config.output / "results.json", outcome)
    return outcome
