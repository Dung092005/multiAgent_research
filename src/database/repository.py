"""Purpose-specific, parameterized, read-only Olist queries."""

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database.models import Order, OrderItem, OrderPayment, OrderReview, Seller
from src.errors import DatabaseError, OrderNotFoundError
from src.finance.calculator import FinancialCalculator
from src.schemas.records import (
    DeliveryTimeline,
    EntityReferenceCheck,
    Financials,
    ItemRecord,
    OrderRecord,
    PaymentRecord,
    PolicyContext,
    ReviewRecord,
    SellerRecord,
)

LOGGER = logging.getLogger(__name__)
TraceHook = Callable[[str, float], Awaitable[None]]


class OlistRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        trace_hook: TraceHook | None = None,
    ) -> None:
        self._sessions = sessions
        self._trace_hook = trace_hook

    async def _timed(self, name: str, operation):
        started = perf_counter()
        try:
            return await operation()
        finally:
            latency_ms = (perf_counter() - started) * 1000
            LOGGER.debug("repository method=%s latency_ms=%.2f", name, latency_ms)
            if self._trace_hook:
                await self._trace_hook(name, latency_ms)

    async def get_order(self, order_id: str) -> OrderRecord:
        async def query() -> OrderRecord:
            async with self._sessions() as session:
                row = await session.scalar(select(Order).where(Order.order_id == order_id))
            if row is None:
                raise OrderNotFoundError(f"Order not found: {order_id}")
            return OrderRecord.model_validate(row, from_attributes=True)

        return await self._timed("get_order", query)

    async def get_order_items(self, order_id: str) -> list[ItemRecord]:
        async def query() -> list[ItemRecord]:
            async with self._sessions() as session:
                result = await session.scalars(
                    select(OrderItem)
                    .where(OrderItem.order_id == order_id)
                    .order_by(OrderItem.order_item_id)
                )
                return [ItemRecord.model_validate(row, from_attributes=True) for row in result]

        return await self._timed("get_order_items", query)

    async def get_order_sellers(self, order_id: str) -> list[SellerRecord]:
        async def query() -> list[SellerRecord]:
            async with self._sessions() as session:
                result = await session.scalars(
                    select(Seller)
                    .join(OrderItem, OrderItem.seller_id == Seller.seller_id)
                    .where(OrderItem.order_id == order_id)
                    .distinct()
                    .order_by(Seller.seller_id)
                )
                return [SellerRecord.model_validate(row, from_attributes=True) for row in result]

        return await self._timed("get_order_sellers", query)

    async def get_order_payments(self, order_id: str) -> list[PaymentRecord]:
        async def query() -> list[PaymentRecord]:
            async with self._sessions() as session:
                result = await session.scalars(
                    select(OrderPayment)
                    .where(OrderPayment.order_id == order_id)
                    .order_by(OrderPayment.payment_sequential)
                )
                return [PaymentRecord.model_validate(row, from_attributes=True) for row in result]

        return await self._timed("get_order_payments", query)

    async def get_order_reviews(self, order_id: str) -> list[ReviewRecord]:
        async def query() -> list[ReviewRecord]:
            async with self._sessions() as session:
                result = await session.scalars(
                    select(OrderReview)
                    .where(OrderReview.order_id == order_id)
                    .order_by(OrderReview.review_row_id)
                )
                return [ReviewRecord.model_validate(row, from_attributes=True) for row in result]

        return await self._timed("get_order_reviews", query)

    async def get_order_delivery_timeline(self, order_id: str) -> DeliveryTimeline:
        order = await self.get_order(order_id)
        return DeliveryTimeline(
            order_delivered_carrier_date=order.order_delivered_carrier_date,
            order_delivered_customer_date=order.order_delivered_customer_date,
            order_estimated_delivery_date=order.order_estimated_delivery_date,
        )

    async def calculate_order_financials(self, order_id: str) -> Financials:
        """Aggregate each one-to-many table independently before combining totals."""

        async def query() -> Financials:
            item_total = (
                select(func.coalesce(func.sum(OrderItem.price), 0))
                .where(OrderItem.order_id == order_id)
                .scalar_subquery()
            )
            freight_total = (
                select(func.coalesce(func.sum(OrderItem.freight_value), 0))
                .where(OrderItem.order_id == order_id)
                .scalar_subquery()
            )
            payment_total = (
                select(func.coalesce(func.sum(OrderPayment.payment_value), 0))
                .where(OrderPayment.order_id == order_id)
                .scalar_subquery()
            )
            payment_count = (
                select(func.count())
                .select_from(OrderPayment)
                .where(OrderPayment.order_id == order_id)
                .scalar_subquery()
            )
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(item_total, freight_total, payment_total, payment_count)
                    )
                ).one()
            items = await self.get_order_items(order_id)
            payments = await self.get_order_payments(order_id)
            calculated = FinancialCalculator.calculate(items, payments)
            database_values = (row[0], row[1], row[2], row[3])
            calculated_values = (
                calculated.item_total_brl,
                calculated.freight_total_brl,
                calculated.payment_total_brl,
                calculated.payment_row_count,
            )
            if calculated_values != database_values:
                raise DatabaseError(
                    f"Independent financial aggregates disagree for order {order_id}"
                )
            return calculated

        return await self._timed("calculate_order_financials", query)

    async def get_policy_context(self, order_id: str) -> PolicyContext:
        order = await self.get_order(order_id)
        items = await self.get_order_items(order_id)
        payments = await self.get_order_payments(order_id)
        financials = FinancialCalculator.calculate(items, payments)
        return PolicyContext(order=order, items=items, payments=payments, financials=financials)

    async def validate_entity_references(
        self,
        order_id: str,
        item_ids: list[str],
        seller_ids: list[str],
        payment_ids: list[str],
    ) -> EntityReferenceCheck:
        items = await self.get_order_items(order_id)
        payments = await self.get_order_payments(order_id)
        actual_item_ids = {f"{row.order_id}:{row.order_item_id}" for row in items}
        actual_seller_ids = {row.seller_id for row in items}
        actual_payment_ids = {f"{row.order_id}:{row.payment_sequential}" for row in payments}
        missing_items = sorted(set(item_ids) - actual_item_ids)
        missing_sellers = sorted(set(seller_ids) - actual_seller_ids)
        missing_payments = sorted(set(payment_ids) - actual_payment_ids)
        return EntityReferenceCheck(
            valid=not (missing_items or missing_sellers or missing_payments),
            missing_item_ids=missing_items,
            missing_seller_ids=missing_sellers,
            missing_payment_ids=missing_payments,
        )
