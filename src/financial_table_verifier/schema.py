"""Strict interchange contracts shared by predictions and independent references."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

Index = Annotated[int, Field(ge=0)]
# Gemini rejects integer enums in response schemas; validation still enforces the set.
Scale = Annotated[
    Literal[1, 1000, 1000000, 1000000000] | None,
    WithJsonSchema(
        {
            "anyOf": [{"type": "integer"}, {"type": "null"}],
            "description": "One of 1, 1000, 1000000, 1000000000, or null",
        }
    ),
]
Concept = Literal[
    "total_assets",
    "total_liabilities",
    "total_equity",
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_expenses",
    "operating_income",
    "income_before_tax",
    "income_tax_expense",
    "net_income",
    "basic_numerator",
    "diluted_numerator",
    "basic_shares",
    "diluted_shares",
    "basic_eps",
    "diluted_eps",
    "cash_beginning",
    "cash_ending",
    "cash_net_change",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cell(StrictModel):
    column_index: Index
    raw_value: str
    quantity: Literal["money", "shares", "per_share", "ratio", "unknown"]
    scale: Scale
    currency: str | None


class Column(StrictModel):
    column_index: Index
    label: str
    period: str | None


class Row(StrictModel):
    row_index: Index
    label: str
    path: list[str]
    role: Literal["header", "data", "total"]
    concept: Concept | None
    cells: list[Cell]


class Table(StrictModel):
    table_index: Index
    title: str
    statement: Literal["balance_sheet", "income_statement", "cash_flow", "eps", "other"]
    scope: str
    continuation_of: str | None
    columns: list[Column]
    rows: list[Row]

    @model_validator(mode="after")
    def unique_indices(self) -> Self:
        columns = [c.column_index for c in self.columns]
        rows = [r.row_index for r in self.rows]
        if columns != list(range(len(columns))) or rows != list(range(len(rows))):
            raise ValueError("Columns and rows must have consecutive zero-based indices")
        for row in self.rows:
            cells = [c.column_index for c in row.cells]
            if len(cells) != len(set(cells)) or not set(cells) <= set(columns):
                raise ValueError("Cells must reference unique declared columns")
        return self


class PageExtraction(StrictModel):
    page: Annotated[int, Field(ge=1)]
    tables: list[Table]

    @model_validator(mode="after")
    def unique_tables(self) -> Self:
        if [t.table_index for t in self.tables] != list(range(len(self.tables))):
            raise ValueError("Tables must have consecutive zero-based indices")
        return self


class Document(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")]
    pdf: Path
    pages: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1)]
    split: Literal["pilot", "development", "test"]
    source_url: str | None = None
    accession: str | None = None
    eps_eligible_tables: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def sorted_pages(self) -> Self:
        if self.pages != sorted(set(self.pages)):
            raise ValueError("pages must be unique and sorted")
        return self


class Manifest(StrictModel):
    documents: Annotated[list[Document], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_documents(self) -> Self:
        ids = [d.id for d in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("Document IDs must be unique")
        return self


class Experiment(StrictModel):
    model: Annotated[str, Field(min_length=3)]
    provider: str | None = None
    context: Literal["sequential", "independent"] = "sequential"
    dpi: Annotated[int, Field(ge=72, le=400)] = 300
    temperature: Annotated[float, Field(ge=0, le=2)] = 0
    seed: int | None = 42
    use_seed: bool = True
    max_tokens: Annotated[int, Field(ge=128)] = 12000
    reasoning_max_tokens: Annotated[int, Field(ge=128)] | None = None
    timeout_seconds: Annotated[float, Field(gt=0)] = 120
    max_attempts: Annotated[int, Field(ge=1, le=5)] = 3
    max_requests: Annotated[int, Field(ge=1)] = 20
    max_observed_cost_usd: Annotated[Decimal, Field(gt=0)] = Decimal("5")
    max_context_chars: Annotated[int, Field(ge=1000)] = 100000
    max_image_pixels: Annotated[int, Field(ge=100000)] = 25000000
    relative_tolerance: Annotated[Decimal, Field(ge=0, le=1)] = Decimal("0.005")
    absolute_tolerance: Annotated[Decimal, Field(ge=0)] = Decimal("0.01")
    manifest: Path
    output: Path

    @model_validator(mode="after")
    def reasoning_leaves_room(self) -> Self:
        if self.reasoning_max_tokens is not None and self.reasoning_max_tokens >= self.max_tokens:
            raise ValueError("reasoning_max_tokens must be below max_tokens")
        return self


class Gold(StrictModel):
    document_id: str
    pdf_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    annotator: Annotated[str, Field(min_length=1)]
    reviewed: Literal[True]
    pages: Annotated[list[PageExtraction], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_pages(self) -> Self:
        pages = [p.page for p in self.pages]
        if pages != sorted(set(pages)):
            raise ValueError("Gold pages must be unique and sorted")
        return self
