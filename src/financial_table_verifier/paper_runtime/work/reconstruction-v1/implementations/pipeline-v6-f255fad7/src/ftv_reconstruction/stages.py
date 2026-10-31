"""Read-only verification of a new run's separately preserved stage artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from .contracts import Nonempty, SHA256, Strict

STAGES = ("structure", "schema", "generated_prompt", "raw_extraction", "audited_cleaning")


class Artifact(Strict):
    stage: Literal["structure", "schema", "generated_prompt", "raw_extraction", "audited_cleaning"]
    path: Nonempty
    sha256: SHA256
    input_sha256: list[SHA256]
    model_request_sha256: SHA256 | None = None
    raw_response_sha256: SHA256 | None = None
    request_path: str | None = None
    response_path: str | None = None
    procedure_sha256: SHA256


class StageManifest(Strict):
    experiment_id: Nonempty
    protocol_sha256: SHA256
    source_pdf_sha256: SHA256
    selection_freeze_sha256: SHA256
    development_examples_sha256: list[SHA256]
    development_issuer_ids: list[Nonempty]
    evaluation_issuer_id: Nonempty
    full_document_page_count: int
    structure_reviewed_pages: list[int]
    generated_schema_validated: bool
    generated_prompt_safety_reviewed: bool
    artifacts: list[Artifact]


def verify_stages(manifest: StageManifest, root: Path) -> dict:
    """No transport, regeneration, repairs or stage file writes are performed."""
    if manifest.evaluation_issuer_id in manifest.development_issuer_ids:
        raise ValueError("Prompt examples overlap the evaluation issuer")
    if manifest.full_document_page_count < 1 or manifest.structure_reviewed_pages != list(
        range(1, manifest.full_document_page_count + 1)
    ):
        raise ValueError("Full-document reconnaissance requires every physical PDF page")
    if not manifest.generated_schema_validated or not manifest.generated_prompt_safety_reviewed:
        raise ValueError("Generated schema and prompt require validation before extraction")
    if tuple(a.stage for a in manifest.artifacts) != STAGES:
        raise ValueError("Exactly the five ordered stages must be preserved")
    root = root.resolve()
    available = {manifest.protocol_sha256, manifest.source_pdf_sha256,
                 manifest.selection_freeze_sha256, *manifest.development_examples_sha256}
    previous = None
    def verify_file(relative_name: str, expected: str) -> None:
        relative = Path(relative_name)
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root):
            raise ValueError("Stage paths must remain inside the new run directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Recorded stage/request/response artifact changed")

    for artifact in manifest.artifacts:
        if not artifact.input_sha256 or not set(artifact.input_sha256) <= available:
            raise ValueError("Stage input hashes are missing or outside recorded lineage")
        if previous and previous not in artifact.input_sha256:
            raise ValueError("Stage must consume the immediately preceding stage artifact")
        if artifact.stage == "structure" and manifest.source_pdf_sha256 not in artifact.input_sha256:
            raise ValueError("Document structure stage must consume the PDF")
        if artifact.stage in {"structure", "schema", "generated_prompt", "raw_extraction"}:
            if (artifact.model_request_sha256 is None or artifact.raw_response_sha256 is None
                    or not artifact.request_path or not artifact.response_path):
                raise ValueError("Model stages require request and raw response fingerprints")
            verify_file(artifact.request_path, artifact.model_request_sha256)
            verify_file(artifact.response_path, artifact.raw_response_sha256)
        verify_file(artifact.path, artifact.sha256)
        previous = artifact.sha256
        available.add(previous)
    return {"status": "verified", "stages": list(STAGES), "network_calls": 0,
            "interpretation": "Hash/lineage gates only; not proof of semantic stage quality or human review"}
