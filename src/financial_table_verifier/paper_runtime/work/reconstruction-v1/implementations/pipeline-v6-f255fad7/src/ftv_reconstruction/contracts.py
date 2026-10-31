"""Physical source geometry, not a projection of the historical logical rows."""

from __future__ import annotations

from html import escape
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Nonempty = Annotated[str, Field(min_length=1)]
Index = Annotated[int, Field(ge=0)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Box(Strict):
    """Normalized physical page coordinates: left/top/right/bottom, origin top-left."""

    left: Annotated[float, Field(ge=0, le=1)]
    top: Annotated[float, Field(ge=0, le=1)]
    right: Annotated[float, Field(ge=0, le=1)]
    bottom: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("Physical box must have positive width and height")
        return self

    def contains(self, other: Box) -> bool:
        return (self.left <= other.left and self.top <= other.top
                and self.right >= other.right and self.bottom >= other.bottom)


class SourceEvidence(Strict):
    pdf_sha256: SHA256
    page: Annotated[int, Field(ge=1)]
    quote: Nonempty
    box: Box | None = None


class GridCell(Strict):
    source_cell_id: Nonempty
    row: Index
    column: Index
    rowspan: Annotated[int, Field(ge=1)]
    colspan: Annotated[int, Field(ge=1)]
    text: str
    kind: Literal["header", "data", "blank"]
    header_scope: Literal["row", "column", "both"] | None
    box: Box | None = None

    @model_validator(mode="after")
    def kind_consistent(self) -> Self:
        if self.kind == "blank" and self.text != "":
            raise ValueError("A physical blank must have empty text")
        if (self.kind == "header") != (self.header_scope is not None):
            raise ValueError("Only header cells carry explicit header scope")
        return self


class SourceGrid(Strict):
    pdf_sha256: SHA256
    page: Annotated[int, Field(ge=1)]
    image_sha256: SHA256
    dpi: Annotated[int, Field(ge=72, le=400)]
    table_id: Nonempty
    box: Box | None = None
    row_count: Annotated[int, Field(ge=1, le=1000)]
    column_count: Annotated[int, Field(ge=1, le=100)]
    row_sections: list[Literal["head", "body"]]
    cells: list[GridCell]
    provenance: Literal["source_image_annotation", "new_model_prediction"]

    @model_validator(mode="after")
    def exact_physical_grid(self) -> Self:
        if len(self.row_sections) != self.row_count:
            raise ValueError("Every physical row requires a head/body section")
        if "body" in self.row_sections and "head" in self.row_sections[self.row_sections.index("body"):]:
            raise ValueError("Header rows must precede body rows")
        occupied = set()
        ids = set()
        positions = []
        for cell in self.cells:
            if cell.source_cell_id in ids:
                raise ValueError("Duplicate physical cell ID")
            ids.add(cell.source_cell_id)
            positions.append((cell.row, cell.column))
            if cell.row + cell.rowspan > self.row_count or cell.column + cell.colspan > self.column_count:
                raise ValueError("Span extends beyond physical grid")
            if self.box is not None and cell.box is not None and not self.box.contains(cell.box):
                raise ValueError("Cell box lies outside physical table box")
            if len(set(self.row_sections[cell.row:cell.row + cell.rowspan])) != 1:
                raise ValueError("A rowspan cannot cross head/body sections")
            for row in range(cell.row, cell.row + cell.rowspan):
                for column in range(cell.column, cell.column + cell.colspan):
                    position = (row, column)
                    if position in occupied:
                        raise ValueError("Overlapping cell spans")
                    occupied.add(position)
        if positions != sorted(positions):
            raise ValueError("Cells must be in physical row-major order")
        if len(occupied) != self.row_count * self.column_count:
            raise ValueError("Uncovered physical slots require explicit blank cells")
        # Logical tiling does not excuse physically superimposed annotations.
        active = []
        for cell in sorted((c for c in self.cells if c.box is not None), key=lambda c: c.box.left):
            active = [other for other in active if other.box.right > cell.box.left]
            for other in active:
                if min(other.box.bottom, cell.box.bottom) > max(other.box.top, cell.box.top):
                    raise ValueError("Physical cell box interiors overlap")
            active.append(cell)
        return self


class LayoutReference(Strict):
    grid: SourceGrid
    annotator: Nonempty
    reviewer: str | None = None
    reviewed: bool = False
    annotation_instructions_sha256: SHA256
    prediction_blind: bool = False
    annotator_kind: Literal["agent_assisted", "human"] = "agent_assisted"
    reviewer_kind: Literal["agent_assisted", "human"] = "agent_assisted"
    prediction_blind_scope: Literal["before_new_predictions_only"] = "before_new_predictions_only"

    def require_scoreable(self) -> None:
        if self.grid.provenance != "source_image_annotation":
            raise ValueError("Reference must be annotated from source pixels")
        if not self.reviewed or not self.reviewer or not self.prediction_blind:
            raise ValueError("Source-layout review and prediction-blind annotation are required")


def grid_html(grid: SourceGrid) -> str:
    """Preserve observed spans/text in PubTabNet-style HTML. Does not calculate TEDS.

    Header/data cells use td as in PubTabNet; thead preserves header row groups.
    No spans or missing label/header columns are invented from historical JSON.
    """
    rows = []
    current = None
    for row, section in enumerate(grid.row_sections):
        if section != current:
            if current is not None:
                rows.append(f"</t{'head' if current == 'head' else 'body'}>")
            rows.append(f"<t{'head' if section == 'head' else 'body'}>")
            current = section
        rows.append("<tr>")
        for cell in grid.cells:
            if cell.row == row:
                attributes = ""
                if cell.rowspan != 1:
                    attributes += f' rowspan="{cell.rowspan}"'
                if cell.colspan != 1:
                    attributes += f' colspan="{cell.colspan}"'
                rows.append(f"<td{attributes}>{escape(cell.text)}</td>")
        rows.append("</tr>")
    rows.append(f"</t{'head' if current == 'head' else 'body'}>")
    return "<html><body><table>" + "".join(rows) + "</table></body></html>"


def historical_json_to_grid(_: dict) -> SourceGrid:
    raise ValueError("Historical JSON lacks physical spans/blank/header geometry; reannotation and new predictions required")


def teds_inputs(reference: LayoutReference, prediction: SourceGrid) -> dict:
    """Prepare a gated pair for a separately pinned official TEDS implementation.

    No home-grown tree score is labelled TEDS. Table matching, missing-table
    penalties, score averaging, structure_only and code revision must be frozen
    in the new protocol before any predictions. Header scope/geometry metadata
    are retained separately because TEDS does not score their semantic meaning.
    """
    reference.require_scoreable()
    source = reference.grid
    fields = ("pdf_sha256", "page", "image_sha256", "dpi", "table_id")
    if any(getattr(source, f) != getattr(prediction, f) for f in fields):
        raise ValueError("TEDS pair has different source image/table identity")
    if prediction.provenance != "new_model_prediction":
        raise ValueError("Prediction must be a new geometry-aware extraction")
    return {"reference_html": grid_html(source), "prediction_html": grid_html(prediction),
            "score": None, "status": "ready_for_pinned_official_TEDS",
            "interpretation": "No TEDS score computed; physical grid is not semantic row hierarchy"}
