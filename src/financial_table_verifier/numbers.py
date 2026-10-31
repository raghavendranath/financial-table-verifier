"""Conservative numeric parsing: ambiguous strings stay unparsed, never repaired."""

from __future__ import annotations

import re
from decimal import Decimal


def parse_number(raw: str) -> Decimal | None:
    value = raw.strip().replace("\u2212", "-").replace("\u00a0", " ")
    if value in {"", "-", "\u2013", "\u2014", "N/A", "n/a"}:
        return None
    currency = r"^[$\u00a3\u20ac]\s*"
    # The symbol may sit outside or inside the parentheses: "$ (12)" and "($12)".
    value = re.sub(currency, "", value)
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = re.sub(currency, "", value[1:-1].strip())
    pattern = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    if not re.fullmatch(pattern, value):
        return None
    if negative and value.startswith(("+", "-")):
        return None
    result = Decimal(value.replace(",", ""))
    return -result if negative else result
