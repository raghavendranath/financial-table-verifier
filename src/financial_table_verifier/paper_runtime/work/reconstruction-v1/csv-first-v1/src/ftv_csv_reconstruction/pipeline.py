"""Primary actual-CSV calls, exact predecessor CSV, and read-only artifact replay."""
from pathlib import Path
import base64
import hashlib
import json

from . import prompts
from .syntax_examples import syntax_examples, FORMAT_NOTICE
from .artifacts import (ModelOutputError, call, digest, file_hash, lock_directory, payload,
                        procedure_hash, read_json, replay_call, source_image, write_bytes,
                        write_json, default_renderer)
from .cleaning import clean_page
from .contracts import GeneratedPrompt, PageEnvelope, SchemaPlan, strict_schema
from .csv_io import ContextTooLarge, context_for, page_context, parse_page
from .preparation import prepare_document, validate_prepared


def _validate_envelope(value):
    if not isinstance(value, dict) or set(value) != {"tables"} or not isinstance(value["tables"], list):
        raise ValueError("Response must contain only a tables array")


def _save_csvs(directory, raw, clean):
    hashes = {}
    for i, table in enumerate(raw["tables"]):
        if isinstance(table, dict) and isinstance(table.get("csv"), str):
            for kind, envelope in (("raw", raw), ("clean", clean)):
                filename = f"tables/t{i:03d}/{kind}.csv"
                write_bytes(directory / filename, envelope["tables"][i]["csv"].encode("utf-8"))
                hashes[filename] = file_hash(directory / filename)
    return hashes


def load_preparation(config, prepared):
    validate_prepared(config, prepared)
    plan = SchemaPlan.model_validate(read_json(config.prepared_dir / "generated-schema-plan.json"))
    generated = GeneratedPrompt.model_validate(read_json(config.prepared_dir / "generated-prompt.json"))
    if generated.schema_plan_sha256 != digest(plan.model_dump(mode="json")):
        raise ValueError("Generated prompt/schema binding changed")
    if not prompts.safety_audit(generated.instructions)["passed"]:
        raise ValueError("Generated prompt failed immutable source-only lint")
    return plan, generated


def extract_document(config, prepared, transport, *, renderer=default_renderer):
    plan, generated = load_preparation(config, prepared)
    snapshot = {"config": config.model_dump(mode="json"), "procedure_sha256": procedure_hash(),
                "preparation_fingerprint": prepared["fingerprint"],
                "schema_plan_sha256": digest(plan.model_dump(mode="json")),
                "generated_prompt_sha256": digest(generated.model_dump(mode="json"))}
    fingerprint = digest(snapshot)
    results = {"status": "running", "fingerprint": fingerprint, "document_id": config.document_id,
               "model": config.model, "condition": config.condition, "requested_pages": config.selected_pages,
               "pages": [], "rejected_pages": [], "blocked_pages": [], "raw_records": [], "clean_records": [],
               "processed_pages": [], "attempted_pages": [], "model_response_pages": [],
               "attempted_definition": "Caller transport stage invoked or preserved attempted request replayed; not proof of provider inference completion",
               "context_policy": "literal raw CSV plus schema_id only; immediate physical predecessor; ragged retained; no truncation"}
    with lock_directory(config.output):
        run_path = config.output / "run.json"
        if run_path.exists() and read_json(run_path) != {**snapshot, "fingerprint": fingerprint}:
            raise ValueError("CSV run inputs changed; use a new directory")
        if not run_path.exists():
            if any(p.name != ".lock" for p in config.output.iterdir()):
                raise ValueError("New CSV run directory must be empty")
            write_json(run_path, {**snapshot, "fingerprint": fingerprint})
        write_json(config.output / "results.json", results)
        previous = None
        try:
            for page in config.selected_pages:
                directory = config.output / "pages" / f"p{page:04d}"
                context = previous if config.condition == "sequential" and previous and previous["page"] == page-1 else None
                record = {"page": page, "document_id": config.document_id, "previous_raw_csv": context,
                          "previous_raw_csv_sha256": digest(context), "run_fingerprint": fingerprint}
                try:
                    context_for(previous, page, config.condition, config.max_context_chars)
                except ContextTooLarge:
                    record["status"] = "blocked_context_too_large"
                    write_json(directory / "page-record.json", record)
                    results["blocked_pages"].append({"page": page, "status": record["status"]})
                    results["processed_pages"].append(page)
                    previous = None
                    write_json(config.output / "results.json", results)
                    continue
                raw_image, image = source_image(config, page, config.output / "images", renderer)
                record["image"] = image
                task = {"physical_page": page, "document_id": config.document_id,
                        "generated_schema": plan.model_dump(mode="json"), "generated_instructions": generated.instructions,
                        "untrusted_previous_raw_csv": context,
                        "formatting_notice": FORMAT_NOTICE, "formatting_examples": syntax_examples(plan)}
                request = payload(config, preparation=False, instruction=prompts.EXTRACT, task=task,
                                  schema=strict_schema(PageEnvelope), max_tokens=config.extraction_max_tokens,
                                  name="financial_csv_page", images=[(page, raw_image)])
                record.update(status="request_prepared", request_body_sha256=digest(request))
                write_json(directory / "page-record.json", record)
                results["attempted_pages"].append(page)
                try:
                    envelope, receipt = call(directory / "call", request, transport, validate=_validate_envelope)
                except ModelOutputError as exc:
                    record.update(status="rejected_response_envelope", error=str(exc))
                    write_json(directory / "page-record.json", record)
                    results["rejected_pages"].append({"page": page, "status": record["status"]})
                    results["processed_pages"].append(page)
                    if (directory / "call/response.json").exists():
                        results["model_response_pages"].append(page)
                    previous = None
                    write_json(config.output / "results.json", results)
                    continue
                except Exception as exc:
                    record.update(status="transport_or_saved_call_failure", exception_type=type(exc).__name__, automatic_retry=False)
                    write_json(directory / "page-record.json", record)
                    raise
                results["model_response_pages"].append(page)
                parsed = parse_page(envelope, plan, document_id=config.document_id, page=page)
                cleaned, edits, audit = clean_page(envelope, plan, document_id=config.document_id, page=page)
                clean_parsed = parse_page(cleaned, plan, document_id=config.document_id, page=page)
                write_json(directory / "raw-envelope.json", envelope)
                write_json(directory / "raw-parsed.json", parsed)
                write_json(directory / "clean-envelope.json", cleaned)
                write_json(directory / "clean-parsed.json", clean_parsed)
                write_json(directory / "cleaning-edits.json", edits)
                write_json(directory / "cleaning-audit.json", audit)
                csv_hashes = _save_csvs(directory, envelope, cleaned)
                record.update(status="response_processed", call=receipt, csv_files_sha256=csv_hashes,
                    context_eligible=parsed["has_context_csv"], schema_plan_sha256=snapshot["schema_plan_sha256"],
                    generated_prompt_sha256=snapshot["generated_prompt_sha256"],
                    derived_files_sha256={n: file_hash(directory / n) for n in (
                        "raw-envelope.json", "raw-parsed.json", "clean-envelope.json", "clean-parsed.json",
                        "cleaning-edits.json", "cleaning-audit.json")})
                write_json(directory / "page-record.json", record)
                results["pages"].append({"page": page, "directory": str(directory),
                                         "context_eligible": parsed["has_context_csv"],
                                         "table_issue_count": sum(len(t["issues"]) for t in parsed["tables"])})
                results["processed_pages"].append(page)
                results["raw_records"].extend(parsed["records"])
                results["clean_records"].extend(clean_parsed["records"])
                previous = page_context(parsed)
                write_json(config.output / "results.json", results)
            results.update(status="complete", all_requested_pages_processed=set(results["processed_pages"]) == set(config.selected_pages),
                           all_requested_pages_attempted=set(results["attempted_pages"]) == set(config.selected_pages),
                           has_local_failures=bool(results["rejected_pages"] or results["blocked_pages"]
                               or any(p["table_issue_count"] for p in results["pages"])
                               or any(r["metadata_issues"] for r in results["raw_records"])))
        except Exception as exc:
            results.update(status="failed", exception_type=type(exc).__name__, all_requested_pages_attempted=set(results["attempted_pages"]) == set(config.selected_pages),
                           all_requested_pages_processed=False, automatic_retry=False, error=str(exc)[:1500])
            write_json(config.output / "results.json", results)
            raise
        write_json(config.output / "results.json", results)
    return results


def replay_page(directory, plan, *, document_id, page, expected_payload=None):
    """Pure read-only actual-response→CSV→clean replay. Never instantiates transport."""
    directory = Path(directory)
    envelope, call_record = replay_call(directory / "call", expected_payload)
    record = read_json(directory / "page-record.json")
    if record["page"] != page or record["document_id"] != document_id:
        raise ValueError("Page identity changed")
    request = read_json(directory / "call/request.json")
    task = json.loads(request["messages"][1]["content"][0]["text"])
    if (digest(request) != record["request_body_sha256"]
            or task["untrusted_previous_raw_csv"] != record["previous_raw_csv"]
            or digest(record["previous_raw_csv"]) != record["previous_raw_csv_sha256"]
            or task["physical_page"] != page or task["document_id"] != document_id
            or task["generated_schema"] != plan.model_dump(mode="json")):
        raise ValueError("Actual request/context/schema binding changed")
    content = request["messages"][1]["content"]
    if len(content) != 3 or content[1] != {"type": "text", "text": f"Physical source PDF page {page}"}:
        raise ValueError("Actual source image/page ordering changed")
    url = content[2]["image_url"]["url"]
    prefix = "data:image/png;base64,"
    if not url.startswith(prefix):
        raise ValueError("Actual source image encoding changed")
    actual_image = base64.b64decode(url[len(prefix):], validate=True)
    if (hashlib.sha256(actual_image).hexdigest() != record["image"]["image_sha256"]
            or file_hash(record["image"]["image_file"]) != record["image"]["image_sha256"]):
        raise ValueError("Actual request image bytes differ from preserved source image")
    if any(record["call"].get(key) != call_record.get(key) for key in (
            "request_body_sha256", "response_file_sha256", "response_sha256", "model_content_sha256")):
        raise ValueError("Page/call response fingerprint binding changed")
    for name, expected in {**record["csv_files_sha256"], **record["derived_files_sha256"]}.items():
        if file_hash(directory / name) != expected:
            raise ValueError("Saved derived artifact bytes changed")
    raw_parsed = parse_page(envelope, plan, document_id=document_id, page=page)
    cleaned, edits, audit = clean_page(envelope, plan, document_id=document_id, page=page)
    expected = {"raw-envelope.json": envelope, "raw-parsed.json": raw_parsed,
                "clean-envelope.json": cleaned,
                "clean-parsed.json": parse_page(cleaned, plan, document_id=document_id, page=page),
                "cleaning-edits.json": edits, "cleaning-audit.json": audit}
    for name, value in expected.items():
        if read_json(directory / name) != value:
            raise ValueError("Saved CSV derivation differs from actual response replay")
    for i, table in enumerate(envelope["tables"]):
        if isinstance(table, dict) and isinstance(table.get("csv"), str):
            for kind, source in (("raw", envelope), ("clean", cleaned)):
                if (directory / f"tables/t{i:03d}/{kind}.csv").read_bytes() != source["tables"][i]["csv"].encode("utf-8"):
                    raise ValueError("CSV bytes differ from actual model/cleaner output")
    return {"status": "pass", "page": page, "response_sha256": call_record["response_sha256"],
            "model_content_sha256": call_record["model_content_sha256"], "records": raw_parsed["records"],
            "next_context": page_context(raw_parsed)}


def run_reconstruction(config, transport, *, renderer=default_renderer):
    prepared = prepare_document(config, transport, renderer=renderer)
    return extract_document(config, prepared, transport, renderer=renderer)
