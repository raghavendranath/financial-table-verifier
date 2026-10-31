"""Arithmetic screens, never a substitute for extraction correctness."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import re

from .numbers import parse_number
from .schema import Experiment, PageExtraction


def check_consistency(
    pages: list[PageExtraction],
    config: Experiment,
    eps_eligible_tables: list[str],
) -> dict:
    groups: dict[tuple[str, str | None], dict] = {}
    roots: dict[str, str] = {}
    excluded = []
    for page in pages:
        for table in page.tables:
            table_id = f"p{page.page}-t{table.table_index}"
            root = roots.get(table.continuation_of, table_id)
            roots[table_id] = root
            if table.statement == "other":
                excluded.append({"table": table_id, "reason": "other/non-GAAP/segment table"})
                continue
            for column in table.columns:
                group = groups.setdefault(
                    (root, column.period),
                    {
                        "statement": table.statement,
                        "scope": table.scope,
                        "facts": defaultdict(list),
                    },
                )
                for row in table.rows:
                    if row.concept is None:
                        continue
                    for cell in row.cells:
                        if cell.column_index == column.column_index:
                            group["facts"][row.concept].append(
                                {
                                    "value": parse_number(cell.raw_value),
                                    "scale": cell.scale,
                                    "currency": cell.currency,
                                    "quantity": cell.quantity,
                                    "row_label": row.label,
                                    "source": {
                                        "page": page.page,
                                        "table": table.table_index,
                                        "row": row.row_index,
                                        "column": column.column_index,
                                        "raw_value": cell.raw_value,
                                    },
                                }
                            )

    checks = []
    for (root, period), group in groups.items():
        statement = group["statement"]
        equations = []
        if statement == "balance_sheet":
            equations = [("balance_sheet", ["total_assets", "total_liabilities", "total_equity"])]
        elif statement == "income_statement":
            equations = [
                ("gross_profit", ["gross_profit", "revenue", "cost_of_revenue"]),
                ("operating_income", ["operating_income", "gross_profit", "operating_expenses"]),
                ("net_income", ["net_income", "income_before_tax", "income_tax_expense"]),
            ]
        elif statement == "eps":
            equations = [
                ("basic_eps", ["basic_eps", "basic_numerator", "basic_shares"]),
                ("diluted_eps", ["diluted_eps", "diluted_numerator", "diluted_shares"]),
            ]
        elif statement == "cash_flow":
            equations = [("cash_rollforward", ["cash_ending", "cash_beginning", "cash_net_change"])]
        for name, concepts in equations:
            result = {"table": root, "period": period, "check": name, "status": "skipped"}
            facts = group["facts"]
            reason = None
            if group["scope"] == "unknown" or period is None:
                reason = "unknown entity scope or period"
            elif statement == "eps" and root not in eps_eligible_tables:
                reason = "EPS eligibility not independently confirmed in manifest"
            elif any(len(facts[c]) != 1 for c in concepts):
                reason = "missing or ambiguous duplicate concepts"
            else:
                selected = [facts[c][0] for c in concepts]
                result["sources"] = [f["source"] for f in selected]
                # A broad table scope cannot establish that parent/common-shareholder
                # income has the same basis as consolidated pre-tax income and tax.
                # Never replace or repair the extracted amount to make the identity pass.
                attributed_income = name == "net_income" and re.search(
                    r"\battributable\b|\bavailable\s+to\b|\b(?:common|preferred)\s+"
                    r"(?:stockholders|shareholders)\b|\bnon[- ]?controlling\b",
                    selected[0]["row_label"], flags=re.IGNORECASE,
                )
                if attributed_income:
                    reason = "net income attribution basis is not independently reconciled " \
                        "with pretax income and tax"
                elif any(f["value"] is None or f["scale"] is None for f in selected):
                    reason = "unparsed value or unknown scale"
                else:
                    quantities = [f["quantity"] for f in selected]
                    expected = (
                        ["per_share", "money", "shares"] if statement == "eps" else ["money"] * 3
                    )
                    currencies = [f["currency"] for f in selected[:2] if statement == "eps"]
                    if statement != "eps":
                        currencies = [f["currency"] for f in selected]
                    if quantities != expected or None in currencies or len(set(currencies)) != 1:
                        reason = "quantity or currency mismatch/unknown"
                    else:
                        values = [f["value"] * f["scale"] for f in selected]
                        lhs = values[0]
                        if statement == "eps":
                            if values[2] <= 0:
                                reason = "nonpositive share denominator"
                                rhs = Decimal(0)
                            else:
                                rhs = values[1] / values[2]
                        elif statement in {"balance_sheet", "cash_flow"}:
                            rhs = values[1] + values[2]
                        else:
                            rhs = values[1] - values[2]
                        if reason is None:
                            tolerance = max(
                                config.absolute_tolerance, abs(lhs) * config.relative_tolerance
                            )
                            result.update(
                                status="pass" if abs(lhs - rhs) <= tolerance else "fail",
                                observed=str(lhs),
                                expected=str(rhs),
                                absolute_difference=str(abs(lhs - rhs)),
                                tolerance=str(tolerance),
                            )
            if reason:
                result["reason"] = reason
            checks.append(result)
    passed = sum(c["status"] == "pass" for c in checks)
    failed = sum(c["status"] == "fail" for c in checks)
    return {
        "passed": passed,
        "failed": failed,
        "skipped": sum(c["status"] == "skipped" for c in checks),
        "applicable": passed + failed,
        "pass_rate": passed / (passed + failed) if passed + failed else None,
        "candidate_checks": len(checks),
        "excluded_tables": excluded,
        "checks": checks,
        "interpretation": "Arithmetic consistency only; concept mappings also need human review.",
    }
