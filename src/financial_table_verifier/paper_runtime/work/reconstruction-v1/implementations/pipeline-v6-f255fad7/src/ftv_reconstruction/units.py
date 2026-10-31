"""Immutable interchange units; generated stages may not redefine these fields."""
import re
import unicodedata

VERSION = "multiplicative-scale-iso-currency-v1"
SCALE_DESCRIPTION = (
    "scale is the multiplicative factor 1, 1000, 1000000, or 1000000000, or null when unknown. "
    "It is NOT a base-10 exponent: never use 0, 3, 6, or 9 as scale values. "
    "Normalize a printed value by multiplying by scale; keep raw_value unchanged. "
    "Money and shares have separately source-supported factors; per_share scale is 1."
)
CURRENCY_DESCRIPTION = (
    "currency is an explicit uppercase ISO 4217 currency code such as USD, or null when unknown. "
    "For quantity shares currency MUST be null; never use Shares as a currency. "
    "For quantity per_share use the supported currency code such as USD and scale 1; "
    "never use USDperShare, USD/share, or USD per share as currency values."
)
UNIT_POLICY = SCALE_DESCRIPTION + "\n" + CURRENCY_DESCRIPTION


def _narrative(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def definition_equivalent(actual, expected, field):
    """Bounded cosmetic equivalence, not general paraphrase or unit repair."""
    def canonical(text):
        value = _narrative(text)
        value = re.sub(r"^" + re.escape(field) + r"\s+is\s+", "", value)
        return re.sub(r"^(?:the|an|a)\s+", "", value)
    return canonical(actual) == canonical(expected)


def policy_equivalent(actual):
    """Allow the same cosmetic prefixes in each of the two policy definitions."""
    value = _narrative(actual)
    start = re.search(r"(?:currency\s+is\s+)?(?:the\s+|an\s+|a\s+)?explicit uppercase iso 4217 currency code", value)
    if start is None:
        return False
    return (definition_equivalent(value[:start.start()].strip(), SCALE_DESCRIPTION, "scale")
            and definition_equivalent(value[start.start():], CURRENCY_DESCRIPTION, "currency"))


def unit_text_contradictions(text):
    """Reject positive redefinitions; negated forbidden examples remain allowed."""
    bad = []
    negative = re.compile(r"\b(?:not|never|no|cannot|don't|do\s+not|rather\s+than)\b", re.I)
    patterns = (
        r"\b(?:base[- ]?10\s+)?(?:exponent|power\s+of\s*(?:10|ten)|log10)\b",
        r"\bscale\s*(?:=|is|as|to|:)\s*[\"']?(?:0|3|6|9)\b",
        r"\b(?:0|3|6|9)\s+(?:for|means)\s+(?:units|thousands|millions|billions)\b",
        r"\bcurrency\s*(?:=|is|as|to|:)\s*[\"']?(?:shares|USDperShare|USD/share)\b",
        r"\bUSDperShare\b|\bUSD/share\b",
    )
    for clause in re.split(r"[;\n.!?]", text):
        for pattern in patterns:
            for match in re.finditer(pattern, clause, flags=re.I):
                if not negative.search(clause[max(0, match.start() - 100):match.start()]):
                    bad.append(match.group(0))
    return bad
