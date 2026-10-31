"""Source-preserving geometry -> logical/numeric record adapters, without gold input."""

from __future__ import annotations

from .contracts import SourceGrid
from .model_contracts import GeometryPage


def logical_page(page: GeometryPage) -> dict:
    """Keep exact source cell text and original financial metadata before cleaning."""
    tables = []
    for table in page.tables:
        by_id = {c.source_cell_id: c for c in table.grid.cells}
        rows = []
        for row in table.rows:
            labels = [by_id[ident] for ident in row.label_cell_ids]
            label = " ".join(c.text for c in sorted(labels, key=lambda c: c.column) if c.text)
            cells = []
            for source in table.grid.cells:
                if source.row != row.row_index or source.source_cell_id in row.label_cell_ids:
                    continue
                # One physical amount spanning numeric columns is retained once,
                # and marked ambiguous rather than duplicated into invented cells.
                columns = [c for c in table.columns
                           if source.column <= c.grid_column < source.column + source.colspan]
                if not columns or source.kind in {"header", "blank"}:
                    continue
                column = columns[0]
                cells.append({"source_cell_id": f"p{page.page}-t{table.table_index}:{source.source_cell_id}",
                    "column_index": column.column_index, "raw_value": source.text,
                    "quantity": source.quantity, "scale": source.scale,
                    "currency": source.currency, "period": column.period,
                    "physical_row": source.row, "physical_column": source.column,
                    "rowspan": source.rowspan, "colspan": source.colspan,
                    "column_alignment_ambiguous": len(columns) != 1})
            rows.append({"row_id": f"p{page.page}-t{table.table_index}-r{row.row_index}",
                "row_index": row.row_index, "label": label, "role": row.role,
                "path": list(row.path), "concept": row.concept, "cells": cells})
        tables.append({"table_id": f"p{page.page}-t{table.table_index}",
            "table_index": table.table_index, "title": table.title, "statement": table.statement,
            "scope": table.scope, "continuation_of": table.continuation_of,
            "columns": [{"column_index": c.column_index, "grid_column": c.grid_column,
                         "label": c.label, "period": c.period} for c in table.columns],
            "rows": rows})
    return {"page": page.page, "tables": tables}


def numeric_records(document_id: str, logical: dict) -> list[dict]:
    records = []
    for table in logical["tables"]:
        columns = {c["column_index"]: c for c in table["columns"]}
        for row in table["rows"]:
            for cell in row["cells"]:
                column = columns[cell["column_index"]]
                records.append({"document_id": document_id, "page": logical["page"],
                    "table_id": table["table_id"], "table_index": table["table_index"],
                    "row_id": row["row_id"], "row_index": row["row_index"],
                    "row_label": row["label"], "row_context": row["path"],
                    "role": row["role"], "concept": row["concept"],
                    "statement": table["statement"], "scope": table["scope"],
                    "column_label": column["label"], **cell})
    return records


def canonical_grids(page: GeometryPage, *, pdf_sha256: str, image_sha256: str, dpi: int) -> list[SourceGrid]:
    return [table.grid.source_grid(pdf_sha256=pdf_sha256, image_sha256=image_sha256,
                                  page=page.page, dpi=dpi, table_id=f"p{page.page}-t{table.table_index}")
            for table in page.tables]


def validate_continuations(page: GeometryPage, previous: GeometryPage | None) -> None:
    parents = {f"p{previous.page}-t{table.table_index}": table for table in previous.tables} if previous else {}
    used = set()
    for table in page.tables:
        if table.continuation_of is None:
            continue
        parent = parents.get(table.continuation_of)
        if parent is None or table.continuation_of in used or previous.page != page.page - 1:
            raise ValueError("Continuation must reference one unused table on the immediately preceding accepted page")
        if (parent.statement, parent.scope) != (table.statement, table.scope):
            raise ValueError("Continuation changes statement or reporting entity scope")
        if [(c.label, c.period) for c in parent.columns] != [(c.label, c.period) for c in table.columns]:
            raise ValueError("Continuation column labels/reporting intervals differ")
        used.add(table.continuation_of)
