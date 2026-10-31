"""Only classify row-role fields; never move, create, or rewrite amount tokens."""
from copy import deepcopy
import hashlib
import re

from .csv_io import parse_csv, parse_page, serialize


def clean_page(envelope, plan, *, document_id, page):
    raw = parse_page(envelope, plan, document_id=document_id, page=page)
    clean = deepcopy(envelope)
    edits = []
    for table in raw["tables"]:
        if not table["header_matches_schema"]:
            continue
        archetype = next(a for a in plan.archetypes if a.schema_id == table["emitted"]["schema_id"])
        role_index = next(i for i, c in enumerate(archetype.columns) if c.role == "row_role")
        pieces = [table["records"][0]["raw_record"]]
        changed = False
        for record in table["records"][1:]:
            original = record["raw_record"]
            fields = list(record["fields"])
            if record.get("semantic_eligible"):
                roles = record["roles"]
                role = roles["row_role"]
                label = roles["row_label"]
                if role in {"", "unknown"}:
                    if label and not roles["printed_value"]:
                        classified = "Header"
                    elif re.match(r"^\s*total\b", label, re.I):
                        classified = "Total"
                    elif roles["printed_value"]:
                        classified = "Data"
                    else:
                        classified = None
                    if classified:
                        fields[role_index] = classified
                        replacement = serialize([fields])
                        # Preserve whether the original final record had a newline.
                        if not original.endswith(("\r", "\n")):
                            replacement = replacement[:-2]
                        pieces.append(replacement)
                        edits.append({"table_ordinal": table["table_ordinal"], "record_ordinal": record["record_ordinal"],
                                      "column_name": archetype.columns[role_index].name, "role": "row_role",
                                      "before": role, "after": classified, "rule": "visible_label_and_amount_presence"})
                        changed = True
                        continue
            pieces.append(original)
        if changed:
            value = "".join(pieces)
            before_fields = [r["fields"] for r in table["records"]]
            after_fields = [r["fields"] for r in parse_csv(value)]
            if len(before_fields) != len(after_fields):
                raise ValueError("Cleaner changed record count")
            for before, after in zip(before_fields, after_fields):
                if len(before) != len(after) or any(a != b for i, (a, b) in enumerate(zip(before, after)) if i != role_index):
                    raise ValueError("Cleaner attempted a protected field edit")
            clean["tables"][table["table_ordinal"]]["csv"] = value
    protected = [{"table_ordinal": t["table_ordinal"], "csv_sha256": t.get("csv_sha256"),
                  "field_records": [r["fields"] for r in t["records"]]} for t in raw["tables"]]
    audit = {"policy": "row-role classification only; no alignment guesses or numeric/metadata edits",
             "protected_fields_unchanged": True, "row_count_unchanged": True, "edit_count": len(edits),
             "raw_field_snapshot_sha256": hashlib.sha256(repr(protected).encode()).hexdigest()}
    return clean, edits, audit
