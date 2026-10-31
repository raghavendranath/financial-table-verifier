"""Model-only redundant metadata derivation; never repair a cell or geometry."""
import copy
import hashlib
import json


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _normalize_geometry(value):
    data = copy.deepcopy(value)
    if not isinstance(data, dict) or not isinstance(data.get("tables"), list):
        raise ValueError("Model geometry requires a table array")
    derived, before_cells, after_cells, before_rows, after_rows, before_columns, after_columns = [], [], [], [], [], [], []
    unchanged_fields = ("source_cell_id", "row", "column", "rowspan", "colspan", "text", "quantity", "scale", "currency", "box")
    for ordinal, table in enumerate(data["tables"]):
        original = value["tables"][ordinal]
        if "table_index" in table and table["table_index"] != ordinal:
            raise ValueError("Supplied canonical table index contradicts table-array order")
        table["table_index"] = ordinal
        numeric_ordinals = []
        for index, column in enumerate(table["columns"]):
            if "column_index" in column and column["column_index"] != index:
                raise ValueError("Supplied canonical numeric column index contradicts columns-array order")
            column["column_index"] = index
            numeric_ordinals.append({"array_position": index, "derived_column_index": index,
                                     "unchanged_grid_column": column["grid_column"]})
        before_columns.append([{key: val for key, val in column.items() if key != "column_index"} for column in original["columns"]])
        after_columns.append([{key: val for key, val in column.items() if key != "column_index"} for column in table["columns"]])
        grid = table["grid"]
        cells = grid["cells"]
        for cell in cells:
            if (not isinstance(cell.get("text"), str) or any(type(cell.get(k)) is not int for k in ("row", "column", "rowspan", "colspan"))
                    or cell["row"] < 0 or cell["column"] < 0 or cell["rowspan"] < 1 or cell["colspan"] < 1):
                raise ValueError("Cell coordinates/spans/text must already be valid model values")
        rows = max((cell["row"] + cell["rowspan"] for cell in cells), default=0)
        columns = max((cell["column"] + cell["colspan"] for cell in cells), default=0)
        scope_resets = []
        for cell in cells:
            kind = "blank" if cell["text"] == "" else "header" if cell.get("header_scope") is not None else "data"
            if "kind" in cell and cell["kind"] != kind:
                raise ValueError("Supplied canonical cell kind contradicts emitted text/header scope")
            cell["kind"] = kind
            if kind == "blank" and cell.get("header_scope") is not None:
                scope_resets.append(cell["source_cell_id"])
                cell["header_scope"] = None
        header_rows = {row for row in range(rows) if any(
            cell["row"] <= row < cell["row"] + cell["rowspan"] and cell.get("header_scope") in {"column", "both"}
            for cell in cells)}
        leading = 0
        while leading in header_rows:
            leading += 1
        sections = ["head"] * leading + ["body"] * (rows - leading)
        for field, calculated in (("row_count", rows), ("column_count", columns), ("row_sections", sections)):
            if field in grid and grid[field] != calculated:
                raise ValueError("Supplied canonical " + field + " contradicts emitted cell topology")
            grid[field] = calculated
        supplied = table["rows"]
        indices = [row["row_index"] for row in supplied]
        if indices != sorted(set(indices)) or any(type(index) is not int or not 0 <= index < rows for index in indices):
            raise ValueError("Semantic body row indices must be unique, ordered and within emitted geometry")
        by_index = {row["row_index"]: row for row in supplied}
        defaults = []
        for row in range(rows):
            if row not in by_index:
                if row >= leading:
                    raise ValueError("Missing semantic metadata for an emitted BODY row; no body row is invented")
                defaults.append(row)
                by_index[row] = {"row_index": row, "label_cell_ids": [], "role": "header", "path": [], "concept": None}
        table["rows"] = [by_index[row] for row in range(rows)]
        before_cells.append([{key: cell.get(key) for key in unchanged_fields} for cell in original["grid"]["cells"]])
        after_cells.append([{key: cell.get(key) for key in unchanged_fields} for cell in cells])
        before_rows.append([row for row in original["rows"] if row["row_index"] >= leading])
        after_rows.append([row for row in table["rows"] if row["row_index"] >= leading])
        derived.append({"table_index": ordinal, "row_count": rows, "column_count": columns,
                        "numeric_column_ordinals": numeric_ordinals,
                        "leading_column_header_rows": leading, "row_sections": sections,
                        "default_header_semantic_rows": defaults, "empty_cell_header_scope_resets": scope_resets,
                        "cell_kinds": {cell["source_cell_id"]: cell["kind"] for cell in cells}})
    cells_same, body_same, columns_same = before_cells == after_cells, before_rows == after_rows, before_columns == after_columns
    if not cells_same or not body_same or not columns_same:
        raise ValueError("Normalization attempted to change protected model cell/body content")
    return data, {"policy": "model-only-redundant-metadata-v1", "raw_model_object_sha256": _digest(value),
        "normalized_object_sha256": _digest(data), "derived_metadata": derived,
        "unchanged_cell_text_ids_coordinates_spans_units_sha256": _digest(before_cells),
        "unchanged_body_rows_sha256": _digest(before_rows),
        "unchanged_physical_columns_labels_periods_sha256": _digest(before_columns),
        "physical_columns_labels_periods_unchanged": columns_same,
        "cell_text_coordinates_spans_units_unchanged": cells_same, "body_metadata_unchanged": body_same,
        "cells_added": 0, "cells_removed": 0, "used_source_or_gold": False,
        "interpretation": "Only counts, header bands, cell kinds, array table IDs and missing column-header metadata derived; true grid/column/body errors still rejected"}


def normalize_geometry(value):
    """Malformed required JSON shape is a schema rejection, never a repair."""
    try:
        return _normalize_geometry(value)
    except (KeyError, IndexError, AttributeError) as exc:
        raise ValueError("Malformed required model geometry shape: " + type(exc).__name__) from exc
