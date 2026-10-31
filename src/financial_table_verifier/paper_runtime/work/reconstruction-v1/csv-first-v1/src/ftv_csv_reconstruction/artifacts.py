"""Caller-injected single-attempt transport and byte-bound durable stage artifacts."""
from __future__ import annotations
import base64
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import hashlib
import io
import json
from pathlib import Path
import time
from typing import Protocol
import uuid

from . import prompts
from .contracts import strict_schema


class Transport(Protocol):
    def request(self, method: str, path: str, payload: dict | None = None) -> dict: ...


class SavedCallFailure(RuntimeError):
    pass


class ModelOutputError(ValueError):
    pass


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    temporary.write_bytes(data)
    temporary.replace(path)


def write_json(path, value):
    write_bytes(path, json_bytes(value) + b"\n")


def procedure_hash():
    return digest({p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))})


@contextmanager
def lock_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    with (path / ".lock").open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another worker holds this artifact directory") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def response_content(response):
    choices = response.get("choices") or []
    if response.get("error") or len(choices) != 1 or choices[0].get("error"):
        raise ModelOutputError("Missing/ambiguous provider response choice")
    if choices[0].get("finish_reason") != "stop":
        raise ModelOutputError("Truncated or unfinished model response")
    text = choices[0].get("message", {}).get("content")
    if not isinstance(text, str):
        raise ModelOutputError("Model content must be a string")
    return text


def replay_call(directory, expected_payload=None):
    """Read only. Validate raw transport object, request, and exact content bytes."""
    directory = Path(directory)
    request, response, call = (read_json(directory / n) for n in ("request.json", "response.json", "call.json"))
    if (call.get("request_file_sha256") != file_hash(directory / "request.json")
            or call.get("request_body_sha256") != digest(request)
            or (expected_payload is not None and digest(request) != digest(expected_payload))
            or call.get("response_file_sha256") != file_hash(directory / "response.json")
            or call.get("response_sha256") != digest(response)):
        raise ValueError("Raw request/response artifact fingerprints disagree")
    text = response_content(response)
    if (call.get("model_content_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest()
            or (directory / "model-content.utf8").read_bytes() != text.encode("utf-8")):
        raise ValueError("Actual model content bytes changed")
    return json.loads(text), call


def call(directory, payload, transport, model=None, validate=None):
    """Never retries a failed/pending request. Valid saved responses replay offline."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    request_path, response_path = directory / "request.json", directory / "response.json"
    if request_path.exists():
        if digest(read_json(request_path)) != digest(payload):
            raise ValueError("Saved stage payload changed; use a new version")
        if not response_path.exists():
            raise SavedCallFailure("Saved request has no response; no automatic replay of possibly billed call")
    else:
        if list(directory.iterdir()):
            raise ValueError("New stage directory must be empty")
        write_json(request_path, payload)
        write_json(directory / "call.json", {"status": "pending", "request_body_sha256": digest(payload),
                                             "started_at_utc": datetime.now(UTC).isoformat()})
        started = time.monotonic()
        try:
            response = transport.request("POST", "chat/completions", payload)
        except Exception as exc:
            write_json(directory / "call.json", {"status": "transport_failure", "exception_type": type(exc).__name__,
                "request_file_sha256": file_hash(request_path), "request_body_sha256": digest(payload),
                "automatic_retry": False, "elapsed_seconds": time.monotonic()-started,
                "billing": "external caller ledger is authoritative"})
            raise
        write_json(response_path, response)
        # Store content even for truncated/error responses whenever it exists.
        choices = response.get("choices") or []
        content = choices[0].get("message", {}).get("content") if len(choices) == 1 else None
        if isinstance(content, str):
            write_bytes(directory / "model-content.utf8", content.encode("utf-8"))
        write_json(directory / "call.json", {"status": "response_saved",
            "request_file_sha256": file_hash(request_path), "request_body_sha256": digest(payload),
            "response_file_sha256": file_hash(response_path), "response_sha256": digest(response),
            "model_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest() if isinstance(content, str) else None,
            "usage": response.get("usage"), "generation_id": response.get("id"),
            "reported_model": response.get("model"), "reported_provider": response.get("provider"),
            "elapsed_seconds": time.monotonic()-started,
            "byte_scope": "transport-returned JSON object plus exact UTF-8 model-content string; not raw SSE wire bytes"})
    try:
        parsed, receipt = replay_call(directory, payload)
        if model is not None:
            parsed = model.model_validate(parsed)
        if validate:
            validate(parsed)
    except (ValueError, TypeError, KeyError) as exc:
        write_json(directory / "rejection.json", {"status": "rejected", "exception_type": type(exc).__name__,
                   "message": str(exc)[:1500], "automatic_retry": False})
        raise ModelOutputError(str(exc)[:1500]) from exc
    write_json(directory / "parsed.json", parsed.model_dump(mode="json") if model is not None else parsed)
    return parsed, {"directory": str(directory), **receipt}


def messages(instruction, task, images=()):
    content = [{"type": "text", "text": json.dumps(task, ensure_ascii=False)}]
    for page, raw in images:
        content.extend([{"type": "text", "text": f"Physical source PDF page {page}"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")}}])
    return [{"role": "system", "content": prompts.SOURCE_ONLY + instruction}, {"role": "user", "content": content}]


def payload(config, *, preparation, instruction, task, schema, max_tokens, name, images=()):
    model = config.preparation_model if preparation else config.model
    result = {"model": model, "messages": messages(instruction, task, images),
              "temperature": config.temperature, "max_tokens": max_tokens, "stream": True,
              "usage": {"include": True},
              "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}}
    provider = config.preparation_provider if preparation else config.provider
    if provider:
        result["provider"] = {"order": [provider], "allow_fallbacks": False, "require_parameters": True}
    if config.seed is not None:
        result["seed"] = config.seed
    reason = config.preparation_reasoning_max_tokens if preparation else config.extraction_reasoning_max_tokens
    if model.startswith("google/gemini") and reason is not None:
        result["reasoning"] = {"max_tokens": reason}
    return result


def default_renderer(pdf, page, dpi, max_pixels):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(pdf) as document:
        source = document[page-1]
        try:
            width, height = source.get_size()
            if width*height*(dpi/72)**2 > max_pixels:
                raise ValueError("Source render exceeds pixel limit")
            bitmap = source.render(scale=dpi/72)
            try:
                output = io.BytesIO()
                bitmap.to_pil().save(output, format="PNG", optimize=False)
                return output.getvalue()
            finally:
                bitmap.close()
        finally:
            source.close()


def source_image(config, page, destination, renderer=default_renderer):
    if file_hash(config.pdf) != config.pdf_sha256:
        raise ValueError("Source PDF changed")
    path = Path(destination) / f"p{page:04d}.png"
    asset = config.selected_images.get(page)
    if asset:
        raw = asset.path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset.sha256:
            raise ValueError("Predeclared source image changed")
    elif path.exists():
        raw = path.read_bytes()
        # A cached image without a receipt is not accepted on trust.
        receipt = read_json(path.with_suffix(".json"))
        if (receipt.get("image_sha256") != hashlib.sha256(raw).hexdigest()
                or receipt.get("pdf_sha256") != config.pdf_sha256
                or receipt.get("page") != page or receipt.get("dpi") != config.dpi):
            raise ValueError("Cached source image provenance changed")
    else:
        raw = renderer(config.pdf, page, config.dpi, config.max_image_pixels)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Cached and declared image bytes differ")
    receipt = {"page": page, "pdf_sha256": config.pdf_sha256, "dpi": config.dpi,
               "image_file": str(path), "image_sha256": hashlib.sha256(raw).hexdigest()}
    write_bytes(path, raw)
    write_json(path.with_suffix(".json"), receipt)
    return raw, receipt
