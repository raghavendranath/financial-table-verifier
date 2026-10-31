"""Compact model-facing geometry and genuinely generated financial schemas."""

from __future__ import annotations

from typing import Annotated, Literal, Self
import re

from pydantic import Field, model_validator

from .contracts import Box, GridCell, Index, Nonempty, SourceGrid, Strict
from .units import VERSION, UNIT_POLICY, SCALE_DESCRIPTION, CURRENCY_DESCRIPTION, unit_text_contradictions, definition_equivalent, policy_equivalent
from .normalization import normalize_geometry

Statement = Literal["balance_sheet", "income_statement", "cash_flow", "eps", "equity", "other"]
Concept = Literal[
    "total_assets", "total_liabilities", "total_equity", "revenue", "cost_of_revenue",
    "gross_profit", "operating_expenses", "operating_income", "income_before_tax",
    "income_tax_expense", "net_income", "basic_numerator", "diluted_numerator",
    "basic_shares", "diluted_shares", "basic_eps", "diluted_eps", "cash_beginning",
    "cash_ending", "cash_net_change", "retained_earnings", "dividends",
    "preferred_dividends", "other_retained_movement",
]
Quantity = Literal["money", "shares", "per_share", "ratio", "unknown"]


class PageObservation(Strict):
    page: Annotated[int, Field(ge=1)]
    table_titles: list[str]
    statement_types: list[Statement]
    hierarchy_notes: str
    continuation_notes: str


class SurveyChunk(Strict):
    pages: list[PageObservation]
    document_notes: str


class StructurePlan(Strict):
    document_summary: Nonempty
    reviewed_pages: list[Annotated[int, Field(ge=1)]]
    statement_types: list[Statement]
    table_locations: list[PageObservation]
    hierarchy_guidance: str
    unit_and_eps_complexity_cautions: str


class CSVColumnDefinition(Strict):
    name: Literal["row_label", "ancestor_path", "row_role", "period", "raw_value", "quantity", "scale", "currency", "concept"]
    data_type: Literal["string", "string_array", "integer_or_null"]
    description: Nonempty


class TableArchetype(Strict):
    statement: Statement
    csv_columns: list[CSVColumnDefinition]
    supported_concepts: list[Concept]
    source_number_format_rules: Nonempty
    hierarchy_rules: Nonempty

    @model_validator(mode="after")
    def compatible_csv(self) -> Self:
        expected = {"row_label": "string", "ancestor_path": "string_array", "row_role": "string",
                    "period": "string", "raw_value": "string", "quantity": "string",
                    "scale": "integer_or_null", "currency": "string", "concept": "string"}
        found = {column.name: column.data_type for column in self.csv_columns}
        if len(found) != len(self.csv_columns) or found != expected:
            raise ValueError("Generated CSV schema must preserve all nine common lossless fields and types")
        if len(self.supported_concepts) != len(set(self.supported_concepts)):
            raise ValueError("Generated concepts must be unique")
        definitions = {column.name: column.description for column in self.csv_columns}
        if (not definition_equivalent(definitions["scale"], SCALE_DESCRIPTION, "scale")
                or not definition_equivalent(definitions["currency"], CURRENCY_DESCRIPTION, "currency")):
            raise ValueError("Generated scale/currency definitions must exactly preserve the immutable multiplicative-factor/ISO-currency contract")
        other_text = "\n".join(column.description for column in self.csv_columns if column.name not in {"scale", "currency"})
        contradictions = unit_text_contradictions(other_text + "\n" + self.source_number_format_rules + "\n" + self.hierarchy_rules)
        if contradictions:
            raise ValueError("Generated schema contradicts the unit contract: " + ", ".join(contradictions))
        return self


class SchemaPlan(Strict):
    archetypes: Annotated[list[TableArchetype], Field(min_length=1)]
    geometry_contract_acknowledged: Literal[True]
    no_numeric_repair: Literal[True]
    unit_contract_version: str

    @model_validator(mode="after")
    def unique_archetypes(self) -> Self:
        if self.unit_contract_version != VERSION:
            raise ValueError("Generated schema must explicitly acknowledge the immutable unit contract version")
        kinds = [x.statement for x in self.archetypes]
        if len(kinds) != len(set(kinds)) or "other" not in kinds:
            raise ValueError("Generated archetypes must be unique and retain an other fallback")
        return self


class GeneratedPrompt(Strict):
    instructions: Nonempty
    source_only: Literal[True]
    changes_numbers_allowed: Literal[False]
    schema_plan_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    unit_contract_version: str
    unit_policy: str

    @model_validator(mode="after")
    def compatible_units(self) -> Self:
        if self.unit_contract_version != VERSION or not policy_equivalent(self.unit_policy):
            raise ValueError("Generated prompt must preserve the exact immutable unit policy")
        contradictions = unit_text_contradictions(self.instructions)
        if contradictions:
            raise ValueError("Generated prompt contradicts unit semantics: " + ", ".join(contradictions))
        return self


class FinancialCell(GridCell):
    quantity: Quantity
    scale: Annotated[int | None, Field(description=SCALE_DESCRIPTION)]
    currency: Annotated[str | None, Field(description=CURRENCY_DESCRIPTION)]

    @model_validator(mode="after")
    def scale_supported(self) -> Self:
        if self.scale not in {None, 1, 1000, 1000000, 1000000000}:
            raise ValueError("Unsupported explicitly observed scale")
        if self.currency is not None and not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("Currency must be an uppercase three-letter ISO code, not a quantity or compound per-share unit")
        if self.quantity == "shares" and self.currency is not None:
            raise ValueError("Share quantity has no currency")
        if self.quantity == "per_share" and self.scale not in {None, 1}:
            raise ValueError("Per-share scale must be one or unknown, never a money-table multiplier")
        return self


class PhysicalGrid(Strict):
    box: Box | None = None
    row_count: Annotated[int, Field(ge=1, le=1000)]
    column_count: Annotated[int, Field(ge=1, le=100)]
    row_sections: list[Literal["head", "body"]]
    cells: list[FinancialCell]

    @model_validator(mode="after")
    def validate_physical(self) -> Self:
        self.source_grid(pdf_sha256="0" * 64, image_sha256="0" * 64, page=1, dpi=300, table_id="validation")
        return self

    def source_grid(self, *, pdf_sha256, image_sha256, page, dpi, table_id) -> SourceGrid:
        data = self.model_dump(mode="json")
        data["cells"] = [{k: v for k, v in cell.items() if k not in {"quantity", "scale", "currency"}}
                         for cell in data["cells"]]
        return SourceGrid.model_validate({**data, "pdf_sha256": pdf_sha256,
            "image_sha256": image_sha256, "page": page, "dpi": dpi,
            "table_id": table_id, "provenance": "new_model_prediction"})


class NumericColumn(Strict):
    column_index: Index
    grid_column: Index
    label: str
    period: str | None


class FinancialRow(Strict):
    row_index: Index
    label_cell_ids: list[str]
    role: Literal["header", "data", "total"]
    path: list[str]
    concept: Concept | None


class GeometryTable(Strict):
    table_index: Index
    title: str
    statement: Statement
    scope: str
    continuation_of: str | None
    grid: PhysicalGrid
    columns: list[NumericColumn]
    rows: list[FinancialRow]

    @model_validator(mode="after")
    def semantic_links(self) -> Self:
        if [r.row_index for r in self.rows] != list(range(self.grid.row_count)):
            raise ValueError("Every physical grid row requires semantic metadata")
        if [c.column_index for c in self.columns] != list(range(len(self.columns))):
            raise ValueError("Numeric column indices must be consecutive")
        physical = [c.grid_column for c in self.columns]
        if len(set(physical)) != len(physical) or any(c >= self.grid.column_count for c in physical):
            raise ValueError("Numeric columns must map to distinct existing physical columns")
        cells = {c.source_cell_id: c for c in self.grid.cells}
        for row in self.rows:
            if len(set(row.label_cell_ids)) != len(row.label_cell_ids):
                raise ValueError("Duplicate label-cell reference")
            for ident in row.label_cell_ids:
                cell = cells.get(ident)
                if cell is None or not cell.row <= row.row_index < cell.row + cell.rowspan:
                    raise ValueError("Row label must reference actual source cells covering that row")
                numeric_text = re.fullmatch(r"\s*(?:[A-Z]{3}\s*)?[$£€¥]?\s*\(?\s*[+\-\u2212]?\s*\d[\d,.\s]*(?:\(\d+\)|[a-z])?\s*\)?%?\s*", cell.text)
                if (self.grid.row_sections[row.row_index] == "body" and numeric_text
                        and any(column in physical for column in range(cell.column, cell.column + cell.colspan))):
                    raise ValueError("A numeric data cell cannot also be a row label")
        return self


class GeometryPage(Strict):
    page: Annotated[int, Field(ge=1)]
    tables: list[GeometryTable]

    @model_validator(mode="before")
    @classmethod
    def derive_model_metadata(cls, value):
        if isinstance(value, dict):
            return normalize_geometry(value)[0]
        return value

    @model_validator(mode="after")
    def table_order(self) -> Self:
        if [t.table_index for t in self.tables] != list(range(len(self.tables))):
            raise ValueError("Tables must be in consecutive source reading order")
        return self


def strict_schema(model: type[Strict]) -> dict:
    """Portable strict JSON response schema; optional fields are required nullable."""
    schema = model.model_json_schema()
    def visit(value):
        if isinstance(value, dict):
            value.pop("default", None)
            # Keep bool constraints in Pydantic validation; provider JSON-schema
            # subsets differ in support for non-string const/enum values.
            if value.get("type") == "boolean":
                value.pop("const", None)
            if value.get("type") == "object" and "properties" in value:
                value["additionalProperties"] = False
                value["required"] = list(value["properties"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(schema)
    return schema


def generated_extraction_schema(plan: SchemaPlan) -> dict:
    """The generated financial schema actually constrains the extraction response.

    Physical geometry stays fixed for cross-condition comparability. Generated
    archetypes select statement and concept enums and supply field descriptions;
    the exact plan and compiled response schema are separately persisted.
    """
    schema = strict_schema(GeometryPage)
    # Pixel localization is outside the original CSV method and this evaluation.
    # Preserve all physical row/column/span/cell targets without asking for or
    # spending output tokens on unscored, potentially conflicting coordinates.
    for model in ("PhysicalGrid", "FinancialCell"):
        schema["$defs"][model]["properties"]["box"] = {"type": "null"}
    for model, fields in (("PhysicalGrid", ("row_count", "column_count", "row_sections")),
                          ("FinancialCell", ("kind",)), ("GeometryTable", ("table_index",)),
                          ("NumericColumn", ("column_index",))):
        for field in fields:
            schema["$defs"][model]["properties"].pop(field)
            schema["$defs"][model]["required"].remove(field)
    kinds = [a.statement for a in plan.archetypes]
    concepts = sorted({c for a in plan.archetypes for c in a.supported_concepts})
    schema["$defs"]["GeometryTable"]["properties"]["statement"]["enum"] = kinds
    schema["$defs"]["FinancialRow"]["properties"]["concept"] = {
        "anyOf": ([{"type": "string", "enum": concepts}] if concepts else []) + [{"type": "null"}],
        "description": "Only source-matching concepts in the document-generated schema; otherwise null",
    }
    descriptions = "\n".join(f"{a.statement}: {a.source_number_format_rules}; {a.hierarchy_rules}" for a in plan.archetypes)
    schema["$defs"]["GeometryTable"]["description"] = descriptions
    return schema
