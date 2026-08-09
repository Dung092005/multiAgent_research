"""Financial consistency checks."""

from src.finance.calculator import money
from src.schemas.agent_reports import PolicyDecision, VerificationError
from src.schemas.records import Financials


def validate_financials(
    actual: Financials, expected: Financials, decision: PolicyDecision
) -> list[VerificationError]:
    errors: list[VerificationError] = []
    for field in ("item_total_brl", "freight_total_brl", "payment_total_brl"):
        actual_value = money(getattr(actual, field))
        expected_value = money(getattr(expected, field))
        if actual_value != expected_value:
            errors.append(
                VerificationError(
                    code="FINANCIAL_MISMATCH",
                    field=field,
                    message=f"{field} does not match database totals",
                    expected=str(expected_value),
                    actual=str(actual_value),
                    repair_target="payment_agent",
                )
            )
    expected_refund = (
        expected.payment_total_brl
        if decision.primary_issue in {"canceled_order_paid", "unavailable_order_paid"}
        else expected.freight_total_brl
        if decision.primary_issue in {"late_delivery_seller", "late_delivery_logistics"}
        else money(0)
    )
    if money(decision.recommended_refund_brl) != money(expected_refund):
        errors.append(
            VerificationError(
                code="REFUND_MISMATCH",
                field="financial_resolution.recommended_refund_brl",
                message="Refund does not match the authoritative policy basis",
                expected=str(money(expected_refund)),
                actual=str(money(decision.recommended_refund_brl)),
                repair_target="policy_agent",
            )
        )
    return errors
