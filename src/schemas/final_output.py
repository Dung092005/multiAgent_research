"""Strict final output schema matching README.md."""

from decimal import Decimal
from typing import Literal

from pydantic import Field, field_serializer, model_validator

from src.schemas.agent_reports import RankedCause, ResponsibleParty
from src.schemas.common import StrictModel

PrimaryIssue = Literal[
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "valid_split_payment",
    "unsupported_late_claim",
]


class Assessment(StrictModel):
    primary_issue: PrimaryIssue
    case_status: Literal["action_required", "no_action"]
    confidence: float = Field(ge=0, le=1)


class AffectedEntities(StrictModel):
    order_ids: list[str] = Field(max_length=5)
    item_ids: list[str] = Field(max_length=5)
    seller_ids: list[str] = Field(max_length=5)
    payment_ids: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def unique_ids(self) -> "AffectedEntities":
        for name in ("order_ids", "item_ids", "seller_ids", "payment_ids"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} contains duplicate IDs")
        return self


class RootCauseAnalysis(StrictModel):
    ranked_causes: list[RankedCause] = Field(min_length=1, max_length=3)
    responsible_parties: list[ResponsibleParty] = Field(max_length=3)


class FinancialResolution(StrictModel):
    currency: Literal["BRL"] = "BRL"
    item_total_brl: Decimal = Field(ge=0)
    freight_total_brl: Decimal = Field(ge=0)
    payment_total_brl: Decimal = Field(ge=0)
    recommended_refund_brl: Decimal = Field(ge=0)

    @field_serializer(
        "item_total_brl",
        "freight_total_brl",
        "payment_total_brl",
        "recommended_refund_brl",
        when_used="json",
    )
    def serialize_money(self, value: Decimal) -> float:
        return float(value)


class FinalCaseOutput(StrictModel):
    case_id: str = Field(pattern=r"^EC_\d{3}$")
    assessment: Assessment
    affected_entities: AffectedEntities
    root_cause_analysis: RootCauseAnalysis
    evidence_ids: list[str] = Field(max_length=10)
    financial_resolution: FinancialResolution
    resolution_actions: list[
        Literal[
            "issue_full_refund",
            "refund_freight",
            "explain_valid_split_payment",
            "reject_late_refund",
        ]
    ] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_invariants(self) -> "FinalCaseOutput":
        refund = self.financial_resolution.recommended_refund_brl
        if self.assessment.case_status == "action_required" and refund <= 0:
            raise ValueError("action_required must have a positive refund")
        if self.assessment.case_status == "no_action" and refund != 0:
            raise ValueError("no_action must have a zero refund")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids contains duplicates")
        if self.root_cause_analysis.ranked_causes[0].rank != 1:
            raise ValueError("first ranked cause must have rank 1")
        return self
