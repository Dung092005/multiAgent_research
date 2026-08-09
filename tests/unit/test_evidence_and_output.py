import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.errors import EvidenceValidationError
from src.schemas.agent_reports import RankedCause, ResponsibleParty
from src.schemas.final_output import (
    AffectedEntities,
    Assessment,
    FinalCaseOutput,
    FinancialResolution,
    RootCauseAnalysis,
)
from src.verification.evidence_validator import parse_evidence_id, prioritize_evidence


@pytest.mark.parametrize(
    "evidence_id",
    [
        "order:abc123",
        "item:abc123:2",
        "payment:abc123:1",
        "seller:seller123",
        "policy:SELLER_HANDOFF_AFTER_LIMIT",
    ],
)
def test_evidence_parser_accepts_supported_ids(evidence_id):
    assert parse_evidence_id(evidence_id).kind


@pytest.mark.parametrize(
    "evidence_id",
    ["review:abc", "tracking:123", "item:abc:x", "policy:MADE_UP", "free text"],
)
def test_evidence_parser_rejects_false_positives(evidence_id):
    with pytest.raises(EvidenceValidationError):
        parse_evidence_id(evidence_id)


def test_evidence_priority_and_limit():
    evidence = prioritize_evidence(
        "order1",
        "DELIVERY_WITHIN_ESTIMATE",
        [f"order1:{index}" for index in range(1, 6)],
        [f"order1:{index}" for index in range(1, 6)],
        ["seller1"],
    )
    assert len(evidence) == 10
    assert evidence[:2] == ["order:order1", "policy:DELIVERY_WITHIN_ESTIMATE"]


def make_output(**overrides):
    payload = {
        "case_id": "EC_001",
        "assessment": Assessment(
            primary_issue="late_delivery_seller", case_status="action_required", confidence=0.98
        ),
        "affected_entities": AffectedEntities(
            order_ids=["order1"],
            item_ids=["order1:1"],
            seller_ids=["seller1"],
            payment_ids=["order1:1"],
        ),
        "root_cause_analysis": RootCauseAnalysis(
            ranked_causes=[RankedCause(cause_code="SELLER_HANDOFF_AFTER_LIMIT", rank=1)],
            responsible_parties=[ResponsibleParty(party_type="seller", party_id="seller1")],
        ),
        "evidence_ids": ["order:order1", "policy:SELLER_HANDOFF_AFTER_LIMIT"],
        "financial_resolution": FinancialResolution(
            item_total_brl=Decimal("10.00"),
            freight_total_brl=Decimal("1.00"),
            payment_total_brl=Decimal("11.00"),
            recommended_refund_brl=Decimal("1.00"),
        ),
        "resolution_actions": ["refund_freight"],
    }
    payload.update(overrides)
    return FinalCaseOutput(**payload)


def test_money_serializes_as_json_number():
    payload = json.loads(make_output().model_dump_json())
    assert payload["financial_resolution"]["payment_total_brl"] == 11.0
    assert not isinstance(payload["financial_resolution"]["payment_total_brl"], str)


def test_action_required_rejects_zero_refund():
    with pytest.raises(ValidationError):
        make_output(
            financial_resolution=FinancialResolution(
                item_total_brl=Decimal("10"),
                freight_total_brl=Decimal("1"),
                payment_total_brl=Decimal("11"),
                recommended_refund_brl=Decimal("0"),
            )
        )


def test_output_limits_are_enforced():
    with pytest.raises(ValidationError):
        AffectedEntities(
            order_ids=[str(index) for index in range(6)], item_ids=[], seller_ids=[], payment_ids=[]
        )
