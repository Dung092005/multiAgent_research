import os
from pathlib import Path

import pytest

from scripts.validate_inputs import load_and_validate_inputs
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository
from src.graph import Workflow
from src.llm.openrouter_client import StructuredResponse
from src.observability.trace_logger import TraceLogger
from src.state import initial_state

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 with PostgreSQL available",
)


class DeterministicMockLLM:
    async def structured(self, *, system_prompt, user_payload, response_model):
        name = response_model.__name__
        if name == "TriageResponse":
            payload = {
                "intent": "ambiguous",
                "summary": "Structured triage completed.",
                "order_objective": "Verify order and sellers.",
                "payment_objective": "Reconcile all payment rows.",
                "delivery_objective": "Verify delivery timestamps.",
            }
        elif name == "Narrative":
            payload = {"summary": "Verified supplied tool facts.", "warnings": []}
        elif name == "PolicyReview":
            decision = user_payload["authoritative_decision"]
            payload = {
                "primary_issue": decision["primary_issue"],
                "root_cause": decision["ranked_causes"][0]["cause_code"],
                "action": decision["resolution_actions"][0],
                "summary": "Deterministic policy is consistent.",
                "warnings": [],
            }
        elif name == "VerifierReview":
            payload = {"passed": True, "summary": "Candidate reviewed.", "warnings": []}
        else:
            raise AssertionError(f"Unexpected response model: {name}")
        return StructuredResponse(response_model.model_validate(payload))


@pytest.mark.asyncio
async def test_each_rule_runs_through_graph_with_mock_llm(tmp_path):
    engine = create_engine(get_settings(), read_only=True)
    repository = OlistRepository(create_session_factory(engine))
    cases = load_and_validate_inputs(Path("input"))
    representatives = [cases[index] for index in (0, 9, 18, 26, 34, 42)]
    trace = TraceLogger(tmp_path / "trace.jsonl", "test-run")
    trace.reset()
    workflow = Workflow(
        llm=DeterministicMockLLM(),
        repository=repository,
        trace=trace,
        output_dir=tmp_path / "output",
    )
    for case in representatives:
        state = await workflow.graph.ainvoke(initial_state("test-run", case))
        assert state["output_path"]
        assert Path(state["output_path"]).is_file()
        assert not state["errors"]
    trace_text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "task_assigned" in trace_text
    assert "handoff_created" in trace_text
    assert "verification_passed" in trace_text
    await engine.dispose()
