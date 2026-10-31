"""Read-only V6 survey/structure import, then NEW actual-CSV schema/prompt stages."""
from __future__ import annotations
import ast
import base64
import hashlib
import json
from pathlib import Path

from . import prompts
from .artifacts import (call, digest, file_hash, lock_directory, payload, procedure_hash,
                        read_json, response_content, source_image, write_json, default_renderer)
from .contracts import GeneratedPrompt, SchemaPlan, strict_schema, description_diagnostics

V6_PROCEDURE = "f255fad79eccfc9f941457a9e1821ff0e326ad8b890cb2319a7ac6c80513be7b"


def archive_directory():
    return Path(__file__).resolve().parents[3] / "implementations/pipeline-v6-f255fad7"


def _archive_prompts():
    archive = archive_directory()
    receipt = read_json(archive / "preservation-receipt.json")
    if receipt["procedure_sha256"] != V6_PROCEDURE:
        raise ValueError("Unexpected V6 preservation receipt")
    for name, expected in receipt["file_sha256"].items():
        if file_hash(archive / name) != expected:
            raise ValueError("Archived V6 source changed")
    source_dir = archive / "src/ftv_reconstruction"
    if digest({p.name: file_hash(p) for p in sorted(source_dir.glob("*.py"))}) != V6_PROCEDURE:
        raise ValueError("Archived V6 procedure fingerprint differs")
    found = {}
    for node in ast.parse((source_dir / "prompts.py").read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"SOURCE_ONLY", "SURVEY", "STRUCTURE"}:
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found


def _stage_origin(directory, lineage=None, seen=None):
    directory = Path(directory)
    lineage = {} if lineage is None else lineage
    seen = set() if seen is None else seen
    if directory.resolve() in seen:
        raise ValueError("Cyclic imported stage lineage")
    seen.add(directory.resolve())
    origin = None
    if (directory / "import-origin.json").exists():
        lineage[str(directory / "import-origin.json")] = file_hash(directory / "import-origin.json")
        origin = read_json(directory / "import-origin.json")
        for filename, expected in origin.get("files", {}).items():
            if file_hash(filename) != expected:
                raise ValueError("Imported origin artifact changed")
            lineage[filename] = expected
    if ((directory / "request.json").exists() and (directory / "response.json").exists()
            and not (directory / "rejection.json").exists()):
        return directory
    candidates = [p for p in sorted(directory.glob("attempt-*"))
                  if (p / "response.json").exists() and not (p / "rejection.json").exists()]
    if len(candidates) == 1:
        return _stage_origin(candidates[0], lineage, seen)
    if not candidates and origin and origin.get("status") == "verified_stage_import":
        return _stage_origin(Path(origin["request"]).parent, lineage, seen)
    raise ValueError("Stage requires exactly one preserved accepted response")


def _old_stage(directory, expected, snapshot, system, name):
    lineage = {}
    origin = _stage_origin(directory, lineage)
    request, response, record = [read_json(origin / n) for n in ("request.json", "response.json", "call.json")]
    if (record.get("request_file_sha256") != file_hash(origin / "request.json")
            or record.get("request_body_sha256") != digest(request)
            or record.get("response_file_sha256") != file_hash(origin / "response.json")
            or record.get("response_sha256") != digest(response)):
        raise ValueError("V6 origin actual request/response fingerprints disagree")
    parsed = json.loads(response_content(response))
    if parsed != expected or read_json(origin / "parsed.json") != expected:
        raise ValueError("V6 derived stage differs from actual model response")
    if request["messages"][0] != {"role": "system", "content": system}:
        raise ValueError("V6 survey/structure instruction changed")
    for key, value in (("model", snapshot["preparation_model"]), ("temperature", snapshot["temperature"]),
                       ("seed", snapshot["seed"]), ("max_tokens", snapshot["limits"][0 if name == "document_survey_chunk" else 1])):
        if request.get(key) != value:
            raise ValueError("V6 origin generator settings differ")
    provider = snapshot["preparation_provider"]
    if provider and request.get("provider") != {"order": [provider], "allow_fallbacks": False, "require_parameters": True}:
        raise ValueError("V6 provider pin changed")
    reason = snapshot["preparation_reasoning_max_tokens"]
    actual_reason = request.get("reasoning")
    expected_reason = {"max_tokens": reason} if reason is not None and snapshot["preparation_model"].startswith("google/gemini") else None
    if actual_reason != expected_reason or request["response_format"]["json_schema"]["name"] != name:
        raise ValueError("V6 reasoning/schema stage differs")
    files = {**lineage, **{str(origin / n): file_hash(origin / n) for n in ("request.json", "response.json", "call.json", "parsed.json")}}
    return request, files


def import_source_stages(config):
    """No transport parameter exists: stage import cannot make a model call."""
    root = config.source_prepared_dir
    expected_files = {"snapshot.json": config.source_snapshot_sha256,
                      "survey.json": config.source_survey_sha256, "structure.json": config.source_structure_sha256}
    if config.source_prepared_sha256:
        expected_files["prepared.json"] = config.source_prepared_sha256
    imports = {}
    for name, expected in expected_files.items():
        path = root / name
        if file_hash(path) != expected:
            raise ValueError("Pinned V6 stage file changed: " + name)
        imports[str(path)] = expected
    snapshot, survey, structure = [read_json(root / name) for name in ("snapshot.json", "survey.json", "structure.json")]
    if snapshot.get("procedure_sha256") != V6_PROCEDURE:
        raise ValueError("Only the archived V6 preparation version is accepted")
    for key, value in {"document_id": config.document_id, "issuer_id": config.issuer_id,
                       "pdf_sha256": config.pdf_sha256, "dpi": config.dpi,
                       "preparation_model": config.preparation_model, "preparation_provider": config.preparation_provider,
                       "temperature": config.temperature, "seed": config.seed,
                       "preparation_reasoning_max_tokens": config.preparation_reasoning_max_tokens}.items():
        if snapshot.get(key) != value:
            raise ValueError("V6 source/settings mismatch: " + key)
    if file_hash(config.pdf) != config.pdf_sha256:
        raise ValueError("Source PDF changed")
    count = snapshot["pdf_page_count"]
    coverage = list(range(1, count+1))
    if (survey["covered_pages"] != coverage or survey["full_document_page_count"] != count
            or structure["reviewed_pages"] != coverage):
        raise ValueError("Incomplete source survey/structure page coverage")
    if max(config.selected_pages + config.illustrative_pages) > count:
        raise ValueError("Requested source page beyond surveyed document")
    archive_prompts = _archive_prompts()
    covered = []
    image_records = {r["page"]: r for r in survey["image_provenance"]}
    if sorted(image_records) != coverage or len(survey["image_provenance"]) != count:
        raise ValueError("Survey image coverage duplicated/missing")
    for chunk in survey["chunks"]:
        pages = [p["page"] for p in chunk["pages"]]
        if not pages:
            raise ValueError("Empty survey chunk")
        covered.extend(pages)
        request, files = _old_stage(root / "survey" / f"chunk-{pages[0]:04d}", chunk, snapshot,
                                   archive_prompts["SOURCE_ONLY"] + archive_prompts["SURVEY"], "document_survey_chunk")
        imports.update(files)
        content = request["messages"][1]["content"]
        if json.loads(content[0]["text"]) != {"physical_pages": pages, "document_id": config.document_id}:
            raise ValueError("Survey request source-page selection differs")
        if len(content) != 1 + 2*len(pages):
            raise ValueError("Survey request image count differs")
        for index, page in enumerate(pages):
            record = image_records[page]
            if record["pdf_sha256"] != config.pdf_sha256 or record["dpi"] != config.dpi:
                raise ValueError("Survey image PDF/DPI differs")
            if content[1+2*index] != {"type": "text", "text": f"Physical source PDF page {page}"}:
                raise ValueError("Survey image page ordering differs")
            url = content[2+2*index]["image_url"]["url"]
            prefix = f"data:{record.get('mime_type', 'image/png')};base64,"
            if not url.startswith(prefix):
                raise ValueError("Survey image encoding differs")
            actual = base64.b64decode(url[len(prefix):], validate=True)
            if hashlib.sha256(actual).hexdigest() != record["image_sha256"]:
                raise ValueError("Actual request image hash differs")
            for path_key, hash_key in (("image_file", "image_sha256"), ("source_png_file", "source_png_sha256")):
                if record.get(path_key):
                    if file_hash(record[path_key]) != record[hash_key]:
                        raise ValueError("Preserved survey image changed")
                    imports[record[path_key]] = record[hash_key]
    if covered != coverage:
        raise ValueError("Actual survey calls do not cover every page in order")
    request, files = _old_stage(root / "structure", structure, snapshot,
                               archive_prompts["SOURCE_ONLY"] + archive_prompts["STRUCTURE"], "document_structure")
    imports.update(files)
    if json.loads(request["messages"][1]["content"][0]["text"]) != survey:
        raise ValueError("Actual structure request used a different survey")
    return {"survey": survey, "structure": structure, "origin_snapshot": snapshot,
            "files_sha256": imports, "lineage": "actual V6 survey/structure only; no old CSV schema/prompt reuse"}


def preparation_snapshot(config, imported):
    examples = []
    for example in config.examples:
        if file_hash(example.data_file) != example.data_sha256:
            raise ValueError("CSV development example changed")
        if example.image and file_hash(example.image.path) != example.image.sha256:
            raise ValueError("Development image changed")
        examples.append(example.model_dump(mode="json"))
    return {"document_id": config.document_id, "issuer_id": config.issuer_id, "pdf_sha256": config.pdf_sha256,
            "dpi": config.dpi, "procedure_sha256": procedure_hash(), "model": config.preparation_model,
            "provider": config.preparation_provider, "temperature": config.temperature, "seed": config.seed,
            "reasoning": config.preparation_reasoning_max_tokens, "limits": [config.schema_max_tokens, config.prompt_max_tokens],
            "source_stage_files": imported["files_sha256"], "illustrative_pages": config.illustrative_pages,
            "illustrative_images": {str(p): config.selected_images[p].sha256 for p in config.illustrative_pages if p in config.selected_images},
            "examples": examples, "protocol_sha256": config.protocol_sha256, "selection_sha256": config.selection_sha256}


def validate_prepared(config, prepared):
    imported = import_source_stages(config)
    snapshot = preparation_snapshot(config, imported)
    if prepared.get("fingerprint") != digest(snapshot) or prepared.get("snapshot") != snapshot:
        raise ValueError("CSV preparation inputs changed")
    for name, expected in prepared["preserved_artifacts"].items():
        if file_hash(config.prepared_dir / name) != expected:
            raise ValueError("CSV preparation artifact changed")
    return imported


def prepare_document(config, transport, *, renderer=default_renderer):
    imported = import_source_stages(config)
    snapshot = preparation_snapshot(config, imported)
    destination = config.prepared_dir
    with lock_directory(destination):
        final = destination / "prepared.json"
        if final.exists():
            prepared = read_json(final)
            validate_prepared(config, prepared)
            return prepared
        saved = destination / "snapshot.json"
        if saved.exists() and read_json(saved) != snapshot:
            raise ValueError("Incomplete CSV preparation inputs changed; use a new version")
        write_json(saved, snapshot)
        write_json(destination / "source-stage-import.json", imported)
        plan, schema_call = call(destination / "schema", payload(config, preparation=True,
            instruction=prompts.SCHEMA, task={"structure": imported["structure"], "contract": prompts.ROLE_POLICY},
            schema=strict_schema(SchemaPlan), max_tokens=config.schema_max_tokens, name="generated_csv_schema"), transport, SchemaPlan)
        plan_data = plan.model_dump(mode="json")
        write_json(destination / "generated-schema-plan.json", plan_data)
        write_json(destination / "administrative-self-attestations.json", {
            "authoritative": False, "compliance_proven": False,
            "schema_no_numeric_repair": plan.no_numeric_repair,
            "policy": "Observed model echoes only; immutable request instructions and code govern behavior"})
        write_json(destination / "schema-description-diagnostics.json", description_diagnostics(plan))
        illustrations = [source_image(config, p, destination / "images", renderer)[0] for p in config.illustrative_pages]
        examples, example_images = [], []
        for example in config.examples:
            examples.append({"issuer_id": example.issuer_id, "review_kind": example.review_kind,
                             "data": read_json(example.data_file), "data_sha256": example.data_sha256})
            if example.image:
                example_images.append((0, example.image.path.read_bytes()))
        def validate_prompt(prompt):
            if prompt.schema_plan_sha256 != digest(plan_data):
                raise ValueError("Generated prompt refers to a different CSV schema")
            audit = prompts.safety_audit(prompt.instructions)
            write_json(destination / "generated-prompt-safety-audit.json", audit)
            if not audit["passed"]:
                raise ValueError("Generated prompt contains an unnegated numeric-repair directive")
        generated, prompt_call = call(destination / "generated-prompt", payload(config, preparation=True,
            instruction=prompts.GENERATE_PROMPT, task={"structure": imported["structure"], "generated_schema": plan_data,
                "schema_plan_sha256": digest(plan_data), "development_csv_examples": examples,
                "example_status": "reviewed development-only" if examples else "none; no few-shot example benefit claim"},
            schema=strict_schema(GeneratedPrompt), max_tokens=config.prompt_max_tokens, name="generated_csv_prompt",
            images=list(zip(config.illustrative_pages, illustrations)) + example_images),
            transport, GeneratedPrompt, validate_prompt)
        write_json(destination / "generated-prompt.json", generated.model_dump(mode="json"))
        write_json(destination / "administrative-self-attestations.json", {
            "authoritative": False, "compliance_proven": False,
            "schema_no_numeric_repair": plan.no_numeric_repair,
            "prompt_source_only": generated.source_only,
            "prompt_changes_numbers_allowed": generated.changes_numbers_allowed,
            "policy": "Observed model echoes only; immutable request instructions, schema linkage and lexical audit remain authoritative gates"})
        files = {str(p.relative_to(destination)): file_hash(p) for p in sorted(destination.rglob("*"))
                 if p.is_file() and p.name not in {".lock", "prepared.json"}}
        prepared = {"status": "complete", "fingerprint": digest(snapshot), "snapshot": snapshot,
                    "preserved_artifacts": files, "calls": [schema_call, prompt_call],
                    "generator_origin": {"model": config.preparation_model, "provider": config.preparation_provider},
                    "source_stages_reused": imported["lineage"]}
        write_json(final, prepared)
        return prepared
