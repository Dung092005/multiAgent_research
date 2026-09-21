from datetime import UTC, datetime
from decimal import Decimal

import pytest

from experiments.pvoc_v0.counterfactual import CounterfactualRunner
from experiments.pvoc_v0.metrics import communication_cost, immediate_utility, v_star
from experiments.pvoc_v0.observations import PrivateObservationBuilder, observation_fingerprint
from experiments.pvoc_v0.schemas import (
    CandidateMessage,
    DecisionExecution,
    PaymentObservation,
)
from src.schemas.case_input import CaseInput, CustomerRequest
from src.schemas.records import (
    DeliveryTimeline,
    ItemRecord,
    OrderRecord,
    PaymentRecord,
    ReviewRecord,
    SellerRecord,
)


class FakeRepository:
    async def get_order(self, _order_id: str) -> OrderRecord:
        return OrderRecord(
            order_id="order-1",
            customer_id="customer-1",
            order_status="delivered",
            order_purchase_timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            order_approved_at=datetime(2025, 1, 1, 1, tzinfo=UTC),
            order_delivered_carrier_date=datetime(2025, 1, 3, tzinfo=UTC),
            order_delivered_customer_date=datetime(2025, 1, 9, tzinfo=UTC),
            order_estimated_delivery_date=datetime(2025, 1, 7, tzinfo=UTC),
        )

    async def get_order_items(self, _order_id: str) -> list[ItemRecord]:
        return [
            ItemRecord(
                order_id="order-1",
                order_item_id=1,
                product_id="product-1",
                seller_id="seller-1",
                shipping_limit_date=datetime(2025, 1, 2, tzinfo=UTC),
                price=Decimal("10.00"),
                freight_value=Decimal("2.00"),
            )
        ]

    async def get_order_sellers(self, _order_id: str) -> list[SellerRecord]:
        return [
            SellerRecord(
                seller_id="seller-1",
                seller_zip_code_prefix=12345,
                seller_city="Sao Paulo",
                seller_state="SP",
            )
        ]

    async def get_order_payments(self, _order_id: str) -> list[PaymentRecord]:
        return [
            PaymentRecord(
                order_id="order-1",
                payment_sequential=1,
                payment_type="credit_card",
                payment_installments=2,
                payment_value=Decimal("12.00"),
            )
        ]

    async def get_order_delivery_timeline(self, _order_id: str) -> DeliveryTimeline:
        return DeliveryTimeline(
            order_delivered_carrier_date=datetime(2025, 1, 3, tzinfo=UTC),
            order_delivered_customer_date=datetime(2025, 1, 9, tzinfo=UTC),
            order_estimated_delivery_date=datetime(2025, 1, 7, tzinfo=UTC),
        )

    async def get_order_reviews(self, _order_id: str) -> list[ReviewRecord]:
        return [
            ReviewRecord(
                review_id="review-1",
                order_id="order-1",
                review_score=1,
                review_creation_date=datetime(2025, 1, 10, tzinfo=UTC),
                review_answer_timestamp=datetime(2025, 1, 11, tzinfo=UTC),
            )
        ]


def sample_case() -> CaseInput:
    return CaseInput(
        case_id="EC_001",
        opened_at=datetime(2025, 1, 12, tzinfo=UTC),
        customer_request=CustomerRequest(
            language="en",
            message="My order was late.",
            claimed_order_id="order-1",
        ),
        policy_version="EC_POLICY_V1",
    )


@pytest.mark.asyncio
async def test_private_observations_keep_evidence_isolated() -> None:
    observations = await PrivateObservationBuilder(FakeRepository()).build(sample_case())

    order_payload = observations.order_seller.model_dump(mode="json")
    payment_payload = observations.payment.model_dump(mode="json")
    delivery_payload = observations.delivery.model_dump(mode="json")

    assert "payments" not in order_payload
    assert "timeline" not in order_payload
    assert "order_status" not in payment_payload
    assert "items" not in payment_payload
    assert "timeline" not in payment_payload
    assert "order_status" not in delivery_payload
    assert "payments" not in delivery_payload
    assert observations.payment.payment_total_brl == Decimal("12.00")


class FakeRecipient:
    async def decide(self, observation, received_message=None) -> DecisionExecution:
        action = (
            "late_delivery_logistics"
            if received_message is not None
            else "unsupported_late_claim"
        )
        return DecisionExecution(
            agent=observation.agent,
            predicted_root_cause=action,
            confidence=0.8,
            short_reason="Controlled fake recipient response.",
            prompt_tokens=11,
            completion_tokens=7,
            latency_ms=3.5,
            observation_fingerprint=observation_fingerprint(observation),
        )


@pytest.mark.asyncio
async def test_counterfactual_branches_start_from_identical_private_state() -> None:
    observation = PaymentObservation(
        case_id="EC_001",
        order_id="order-1",
        payments=[],
        payment_total_brl=Decimal("0.00"),
        payment_row_count=0,
    )
    message = CandidateMessage(
        case_id="EC_001",
        sender="order_seller_agent",
        recipient="payment_agent",
        content="Delivery timing indicates a logistics delay.",
        evidence_ids=["order:order-1"],
    )

    record = await CounterfactualRunner(lambda_cost=0.1).run(
        message=message,
        recipient=FakeRecipient(),
        recipient_observation=observation,
        oracle_action="late_delivery_logistics",
    )

    assert record.with_message.observation_fingerprint == record.without_message.observation_fingerprint
    assert record.action_with_message == "late_delivery_logistics"
    assert record.action_without_message == "unsupported_late_claim"
    assert record.immediate_utility_with_message == 1.0
    assert record.immediate_utility_without_message == 0.0


def test_metric_calculation_uses_oracle_accuracy_and_message_cost() -> None:
    message = CandidateMessage(
        case_id="EC_001",
        sender="payment_agent",
        recipient="delivery_agent",
        content="Payment evidence is complete.",
        evidence_ids=["payment:order-1:1", "payment:order-1:2"],
    )

    cost = communication_cost(message)

    assert cost == 6.0
    assert immediate_utility("valid_split_payment", "valid_split_payment") == 1.0
    assert immediate_utility("valid_split_payment", "unsupported_late_claim") == 0.0
    assert v_star(1.0, 0.0, 0.25, cost) == pytest.approx(-0.5)
