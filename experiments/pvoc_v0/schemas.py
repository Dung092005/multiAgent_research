"""Strict, research-only contracts for the PVoC v0 study."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from src.schemas.common import StrictModel
from src.schemas.records import DeliveryTimeline, PaymentRecord, SellerRecord

PVoCAgentName = Literal["order_seller_agent", "payment_agent", "delivery_agent"]
RootCauseCode = Literal[
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "unsupported_late_claim",
]
StabilityCondition = Literal["with_message", "without_message"]


class OrderSellerItemEvidence(StrictModel):
    """Order/seller fields only; delivery timestamps and payments are intentionally absent."""

    order_item_id: int
    seller_id: str
    product_id: str
    shipping_limit_date: datetime
    price: Decimal
    freight_value: Decimal


class OrderSellerObservation(StrictModel):
    agent: Literal["order_seller_agent"] = "order_seller_agent"
    case_id: str = Field(pattern=r"^EC_\d{3}$")
    order_id: str
    order_status: str
    order_purchase_timestamp: datetime
    order_approved_at: datetime | None = None
    items: list[OrderSellerItemEvidence]
    sellers: list[SellerRecord]


class PaymentObservation(StrictModel):
    """Payment rows plus payment-only aggregates; no order, item, or delivery fields."""

    agent: Literal["payment_agent"] = "payment_agent"
    case_id: str = Field(pattern=r"^EC_\d{3}$")
    order_id: str
    payments: list[PaymentRecord]
    payment_total_brl: Decimal
    payment_row_count: int = Field(ge=0)


class DeliveryShippingEvidence(StrictModel):
    order_item_id: int
    seller_id: str
    shipping_limit_date: datetime


class DeliveryObservation(StrictModel):
    """Delivery timeline and shipping deadlines; no status or payment fields."""

    agent: Literal["delivery_agent"] = "delivery_agent"
    case_id: str = Field(pattern=r"^EC_\d{3}$")
    order_id: str
    timeline: DeliveryTimeline
    shipping_limits: list[DeliveryShippingEvidence]
    review_count: int = Field(ge=0)


PrivateObservation = OrderSellerObservation | PaymentObservation | DeliveryObservation


class ObservationBundle(StrictModel):
    order_seller: OrderSellerObservation
    payment: PaymentObservation
    delivery: DeliveryObservation

    def for_agent(self, agent: PVoCAgentName) -> PrivateObservation:
        return {
            "order_seller_agent": self.order_seller,
            "payment_agent": self.payment,
            "delivery_agent": self.delivery,
        }[agent]


class CandidateMessage(StrictModel):
    case_id: str = Field(pattern=r"^EC_\d{3}$")
    sender: PVoCAgentName
    recipient: PVoCAgentName
    content: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_distinct_agents(self) -> "CandidateMessage":
        if self.sender == self.recipient:
            raise ValueError("CandidateMessage sender and recipient must differ")
        return self


class DecisionResponse(StrictModel):
    """The only content the LLM is permitted to return for a PVoC decision point."""

    predicted_root_cause: RootCauseCode
    confidence: float = Field(ge=0, le=1)
    short_reason: str = Field(min_length=1, max_length=400)


class DecisionExecution(DecisionResponse):
    agent: PVoCAgentName
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    observation_fingerprint: str = Field(min_length=8, max_length=128)


class CounterfactualOutcome(StrictModel):
    action: RootCauseCode
    immediate_utility: float
    terminal_utility: float
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    observation_fingerprint: str = Field(min_length=8, max_length=128)


class CounterfactualRecord(StrictModel):
    """One directed-message experiment with paired deliver/drop outcomes."""

    experiment_id: str
    case_id: str = Field(pattern=r"^EC_\d{3}$")
    candidate_message: CandidateMessage
    oracle_action: RootCauseCode
    action_with_message: RootCauseCode
    action_without_message: RootCauseCode
    immediate_utility_with_message: float
    immediate_utility_without_message: float
    terminal_utility_with_message: float
    terminal_utility_without_message: float
    token_usage_with_message: int = Field(ge=0)
    token_usage_without_message: int = Field(ge=0)
    latency_with_message_ms: float = Field(ge=0)
    latency_without_message_ms: float = Field(ge=0)
    communication_cost: float = Field(ge=0)
    lambda_cost: float = Field(ge=0)
    v_star: float
    with_message: CounterfactualOutcome
    without_message: CounterfactualOutcome

    @model_validator(mode="after")
    def ensure_paired_state(self) -> "CounterfactualRecord":
        if (
            self.with_message.observation_fingerprint
            != self.without_message.observation_fingerprint
        ):
            raise ValueError("deliver and drop must use the same private observation")
        return self


class StabilityTrialRecord(StrictModel):
    """One raw repeated recipient execution for the EC_001 stability study."""

    case_id: str = Field(pattern=r"^EC_\d{3}$")
    sender: PVoCAgentName
    recipient: PVoCAgentName
    trial_index: int = Field(ge=1)
    condition: StabilityCondition
    predicted_root_cause: RootCauseCode
    confidence: float = Field(ge=0, le=1)
    short_reason: str = Field(min_length=1, max_length=400)
    oracle_action: RootCauseCode
    utility: float
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    observation_fingerprint: str = Field(min_length=8, max_length=128)
    candidate_message_id: str = Field(min_length=8, max_length=128)
    candidate_message_content_hash: str = Field(min_length=8, max_length=128)
    communication_cost: float = Field(ge=0)
    lambda_cost: float = Field(ge=0)
