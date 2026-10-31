"""Read local XBRL instance facts and compare explicitly mapped extraction cells.

This handles XML XBRL instances, not arbitrary HTML Inline XBRL transformations.
No taxonomy inference or automatic financial concept/context alignment is performed.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .numbers import parse_number
from .schema import PageExtraction, StrictModel
from .storage import file_hash, read_json, write_json

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def read_companyfacts(path: Path, accession: str) -> dict:
    """Select exact-accession facts from a cached SEC companyfacts response.

    Synthetic context IDs identify period + accession, not original XBRL contextRef.
    These API facts omit custom/dimensional tables; they cannot label whole hierarchies.
    """
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("Companyfacts file exceeds 100 MiB limit")
    payload = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    facts, seen = [], set()
    for namespace, concepts in payload["facts"].items():
        for concept, description in concepts.items():
            for unit, entries in description.get("units", {}).items():
                for entry in entries:
                    if entry.get("accn") != accession:
                        continue
                    value = Decimal(str(entry["val"]))
                    if not value.is_finite():
                        continue
                    start, end = entry.get("start"), entry["end"]
                    key = (namespace, concept, unit, start, end, str(value))
                    if key in seen:
                        continue
                    seen.add(key)
                    context_id = accession + "|" + (start or "instant") + "|" + end
                    period = {"startDate": start, "endDate": end} if start else {"instant": end}
                    facts.append(
                        {
                            "concept": namespace + ":" + concept,
                            "context_id": context_id,
                            "unit_id": unit,
                            "value": str(value),
                            "decimals": None,
                            "context": {
                                "entity": str(payload["cik"]),
                                "period": period,
                                "dimensions": [],
                                "accession": accession,
                                "form": entry.get("form"),
                                "filed": entry.get("filed"),
                            },
                            "unit": {"companyfacts_unit": unit},
                        }
                    )
    if not facts:
        raise ValueError("No numeric companyfacts for the exact requested accession")
    return {
        "source_file": str(path.resolve()),
        "source_sha256": file_hash(path),
        "accession": accession,
        "facts": facts,
        "note": "SEC companyfacts subset. Context IDs are synthetic; select exact accession "
        "AND date interval. No custom/dimensional-table reference coverage.",
    }


def read_instance(path: Path) -> dict:
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("XBRL instance exceeds 50 MiB input limit")
    raw = path.read_bytes()
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)\b", raw, flags=re.IGNORECASE):
        raise ValueError("DTD/entity declarations are not supported")
    root = ET.fromstring(raw)
    if root.tag != f"{{{XBRLI}}}xbrl":
        raise ValueError("Expected XML XBRL instance root, not Inline XBRL HTML")
    contexts, units = {}, {}
    for node in root.findall(f"{{{XBRLI}}}context"):
        identifier = node.find(f"{{{XBRLI}}}entity/{{{XBRLI}}}identifier")
        period_node = node.find(f"{{{XBRLI}}}period")
        period = {}
        if period_node is not None:
            period = {child.tag.rsplit("}", 1)[-1]: child.text for child in period_node}
        dimensions = [
            ET.tostring(child, encoding="unicode")
            for child in node.iter()
            if child.tag in {f"{{{XBRLDI}}}explicitMember", f"{{{XBRLDI}}}typedMember"}
        ]
        identifier_text = identifier.text if identifier is not None else None
        if not node.get("id") or node.get("id") in contexts:
            raise ValueError("Missing/duplicate XBRL context identifier")
        contexts[node.get("id")] = {
            "entity": identifier_text,
            "period": period,
            "dimensions": dimensions,
        }
    for node in root.findall(f"{{{XBRLI}}}unit"):
        measures = [child.text for child in node.findall(f"{{{XBRLI}}}measure")]
        numerator = [
            child.text
            for child in node.findall(
                f"{{{XBRLI}}}divide/{{{XBRLI}}}unitNumerator/{{{XBRLI}}}measure"
            )
        ]
        denominator = [
            child.text
            for child in node.findall(
                f"{{{XBRLI}}}divide/{{{XBRLI}}}unitDenominator/{{{XBRLI}}}measure"
            )
        ]
        if not node.get("id") or node.get("id") in units:
            raise ValueError("Missing/duplicate XBRL unit identifier")
        units[node.get("id")] = {
            "measures": measures,
            "numerator": numerator,
            "denominator": denominator,
        }
    facts, skipped = [], 0
    for node in root:
        context_id, unit_id = node.get("contextRef"), node.get("unitRef")
        if not context_id or not unit_id:
            continue
        text = (node.text or "").strip()
        if node.get(f"{{{XSI}}}nil") in {"true", "1"} or len(node):
            skipped += 1
            continue
        try:
            value = Decimal(text)
        except InvalidOperation:
            skipped += 1
            continue
        if not value.is_finite():
            skipped += 1
            continue
        if context_id not in contexts or unit_id not in units:
            raise ValueError("Fact references missing context or unit")
        facts.append(
            {
                "concept": node.tag,
                "context_id": context_id,
                "unit_id": unit_id,
                "value": str(value),
                "decimals": node.get("decimals"),
                "context": contexts[context_id],
                "unit": units[unit_id],
            }
        )
    return {
        "source_file": str(path.resolve()),
        "source_sha256": file_hash(path),
        "facts": facts,
        "skipped_numeric_facts": skipped,
        "note": "XML numeric facts. Preserve exact concept namespace, context, unit and accession.",
    }


class FactMapping(StrictModel):
    document_id: str
    pdf_sha256: str
    page: int
    table: int
    row: int
    column: int
    concept: str
    context_id: str
    unit_id: str
    # Independently read from the XBRL unit/period; explicit to avoid heuristic QName inference.
    quantity: str
    currency: str | None
    period: str


def compare_facts(run: Path, facts_path: Path, mapping_path: Path) -> dict:
    run_info = read_json(run / "run.json")
    results = read_json(run / "results.json")
    facts = read_json(facts_path)
    spec = read_json(mapping_path)
    if spec["instance_sha256"] != facts["source_sha256"]:
        raise ValueError("Mapping refers to a different XBRL instance hash")
    documents = {d["id"]: d for d in run_info["documents"]}
    outcomes, used = [], set()
    for entry in spec["mappings"]:
        mapping = FactMapping.model_validate(entry)
        doc = documents.get(mapping.document_id)
        if doc is None or mapping.pdf_sha256 != doc["pdf_sha256"]:
            raise ValueError("Mapping document/PDF hash does not match run")
        key = (mapping.document_id, mapping.page, mapping.table, mapping.row, mapping.column)
        if key in used:
            raise ValueError("Duplicate mapped cell")
        used.add(key)
        candidates = [
            fact
            for fact in facts["facts"]
            if (fact["concept"], fact["context_id"], fact["unit_id"])
            == (mapping.concept, mapping.context_id, mapping.unit_id)
        ]
        if len(candidates) != 1:
            raise ValueError("XBRL fact is absent or ambiguous; resolve duplicates independently")
        fact = candidates[0]
        context_period = fact["context"]["period"]
        actual_period = context_period.get("instant")
        if actual_period is None and {"startDate", "endDate"} <= set(context_period):
            actual_period = context_period["startDate"] + "/" + context_period["endDate"]
        if mapping.period != actual_period:
            raise ValueError("Mapped period does not match XBRL context")
        pages = [
            PageExtraction.model_validate(p)
            for p in results["documents"][mapping.document_id]["pages"]
        ]
        matches = [
            (cell, column)
            for page in pages
            if page.page == mapping.page
            for table in page.tables
            if table.table_index == mapping.table
            for row in table.rows
            if row.row_index == mapping.row
            for cell in row.cells
            if cell.column_index == mapping.column
            for column in table.columns
            if column.column_index == mapping.column
        ]
        outcome = {"cell": key, "matches": False, "fact": fact}
        if len(matches) != 1:
            outcome["reason"] = "missing extraction cell"
        else:
            cell, column = matches[0]
            value = parse_number(cell.raw_value)
            normalized = (
                value * cell.scale if value is not None and cell.scale is not None else None
            )
            outcome.update(
                extracted_value=str(normalized) if normalized is not None else None,
                matches=(
                    normalized == Decimal(fact["value"])
                    and cell.quantity == mapping.quantity
                    and cell.currency == mapping.currency
                    and column.period == mapping.period
                ),
            )
        outcomes.append(outcome)
    if not outcomes:
        raise ValueError("No fact mappings supplied")
    matched = sum(o["matches"] for o in outcomes)
    report = {
        "mapped_cells": len(outcomes),
        "matched_cells": matched,
        "exact_match_rate": matched / len(outcomes),
        "outcomes": outcomes,
        "fact_source_sha256": facts["source_sha256"],
        "mapping_sha256": file_hash(mapping_path),
        "note": "Selected facts only, not full-table accuracy. Mapping metadata must be "
        "reviewed against unit, dimensions, entity and reporting interval. "
        "Exact comparison uses scaled values; inspect rounding/context mismatches.",
    }
    write_json(run / "xbrl-comparison.json", report)
    return report
