"""Model-generated CSV headers/order with unambiguous machine role meanings."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Nonempty = Annotated[str, Field(min_length=1)]
SHA = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Role = Literal["printed_value", "row_label", "ancestor_path", "row_role", "period",
               "quantity_kind", "scale", "currency", "source_column_ordinal", "source_column_label", "uninterpreted"]
REQUIRED_ROLES = {"printed_value", "row_label", "ancestor_path", "row_role", "period",
                  "quantity_kind", "scale", "currency", "source_column_ordinal", "source_column_label"}
ROLE_TYPES = {
    "printed_value": {"string"}, "row_label": {"string"}, "ancestor_path": {"json_array"},
    "row_role": {"string", "enum_string"}, "period": {"string"},
    "quantity_kind": {"enum_string"}, "scale": {"string", "integer_or_blank", "enum_string"},
    "currency": {"string", "enum_string"}, "source_column_ordinal": {"integer_or_blank"},
    "source_column_label": {"string"}, "uninterpreted": {"string"},
}


class CSVColumn(Strict):
    name: Nonempty
    role: Role
    data_type: Literal["string", "integer_or_blank", "json_array", "enum_string"]
    description: Nonempty

    @model_validator(mode="after")
    def compatible(self):
        if any(char in self.name for char in "\r\n"):
            raise ValueError("CSV column names cannot contain line breaks")
        if self.data_type not in ROLE_TYPES[self.role]:
            raise ValueError("Generated data type contradicts its explicit semantic role")
        return self


class Archetype(Strict):
    schema_id: Nonempty
    statement: Nonempty
    columns: Annotated[list[CSVColumn], Field(min_length=10, max_length=16)]
    source_number_format_rules: Nonempty
    hierarchy_rules: Nonempty

    @model_validator(mode="after")
    def roles(self):
        names = [c.name for c in self.columns]
        roles = [c.role for c in self.columns if c.role != "uninterpreted"]
        if len(names) != len(set(names)) or len(roles) != len(set(roles)) or set(roles) != REQUIRED_ROLES:
            raise ValueError("Unique generated names and exactly one column per required role are necessary")
        return self


class SchemaPlan(Strict):
    archetypes: Annotated[list[Archetype], Field(min_length=1)]
    contract_version: Nonempty
    no_numeric_repair: bool

    @model_validator(mode="after")
    def identifiers(self):
        ids = [a.schema_id for a in self.archetypes]
        if len(ids) != len(set(ids)) or not any(a.statement == "other" for a in self.archetypes):
            raise ValueError("Unique archetypes and an other fallback are necessary")
        return self


class GeneratedPrompt(Strict):
    instructions: Nonempty
    schema_plan_sha256: SHA
    source_only: bool
    changes_numbers_allowed: bool
    contract_version: Nonempty


class CSVTable(Strict):
    schema_id: str
    title: str
    scope: str | None
    csv: str


class PageEnvelope(Strict):
    tables: list[CSVTable]


class HTMLTable(Strict):
    title: str
    html: str


class HTMLEnvelope(Strict):
    tables: list[HTMLTable]


class ImageAsset(Strict):
    path: Path
    sha256: SHA


class CSVExample(Strict):
    issuer_id: Nonempty
    data_file: Path
    data_sha256: SHA
    image: ImageAsset | None = None
    reviewed: bool
    review_kind: Literal["agent_source_review", "human_source_review"]


class CSVConfig(Strict):
    experiment_id: Nonempty
    document_id: Nonempty
    issuer_id: Nonempty
    pdf: Path
    pdf_sha256: SHA
    selected_pages: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1)]
    selected_images: dict[int, ImageAsset] = {}
    illustrative_pages: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1)]
    examples: list[CSVExample] = []
    condition: Literal["independent", "sequential"]
    model: Nonempty
    provider: str | None = None
    preparation_model: str = "google/gemini-2.5-pro"
    preparation_provider: str | None = "google-ai-studio"
    temperature: float = 0.0
    seed: int | None = 42
    dpi: Literal[300] = 300
    max_image_pixels: int = 25000000
    max_context_chars: Literal[500000] = 500000
    schema_max_tokens: int = 6000
    prompt_max_tokens: int = 6000
    extraction_max_tokens: int = 32768
    html_max_tokens: int = 32768
    preparation_reasoning_max_tokens: int | None = 256
    extraction_reasoning_max_tokens: int | None = 256
    source_prepared_dir: Path
    source_prepared_sha256: SHA | None = None
    source_snapshot_sha256: SHA
    source_survey_sha256: SHA
    source_structure_sha256: SHA
    output: Path
    prepared_dir: Path
    protocol_sha256: SHA
    selection_sha256: SHA

    @model_validator(mode="after")
    def constraints(self):
        if self.selected_pages != sorted(set(self.selected_pages)):
            raise ValueError("Selected physical pages must be sorted and unique")
        if any(not e.reviewed or e.issuer_id == self.issuer_id for e in self.examples):
            raise ValueError("Reviewed CSV examples must be outside the evaluation issuer")
        if self.output.resolve() == self.prepared_dir.resolve():
            raise ValueError("Run and shared preparation directories must differ")
        if any(x <= 0 for x in (self.max_image_pixels, self.schema_max_tokens, self.prompt_max_tokens,
                                self.extraction_max_tokens, self.html_max_tokens)):
            raise ValueError("Limits must be positive")
        for model, reason, cap in ((self.preparation_model, self.preparation_reasoning_max_tokens,
                                  min(self.schema_max_tokens, self.prompt_max_tokens)),
                                 (self.model, self.extraction_reasoning_max_tokens,
                                  min(self.extraction_max_tokens, self.html_max_tokens))):
            if reason is not None and (reason <= 0 or (model.startswith("google/gemini") and reason >= cap)):
                raise ValueError("Reasoning limit must leave room for visible output")
        return self


def strict_schema(model):
    """All model fields required; nullable values carry unknowns explicitly."""
    schema = model.model_json_schema()
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(schema)
    # API generation guidance remains byte-identical to the historical request.
    # These echoes are retained as observations, never proof of compliance;
    # immutable instructions and audited transformations govern behavior.
    requested_echoes = ({"no_numeric_repair": True} if model is SchemaPlan else
                        {"source_only": True, "changes_numbers_allowed": False}
                        if model is GeneratedPrompt else {})
    for name, requested in requested_echoes.items():
        schema["properties"][name]["const"] = requested
    return schema


def description_diagnostics(plan):
    """Nonfatal narrative lint; machine roles/types, never exact prose, govern data."""
    warnings = []
    for archetype in plan.archetypes:
        for column in archetype.columns:
            text = column.description.casefold()
            pattern = (r"(?:numeric|numerical|number|amount)\s+(?:value|string)|parsed\s+(?:amount|number|value)"
                       if column.role == "quantity_kind" else None)
            if pattern:
                for match in re.finditer(pattern, text):
                    prefix = text[max(0, match.start()-50):match.start()]
                    if not re.search(r"\b(?:not|never|no|rather than|without)\b", prefix):
                        warnings.append({"schema_id": archetype.schema_id, "column": column.name,
                                         "kind": "possible_narrative_role_conflict", "fatal": False})
    return {"machine_roles_authoritative": True, "warnings": warnings}
