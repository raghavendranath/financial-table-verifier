"""Actual staged reconstruction with caller-injected transport and durable artifacts.

No credential loading or default network transport exists in this package. A caller
must provide its separately budgeted transport. Every invocation is single-attempt;
saved failures/unknown outcomes are never silently replayed as new paid requests.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import hashlib
import io
import json
from pathlib import Path
import platform
import time
from typing import Annotated, Callable, Literal, Protocol
import uuid

from pydantic import Field, model_validator

from . import prompts
from .adapters import canonical_grids, logical_page, numeric_records, validate_continuations
from .cleaning import deterministic_clean
from .contracts import Nonempty, SHA256, Strict
from .model_contracts import (
    GeneratedPrompt, GeometryPage, SchemaPlan, StructurePlan, SurveyChunk,
    generated_extraction_schema, strict_schema,
)
from .normalization import normalize_geometry


class Transport(Protocol):
    def request(self, method: str, path: str, payload: dict | None = None) -> dict: ...


class ImageAsset(Strict):
    path: Path
    sha256: SHA256


class DevelopmentExample(Strict):
    issuer_id: Nonempty
    extraction_path: Path
    image: ImageAsset | None = None
    reviewed: bool
    review_kind: Literal["agent_source_review", "human_source_review"]


class ReconstructionConfig(Strict):
    experiment_id: Nonempty
    document_id: Nonempty
    issuer_id: Nonempty
    pdf: Path
    pdf_sha256: SHA256
    selected_pages: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1)]
    structural_pages: list[int]
    accounting_pages: list[int] = []
    selected_images: dict[int, ImageAsset] = {}
    preparation_illustrative_pages: list[int]
    development_examples: list[DevelopmentExample] = []
    model: Nonempty
    provider: str | None = None
    preparation_model: str = "google/gemini-2.5-pro"
    preparation_provider: str | None = "google-ai-studio"
    condition: Literal["independent", "sequential"]
    repeat: Annotated[int, Field(ge=1)] = 1
    dpi: Annotated[int, Field(ge=72, le=400)] = 300
    max_image_pixels: Annotated[int, Field(ge=100000)] = 25000000
    survey_chunk_pages: Annotated[int, Field(ge=1, le=12)] = 6
    survey_image_format: Literal["png", "jpeg"] = "jpeg"
    survey_jpeg_quality: Annotated[int, Field(ge=1, le=100)] = 90
    survey_max_tokens: Annotated[int, Field(ge=128)] = 3500
    structure_max_tokens: Annotated[int, Field(ge=128)] = 12000
    schema_max_tokens: Annotated[int, Field(ge=128)] = 6000
    prompt_max_tokens: Annotated[int, Field(ge=128)] = 6000
    extraction_max_tokens: Annotated[int, Field(ge=128)] = 32768
    reasoning_max_tokens: Annotated[int, Field(ge=128)] | None = None
    preparation_reasoning_max_tokens: Annotated[int, Field(ge=128)] | None = None
    extraction_reasoning_max_tokens: Annotated[int, Field(ge=128)] | None = None
    temperature: Annotated[float, Field(ge=0, le=2)] = 0.0
    seed: int | None = 42
    max_context_chars: Annotated[int, Field(ge=1000)] = 500000
    output: Path
    prepared_dir: Path
    protocol_sha256: SHA256
    selection_freeze_sha256: SHA256
    reuse_preparation_from: Path | None = None
    survey_validation_attempts: Annotated[int, Field(ge=1, le=2)] = 1

    @model_validator(mode="after")
    def page_selection(self):
        if self.selected_pages != sorted(set(self.selected_pages)):
            raise ValueError("Selected physical pages must be sorted and unique")
        if not set(self.structural_pages + self.accounting_pages) <= set(self.selected_pages):
            raise ValueError("Endpoint page lists must be contained in selected pages")
        if not self.preparation_illustrative_pages or any(p < 1 for p in self.preparation_illustrative_pages):
            raise ValueError("Prompt generation requires explicit source illustrative pages")
        if any(not e.reviewed or e.issuer_id == self.issuer_id for e in self.development_examples):
            raise ValueError("Examples must be reviewed development sources outside the evaluation issuer")
        preparation_reasoning = self.preparation_reasoning_max_tokens or self.reasoning_max_tokens
        extraction_reasoning = self.extraction_reasoning_max_tokens or self.reasoning_max_tokens
        if (self.preparation_model.startswith("google/gemini") and preparation_reasoning is not None
                and preparation_reasoning >= min(self.survey_max_tokens, self.structure_max_tokens,
                                                 self.schema_max_tokens, self.prompt_max_tokens)):
            raise ValueError("Preparation reasoning allowance must leave stage output room")
        if (self.model.startswith("google/gemini") and extraction_reasoning is not None
                and extraction_reasoning >= self.extraction_max_tokens):
            raise ValueError("Extraction reasoning allowance must leave output room")
        return self


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        result = hashlib.file_digest(stream, "sha256")
    return result.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    temporary.write_bytes(json_bytes(value) + b"\n")
    temporary.replace(path)


def read_json(path: Path):
    return json.loads(path.read_bytes())


def procedure_hash() -> str:
    return digest({p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))})


@contextmanager
def lock_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    with (path / ".lock").open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another worker is using this reconstruction directory") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def default_page_count(pdf: Path) -> int:
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(pdf) as document:
        return len(document)


def default_renderer(pdf: Path, page: int, dpi: int, max_pixels: int) -> bytes:
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(pdf) as document:
        source = document[page - 1]
        try:
            width, height = source.get_size()
            if width * height * (dpi / 72) ** 2 > max_pixels:
                raise ValueError("Source page exceeds configured render pixel limit")
            bitmap = source.render(scale=dpi / 72)
            try:
                stream = io.BytesIO()
                bitmap.to_pil().save(stream, format="PNG", optimize=False)
                return stream.getvalue()
            finally:
                bitmap.close()
        finally:
            source.close()


def _image(config, page, renderer):
    path = config.prepared_dir / "images" / f"p{page:04d}.png"
    asset = config.selected_images.get(page)
    if asset is not None:
        raw = asset.path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset.sha256:
            raise ValueError("Predeclared selected source image changed")
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("Cached and selected source PNG bytes disagree")
    elif path.exists():
        raw = path.read_bytes()
    else:
        raw = renderer(config.pdf, page, config.dpi, config.max_image_pixels)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return raw, {"page": page, "image_file": str(path), "image_sha256": hashlib.sha256(raw).hexdigest(),
                 "pdf_sha256": config.pdf_sha256, "dpi": config.dpi}


def _survey_image(config, page, renderer):
    """Encode the actual survey payload separately from canonical extraction PNGs.

    JPEG retains full rendered resolution and uses explicitly registered quality,
    fixed subsampling=2, optimize=False/progressive=False. It never changes source
    layout references or extraction inputs. Both actual JPEG and source PNG hashes
    are preserved, and exact bytes sent are available for request reconstruction.
    """
    png, source = _image(config, page, renderer)
    if config.survey_image_format == "png":
        return png, {**source, "image_format": "png", "mime_type": "image/png",
                     "source_png_file": source["image_file"], "source_png_sha256": source["image_sha256"],
                     "jpeg_quality": None, "jpeg_subsampling": None}
    from PIL import Image
    with Image.open(io.BytesIO(png)) as image:
        stream = io.BytesIO()
        image.convert("RGB").save(stream, format="JPEG", quality=config.survey_jpeg_quality,
                                  subsampling=2, optimize=False, progressive=False)
        raw = stream.getvalue()
        width, height = image.size
    path = config.prepared_dir / "survey-images" / f"p{page:04d}.jpg"
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Saved survey JPEG differs from source PNG and registered encoding")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return raw, {**source, "image_file": str(path), "image_sha256": hashlib.sha256(raw).hexdigest(),
                 "image_format": "jpeg", "mime_type": "image/jpeg", "source_png_file": source["image_file"],
                 "source_png_sha256": source["image_sha256"], "width_pixels": width, "height_pixels": height,
                 "jpeg_quality": config.survey_jpeg_quality, "jpeg_subsampling": 2}


def _messages(instruction: str, text: str, images: list[tuple] = ()) -> list[dict]:
    content = [{"type": "text", "text": text}]
    for item in images:
        page, image = item[:2]
        mime_type = item[2] if len(item) == 3 else "image/png"
        if mime_type not in {"image/png", "image/jpeg"}:
            raise ValueError("Unsupported source image MIME type")
        content.extend([{"type": "text", "text": f"Physical source PDF page {page}"},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64," + base64.b64encode(image).decode("ascii")}}])
    return [{"role": "system", "content": prompts.SOURCE_ONLY + instruction},
            {"role": "user", "content": content}]


def _payload(config, *, preparation, messages, schema, max_tokens, name):
    payload = {"model": config.preparation_model if preparation else config.model,
        "messages": messages, "temperature": config.temperature, "max_tokens": max_tokens,
        "stream": True, "usage": {"include": True},
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}}
    provider = config.preparation_provider if preparation else config.provider
    if provider:
        payload["provider"] = {"order": [provider], "allow_fallbacks": False, "require_parameters": True}
    if config.seed is not None:
        payload["seed"] = config.seed
    reasoning = (config.preparation_reasoning_max_tokens if preparation else config.extraction_reasoning_max_tokens) or config.reasoning_max_tokens
    if reasoning is not None and payload["model"].startswith("google/gemini"):
        payload["reasoning"] = {"max_tokens": reasoning}
    return payload


class SavedCallFailure(RuntimeError):
    """A preserved failed/pending call requires an explicit new version, not replay."""


def _call(directory: Path, payload: dict, transport: Transport, model_type, validate: Callable | None = None):
    """Persist before/after request; recover saved responses without another call."""
    directory.mkdir(parents=True, exist_ok=True)
    request_path, response_path = directory / "request.json", directory / "response.json"
    record_path = directory / "call.json"
    request_hash = digest(payload)
    if request_path.exists():
        if digest(read_json(request_path)) != request_hash:
            raise ValueError("Saved stage request differs; use a new reconstruction version")
        if not response_path.exists():
            raise SavedCallFailure("Saved request has no preserved response; no automatic replay of possibly billed request")
    else:
        if list(directory.iterdir()):
            raise ValueError("New call directory must be empty")
        write_json(request_path, payload)
        write_json(record_path, {"status": "pending", "request_body_sha256": request_hash,
                                "started_at_utc": datetime.now(UTC).isoformat()})
        started = time.monotonic()
        try:
            response = transport.request("POST", "chat/completions", payload)
        except Exception as exc:
            write_json(record_path, {"status": "transport_failure", "exception_type": type(exc).__name__,
                "request_body_sha256": request_hash, "request_file_sha256": file_hash(request_path),
                "elapsed_seconds": time.monotonic() - started,
                "automatic_retry": False, "billing": "caller transport ledger is authoritative"})
            raise
        write_json(response_path, response)
        write_json(record_path, {"status": "response_saved", "request_body_sha256": request_hash,
            "request_file_sha256": file_hash(request_path), "response_file_sha256": file_hash(response_path),
            "response_sha256": digest(response), "usage": response.get("usage"),
            "generation_id": response.get("id"), "reported_model": response.get("model"),
            "reported_provider": response.get("provider"), "elapsed_seconds": time.monotonic() - started})
    record = read_json(record_path)
    response = read_json(response_path)
    if record.get("request_file_sha256") != file_hash(request_path):
        raise ValueError("Preserved request artifact fingerprint changed")
    if record.get("response_file_sha256") != file_hash(response_path) or record.get("response_sha256") != digest(response):
        raise ValueError("Preserved response fingerprint changed")
    normalization_audit = None
    try:
        choices = response.get("choices") or []
        if response.get("error") or len(choices) != 1 or choices[0].get("error"):
            raise ValueError("Provider error or missing/ambiguous response choice")
        if choices[0].get("finish_reason") != "stop":
            raise ValueError("Response was truncated or did not finish normally")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise ValueError("Response content must be one JSON string")
        if model_type is GeometryPage:
            _, normalization_audit = normalize_geometry(json.loads(content))
            normalization_audit.update(raw_response_file_sha256=file_hash(response_path),
                raw_response_json_sha256=digest(response), model_content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                canonical_validation="pending")
            write_json(directory / "model-normalization-audit.json", normalization_audit)
        parsed = model_type.model_validate_json(content)
        if validate:
            validate(parsed)
        if normalization_audit is not None:
            normalization_audit.update(canonical_validation="pass", canonical_geometry_sha256=digest(parsed.model_dump(mode="json")))
            write_json(directory / "model-normalization-audit.json", normalization_audit)
    except (ValueError, TypeError) as exc:
        if normalization_audit is not None:
            normalization_audit.update(canonical_validation="rejected", exception_type=type(exc).__name__)
            write_json(directory / "model-normalization-audit.json", normalization_audit)
        rejection = {"status": "rejected", "exception_type": type(exc).__name__,
                     "message": str(exc)[:1500], "automatic_retry": False}
        write_json(directory / "rejection.json", rejection)
        raise ValueError("Saved model output rejected: " + str(exc)[:1500]) from exc
    write_json(directory / "parsed.json", parsed.model_dump(mode="json"))
    return parsed, {"request": str(request_path), "request_file_sha256": file_hash(request_path),
                    "request_body_sha256": request_hash, "response": str(response_path),
                    "response_file_sha256": file_hash(response_path), "response_sha256": digest(response),
                    "call": str(record_path)}


def _preparation_snapshot(config: ReconstructionConfig, page_counter):
    if file_hash(config.pdf) != config.pdf_sha256:
        raise ValueError("Source PDF fingerprint changed")
    pages = page_counter(config.pdf)
    if max(config.selected_pages + config.preparation_illustrative_pages) > pages:
        raise ValueError("Selected/illustrative physical page lies beyond the source PDF")
    examples = []
    for example in config.development_examples:
        if example.image and file_hash(example.image.path) != example.image.sha256:
            raise ValueError("Development example image changed")
        examples.append({**example.model_dump(mode="json"), "extraction_sha256": file_hash(example.extraction_path)})
    return {"schema_version": 1, "document_id": config.document_id, "issuer_id": config.issuer_id,
        "pdf_sha256": config.pdf_sha256, "pdf_page_count": pages, "dpi": config.dpi,
        "max_image_pixels": config.max_image_pixels, "procedure_sha256": procedure_hash(),
        "preparation_model": config.preparation_model, "preparation_provider": config.preparation_provider,
        "temperature": config.temperature, "seed": config.seed,
        "preparation_reasoning_max_tokens": config.preparation_reasoning_max_tokens or config.reasoning_max_tokens,
        "survey_chunk_pages": config.survey_chunk_pages,
        "survey_image_format": config.survey_image_format, "survey_jpeg_quality": config.survey_jpeg_quality,
        "survey_jpeg_subsampling": 2,
        "limits": [config.survey_max_tokens, config.structure_max_tokens, config.schema_max_tokens, config.prompt_max_tokens],
        "illustrative_pages": config.preparation_illustrative_pages, "development_examples": examples,
        "selected_source_images": {str(p): asset.sha256 for p, asset in config.selected_images.items()},
        "protocol_sha256": config.protocol_sha256, "selection_freeze_sha256": config.selection_freeze_sha256,
        "reuse_preparation_from": str(config.reuse_preparation_from) if config.reuse_preparation_from else None,
        "survey_validation_attempts": config.survey_validation_attempts,
        "python": platform.python_version()}


def _validate_prepared(config, snapshot, prepared):
    if prepared.get("preparation_fingerprint") != digest(snapshot):
        raise ValueError("Prepared PDF/procedure/DPI/examples/generator/config changed; cannot reuse stages")
    for path, expected in prepared["preserved_artifacts"].items():
        if file_hash(config.prepared_dir / path) != expected:
            raise ValueError("Preserved preparation artifact changed: " + path)
    for path, expected in prepared.get("imported_artifacts", {}).items():
        if file_hash(Path(path)) != expected:
            raise ValueError("Imported survey/structure origin artifact changed")


def _reuse_snapshot(config, snapshot):
    """Permit only exact source/settings V2 survey+structure cache compatibility."""
    source = config.reuse_preparation_from
    if source is None:
        return None
    if source.resolve() == config.prepared_dir.resolve():
        raise ValueError("Stage import must use a different preserved preparation directory")
    previous = read_json(source / "snapshot.json")
    if previous.get("procedure_sha256") != "e62953c937b668353ae4d1d29e66a5882b091fb4dba26d94caa1b678275d34b9":
        raise ValueError("Only the explicitly preserved V2 procedure is approved for stage import")
    archive = Path(__file__).resolve().parents[3] / "implementations/pipeline-v2-e62953c9"
    receipt = read_json(archive / "preservation-receipt.json")
    for name, expected in receipt["file_sha256"].items():
        if file_hash(archive / name) != expected:
            raise ValueError("Preserved V2 implementation changed")
    # Protocol/procedure identifiers necessarily change for the prospectively
    # repaired schema/prompt, while all stage input settings remain identical.
    ignored = {"procedure_sha256", "protocol_sha256", "reuse_preparation_from", "survey_validation_attempts"}
    before = {k: v for k, v in previous.items() if k not in ignored}
    after = {k: v for k, v in snapshot.items() if k not in ignored}
    before["limits"], after["limits"] = before["limits"][:2], after["limits"][:2]
    if before != after:
        raise ValueError("Imported survey source/PDF/DPI/examples/generator/settings do not match")
    return {"source": source, "snapshot": previous, "snapshot_sha256": file_hash(source / "snapshot.json")}


def _readonly_call(directory, payload, model_type, validate):
    request, response, call = (directory / name for name in ("request.json", "response.json", "call.json"))
    if not request.exists():
        return None, None
    if not response.exists():
        saved = read_json(call) if call.exists() else {}
        if saved.get("exception_type") == "ConnectError" and saved.get("status") == "transport_failure":
            if (digest(read_json(request)) != digest(payload) or saved.get("request_body_sha256") != digest(payload)
                    or saved.get("request_file_sha256") != file_hash(request)):
                raise ValueError("Known-not-sent origin request fingerprints differ")
            return None, {"status": "known_not_sent_origin", "source_directory": str(directory),
                          "files": {str(p): file_hash(p) for p in (request, call) if p.exists()}}
        raise SavedCallFailure("Imported origin has no response and no established ConnectError; no automatic replay")
    record, raw = read_json(call), read_json(response)
    if (digest(read_json(request)) != digest(payload) or file_hash(request) != record.get("request_file_sha256")
            or digest(payload) != record.get("request_body_sha256") or file_hash(response) != record.get("response_file_sha256")
            or digest(raw) != record.get("response_sha256")):
        raise ValueError("Imported actual request/response fingerprints or reconstructed payload differ")
    origin = {"source_directory": str(directory), "files": {str(p): file_hash(p) for p in (request, response, call)},
              "request": str(request), "request_body_sha256": digest(payload), "response": str(response),
              "response_sha256": digest(raw), "reused": True}
    try:
        choices = raw.get("choices") or []
        if raw.get("error") or len(choices) != 1 or choices[0].get("finish_reason") != "stop":
            raise ValueError("Imported model stage did not finish normally")
        parsed = model_type.model_validate_json(choices[0]["message"]["content"])
        validate(parsed)
    except (ValueError, TypeError, KeyError) as exc:
        return None, {**origin, "status": "known_invalid_model_stage", "reason": str(exc)[:500]}
    saved = directory / "parsed.json"
    if not saved.exists() or read_json(saved) != parsed.model_dump(mode="json"):
        raise ValueError("Imported parsed stage differs from actual response")
    origin["files"][str(saved)] = file_hash(saved)
    return parsed, {**origin, "status": "verified_stage_import"}


def _stage_call(config, relative, payload, transport, model_type, validate, reuse, imports, *, survey=False):
    directory = config.prepared_dir / relative
    first = 1
    if reuse:
        parsed, origin = _readonly_call(reuse["source"] / relative, payload, model_type, validate)
        if origin:
            imports.update(origin["files"])
            write_json(directory / "import-origin.json", origin)
            if parsed is not None:
                write_json(directory / "parsed.json", parsed.model_dump(mode="json"))
                return parsed, origin
            if origin["status"] == "known_invalid_model_stage":
                if not survey:
                    raise ValueError("Imported structure is a preserved model-stage failure; explicit new protocol required")
                first = 2  # Imported invalid response consumes one declared validation attempt.
    maximum = config.survey_validation_attempts if survey else 1
    for attempt in range(first, maximum + 1):
        target = directory / f"attempt-{attempt:02d}" if survey or (directory / "import-origin.json").exists() else directory
        try:
            parsed, call = _call(target, payload, transport, model_type, validate)
            return parsed, {**call, "validation_attempt": attempt, "origin_imported": bool(reuse)}
        except ValueError:
            if not survey or not (target / "rejection.json").exists() or attempt == maximum:
                raise
            # Same stage, identical payload/settings. All raw attempts retained.
            # No extraction/numeric-output retry or unknown/transport replay.
    raise ValueError("Declared maximum survey validation attempts exhausted")


def prepare_document(config: ReconstructionConfig, transport: Transport, *, renderer=default_renderer,
                     page_counter=default_page_count) -> dict:
    """One shared generator preparation per source filing; no extractor-specific replay."""
    snapshot = _preparation_snapshot(config, page_counter)
    reuse = _reuse_snapshot(config, snapshot)
    prepared_path = config.prepared_dir / "prepared.json"
    with lock_directory(config.prepared_dir):
        if prepared_path.exists():
            prepared = read_json(prepared_path)
            _validate_prepared(config, snapshot, prepared)
            return {**prepared, "reused": True}
        snapshot_path = config.prepared_dir / "snapshot.json"
        if snapshot_path.exists() and read_json(snapshot_path) != snapshot:
            raise ValueError("Incomplete preparation inputs changed; use a new preparation directory")
        write_json(snapshot_path, snapshot)
        chunks, calls, image_records, imports = [], [], [], {}
        if reuse:
            imports[str(reuse["source"] / "snapshot.json")] = reuse["snapshot_sha256"]
        count = snapshot["pdf_page_count"]
        try:
            for start in range(1, count + 1, config.survey_chunk_pages):
                numbers = list(range(start, min(count + 1, start + config.survey_chunk_pages)))
                images = []
                for number in numbers:
                    raw, record = _survey_image(config, number, renderer)
                    images.append((number, raw, record["mime_type"])); image_records.append(record)
                def validate_chunk(chunk):
                    if [p.page for p in chunk.pages] != numbers:
                        raise ValueError("Survey must account for every supplied physical page exactly once")
                chunk, call = _stage_call(config, Path("survey") / f"chunk-{start:04d}",
                    _payload(config, preparation=True, messages=_messages(prompts.SURVEY,
                        json.dumps({"physical_pages": numbers, "document_id": config.document_id}), images),
                        schema=strict_schema(SurveyChunk), max_tokens=config.survey_max_tokens, name="document_survey_chunk"),
                    transport, SurveyChunk, validate_chunk, reuse, imports, survey=True)
                chunks.append(chunk.model_dump(mode="json")); calls.append(call)
            survey = {"full_document_page_count": count, "covered_pages": list(range(1, count + 1)),
                      "chunks": chunks, "image_provenance": image_records}
            if reuse and (reuse["source"] / "survey.json").exists():
                old_survey = read_json(reuse["source"] / "survey.json")
                # Original image paths are origin provenance, not semantic inputs.
                # Preserve the exact old survey object so the already-paid
                # structure request can be reconstructed byte-for-byte.
                def semantic_survey(value):
                    result = copy_survey = json.loads(json.dumps(value))
                    for record in result["image_provenance"]:
                        record.pop("image_file", None); record.pop("source_png_file", None)
                    return copy_survey
                if semantic_survey(old_survey) != semantic_survey(survey):
                    raise ValueError("Imported complete survey differs from independently verified chunk responses/images")
                for image in old_survey["image_provenance"]:
                    for path_key, hash_key in (("image_file", "image_sha256"), ("source_png_file", "source_png_sha256")):
                        if image.get(path_key):
                            path = Path(image[path_key])
                            if file_hash(path) != image[hash_key]:
                                raise ValueError("Imported survey image origin changed")
                            imports[str(path)] = image[hash_key]
                imports[str(reuse["source"] / "survey.json")] = file_hash(reuse["source"] / "survey.json")
                survey = old_survey
            write_json(config.prepared_dir / "survey.json", survey)
            def validate_structure(structure):
                if structure.reviewed_pages != list(range(1, count + 1)):
                    raise ValueError("Consolidated structure must retain every surveyed physical page")
                locations = [p.page for p in structure.table_locations]
                if locations != sorted(set(locations)) or not set(locations) <= set(structure.reviewed_pages):
                    raise ValueError("Structure table locations must be unique surveyed physical pages")
            structure_payload = _payload(config, preparation=True,
                messages=_messages(prompts.STRUCTURE, json.dumps(survey, ensure_ascii=False)),
                schema=strict_schema(StructurePlan), max_tokens=config.structure_max_tokens, name="document_structure")
            if reuse and (reuse["source"] / "structure/request.json").exists():
                # JSON key order inside a user-text string is byte-significant to
                # request hashes. Preserve original serialization only after its
                # decoded object equals the fully verified survey; all remaining
                # payload/schema/system/settings fields are compared by the gate.
                old_request = read_json(reuse["source"] / "structure/request.json")
                old_text = old_request["messages"][1]["content"][0]["text"]
                if json.loads(old_text) != survey:
                    raise ValueError("Imported structure request consumed a different survey")
                structure_payload["messages"][1]["content"][0]["text"] = old_text
            structure, call = _stage_call(config, Path("structure"), structure_payload,
                transport, StructurePlan, validate_structure, reuse, imports)
            calls.append(call); write_json(config.prepared_dir / "structure.json", structure.model_dump(mode="json"))
            plan, call = _call(config.prepared_dir / "schema", _payload(config, preparation=True,
                messages=_messages(prompts.SCHEMA, json.dumps(structure.model_dump(mode="json"), ensure_ascii=False)),
                schema=strict_schema(SchemaPlan), max_tokens=config.schema_max_tokens, name="document_generated_schema"), transport, SchemaPlan)
            calls.append(call); plan_data = plan.model_dump(mode="json")
            write_json(config.prepared_dir / "generated-schema-plan.json", plan_data)
            response_schema = generated_extraction_schema(plan)
            write_json(config.prepared_dir / "compiled-extraction-schema.json", response_schema)
            illustration = [_image(config, number, renderer) for number in config.preparation_illustrative_pages]
            example_data, example_images = [], []
            for example in config.development_examples:
                example_data.append({"issuer_id": example.issuer_id, "review_kind": example.review_kind,
                                     "extraction": read_json(example.extraction_path)})
                if example.image:
                    example_images.append((0, example.image.path.read_bytes()))
            text = json.dumps({"structure": structure.model_dump(mode="json"), "generated_schema": plan_data,
                "schema_plan_sha256": digest(plan_data), "development_examples": example_data,
                "example_status": "reviewed development-only" if example_data else "none supplied; no manually corrected few-shot benefit claim",
                "physical_geometry_instructions": prompts.GEOMETRY}, ensure_ascii=False)
            def validate_prompt(prompt):
                if prompt.schema_plan_sha256 != digest(plan_data):
                    raise ValueError("Generated prompt names a different generated schema")
                audit = prompts.prompt_safety_audit(prompt.instructions)
                write_json(config.prepared_dir / "generated-prompt-safety-audit.json", audit)
                if not audit["passed"]:
                    raise ValueError("Generated prompt contains an unnegated numeric repair/fill directive")
            prompt, call = _call(config.prepared_dir / "generated-prompt", _payload(config, preparation=True,
                messages=_messages(prompts.GENERATE_PROMPT, text,
                    [(number, image[0]) for number, image in zip(config.preparation_illustrative_pages, illustration)] + example_images),
                schema=strict_schema(GeneratedPrompt), max_tokens=config.prompt_max_tokens, name="document_generated_prompt"),
                transport, GeneratedPrompt, validate_prompt)
            calls.append(call); write_json(config.prepared_dir / "generated-prompt.json", prompt.model_dump(mode="json"))
            preserved = {str(path.relative_to(config.prepared_dir)): file_hash(path)
                         for path in sorted(config.prepared_dir.rglob("*")) if path.is_file()
                         and path.name not in {".lock", "prepared.json", "failure.json"}}
            prepared = {"status": "complete", "preparation_fingerprint": digest(snapshot), "snapshot": snapshot,
                "imported_artifacts": imports,
                "import_origin": str(reuse["source"]) if reuse else None,
                "generator_origin": {"model": config.preparation_model, "provider": config.preparation_provider},
                "stages": ["full_document_image_survey", "document_structure", "generated_schema", "generated_prompt"],
                "calls": calls, "preserved_artifacts": preserved,
                "manual_examples_used": bool(example_data), "created_at_utc": datetime.now(UTC).isoformat(), "reused": False}
            write_json(prepared_path, prepared)
            return prepared
        except Exception as exc:
            write_json(config.prepared_dir / "failure.json", {"status": "failed", "exception_type": type(exc).__name__,
                "message": str(exc)[:1500], "automatic_retry": False})
            raise


def extract_document(config: ReconstructionConfig, prepared: dict, transport: Transport, *,
                     renderer=default_renderer, page_counter=default_page_count) -> dict:
    snapshot = _preparation_snapshot(config, page_counter)
    _validate_prepared(config, snapshot, prepared)
    plan = SchemaPlan.model_validate(read_json(config.prepared_dir / "generated-schema-plan.json"))
    compiled = generated_extraction_schema(plan)
    if read_json(config.prepared_dir / "compiled-extraction-schema.json") != compiled:
        raise ValueError("Compiled generated schema no longer matches its plan")
    generated = GeneratedPrompt.model_validate(read_json(config.prepared_dir / "generated-prompt.json"))
    safety = prompts.prompt_safety_audit(generated.instructions)
    if not safety["passed"] or read_json(config.prepared_dir / "generated-prompt-safety-audit.json") != safety:
        raise ValueError("Generated prompt safety audit changed or failed")
    run_snapshot = {"config": config.model_dump(mode="json"), "preparation_fingerprint": prepared["preparation_fingerprint"],
                    "preparation_artifacts": prepared["preserved_artifacts"], "procedure_sha256": procedure_hash()}
    fingerprint = digest(run_snapshot)
    results = {"status": "running", "fingerprint": fingerprint, "document_id": config.document_id,
        "condition": config.condition, "model": config.model, "requested_pages": config.selected_pages,
        "structural_pages": config.structural_pages, "accounting_pages": config.accounting_pages,
        "preparation_fingerprint": prepared["preparation_fingerprint"], "generator_origin": prepared["generator_origin"],
        "preparation_reused": prepared.get("reused", False), "accepted_pages": [], "rejected_pages": [],
        "pages": [], "raw_records": [], "clean_records": [], "raw_pages": [],
        "interpretation": "New reconstructed pipeline; no recovered historical experiment claim"}
    with lock_directory(config.output):
        run_path = config.output / "run.json"
        if run_path.exists() and read_json(run_path)["fingerprint"] != fingerprint:
            raise ValueError("Extraction run inputs changed; use a new directory")
        if not run_path.exists():
            if any(p.name != ".lock" for p in config.output.iterdir()):
                raise ValueError("New extraction directory must be empty")
            write_json(run_path, {**run_snapshot, "fingerprint": fingerprint, "created_at_utc": datetime.now(UTC).isoformat()})
        previous = None
        write_json(config.output / "results.json", results)
        try:
            for number in config.selected_pages:
                context = previous if config.condition == "sequential" and previous and previous.page == number - 1 else None
                raw, image = _image(config, number, renderer)
                results["raw_pages"].append(image)
                task = {"physical_page": number, "document_id": config.document_id,
                        "generated_instructions": generated.instructions,
                        "generated_financial_schema": plan.model_dump(mode="json"),
                        "untrusted_previous_accepted_page": context.model_dump(mode="json") if context else None}
                text = json.dumps(task, ensure_ascii=False)
                if len(text) > config.max_context_chars:
                    raise ValueError("Context exceeds declared limit; no silent truncation")
                directory = config.output / "pages" / f"p{number:04d}"
                def validate_page(page):
                    if page.page != number:
                        raise ValueError("Response physical page number differs from request")
                    allowed_statements = {a.statement for a in plan.archetypes}
                    concepts = {a.statement: set(a.supported_concepts) for a in plan.archetypes}
                    for table in page.tables:
                        if table.statement not in allowed_statements or any(r.concept not in concepts[table.statement] for r in table.rows if r.concept):
                            raise ValueError("Extraction violates the document-generated schema choices")
                    validate_continuations(page, context)
                try:
                    page, call = _call(directory, _payload(config, preparation=False,
                        messages=_messages(prompts.GEOMETRY, text, [(number, raw)]), schema=compiled,
                        max_tokens=config.extraction_max_tokens, name="financial_geometry_page"),
                        transport, GeometryPage, validate_page)
                except ValueError as exc:
                    # A saved malformed/schema-rejected response is an endpoint
                    # failure; transport/budget exceptions terminate the run.
                    if not (directory / "rejection.json").exists():
                        raise
                    results["rejected_pages"].append({"page": number, "reason": str(exc)[:1500]})
                    previous = None
                    write_json(config.output / "results.json", results)
                    continue
                logical = logical_page(page)
                cleaned, edits, audit = deterministic_clean(logical, pdf_sha256=config.pdf_sha256)
                grids = [grid.model_dump(mode="json") for grid in canonical_grids(page,
                    pdf_sha256=config.pdf_sha256, image_sha256=image["image_sha256"], dpi=config.dpi)]
                raw_records = numeric_records(config.document_id, logical)
                clean_records = numeric_records(config.document_id, cleaned)
                write_json(directory / "raw-geometry.json", page.model_dump(mode="json"))
                write_json(directory / "raw-logical.json", logical)
                write_json(directory / "clean-logical.json", cleaned)
                write_json(directory / "canonical-grids.json", grids)
                write_json(directory / "cleaning-edits.json", [edit.model_dump(mode="json") for edit in edits])
                write_json(directory / "cleaning-audit.json", audit)
                write_json(directory / "raw-records.json", raw_records)
                write_json(directory / "clean-records.json", clean_records)
                write_json(directory / "page-record.json", {"page": number, "image": image,
                    "previous_page": context.page if context else None, "call": call,
                    "generated_schema_sha256": digest(compiled), "generated_prompt_sha256": digest(generated.model_dump(mode="json")),
                    "preparation_fingerprint": prepared["preparation_fingerprint"]})
                results["accepted_pages"].append(number)
                results["pages"].append({"page": number, "raw_geometry_file": str(directory / "raw-geometry.json"),
                    "canonical_grids_file": str(directory / "canonical-grids.json"), "cleaning_edit_count": len(edits)})
                results["raw_records"].extend(raw_records); results["clean_records"].extend(clean_records)
                previous = page
                write_json(config.output / "results.json", results)
            results.update(status="complete", completed_pages=len(results["accepted_pages"]),
                           has_rejected_pages=bool(results["rejected_pages"]), all_requested_pages_attempted=True)
        except Exception as exc:
            results.update(status="failed", exception_type=type(exc).__name__, error=str(exc)[:1500],
                           all_requested_pages_attempted=False, automatic_retry=False)
            write_json(config.output / "results.json", results)
            raise
        write_json(config.output / "results.json", results)
        return results


def run_reconstruction(config: ReconstructionConfig, transport: Transport, *,
                       renderer=default_renderer, page_counter=default_page_count) -> dict:
    prepared = prepare_document(config, transport, renderer=renderer, page_counter=page_counter)
    return extract_document(config, prepared, transport, renderer=renderer, page_counter=page_counter)
