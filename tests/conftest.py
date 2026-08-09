"""Shared deterministic test records."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.finance.calculator import FinancialCalculator
from src.schemas.records import ItemRecord, OrderRecord, PaymentRecord, PolicyContext


@pytest.fixture
def context_factory():
    def make(
        *,
        status: str = "delivered",
        delivered_late: bool | None = False,
        seller_late: bool = False,
        item_values: tuple[tuple[str, str], ...] = (("10.00", "1.00"),),
        payment_values: tuple[str, ...] = ("11.00",),
    ) -> PolicyContext:
        base = datetime(2018, 1, 1)
        estimated = base + timedelta(days=10)
        delivered = None
        carrier = None
        if delivered_late is not None:
            delivered = estimated + timedelta(days=1 if delivered_late else -1)
            carrier = base + timedelta(days=6 if seller_late else 4)
        order = OrderRecord(
            order_id="order-1",
            customer_id="customer-1",
            order_status=status,
            order_purchase_timestamp=base,
            order_approved_at=base + timedelta(hours=1),
            order_delivered_carrier_date=carrier,
            order_delivered_customer_date=delivered,
            order_estimated_delivery_date=estimated,
        )
        items = [
            ItemRecord(
                order_id=order.order_id,
                order_item_id=index,
                product_id=f"product-{index}",
                seller_id=f"seller-{index}",
                shipping_limit_date=base + timedelta(days=5),
                price=Decimal(price),
                freight_value=Decimal(freight),
            )
            for index, (price, freight) in enumerate(item_values, start=1)
        ]
        payments = [
            PaymentRecord(
                order_id=order.order_id,
                payment_sequential=index,
                payment_type="credit_card",
                payment_installments=1,
                payment_value=Decimal(value),
            )
            for index, value in enumerate(payment_values, start=1)
        ]
        return PolicyContext(
            order=order,
            items=items,
            payments=payments,
            financials=FinancialCalculator.calculate(items, payments),
        )

    return make
