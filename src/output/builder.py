"""Build the README-compatible output exclusively from verified state."""

from src.schemas.agent_reports import EvidenceBoard, PolicyDecision
from src.schemas.final_output import (
    AffectedEntities,
    Assessment,
    FinalCaseOutput,
    FinancialResolution,
    RootCauseAnalysis,
)
from src.verification.evidence_validator import prioritize_evidence


def build_final_output(
    case_id: str, board: EvidenceBoard, decision: PolicyDecision
) -> FinalCaseOutput:
    order = board.order_report.order
    ordered_items = sorted(board.order_report.items, key=lambda row: row.order_item_id)
    ordered_payments = sorted(board.payment_report.payments, key=lambda row: row.payment_sequential)
    item_ids = [f"{order.order_id}:{row.order_item_id}" for row in ordered_items[:5]]
    payment_ids = [f"{order.order_id}:{row.payment_sequential}" for row in ordered_payments[:5]]
    seller_ids = sorted({row.seller_id for row in ordered_items})[:5]
    policy_code = decision.ranked_causes[0].cause_code
    evidence_ids = prioritize_evidence(
        order.order_id,
        policy_code,
        item_ids,
        payment_ids,
        [party.party_id for party in decision.responsible_parties if party.party_type == "seller"],
    )
    financials = board.payment_report.financials
    return FinalCaseOutput(
        case_id=case_id,
        assessment=Assessment(
            primary_issue=decision.primary_issue,
            case_status=decision.case_status,
            confidence=decision.confidence,
        ),
        affected_entities=AffectedEntities(
            order_ids=[order.order_id],
            item_ids=item_ids,
            seller_ids=seller_ids,
            payment_ids=payment_ids,
        ),
        root_cause_analysis=RootCauseAnalysis(
            ranked_causes=decision.ranked_causes,
            responsible_parties=decision.responsible_parties,
        ),
        evidence_ids=evidence_ids,
        financial_resolution=FinancialResolution(
            item_total_brl=financials.item_total_brl,
            freight_total_brl=financials.freight_total_brl,
            payment_total_brl=financials.payment_total_brl,
            recommended_refund_brl=decision.recommended_refund_brl,
        ),
        resolution_actions=decision.resolution_actions,
    )
