"""Private-observation construction using the existing read-only Olist tools."""

import hashlib
import json
from decimal import Decimal
from typing import Any

from experiments.pvoc_v0.schemas import (
    DeliveryObservation,
    DeliveryShippingEvidence,
    ObservationBundle,
    OrderSellerItemEvidence,
    OrderSellerObservation,
    PaymentObservation,
    PrivateObservation,
)
from src.database.repository import OlistRepository
from src.schemas.case_input import CaseInput
from src.tools.delivery_tools import DeliveryTools
from src.tools.order_tools import OrderTools
from src.tools.payment_tools import PaymentTools


class _NoopTrace:
    """Keeps the production tool interfaces while avoiding production trace files."""

    async def emit(self, _event: str, **_fields: Any) -> None:
        return None


class PrivateObservationBuilder:
    """Builds agent-local views without changing production agents or their graph."""

    def __init__(self, repository: OlistRepository) -> None:
        self._repository = repository

    async def build(self, case: CaseInput) -> ObservationBundle:
        case_id = case.case_id
        order_id = case.customer_request.claimed_order_id
        trace = _NoopTrace()

        order_tools = OrderTools(self._repository, trace, case_id, "pvoc_order_observation")
        order = await order_tools.get_order(order_id)
        order_items = await order_tools.get_order_items(order_id)
        sellers = await order_tools.get_order_sellers(order_id)

        payment_tools = PaymentTools(self._repository, trace, case_id, "pvoc_payment_observation")
        payments = await payment_tools.get_order_payments(order_id)

        delivery_tools = DeliveryTools(
            self._repository, trace, case_id, "pvoc_delivery_observation"
        )
        timeline = await delivery_tools.get_order_delivery_timeline(order_id)
        delivery_items = await delivery_tools.get_order_items(order_id)
        reviews = await delivery_tools.get_order_reviews(order_id)

        return ObservationBundle(
            order_seller=OrderSellerObservation(
                case_id=case_id,
                order_id=order.order_id,
                order_status=order.order_status,
                order_purchase_timestamp=order.order_purchase_timestamp,
                order_approved_at=order.order_approved_at,
                items=[
                    OrderSellerItemEvidence(
                        order_item_id=item.order_item_id,
                        seller_id=item.seller_id,
                        product_id=item.product_id,
                        shipping_limit_date=item.shipping_limit_date,
                        price=item.price,
                        freight_value=item.freight_value,
                    )
                    for item in order_items
                ],
                sellers=sellers,
            ),
            payment=PaymentObservation(
                case_id=case_id,
                order_id=order_id,
                payments=payments,
                payment_total_brl=sum(
                    (payment.payment_value for payment in payments), Decimal("0.00")
                ),
                payment_row_count=len(payments),
            ),
            delivery=DeliveryObservation(
                case_id=case_id,
                order_id=order_id,
                timeline=timeline,
                shipping_limits=[
                    DeliveryShippingEvidence(
                        order_item_id=item.order_item_id,
                        seller_id=item.seller_id,
                        shipping_limit_date=item.shipping_limit_date,
                    )
                    for item in delivery_items
                ],
                review_count=len(reviews),
            ),
        )


def observation_fingerprint(observation: PrivateObservation) -> str:
    """Stable state identifier used to assert paired counterfactual conditions."""

    payload = json.dumps(
        observation.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def observation_evidence_ids(observation: PrivateObservation) -> list[str]:
    if isinstance(observation, OrderSellerObservation):
        evidence = [f"order:{observation.order_id}"]
        evidence.extend(
            f"item:{observation.order_id}:{item.order_item_id}" for item in observation.items
        )
        evidence.extend(f"seller:{seller.seller_id}" for seller in observation.sellers)
        return list(dict.fromkeys(evidence))
    if isinstance(observation, PaymentObservation):
        return [
            f"payment:{payment.order_id}:{payment.payment_sequential}"
            for payment in observation.payments
        ]
    evidence = [f"order:{observation.order_id}"]
    evidence.extend(
        f"item:{observation.order_id}:{item.order_item_id}" for item in observation.shipping_limits
    )
    return list(dict.fromkeys(evidence))
