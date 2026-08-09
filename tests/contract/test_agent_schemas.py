import pytest
from pydantic import ValidationError

from src.schemas.agent_messages import AgentResult, AgentTask


def test_agent_task_contract():
    task = AgentTask(
        task_id="task-1",
        run_id="run-1",
        case_id="EC_001",
        from_agent="coordinator_agent",
        to_agent="payment_agent",
        objective="Reconcile payments",
        order_id="order-1",
        allowed_tools=["get_order_payments"],
        provided_context={},
    )
    assert task.to_agent == "payment_agent"


def test_agent_result_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AgentResult(
            task_id="task-1",
            case_id="EC_001",
            agent_name="payment_agent",
            status="success",
            summary="done",
            verified_facts=[],
            evidence_candidates=[],
            warnings=[],
            chain_of_thought="not allowed",
        )
