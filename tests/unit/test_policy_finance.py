from decimal import Decimal

import pytest

from src.errors import PolicyNoMatchError
from src.finance.calculator import money
from src.policy.engine import PolicyEngine


def test_canceled_paid_has_precedence_over_split_payment(context_factory):
    context = context_factory(
        status="canceled", delivered_late=None, payment_values=("5.00", "6.00")
    )
    decision = PolicyEngine().evaluate(context)
    assert decision.primary_issue == "canceled_order_paid"
    assert decision.recommended_refund_brl == Decimal("11.00")
    assert decision.responsible_parties[0].party_id == "OLIST_PLATFORM"


def test_unavailable_paid_with_no_items(context_factory):
    context = context_factory(
        status="unavailable", delivered_late=None, item_values=(), payment_values=("84.00",)
    )
    decision = PolicyEngine().evaluate(context)
    assert decision.primary_issue == "unavailable_order_paid"
    assert context.financials.item_total_brl == Decimal("0.00")
    assert decision.recommended_refund_brl == Decimal("84.00")


def test_late_delivery_seller(context_factory):
    context = context_factory(delivered_late=True, seller_late=True)
    decision = PolicyEngine().evaluate(context)
    assert decision.primary_issue == "late_delivery_seller"
    assert decision.responsible_parties[0].party_id == "seller-1"
    assert decision.recommended_refund_brl == Decimal("1.00")


def test_late_delivery_logistics(context_factory):
    context = context_factory(delivered_late=True, seller_late=False)
    decision = PolicyEngine().evaluate(context)
    assert decision.primary_issue == "late_delivery_logistics"
    assert decision.responsible_parties[0].party_id == "LOGISTICS_PROVIDER"


def test_valid_split_payment(context_factory):
    context = context_factory(payment_values=("5.00", "6.00"))
    decision = PolicyEngine().evaluate(context)
    assert decision.primary_issue == "valid_split_payment"
    assert decision.case_status == "no_action"
    assert decision.recommended_refund_brl == 0


def test_unsupported_late_claim(context_factory):
    decision = PolicyEngine().evaluate(context_factory())
    assert decision.primary_issue == "unsupported_late_claim"
    assert decision.resolution_actions == ["reject_late_refund"]


@pytest.mark.parametrize(
    ("payment", "matches"),
    [("11.10", True), ("11.11", False)],
)
def test_payment_tolerance_boundary(context_factory, payment, matches):
    context = context_factory(payment_values=(payment,))
    assert context.financials.payment_matches_order_total is matches


def test_multiple_items_and_payments_are_not_multiplied(context_factory):
    context = context_factory(
        item_values=(("10.00", "1.00"), ("20.00", "2.00")),
        payment_values=("11.00", "22.00"),
    )
    assert context.financials.item_total_brl == Decimal("30.00")
    assert context.financials.freight_total_brl == Decimal("3.00")
    assert context.financials.payment_total_brl == Decimal("33.00")


def test_money_rounding_and_negative_zero():
    assert money(Decimal("1.005")) == Decimal("1.01")
    assert money(Decimal("-0.001")) == Decimal("0.00")


def test_policy_no_match(context_factory):
    context = context_factory(delivered_late=None, payment_values=())
    with pytest.raises(PolicyNoMatchError):
        PolicyEngine().evaluate(context)
