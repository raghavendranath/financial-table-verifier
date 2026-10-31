"""Preserve literal CSV and every field; ambiguity is local, never padded away."""
import csv
import hashlib
import io
import json
import re
import sys

# Responses are already materialized by the bounded caller transport. Do not let
# csv's default 128 KiB field cap bypass the separately declared context blocker.
csv.field_size_limit(sys.maxsize)

def _rfc_quotes(text):
    # csv.reader(strict=True) accepts quotes inside unquoted fields. Reject those
    # explicitly rather than interpreting a non-RFC string by accident.
    state = "start"
    for char in text:
        if state == "quoted":
            if char == '"':
                state = "after_quote"
        elif state == "after_quote":
            if char == '"':
                state = "quoted"
            elif char == ',':
                state = "start"
            elif char in '\r\n':
                state = "start"
            else:
                raise ValueError("Unexpected character after closing quote")
        elif char == '"':
            if state != "start":
                raise ValueError("Quote inside unquoted field")
            state = "quoted"
        elif char == ',':
            state = "start"
        elif char in '\r\n':
            state = "start"
        else:
            state = "unquoted"
    if state == "quoted":
        raise ValueError("Unclosed quoted field")


def parse_csv(text):
    """Return exact record text/field strings including ragged/empty records."""
    _rfc_quotes(text)
    # splitlines treats Unicode separators as line breaks whereas csv.reader does
    # not. Use only actual CR/LF boundaries for record provenance offsets.
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)
    if lines and lines[-1] == "":
        lines.pop()
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    records, previous_line = [], 0
    for ordinal, fields in enumerate(reader):
        raw = "".join(lines[previous_line:reader.line_num])
        records.append({"record_ordinal": ordinal, "start_line": previous_line+1,
                        "end_line": reader.line_num, "raw_record": raw, "fields": fields})
        previous_line = reader.line_num
    return records


def serialize(fields):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerows(fields)
    return output.getvalue()


def metadata_projection(roles):
    """Non-destructive typed view. Failed metadata never removes amount text."""
    projection = {"raw_value": roles["printed_value"], "row_label": roles["row_label"],
                  "column_label": roles["source_column_label"], "period": roles["period"] or None,
                  "quantity": roles["quantity_kind"] or "unknown", "scale": None,
                  "currency": roles["currency"] or None, "column_index": None, "row_context": []}
    issues = []
    ordinal = roles["source_column_ordinal"]
    # A machine metadata resource bound, never a clamp or source-value repair.
    # Nine digits already exceed any plausible printed financial table width.
    if len(ordinal) <= 9 and re.fullmatch(r"0|[1-9][0-9]*", ordinal):
        projection["column_index"] = int(ordinal)
    else:
        issues.append("unknown_or_invalid_source_column_ordinal")
    scale = roles["scale"]
    if scale in {"1", "1000", "1000000", "1000000000"}:
        projection["scale"] = int(scale)
    elif scale:
        issues.append("invalid_scale_no_conversion")
    if projection["quantity"] not in {"money", "shares", "per_share", "ratio", "unknown"}:
        issues.append("invalid_quantity_kind")
    if projection["currency"] is not None and not re.fullmatch(r"[A-Z]{3}", projection["currency"]):
        issues.append("invalid_currency")
    if projection["quantity"] == "shares" and projection["currency"] is not None:
        issues.append("shares_currency_not_applicable")
    if projection["quantity"] == "per_share" and projection["scale"] not in {None, 1}:
        issues.append("per_share_scale_incompatible")
    if roles["ancestor_path"]:
        try:
            path = json.loads(roles["ancestor_path"])
            if not isinstance(path, list) or any(not isinstance(p, str) for p in path):
                raise ValueError("Not a string array")
            projection["row_context"] = path
        except (ValueError, TypeError, RecursionError, OverflowError):
            issues.append("invalid_ancestor_path")
    return projection, issues


def parse_table(raw, schema_plan, table_ordinal):
    result = {"table_ordinal": table_ordinal, "emitted": raw, "issues": [], "records": [],
              "context_eligible": False, "header_matches_schema": False}
    if not isinstance(raw, dict) or not isinstance(raw.get("csv"), str):
        result["issues"].append({"kind": "invalid_table_csv_string"})
        return result
    for field in ("schema_id", "title"):
        if not isinstance(raw.get(field), str):
            result["issues"].append({"kind": "unknown_or_invalid_" + field})
    if raw.get("scope") is not None and not isinstance(raw.get("scope"), str):
        result["issues"].append({"kind": "invalid_scope"})
    text = raw["csv"]
    result["csv_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        parsed = parse_csv(text)
    except (ValueError, csv.Error) as exc:
        result["issues"].append({"kind": "unreadable_csv_quoting", "detail": str(exc)[:500]})
        return result
    result["records"] = parsed
    if not parsed or not parsed[0]["fields"]:
        result["issues"].append({"kind": "missing_header"})
        return result
    header = parsed[0]["fields"]
    result["header"] = header
    result["context_eligible"] = True  # header+quoting-readable, including ragged/schema-unknown CSV
    archetype = next((a for a in schema_plan.archetypes if a.schema_id == raw.get("schema_id")), None)
    result["statement"] = archetype.statement if archetype else None
    if archetype is None:
        result["issues"].append({"kind": "unknown_schema_id"})
    else:
        result["header_matches_schema"] = header == [c.name for c in archetype.columns]
        if not result["header_matches_schema"]:
            result["issues"].append({"kind": "header_schema_mismatch"})
    for record in parsed[1:]:
        fields = record["fields"]
        record.update(exact_width=len(fields) == len(header), semantic_eligible=False,
                      roles=None, projection=None, metadata_issues=[])
        if not record["exact_width"]:
            record["metadata_issues"].append("ragged_record_no_padding_or_guessing")
        elif result["header_matches_schema"]:
            roles = {column.role: field for column, field in zip(archetype.columns, fields)
                     if column.role != "uninterpreted"}
            projection, issues = metadata_projection(roles)
            record.update(semantic_eligible=True, roles=roles, projection=projection, metadata_issues=issues)
    return result


def parse_page(envelope, schema_plan, *, document_id, page):
    if not isinstance(envelope, dict) or set(envelope) != {"tables"} or not isinstance(envelope["tables"], list):
        raise ValueError("CSV page must be a thin tables-array envelope")
    tables = [parse_table(raw, schema_plan, i) for i, raw in enumerate(envelope["tables"])]
    records = []
    for table in tables:
        emitted = table["emitted"]
        for record in table["records"][1:]:
            if not record.get("semantic_eligible"):
                continue
            records.append({"document_id": document_id, "page": page,
                "table_ordinal": table["table_ordinal"], "row_id": f"p{page}-t{table['table_ordinal']}-r{record['record_ordinal']}",
                "record_ordinal": record["record_ordinal"], "table_title": emitted.get("title") if isinstance(emitted.get("title"), str) else None,
                "scope": emitted.get("scope") if isinstance(emitted.get("scope"), str) else None,
                "raw_table_metadata": {k: v for k, v in emitted.items() if k != "csv"},
                "schema_id": emitted.get("schema_id"), "statement": table["statement"],
                "raw_fields": record["fields"], "roles": record["roles"],
                "metadata_issues": record["metadata_issues"], **record["projection"]})
    return {"document_id": document_id, "page": page, "tables": tables, "records": records,
            "has_context_csv": any(t["context_eligible"] for t in tables)}


def page_context(parsed):
    if not parsed or not parsed["has_context_csv"]:
        return None
    return {"page": parsed["page"], "tables": [
        {"schema_id": t["emitted"].get("schema_id") if isinstance(t["emitted"].get("schema_id"), str) else None,
         "csv": t["emitted"]["csv"]} for t in parsed["tables"] if t["context_eligible"]]}


class ContextTooLarge(ValueError):
    pass


def context_for(previous, page, condition, limit=500000):
    selected = previous if condition == "sequential" and previous and previous["page"] == page-1 else None
    count = sum(len(t["csv"]) for t in selected["tables"]) if selected else 0
    if count > limit:
        raise ContextTooLarge("Predecessor literal CSV exceeds 500000 characters; call blocked, no truncation")
    return selected
