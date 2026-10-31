"""OpenRouter transport, capability checks, bounded retries, and durable usage records."""

from __future__ import annotations

import http.client
import copy
import json
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol

from .schema import Experiment, PageExtraction
from .storage import digest, read_json, write_json


class APIError(RuntimeError):
    def __init__(self, status: int, retry_after: float = 0):
        super().__init__(f"OpenRouter HTTP {status}")
        self.status = status
        self.retry_after = retry_after


class ConnectError(RuntimeError):
    """Connecting or sending failed, so the server never received a complete request."""


class OutcomeUnknown(RuntimeError):
    """The request was sent but the reply was lost; the generation may still be billed."""

    def __init__(self, message: str, generation_id: str | None = None):
        super().__init__(message)
        self.generation_id = generation_id


class Transport(Protocol):
    def request(self, method: str, path: str, payload: dict | None = None) -> dict: ...


def retry_delay(value: str | None) -> float:
    if not value:
        return 0
    try:
        return min(60, max(0, float(value)))
    except ValueError:
        try:
            return min(
                60, max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            )
        except (TypeError, ValueError):
            return 0


RETRYABLE = {429, 500, 502, 503, 504}
UNBILLED_HTTP_REJECTIONS = {400, 401, 402, 403, 404, 422, 429}


def backoff(status: int, attempt: int) -> float:
    # Upstream rate limits are per minute; a few seconds is too short to clear them.
    base = 10 if status == 429 else 1
    return min(60, base * 2**attempt + random.random())


def provider_error(response: dict) -> int | None:
    """Status of an error delivered inside an HTTP 200 body, or None for a model output.

    Such an error carries no usable output, so it is retried; its cost is still recorded.
    Mid-stream error codes may be strings; they map to 502.
    """
    choices = response.get("choices") or []
    errors = [response.get("error")] + [c.get("error") for c in choices]
    failed = any(c.get("finish_reason") == "error" for c in choices)
    for error in errors:
        if isinstance(error, dict):
            code = error.get("code")
            return code if isinstance(code, int) else 502
    return 502 if failed else None


class _Stream:
    """Reassembles OpenRouter server-sent events into a non-streaming response body."""

    def __init__(self, generation_id: str | None):
        self.id = generation_id
        self.meta: dict[str, Any] = {}
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.finish_reason = self.native_finish_reason = None
        self.error = self.usage = None

    def add(self, chunk: dict) -> None:
        self.id = self.id or chunk.get("id")
        for field in ("model", "provider", "created"):
            if chunk.get(field) is not None:
                self.meta[field] = chunk[field]
        if chunk.get("error"):
            self.error = chunk["error"]
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self.content.append(delta["content"])
            if delta.get("reasoning"):
                self.reasoning.append(delta["reasoning"])
            if choice.get("error"):
                self.error = choice["error"]
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
                self.native_finish_reason = choice.get("native_finish_reason")

    @property
    def finished(self) -> bool:
        return self.finish_reason is not None or self.error is not None

    def response(self) -> dict:
        choice: dict[str, Any] = {
            "index": 0,
            "finish_reason": self.finish_reason or ("error" if self.error else None),
            "native_finish_reason": self.native_finish_reason,
            "message": {
                "role": "assistant",
                "content": "".join(self.content) if self.content else None,
                "reasoning": "".join(self.reasoning) or None,
            },
        }
        body: dict[str, Any] = {"id": self.id, "object": "chat.completion", **self.meta}
        if self.error:
            choice["error"] = self.error
            body["error"] = self.error
        return {**body, "streamed": True, "choices": [choice], "usage": self.usage}


def read_stream(lines, generation_id: str | None = None) -> dict:
    """Parse an SSE body. A break before the final event raises OutcomeUnknown."""
    stream = _Stream(generation_id)
    try:
        for raw in lines:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue  # keep-alive comments and blank separators
            data = line[5:].strip()
            if data == "[DONE]":
                break
            stream.add(json.loads(data))
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if not stream.finished:
            raise OutcomeUnknown(
                f"Stream interrupted ({type(exc).__name__})", stream.id
            ) from None
        # The output ended; only the trailing usage event was lost. Cost is looked up.
    if not stream.finished:
        raise OutcomeUnknown("Stream ended without a final event", stream.id)
    return stream.response()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward an API credential through an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPTransport:
    """TLS-verified HTTPS with a fixed host; credentials never enter artifacts."""

    def __init__(self, timeout: float):
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key or key in {"replace-locally", "your-key-here"}:
            raise RuntimeError("Set OPENROUTER_API_KEY locally before a live run")
        if not key.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key):
            raise RuntimeError("OPENROUTER_API_KEY must be a single printable ASCII token")
        self._key = key
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        valid = (method == "GET" and (path == "models" or path.startswith("generation?id="))) or (
            method == "POST" and path == "chat/completions"
        )
        if not valid or any(c in path for c in "\r\n#"):
            raise ValueError("Unsupported OpenRouter method or endpoint")
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self._key,
                "Content-Type": "application/json",
                "X-OpenRouter-Title": "Financial Table Verifier",
            },
        )
        try:
            response = urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=self.timeout
            )
        except urllib.error.HTTPError as exc:
            raise APIError(exc.code, retry_delay(exc.headers.get("Retry-After"))) from None
        except urllib.error.URLError as exc:
            # URLError also wraps TLS/send failures after bytes may have reached the
            # provider. Only DNS lookup and refused TCP connection prove no admission.
            if isinstance(exc.reason, (socket.gaierror, ConnectionRefusedError)):
                raise ConnectError(f"Connection failed ({type(exc.reason).__name__})") from None
            raise OutcomeUnknown(
                f"Network outcome unknown ({type(exc.reason).__name__})"
            ) from None
        except (OSError, http.client.HTTPException) as exc:
            # Sent, but no reply headers: the generation may run and be billed.
            raise OutcomeUnknown(f"Network outcome unknown ({type(exc).__name__})") from None
        with response:
            if payload is not None and payload.get("stream"):
                # timeout bounds each read, so keep-alive events keep long generations open.
                return read_stream(response, response.headers.get("X-Generation-Id"))
            try:
                value = json.load(response)
            except (OSError, http.client.HTTPException, ValueError) as exc:
                raise OutcomeUnknown(
                    f"Network outcome unknown ({type(exc).__name__})",
                    response.headers.get("X-Generation-Id"),
                ) from None
        if not isinstance(value, dict):
            raise RuntimeError("OpenRouter returned an invalid response envelope")
        return value


def request_body(config: Experiment, messages: list[dict]) -> dict:
    body = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "provider": {"allow_fallbacks": False, "require_parameters": True},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "page_extraction",
                "strict": True,
                "schema": PageExtraction.model_json_schema(),
            },
        },
    }
    if config.reasoning_max_tokens is not None:
        body["reasoning"] = {"max_tokens": config.reasoning_max_tokens}
    if config.use_seed and config.seed is not None:
        body["seed"] = config.seed
    if config.provider:
        body["provider"]["only"] = [config.provider]
    return body


class OpenRouterClient:
    def __init__(
        self,
        config: Experiment,
        directory: Path,
        transport: Transport,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config, self.directory, self.transport, self.sleep = (
            config,
            directory,
            transport,
            sleep,
        )
        self.ledger_path = directory / "requests.json"
        self.events: list[dict[str, Any]] = (
            read_json(self.ledger_path) if self.ledger_path.exists() else []
        )
        catalog = directory / "model-catalog-entry.json"
        self.model = read_json(catalog) if catalog.exists() else None

    def preflight(self) -> dict:
        data = self.transport.request("GET", "models")
        model = next((m for m in data.get("data", []) if m.get("id") == self.config.model), None)
        if model is None:
            raise RuntimeError("Configured model is absent from the current OpenRouter catalog")
        if "image" not in model.get("architecture", {}).get("input_modalities", []):
            raise RuntimeError("Configured model does not advertise image input")
        params = set(model.get("supported_parameters", []))
        if not params.intersection({"response_format", "structured_outputs"}):
            raise RuntimeError("Configured model does not advertise structured output")
        if self.config.reasoning_max_tokens is not None and "reasoning" not in params:
            raise RuntimeError("Configured model does not advertise a reasoning budget")
        write_json(self.directory / "model-catalog-entry.json", model)
        self.model = model
        return model

    def _reserve(self) -> Decimal:
        """Conservative unknown-charge reservation estimate, not a guaranteed billing cap."""
        try:
            pricing = self.model["pricing"]
            prompt_tokens = self.config.max_context_chars // 2 + 10000
            return (
                Decimal(pricing["completion"]) * self.config.max_tokens
                + Decimal(pricing["prompt"]) * prompt_tokens
            ).quantize(Decimal("0.000001"))
        except Exception:
            return Decimal("1.00")

    def _generation_cost(self, generation_id: str | None, event: dict | None = None) -> str | None:
        """Charged cost from OpenRouter's generation record; None if it never appears."""
        if not generation_id:
            return None
        path = "generation?id=" + urllib.parse.quote(generation_id)
        # Google cannot cancel a generation, so its record appears only once it finishes.
        for delay in (5, 10, 15, 30, 30, 30, 30, 30):
            self.sleep(delay)
            try:
                data = self.transport.request("GET", path)["data"]
                cost = _cost(data["total_cost"])
            except APIError as exc:
                if exc.status in {404} | RETRYABLE:
                    continue
                return None
            except Exception:
                continue
            if cost is not None:
                # OpenRouter's total can exclude charges on a user's provider key.
                # Keep any known BYOK flag; never downgrade it on a lookup.
                if event is not None and data.get("is_byok"):
                    usage = event.setdefault("usage", {})
                    usage["is_byok"] = True
                    upstream = _cost(data.get("upstream_inference_cost"))
                    if upstream is not None:
                        details = usage.get("cost_details") or {}
                        details["upstream_inference_cost"] = upstream
                        usage["cost_details"] = details
                return cost
        return None

    def _budget(self) -> None:
        if len(self.events) >= self.config.max_requests:
            raise RuntimeError("Run request limit reached, including retry attempts")
        if any(e["status"] in {"started", "failed"} for e in self.events):
            # A crash or unexpected error mid-request; nothing records what it cost.
            raise RuntimeError("A previous request has unknown outcome; use a new run after review")
        for event in self.events:
            reason = unknown_cost_reason(event)
            if reason is not None:
                raise RuntimeError(
                    "A previous sent request has unknown charge (" + reason + "); "
                    "no further completions until billing is independently reconciled"
                )
        cost = sum(
            (Decimal(e.get("cost_usd") or 0) + (byok_cost(e) or Decimal(0))
             for e in self.events), Decimal(0)
        )
        if cost >= self.config.max_observed_cost_usd:
            raise RuntimeError("Observed cost limit reached")

    def _charge(self, event: dict, cost: str | None) -> None:
        event["cost_usd"] = cost
        if cost is None or byok_cost(event) is None:
            event["reserved_usd"] = str(self._reserve())
        else:
            event.pop("reserved_usd", None)

    def complete(self, messages: list[dict], artifact_key: str) -> tuple[dict, dict]:
        body = request_body(self.config, messages)
        request_hash = digest(body)
        for attempt in range(self.config.max_attempts):
            self._budget()
            event: dict[str, Any] = {
                "request_index": len(self.events),
                "artifact_key": artifact_key,
                "attempt": attempt + 1,
                "request_sha256": request_hash,
                "status": "started",
                "reserved_usd": str(self._reserve()),
                "started_at": datetime.now(UTC).isoformat(),
            }
            self.events.append(event)
            write_json(self.ledger_path, self.events)
            started = time.monotonic()
            try:
                response = self.transport.request("POST", "chat/completions", body)
            except APIError as exc:
                event.update(
                    status="http_error",
                    http_status=exc.status,
                    latency_seconds=time.monotonic() - started,
                )
                self._charge(event, "0" if exc.status in UNBILLED_HTTP_REJECTIONS else None)
                write_json(self.ledger_path, self.events)
                if unknown_cost_reason(event) is not None:
                    raise
                if exc.status not in RETRYABLE or attempt + 1 == self.config.max_attempts:
                    raise
                self.sleep(max(exc.retry_after, backoff(exc.status, attempt)))
                continue
            except ConnectError as exc:
                event.update(
                    status="connect_error",
                    cost_usd="0",
                    error=str(exc),
                    latency_seconds=time.monotonic() - started,
                )
                event.pop("reserved_usd", None)
                write_json(self.ledger_path, self.events)
                if attempt + 1 == self.config.max_attempts:
                    raise
                self.sleep(backoff(0, attempt))
                continue
            except OutcomeUnknown as exc:
                event.update(
                    status="unknown",
                    error=str(exc),
                    generation_id=exc.generation_id,
                    latency_seconds=time.monotonic() - started,
                )
                write_json(self.ledger_path, self.events)
                # No output is available, but a second completion can incur another
                # charge. Replay only after this attempt's charge is recovered.
                self._charge(event, self._generation_cost(exc.generation_id, event))
                write_json(self.ledger_path, self.events)
                if unknown_cost_reason(event) is not None:
                    raise
                if attempt + 1 == self.config.max_attempts:
                    raise
                self.sleep(backoff(0, attempt))
                continue
            except Exception as exc:
                event.update(
                    status="failed", error=str(exc), latency_seconds=time.monotonic() - started
                )
                write_json(self.ledger_path, self.events)
                raise
            # Preserve every raw output. Only cut-off outputs are retried, and each is counted.
            raw_path = self.directory / "responses" / f"{event['request_index']:06d}.json"
            write_json(raw_path, response)
            # Billing reconciliation augments the ledger, never the immutable raw
            # response (whose persisted bytes must retain the same fingerprint).
            usage = copy.deepcopy(response.get("usage") or {})
            event["usage"] = usage
            cost = _cost(usage.get("cost"))
            if cost is None:
                cost = self._generation_cost(response.get("id"), event)
            event.update(
                response_file=str(raw_path.relative_to(self.directory)),
                response_sha256=digest(response),
                model=response.get("model"),
                provider=response.get("provider"),
                usage=usage,
                latency_seconds=time.monotonic() - started,
            )
            self._charge(event, cost)
            status = provider_error(response)
            if status is not None:
                # An upstream failure with no output, not a model answer: keep it and retry.
                event.update(status="provider_error", http_status=status)
                write_json(self.ledger_path, self.events)
                if attempt + 1 == self.config.max_attempts:
                    raise APIError(status)
                self.sleep(backoff(status, attempt))
                continue
            finish = (response.get("choices") or [{}])[0].get("finish_reason")
            if finish == "length" and attempt + 1 < self.config.max_attempts:
                # A thinking loop used up max_tokens before the answer; re-sample the page.
                event["status"] = "truncated"
                write_json(self.ledger_path, self.events)
                continue
            event["status"] = "response"
            write_json(self.ledger_path, self.events)
            return response, event
        raise AssertionError("Unreachable retry state")


def byok_cost(event: dict) -> Decimal | None:
    """Charge billed by the provider to the user's own key (BYOK); OpenRouter's cost omits it."""
    usage = event.get("usage") or {}
    if not usage.get("is_byok"):
        return Decimal(0)
    cost = _cost((usage.get("cost_details") or {}).get("upstream_inference_cost"))
    return Decimal(cost) if cost is not None else None


def unknown_cost_reason(event: dict) -> str | None:
    """Whether a sent/billable attempt still lacks a complete charge record."""
    if event.get("status") in {"started", "failed"}:
        return "request outcome unavailable"
    unbilled = event.get("status") == "connect_error" or (
        event.get("status") == "http_error"
        and event.get("http_status") in UNBILLED_HTTP_REJECTIONS
    )
    if not unbilled and _cost(event.get("cost_usd")) is None:
        return "OpenRouter charge unavailable"
    if byok_cost(event) is None:
        return "BYOK upstream provider charge unavailable"
    return None


def _cost(value) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return str(number) if number.is_finite() and number >= 0 else None


def parse_response(response: dict) -> PageExtraction:
    if response.get("error"):
        raise RuntimeError("OpenRouter response contains an error; inspect saved response")
    choices = response.get("choices", [])
    if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
        raise RuntimeError("Incomplete/refused model output; inspect saved response")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise RuntimeError("Model output is not a JSON text response")
    return PageExtraction.model_validate_json(content)
