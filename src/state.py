"""LangGraph case state contract."""

from typing import TypedDict

from src.schemas.agent_reports import (
    CoordinatorPlan,
    DeliveryReport,
    EvidenceBoard,
    OrderSellerReport,
    PaymentReport,
    PolicyDecision,
    VerificationResult,
)
from src.schemas.case_input import CaseInput
from src.schemas.final_output import FinalCaseOutput


class CaseState(TypedDict):
    run_id: str
    case_input: CaseInput
    coordinator_plan: CoordinatorPlan | None
    order_seller_report: OrderSellerReport | None
    payment_report: PaymentReport | None
    delivery_report: DeliveryReport | None
    evidence_board: EvidenceBoard | None
    policy_decision: PolicyDecision | None
    verification_result: VerificationResult | None
    repair_count: int
    final_output: FinalCaseOutput | None
    output_path: str | None
    errors: list[str]


def initial_state(run_id: str, case: CaseInput) -> CaseState:
    return {
        "run_id": run_id,
        "case_input": case,
        "coordinator_plan": None,
        "order_seller_report": None,
        "payment_report": None,
        "delivery_report": None,
        "evidence_board": None,
        "policy_decision": None,
        "verification_result": None,
        "repair_count": 0,
        "final_output": None,
        "output_path": None,
        "errors": [],
    }
