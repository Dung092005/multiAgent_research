"""Agent reports, policy decisions, and verification contracts."""

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from src.schemas.agent_messages import AgentTask
from src.schemas.common import StrictModel
from src.schemas.records import DeliveryTimeline, Financials, ItemRecord, OrderRecord, PaymentRecord


class CoordinatorPlan(StrictModel):
    intent: Literal["canceled", "unavailable", "late_delivery", "payment", "refund", "ambiguous"]
    summary: str
    tasks: list[AgentTask] = Field(min_length=3, max_length=3)


class OrderSellerReport(StrictModel):
    task_id: str
    order: OrderRecord
    items: list[ItemRecord]
    seller_ids: list[str]
    missing_data: list[str] = Field(default_factory=list)
    evidence_candidates: list[str] = Field(default_factory=list)
    summary: str
    warnings: list[str] = Field(default_factory=list)


class PaymentReport(StrictModel):
    task_id: str
    payments: list[PaymentRecord]
    financials: Financials
    is_split_payment: bool
    evidence_candidates: list[str] = Field(default_factory=list)
    summary: str
    warnings: list[str] = Field(default_factory=list)


class DeliveryReport(StrictModel):
    task_id: str
    timeline: DeliveryTimeline
    delivered_after_estimate: bool
    carrier_handoff_after_limit: bool
    late_item_ids: list[str]
    late_seller_ids: list[str]
    missing_data: list[str] = Field(default_factory=list)
    evidence_candidates: list[str] = Field(default_factory=list)
    summary: str
    warnings: list[str] = Field(default_factory=list)


class EvidenceBoard(StrictModel):
    case_id: str
    customer_claim: str
    order_report: OrderSellerReport
    payment_report: PaymentReport
    delivery_report: DeliveryReport
    verified_facts: dict[str, Any]
    evidence_ids: list[str]
    conflicts: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


class RankedCause(StrictModel):
    cause_code: Literal[
        "SELLER_HANDOFF_AFTER_LIMIT",
        "CARRIER_DELIVERED_AFTER_ESTIMATE",
        "ORDER_CANCELED_AFTER_PAYMENT",
        "ORDER_UNAVAILABLE_AFTER_PAYMENT",
        "MULTIPLE_PAYMENTS_RECONCILED",
        "DELIVERY_WITHIN_ESTIMATE",
    ]
    rank: int = Field(ge=1, le=3)


class ResponsibleParty(StrictModel):
    party_type: Literal["platform", "seller", "logistics_provider"]
    party_id: str


class PolicyDecision(StrictModel):
    primary_issue: Literal[
        "canceled_order_paid",
        "unavailable_order_paid",
        "late_delivery_seller",
        "late_delivery_logistics",
        "valid_split_payment",
        "unsupported_late_claim",
    ]
    case_status: Literal["action_required", "no_action"]
    ranked_causes: list[RankedCause] = Field(min_length=1, max_length=3)
    responsible_parties: list[ResponsibleParty] = Field(max_length=3)
    recommended_refund_brl: Decimal
    resolution_actions: list[
        Literal[
            "issue_full_refund",
            "refund_freight",
            "explain_valid_split_payment",
            "reject_late_refund",
        ]
    ] = Field(min_length=1, max_length=5)
    confidence: float = Field(ge=0, le=1)
    summary: str = ""


class VerificationError(StrictModel):
    code: str
    field: str
    message: str
    expected: Any | None = None
    actual: Any | None = None
    repair_target: str | None = None


class VerificationResult(StrictModel):
    passed: bool
    errors: list[VerificationError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    repair_target: str | None = None
