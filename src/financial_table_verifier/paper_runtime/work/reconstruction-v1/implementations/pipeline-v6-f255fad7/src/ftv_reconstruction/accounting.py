"""Explicit eligibility and complete accounting bridges for NEW reviewed ledgers.

These arithmetic screens do not establish numeric transcription correctness.
No existing predictions or historical checks are repaired or relabelled here.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import re
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .contracts import Nonempty, SourceEvidence, Strict


class Period(Strict):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError("Reversed accounting interval")
        return self


def printed_number(raw: str) -> Decimal | None:
    text = raw.strip().replace("\u2212", "-")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        return None
    if negative and text.startswith(("-", "+")):
        return None
    result = Decimal(text.replace(",", ""))
    return -result if negative else result


class Fact(Strict):
    id: Nonempty
    raw_value: str
    quantity: Literal["money", "shares", "per_share"]
    scale: Annotated[int, Field(gt=0)] | None
    currency: str | None
    scope: str | None
    period: Period | None = None
    instant: date | None = None
    evidence: SourceEvidence

    @model_validator(mode="after")
    def date_kind(self) -> Self:
        if self.period is not None and self.instant is not None:
            raise ValueError("A fact cannot be both instant and duration")
        if self.scale is not None and self.scale not in {1, 1000, 1000000, 1000000000}:
            raise ValueError("Unsupported scale; no implicit unit conversion")
        return self

    def value(self) -> Decimal | None:
        parsed = printed_number(self.raw_value)
        return parsed * self.scale if parsed is not None and self.scale is not None else None


class Review(Strict):
    status: Literal["pending", "eligible", "excluded"]
    reviewer: str | None
    reason: Nonempty
    evidence: list[SourceEvidence]
    prediction_blind: bool
    prediction_blind_scope: Literal["before_new_reconstruction_outputs_only"] = "before_new_reconstruction_outputs_only"
    review_kind: Literal["agent_source_review", "human_source_review"] = "agent_source_review"
    author_reviewed: bool = False
    independent_human_review: bool = False
    prior_study_exposure: Literal["yes", "no", "unknown"] = "unknown"

    def gate(self) -> str | None:
        if self.status != "eligible":
            return f"source eligibility is {self.status}: {self.reason}"
        if not self.reviewer or not self.evidence or not self.prediction_blind:
            return "source eligibility lacks reviewer, source evidence or review before new reconstruction outputs"
        return None


class EPSLedger(Strict):
    case_id: Nonempty
    variant: Literal["basic", "diluted"]
    eligibility: Review
    capital_structure: Literal["simple", "complex", "unknown"]
    numerator_basis: Literal["basic_available_common", "diluted_adjusted_available_common", "unreconciled_net_income", "unknown"]
    denominator_basis: Literal["weighted_average_basic", "weighted_average_diluted", "ending_shares", "unknown"]
    numerator: Fact | None
    shares: Fact | None
    reported_eps: Fact | None
    scope: Nonempty
    period: Period
    currency: Nonempty
    # Explicitly registered rounding tolerance in currency/share, not a tuned relative %.
    absolute_tolerance: Annotated[Decimal, Field(ge=0)]
    rounding_policy: Literal["point_absolute", "printed_precision_intervals"] = "printed_precision_intervals"


class Movement(Strict):
    kind: Literal["net_income", "dividends", "other"]
    direction: Literal[-1, 1]
    fact: Fact


class RetainedEarningsLedger(Strict):
    case_id: Nonempty
    eligibility: Review
    scope: Nonempty
    currency: Nonempty
    period: Period
    beginning: Fact | None
    ending: Fact | None
    movements: list[Movement]
    movements_complete: bool
    other_movements_complete: bool
    completeness_evidence: list[SourceEvidence]
    absolute_tolerance: Annotated[Decimal, Field(ge=0)]
    dividend_policy: Literal["reported", "source_verified_absent", "unknown"] = "reported"
    dividend_absence_evidence: list[SourceEvidence] = []
    dividend_absence_review: Review | None = None


def skipped(case_id: str, reason: str) -> dict:
    return {"case_id": case_id, "status": "skipped", "reason": reason}


def common_gate(facts: list[Fact], *, scope: str, currency: str) -> str | None:
    if len({f.id for f in facts}) != len(facts):
        return "duplicate fact identity"
    if len({f.evidence.pdf_sha256 for f in facts}) != 1:
        return "facts are not from the same source PDF"
    if any(f.scope != scope or f.scope in {None, "unknown"} for f in facts):
        return "entity scope mismatch or unknown"
    if any(f.value() is None for f in facts):
        return "unparsed numeric value or unknown scale; no inferred zero or unit"
    if any(f.currency != currency for f in facts if f.quantity != "shares"):
        return "currency mismatch or unknown; no FX conversion"
    if any(f.currency is not None for f in facts if f.quantity == "shares"):
        return "shares must not carry a currency"
    return None


def arithmetic_result(case_id: str, observed: Decimal, expected: Decimal,
                      tolerance: Decimal, facts: list[Fact]) -> dict:
    difference = abs(observed - expected)
    return {"case_id": case_id, "status": "pass" if difference <= tolerance else "fail",
            "observed": str(observed), "expected": str(expected),
            "absolute_difference": str(difference), "absolute_tolerance": str(tolerance),
            "sources": [f.model_dump(mode="json") for f in facts],
            "interpretation": "Conditional arithmetic consistency, not extraction accuracy"}


def check_eps(ledger: EPSLedger) -> dict:
    reason = ledger.eligibility.gate()
    if reason:
        return skipped(ledger.case_id, reason)
    if ledger.capital_structure != "simple":
        return skipped(ledger.case_id, "primary original-method EPS requires reviewed simple capital structure")
    expected_basis = "basic_available_common" if ledger.variant == "basic" else "diluted_adjusted_available_common"
    if ledger.numerator_basis != expected_basis:
        return skipped(ledger.case_id, "explicit matching EPS numerator basis is missing; ordinary net income is not substituted")
    if ledger.denominator_basis != f"weighted_average_{ledger.variant}":
        return skipped(ledger.case_id, "matching weighted-average share basis is missing")
    if any(f is None for f in (ledger.numerator, ledger.shares, ledger.reported_eps)):
        return skipped(ledger.case_id, "missing numerator, shares or reported EPS")
    facts = [ledger.numerator, ledger.shares, ledger.reported_eps]
    reason = common_gate(facts, scope=ledger.scope, currency=ledger.currency)
    if reason:
        return skipped(ledger.case_id, reason)
    if [f.quantity for f in facts] != ["money", "shares", "per_share"]:
        return skipped(ledger.case_id, "EPS requires money / weighted-average shares and a per-share reported value")
    if any(f.period != ledger.period or f.instant is not None for f in facts):
        return skipped(ledger.case_id, "EPS reporting interval mismatch or unknown")
    if ledger.reported_eps.scale != 1:
        return skipped(ledger.case_id, "reported EPS must have an explicit per-share scale of one")
    numerator, shares, reported = [f.value() for f in facts]
    if shares <= 0:
        return skipped(ledger.case_id, "nonpositive weighted-average share denominator")
    result = arithmetic_result(ledger.case_id, reported, numerator / shares,
                               ledger.absolute_tolerance, facts)
    result["rounding_policy"] = ledger.rounding_policy
    if ledger.rounding_policy == "printed_precision_intervals":
        def bounds(fact):
            raw = fact.raw_value.strip().strip("()")
            decimals = len(raw.rsplit(".", 1)[1]) if "." in raw else 0
            half = Decimal("0.5") * Decimal(10) ** -decimals * fact.scale
            return fact.value() - half, fact.value() + half
        nlo, nhi = bounds(ledger.numerator)
        slo, shi = bounds(ledger.shares)
        elo, ehi = bounds(ledger.reported_eps)
        if slo <= 0:
            return skipped(ledger.case_id, "rounded share interval reaches a nonpositive denominator")
        quotients = [a / b for a in (nlo, nhi) for b in (slo, shi)]
        qlo, qhi = min(quotients), max(quotients)
        result.update(status="pass" if max(qlo, elo) <= min(qhi, ehi) else "fail",
                      quotient_interval=[str(qlo), str(qhi)], reported_eps_interval=[str(elo), str(ehi)],
                      numerator_interval=[str(nlo), str(nhi)], shares_interval=[str(slo), str(shi)],
                      point_absolute_tolerance_used=False)
    return result


def check_retained_earnings(ledger: RetainedEarningsLedger) -> dict:
    reason = ledger.eligibility.gate()
    if reason:
        return skipped(ledger.case_id, reason)
    if not ledger.movements_complete or not ledger.other_movements_complete or not ledger.completeness_evidence:
        return skipped(ledger.case_id, "full bridge including other equity movements is not independently reviewed complete")
    if ledger.beginning is None or ledger.ending is None:
        return skipped(ledger.case_id, "missing beginning or ending retained earnings")
    kinds = [m.kind for m in ledger.movements]
    if kinds.count("net_income") != 1:
        return skipped(ledger.case_id, "bridge requires exactly one net-income movement")
    if ledger.dividend_policy == "unknown":
        return skipped(ledger.case_id, "dividend disclosure status is unknown; missing is not zero")
    if ledger.dividend_policy == "reported" and kinds.count("dividends") != 1:
        return skipped(ledger.case_id, "reported-dividend bridge requires one explicit aggregate dividend fact; missing is not zero")
    if ledger.dividend_policy == "source_verified_absent":
        if kinds.count("dividends") != 0:
            return skipped(ledger.case_id, "dividend absence attestation conflicts with extracted dividend movements")
        if (not ledger.dividend_absence_evidence or ledger.dividend_absence_review is None
                or ledger.dividend_absence_review.gate()):
            return skipped(ledger.case_id, "dividend absence requires source evidence and explicit reviewed attestation, not a zero fact")
    if any(m.direction != 1 for m in ledger.movements if m.kind == "net_income"):
        return skipped(ledger.case_id, "net income is added with its printed sign")
    # A dividend can be a positive amount subtracted, or a printed signed negative
    # movement added. Direction remains explicit and cannot be chosen after scoring.
    facts = [ledger.beginning, ledger.ending, *[m.fact for m in ledger.movements]]
    reason = common_gate(facts, scope=ledger.scope, currency=ledger.currency)
    if reason:
        return skipped(ledger.case_id, reason)
    if any(f.quantity != "money" for f in facts):
        return skipped(ledger.case_id, "retained-earnings bridge requires money quantities")
    if any(Decimal(m.direction) * m.fact.value() > 0 for m in ledger.movements if m.kind == "dividends"):
        return skipped(ledger.case_id, "dividend direction must reduce retained earnings; reversals require separate reviewed other movements")
    if ledger.beginning.instant != ledger.period.start - timedelta(days=1) or ledger.ending.instant != ledger.period.end:
        return skipped(ledger.case_id, "opening/closing dates do not bound the bridge reporting interval")
    if any(m.fact.period != ledger.period or m.fact.instant is not None for m in ledger.movements):
        return skipped(ledger.case_id, "movement period mismatch or unknown")
    expected = ledger.beginning.value() + sum(
        (Decimal(m.direction) * m.fact.value() for m in ledger.movements), Decimal(0)
    )
    return arithmetic_result(ledger.case_id, ledger.ending.value(), expected,
                             ledger.absolute_tolerance, facts)


def denominator_ledger(results: list[dict]) -> dict:
    ids = [r["case_id"] for r in results]
    if len(ids) != len(set(ids)):
        raise ValueError("Repeated case IDs cannot silently inflate accounting denominators")
    if any(r["status"] not in {"pass", "fail", "skipped"} for r in results):
        raise ValueError("Unknown accounting outcome")
    passed = sum(r["status"] == "pass" for r in results)
    failed = sum(r["status"] == "fail" for r in results)
    return {"candidates": len(results), "passed": passed, "failed": failed,
            "skipped": len(results) - passed - failed, "applicable": passed + failed,
            "pass_rate": passed / (passed + failed) if passed + failed else None,
            "checks": results}
