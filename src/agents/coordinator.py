"""Coordinator triage and structured specialist assignment."""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from src.agents.base import BaseAgent, StructuredLLM
from src.config.model_config import OPENROUTER_MODEL_ID
from src.errors import LLMRequestError, LLMStructuredOutputError
from src.observability.trace_logger import TraceLogger
from src.schemas.agent_messages import AgentTask
from src.schemas.agent_reports import CoordinatorPlan
from src.schemas.case_input import CaseInput


class TriageResponse(BaseModel):
    intent: Literal["canceled", "unavailable", "late_delivery", "payment", "refund", "ambiguous"]
    summary: str = Field(min_length=1, max_length=1000)
    order_objective: str = Field(min_length=1, max_length=500)
    payment_objective: str = Field(min_length=1, max_length=500)
    delivery_objective: str = Field(min_length=1, max_length=500)


class CoordinatorAgent(BaseAgent):
    name = "coordinator_agent"
    prompt_file = "coordinator.md"

    def __init__(self, llm: StructuredLLM, trace: TraceLogger) -> None:
        super().__init__(llm)
        self._trace = trace

    async def run(self, run_id: str, case: CaseInput) -> CoordinatorPlan:
        await self._trace.emit("agent_started", case_id=case.case_id, agent=self.name)
        prompt_tokens = 0
        completion_tokens = 0
        try:
            response = await self._llm.structured(
                system_prompt=self._prompt,
                user_payload={
                    "case_id": case.case_id,
                    "customer_request": case.customer_request.model_dump(),
                    "policy_version": case.policy_version,
                },
                response_model=TriageResponse,
            )
            triage = response.value
            prompt_tokens = response.prompt_tokens
            completion_tokens = response.completion_tokens
        except (LLMRequestError, LLMStructuredOutputError, TimeoutError) as exc:
            await self._trace.emit(
                "agent_warning",
                case_id=case.case_id,
                agent=self.name,
                message=f"LLM triage skipped: {type(exc).__name__}",
            )
            triage = TriageResponse(
                intent="ambiguous",
                summary="Fallback triage: investigate order, payment, and delivery in parallel.",
                order_objective="Fetch order status, items, and sellers.",
                payment_objective="Fetch payments and reconcile financials.",
                delivery_objective="Fetch delivery timeline and lateness signals.",
            )
        specs = (
            (
                "order_seller_agent",
                triage.order_objective,
                ["get_order", "get_order_items", "get_order_sellers"],
            ),
            (
                "payment_agent",
                triage.payment_objective,
                ["get_order_payments", "calculate_order_financials"],
            ),
            (
                "delivery_agent",
                triage.delivery_objective,
                ["get_order_delivery_timeline", "get_order_items", "get_order_reviews"],
            ),
        )
        tasks = []
        for recipient, objective, allowed in specs:
            task = AgentTask(
                task_id=str(uuid4()),
                run_id=run_id,
                case_id=case.case_id,
                from_agent=self.name,
                to_agent=recipient,
                objective=objective,
                order_id=case.customer_request.claimed_order_id,
                allowed_tools=allowed,
                provided_context={"customer_claim": case.customer_request.message},
            )
            tasks.append(task)
            await self._trace.emit(
                "task_assigned",
                case_id=case.case_id,
                agent=self.name,
                task_id=task.task_id,
                to_agent=recipient,
            )
            await self._trace.emit(
                "handoff_created",
                case_id=case.case_id,
                agent=self.name,
                task_id=task.task_id,
                to_agent=recipient,
            )
        await self._trace.emit(
            "agent_completed",
            case_id=case.case_id,
            agent=self.name,
            model_id=OPENROUTER_MODEL_ID,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return CoordinatorPlan(intent=triage.intent, summary=triage.summary, tasks=tasks)
