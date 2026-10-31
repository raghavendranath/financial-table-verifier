"""Audit declared row classification/column alignment, preserving every source value."""

from __future__ import annotations

from typing import Any, Literal
import copy
import re

from .contracts import Nonempty, SourceEvidence, Strict


class CleaningEdit(Strict):
    target: Nonempty
    field: Literal["role", "path", "column_index"]
    before: Any
    after: Any
    reason: Nonempty
    evidence: SourceEvidence


def audit_cleaning(before: dict, after: dict, edits: list[CleaningEdit]) -> dict:
    """Only raw JSON with stable table/row/source_cell_id keys is accepted.

    Permit role/path classification and column relocation of an unchanged cell.
    Labels, amounts, totals, scales, dates, currency, scope, order, and cell ownership
    are immutable. Missing, duplicated, added or repaired cells reject the stage.
    """
    observed = []

    def compare(left: Any, right: Any, path: str, row=False, cell=False) -> None:
        if type(left) is not type(right):
            raise ValueError(f"Cleaning changes type/value at {path}")
        if isinstance(left, dict):
            if left.keys() != right.keys():
                raise ValueError(f"Cleaning adds/removes fields at {path}")
            for key in left:
                allowed = (row and key in {"role", "path"}) or (cell and key == "column_index")
                if allowed and left[key] != right[key]:
                    observed.append((path, key, left[key], right[key]))
                else:
                    compare(left[key], right[key], f"{path}/{key}")
        elif isinstance(left, list):
            if len(left) != len(right):
                raise ValueError(f"Cleaning adds/removes records at {path}")
            for i, (old, new) in enumerate(zip(left, right)):
                is_row = path.endswith("/rows")
                is_cell = path.endswith("/cells")
                if is_cell and (not isinstance(old, dict) or not old.get("source_cell_id")):
                    raise ValueError("Every raw cell requires a stable source_cell_id")
                compare(old, new, f"{path}/{i}", row=is_row, cell=is_cell)
        elif left != right:
            raise ValueError(f"Cleaning changes protected source data at {path}")

    compare(before, after, "")
    # Validate the raw and cleaned interchange shape in addition to comparing bytes.
    for extraction in (before, after):
        ids = set()
        for table in extraction["tables"]:
            columns = {c["column_index"] for c in table["columns"]}
            for row in table["rows"]:
                if row["role"] not in {"header", "data", "total"}:
                    raise ValueError("Invalid row classification")
                if not isinstance(row["path"], list) or not all(isinstance(x, str) for x in row["path"]):
                    raise ValueError("Row path must be a list of labels")
                occupied = set()
                for cell in row["cells"]:
                    ident = cell["source_cell_id"]
                    if ident in ids:
                        raise ValueError("Duplicate physical source_cell_id")
                    ids.add(ident)
                    if cell["column_index"] not in columns or cell["column_index"] in occupied:
                        raise ValueError("Undeclared or duplicate destination column")
                    occupied.add(cell["column_index"])
    declared = [(e.target, e.field, e.before, e.after) for e in edits]
    if sorted(observed, key=repr) != sorted(declared, key=repr):
        raise ValueError("Cleaning edit log must describe every change exactly once")
    return {"status": "verified", "edit_count": len(edits), "numbers_changed": 0,
            "interpretation": "Audited transformations only; source alignment still requires review"}


def deterministic_clean(logical: dict, *, pdf_sha256: str) -> tuple[dict, list[CleaningEdit], dict]:
    """Fixed source-label row classification and explicit physical-column alignment.

    No value normalization, financial arithmetic, gold or model retry is involved.
    Existing ancestor paths are preserved; indentation levels are not invented.
    Column realignment is permitted only when one declared physical column is
    covered by that original cell; spanning ambiguous amounts are left unchanged.
    """
    cleaned = copy.deepcopy(logical)
    edits = []
    total = re.compile(r"\btotal\b|^net\s+(?:income|loss|earnings|cash)\b", re.IGNORECASE)
    for ti, table in enumerate(cleaned["tables"]):
        for ri, row in enumerate(table["rows"]):
            source = {"pdf_sha256": pdf_sha256, "page": logical["page"], "quote": row["label"] or "[source-visible blank row]"}
            role = "header" if not row["cells"] else "total" if total.search(row["label"]) else "data"
            if row["role"] != role:
                edits.append(CleaningEdit(target=f"/tables/{ti}/rows/{ri}", field="role",
                    before=row["role"], after=role, reason="Fixed source-label/no-numeric-cell row classifier", evidence=source))
                row["role"] = role
            # Raw adapter normally already aligns from actual geometry. This
            # handles a declared-column misalignment without shifting any value.
            for ci, cell in enumerate(row["cells"]):
                candidates = [c for c in table["columns"] if cell["physical_column"] <= c["grid_column"]
                              < cell["physical_column"] + cell["colspan"]]
                if len(candidates) == 1 and not cell["column_alignment_ambiguous"]:
                    destination = candidates[0]["column_index"]
                    if destination != cell["column_index"]:
                        edits.append(CleaningEdit(target=f"/tables/{ti}/rows/{ri}/cells/{ci}",
                            field="column_index", before=cell["column_index"], after=destination,
                            reason="Unique source physical-cell/numeric-column overlap", evidence=source))
                        cell["column_index"] = destination
    return cleaned, edits, audit_cleaning(logical, cleaned, edits)
