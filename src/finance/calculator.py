"""Decimal-only Olist order financial calculations."""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from src.schemas.records import Financials, ItemRecord, PaymentRecord

MONEY_QUANTIZER = Decimal("0.01")
PAYMENT_TOLERANCE = Decimal("0.10")


def money(value: Decimal | int | str) -> Decimal:
    result = Decimal(value).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    return Decimal("0.00") if result == 0 else result


class FinancialCalculator:
    @staticmethod
    def calculate(items: Iterable[ItemRecord], payments: Iterable[PaymentRecord]) -> Financials:
        item_rows = list(items)
        payment_rows = list(payments)
        item_total = money(sum((item.price for item in item_rows), Decimal("0")))
        freight_total = money(sum((item.freight_value for item in item_rows), Decimal("0")))
        payment_total = money(
            sum((payment.payment_value for payment in payment_rows), Decimal("0"))
        )
        difference = money(payment_total - item_total - freight_total)
        return Financials(
            item_total_brl=item_total,
            freight_total_brl=freight_total,
            payment_total_brl=payment_total,
            payment_difference_brl=difference,
            payment_row_count=len(payment_rows),
            payment_matches_order_total=abs(difference) <= PAYMENT_TOLERANCE,
        )
