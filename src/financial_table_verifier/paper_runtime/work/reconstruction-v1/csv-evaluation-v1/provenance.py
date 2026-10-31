"""Read-only CSV candidate loader with independently reconstructed model inputs.

No transport/default network factory exists here. Source references/expected values
are neither accepted nor loaded. The caller must separately pin config_path to its
frozen execution plan; this module verifies that config against all run artifacts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from ftv_csv_reconstruction import prompts
from ftv_csv_reconstruction.syntax_examples import syntax_examples, FORMAT_NOTICE
from ftv_csv_reconstruction.artifacts import (ModelOutputError, default_renderer, digest, file_hash,
    payload, procedure_hash, read_json, response_content)
from ftv_csv_reconstruction.contracts import CSVConfig, PageEnvelope, strict_schema
from ftv_csv_reconstruction.csv_io import ContextTooLarge, context_for, parse_page
from ftv_csv_reconstruction.pipeline import _validate_envelope, load_preparation, replay_page


MONTHS = {name.casefold(): index for index, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}


def _norm(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def explicit_label_date(label):
    """Full ISO or full English month/day/year only; no year/date from references."""
    text = _norm(label)
    matches = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    english = re.findall(r"\b("+"|".join(MONTHS)+r")\s+(\d{1,2}),?\s+(\d{4})\b", text)
    try:
        values = [date.fromisoformat(value) for value in matches]
        values.extend(date(int(year), MONTHS[month], int(day)) for month, day, year in english)
    except ValueError:
        return None
    unique = set(values)
    return next(iter(unique)) if len(unique) == 1 else None


def _source_png(config, page, record):
    """Read-only equivalent of source_image, independently rendering if no pinned PNG."""
    if file_hash(config.pdf) != config.pdf_sha256:
        raise ValueError("Configured source PDF changed")
    image = record["image"]
    if image["page"] != page or image["pdf_sha256"] != config.pdf_sha256 or image["dpi"] != config.dpi:
        raise ValueError("Saved source image identity differs from frozen config")
    declared = config.selected_images.get(page)
    if declared:
        raw = declared.path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != declared.sha256:
            raise ValueError("Predeclared source image changed")
    else:
        raw = default_renderer(config.pdf, page, config.dpi, config.max_image_pixels)
    actual_hash = hashlib.sha256(raw).hexdigest()
    path = config.output / "images" / f"p{page:04d}.png"
    if Path(image["image_file"]).resolve() != path.resolve():
        raise ValueError("Saved source image path differs from configured run")
    if image["image_sha256"] != actual_hash or path.read_bytes() != raw:
        raise ValueError("Saved image differs from independently obtained source PNG")
    expected = {"page": page, "pdf_sha256": config.pdf_sha256, "dpi": config.dpi,
                "image_file": str(path), "image_sha256": actual_hash}
    if read_json(path.with_suffix(".json")) != expected:
        raise ValueError("Source PNG receipt changed")
    return raw


def _raw_call(directory, expected_payload):
    """Validate actual request/response bytes even when model content is invalid."""
    request_path, response_path, receipt_path = (directory / n for n in ("request.json", "response.json", "call.json"))
    request, receipt = read_json(request_path), read_json(receipt_path)
    recorded_file_hash = receipt.get("request_file_sha256")
    allowed_pending = receipt.get("status") == "pending" and recorded_file_hash is None
    if (digest(request) != digest(expected_payload) or receipt["request_body_sha256"] != digest(expected_payload)
            or (not allowed_pending and recorded_file_hash != file_hash(request_path))):
        raise ValueError("Actual CSV request differs from independently reconstructed inputs")
    if not response_path.exists():
        if receipt.get("status") not in {"pending", "transport_failure"}:
            raise ValueError("Missing raw response without preserved pending/transport failure")
        return None, receipt
    response = read_json(response_path)
    if receipt.get("response_file_sha256") != file_hash(response_path) or receipt.get("response_sha256") != digest(response):
        raise ValueError("Actual response object bytes/hash changed")
    choices = response.get("choices") or []
    text = choices[0].get("message", {}).get("content") if len(choices) == 1 else None
    if isinstance(text, str):
        if ((directory / "model-content.utf8").read_bytes() != text.encode("utf-8")
                or receipt.get("model_content_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest()):
            raise ValueError("Actual model-content bytes changed")
    elif receipt.get("model_content_sha256") is not None:
        raise ValueError("Model-content fingerprint exists without corresponding string")
    return response, receipt


def _decorated_records(parsed, content_sha256):
    """Only model-emitted labels/path/date anchors; all candidate metadata preserved."""
    output = []
    for table in parsed["tables"]:
        records = [r for r in parsed["records"] if r["table_ordinal"] == table["table_ordinal"]]
        anchors = []
        for index, record in enumerate(records):
            label = record["row_label"]
            explicit = explicit_label_date(label) if re.search(r"\bbalances?\b", _norm(label)) else None
            if explicit:
                anchors.append((index, record["column_index"], explicit))
        headings = []
        for index, record in enumerate(records):
            label = record["row_label"]
            if label and record["raw_value"] == "":
                headings.append(label)
            compatible = [(i, value) for i, column, value in anchors
                          if column is None or column == record["column_index"]]
            before = [(i, value) for i, value in compatible if i < index]
            after = [(i, value) for i, value in compatible if i > index]
            block = None
            if before and after and before[-1][1] < after[0][1]:
                block = {"start": (before[-1][1]+timedelta(days=1)).isoformat(), "end": after[0][1].isoformat()}
            explicit = explicit_label_date(label) if re.search(r"\bbalances?\b", _norm(label)) else None
            candidate = deepcopy(record)
            address = {"document_id": record["document_id"], "page": record["page"],
                       "table_ordinal": record["table_ordinal"], "record_ordinal": record["record_ordinal"],
                       "model_content_sha256": content_sha256}
            candidate.update(table_index=record["table_ordinal"], row_index=record["record_ordinal"],
                source_cell_id="csv-record-"+digest(address), preceding_headings=headings[-12:],
                candidate_period_block=block, candidate_balance_instant_from_emitted_label=explicit.isoformat() if explicit else None,
                metadata_projection_policy="actual CSV only; invalid metadata remains flagged, never reference-filled")
            output.append(candidate)
    return output


def load_candidates(config_path, variant="raw"):
    """Return (all exact-width source-CSV candidate records, provenance diagnostics).

    Ragged/schema-unknown records remain preserved in page artifacts and diagnostics;
    they cannot acquire guessed role/column bindings. Partial runs return only verified
    processed pages, with unavailable page statuses explicitly retained.
    """
    if variant not in {"raw", "clean"}:
        raise ValueError("variant must be raw or clean")
    config_path = Path(config_path)
    config = CSVConfig.model_validate_json(config_path.read_bytes())
    diagnostics = {"status": "not_started", "variant": variant, "config_sha256": file_hash(config_path),
                   "procedure_sha256": procedure_hash(), "pages": [], "no_network": True, "read_only": True,
                   "requested_pages": config.selected_pages}
    if not (config.output / "run.json").exists():
        diagnostics["unavailable_pages"] = list(config.selected_pages)
        return [], diagnostics
    prepared = read_json(config.prepared_dir / "prepared.json")
    plan, generated = load_preparation(config, prepared)
    expected_snapshot = {"config": config.model_dump(mode="json"), "procedure_sha256": procedure_hash(),
        "preparation_fingerprint": prepared["fingerprint"], "schema_plan_sha256": digest(plan.model_dump(mode="json")),
        "generated_prompt_sha256": digest(generated.model_dump(mode="json"))}
    fingerprint = digest(expected_snapshot)
    run = read_json(config.output / "run.json")
    if run != {**expected_snapshot, "fingerprint": fingerprint}:
        raise ValueError("Run source/config/model/settings/procedure/preparation fingerprint differs")
    result = read_json(config.output / "results.json")
    if result["fingerprint"] != fingerprint:
        raise ValueError("Result fingerprint differs from verified run")
    candidates, all_raw, all_clean = [], [], []
    processed, attempted, response_pages, rejected, blocked = [], [], [], [], []
    previous = None
    for page in config.selected_pages:
        directory = config.output / "pages" / f"p{page:04d}"
        context = previous if config.condition == "sequential" and previous and previous["page"] == page-1 else None
        if not (directory / "page-record.json").exists():
            diagnostics["pages"].append({"page": page, "status": "not_processed"})
            previous = None
            continue
        record = read_json(directory / "page-record.json")
        if (record["page"] != page or record["document_id"] != config.document_id
                or record["run_fingerprint"] != fingerprint or record["previous_raw_csv"] != context
                or record["previous_raw_csv_sha256"] != digest(context)):
            raise ValueError("Page identity or literal actual-predecessor context differs")
        try:
            context_for(previous, page, config.condition, config.max_context_chars)
        except ContextTooLarge:
            if record["status"] != "blocked_context_too_large" or (directory / "call/request.json").exists():
                raise ValueError("Oversized context was not blocked before request")
            processed.append(page)
            blocked.append({"page": page, "status": record["status"]})
            diagnostics["pages"].append({"page": page, "status": record["status"],
                                         "page_record_sha256": file_hash(directory / "page-record.json")})
            previous = None
            continue
        if record["status"] == "blocked_context_too_large":
            raise ValueError("Saved blocked page has no independently oversized context")
        raw_image = _source_png(config, page, record)
        expected_request = payload(config, preparation=False, instruction=prompts.EXTRACT,
            task={"physical_page": page, "document_id": config.document_id,
                  "generated_schema": plan.model_dump(mode="json"), "generated_instructions": generated.instructions,
                  "untrusted_previous_raw_csv": context, "formatting_notice": FORMAT_NOTICE,
                  "formatting_examples": syntax_examples(plan)},
            schema=strict_schema(PageEnvelope), max_tokens=config.extraction_max_tokens,
            name="financial_csv_page", images=[(page, raw_image)])
        if record["request_body_sha256"] != digest(expected_request):
            raise ValueError("Page request hash differs from reconstructed request")
        response, receipt = _raw_call(directory / "call", expected_request)
        attempted.append(page)
        if response is None:
            diagnostics["pages"].append({"page": page, "status": "pending_or_transport_failure", "automatic_retry": False,
                                         "page_record_sha256": file_hash(directory / "page-record.json")})
            previous = None
            continue
        response_pages.append(page)
        try:
            envelope = json.loads(response_content(response))
            _validate_envelope(envelope)
            invalid = False
        except (ValueError, TypeError, KeyError, RecursionError):
            invalid = True
        if invalid:
            if record["status"] != "rejected_response_envelope":
                raise ValueError("Invalid actual response was not preserved as rejection")
            rejected.append({"page": page, "status": record["status"]})
            processed.append(page)
            diagnostics["pages"].append({"page": page, "status": record["status"], "response_sha256": receipt["response_sha256"],
                                         "page_record_sha256": file_hash(directory / "page-record.json")})
            previous = None
            continue
        if record["status"] != "response_processed":
            raise ValueError("Parseable actual envelope was rejected or not processed")
        replay = replay_page(directory, plan, document_id=config.document_id, page=page, expected_payload=expected_request)
        raw_parsed = parse_page(envelope, plan, document_id=config.document_id, page=page)
        clean_parsed = read_json(directory / "clean-parsed.json")  # exact contents already independently replayed above
        all_raw.extend(raw_parsed["records"])
        all_clean.extend(clean_parsed["records"])
        selected = raw_parsed if variant == "raw" else clean_parsed
        candidates.extend(_decorated_records(selected, receipt["model_content_sha256"]))
        processed.append(page)
        diagnostics["pages"].append({"page": page, "status": "response_processed", "response_sha256": receipt["response_sha256"],
            "page_record_sha256": file_hash(directory / "page-record.json"),
            "records": len(selected["records"]), "table_issues": [t["issues"] for t in selected["tables"]],
            "ragged_records": sum(1 for t in selected["tables"] for r in t["records"][1:] if not r.get("exact_width"))})
        previous = replay["next_context"]
    if result["status"] == "complete":
        expected = {"raw_records": all_raw, "clean_records": all_clean, "processed_pages": processed,
                    "attempted_pages": attempted, "model_response_pages": response_pages,
                    "rejected_pages": rejected, "blocked_pages": blocked,
                    "all_requested_pages_processed": set(processed) == set(config.selected_pages),
                    "all_requested_pages_attempted": set(attempted) == set(config.selected_pages)}
        if not expected["all_requested_pages_processed"]:
            raise ValueError("Complete run lacks terminal processing for selected pages")
        for key, value in expected.items():
            if result.get(key) != value:
                raise ValueError("Complete results summary differs from independent replay: " + key)
    diagnostics.update(status=result["status"], processed_pages=processed, attempted_pages=attempted,
        model_response_pages=response_pages, candidate_records=len(candidates),
        unavailable_pages=[p for p in config.selected_pages if p not in response_pages],
        all_requested_pages_processed=set(processed) == set(config.selected_pages),
        all_requested_pages_attempted=set(attempted) == set(config.selected_pages),
        run_file_sha256=file_hash(config.output / "run.json"), results_file_sha256=file_hash(config.output / "results.json"))
    return candidates, diagnostics
