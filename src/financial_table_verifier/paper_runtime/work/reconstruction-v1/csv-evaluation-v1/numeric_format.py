"""Lossless raw preservation plus narrowly defined arithmetic-format projection."""
from decimal import Decimal
import re

# Explicit supported ISO prefixes, not a guessed currency for an unmarked number.
ISO_PREFIXES = set("USD EUR GBP JPY CNY CAD AUD CHF HKD SGD INR KRW BRL MXN ZAR NOK SEK DKK NZD PLN RUB TRY SAR AED ILS THB TWD VND IDR MYR PHP ARS CLP COP CZK HUF RON ISK PKR BDT LKR NPR EGP QAR KWD BHD OMR JOD MAD NGN KES GHS PEN UYU BOB PYG CRC DOP GTQ HNL NIO PAB BZD XCD JMD TTD BBD BSD BMD KYD FJD PGK WST TOP VUV SBD MUR SCR MVR KZT UZS UAH GEL AMD AZN MDL ALL RSD MKD BAM BGN TND DZD LYD ETB UGX TZS RWF ZMW BWP NAD MZN AOA XOF XAF XPF XDR".split())
GLYPHS = {"$": {"USD", "CAD", "AUD", "NZD", "HKD", "SGD", "MXN"},
          "£": {"GBP"}, "€": {"EUR"}, "¥": {"JPY", "CNY"}}


def project_number(raw):
    """Never change a digit/sign/scale to fit an answer; reject ambiguous syntax.

    Currency can occur once, before the number or outside/inside negative
    parentheses. Whitespace is allowed around tokens/signs/commas, not between
    independent digit groups. Footnotes/suffixes/multiple values remain unparsed.
    """
    result = {"original_raw_value": raw, "normalized_arithmetic_raw_value": None,
              "value": None, "currency_token": None, "compatible_currency_codes": [], "status": "unparsed"}
    if not isinstance(raw, str):
        return result
    text = raw.replace("\u2212", "-").replace("\u00a0", " ").strip()
    token = None
    def currency_prefix(value):
        if value and value[0] in GLYPHS:
            return value[0], value[1:].strip()
        match = re.match(r"^([A-Z]{3})(?=\s|[\d(+\-])", value)
        if match and match[1] in ISO_PREFIXES:
            return match[1], value[match.end():].strip()
        return None, value
    token, text = currency_prefix(text)
    parentheses = text.startswith("(") and text.endswith(")")
    if parentheses:
        text = text[1:-1].strip()
        inside, text = currency_prefix(text)
        if inside and token:
            return result
        token = token or inside
    sign = ""
    if text.startswith(("+", "-")):
        sign, text = text[0], text[1:].strip()
        after_sign, text = currency_prefix(text)
        if after_sign and token:
            return result
        token = token or after_sign
    if parentheses and sign:
        return result
    if not re.fullmatch(r"(?:\d{1,3}(?:\s*,\s*\d{3})+|\d+)(?:\.\d+)?", text):
        return result
    digits = re.sub(r"[\s,]", "", text)
    normalized = ("-" if parentheses else sign) + digits
    value = Decimal(normalized)
    result.update(normalized_arithmetic_raw_value=normalized, value=str(value), currency_token=token,
                  compatible_currency_codes=sorted(GLYPHS[token] if token in GLYPHS else {token} if token else set()),
                  status="parsed")
    return result


def presentation_compatible(projection, *, quantity, currency):
    if projection["currency_token"] is None:
        return True
    if quantity == "shares" or not currency:
        return False
    return currency in projection["compatible_currency_codes"]


def numeric(raw):
    projected = project_number(raw)
    return Decimal(projected["value"]) if projected["status"] == "parsed" else None
