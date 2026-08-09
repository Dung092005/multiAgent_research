"""Typed database and deterministic calculation records."""

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from src.schemas.common import StrictModel


class OrderRecord(StrictModel):
    order_id: str
    customer_id: str
    order_status: str
    order_purchase_timestamp: datetime
    order_approved_at: datetime | None = None
    order_delivered_carrier_date: datetime | None = None
    order_delivered_customer_date: datetime | None = None
    order_estimated_delivery_date: datetime


class ItemRecord(StrictModel):
    order_id: str
    order_item_id: int
    product_id: str
    seller_id: str
    shipping_limit_date: datetime
    price: Decimal
    freight_value: Decimal


class SellerRecord(StrictModel):
    seller_id: str
    seller_zip_code_prefix: int
    seller_city: str
    seller_state: str


class PaymentRecord(StrictModel):
    order_id: str
    payment_sequential: int
    payment_type: str
    payment_installments: int
    payment_value: Decimal


class ReviewRecord(StrictModel):
    review_id: str
    order_id: str
    review_score: int
    review_comment_title: str | None = None
    review_comment_message: str | None = None
    review_creation_date: datetime
    review_answer_timestamp: datetime


class Financials(StrictModel):
    item_total_brl: Decimal = Decimal("0.00")
    freight_total_brl: Decimal = Decimal("0.00")
    payment_total_brl: Decimal = Decimal("0.00")
    payment_difference_brl: Decimal = Decimal("0.00")
    payment_row_count: int = 0
    payment_matches_order_total: bool = False


class DeliveryTimeline(StrictModel):
    order_delivered_carrier_date: datetime | None = None
    order_delivered_customer_date: datetime | None = None
    order_estimated_delivery_date: datetime


class EntityReferenceCheck(StrictModel):
    valid: bool
    missing_item_ids: list[str] = Field(default_factory=list)
    missing_seller_ids: list[str] = Field(default_factory=list)
    missing_payment_ids: list[str] = Field(default_factory=list)


class PolicyContext(StrictModel):
    order: OrderRecord
    items: list[ItemRecord]
    payments: list[PaymentRecord]
    financials: Financials

    @property
    def delivered_after_estimate(self) -> bool:
        delivered = self.order.order_delivered_customer_date
        return bool(delivered and delivered > self.order.order_estimated_delivery_date)

    @property
    def delivered_within_estimate(self) -> bool:
        delivered = self.order.order_delivered_customer_date
        return bool(delivered and delivered <= self.order.order_estimated_delivery_date)

    @property
    def late_items(self) -> list[ItemRecord]:
        carrier_date = self.order.order_delivered_carrier_date
        if carrier_date is None:
            return []
        return [item for item in self.items if carrier_date > item.shipping_limit_date]
