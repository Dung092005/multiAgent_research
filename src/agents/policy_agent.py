"""Policy agent anchored to the deterministic engine."""

from pydantic import BaseModel, Field

from src.agents.base import BaseAgent, StructuredLLM
from src.config.model_config import OPENROUTER_MODEL_ID
from src.errors import LLMRequestError, LLMStructuredOutputError
from src.observability.trace_logger import TraceLogger
from src.policy.engine import PolicyEngine
from src.schemas.agent_reports import EvidenceBoard, PolicyDecision
from src.schemas.records import PolicyContext


class PolicyReview(BaseModel):
    primary_issue: str
    root_cause: str
    action: str
    summary: str = Field(min_length=1, max_length=1000)
    warnings: list[str] = Field(default_factory=list, max_length=5)


class PolicyAgent(BaseAgent):
    name = "policy_agent"
    prompt_file = "policy_agent.md"

    def __init__(self, llm: StructuredLLM, trace: TraceLogger) -> None:
        super().__init__(llm)
        self._trace = trace
        self._engine = PolicyEngine()

    async def run(
        self, board: EvidenceBoard, repair_count: int
    ) -> tuple[PolicyDecision, list[str]]:
        await self._trace.emit("agent_started", case_id=board.case_id, agent=self.name)
        context = PolicyContext(
            order=board.order_report.order,
            items=board.order_report.items,
            payments=board.payment_report.payments,
            financials=board.payment_report.financials,
        )
        noncritical_missing = board.delivery_report.missing_data == ["review"]
        deterministic = self._engine.evaluate(
            context, repair_count=repair_count, noncritical_missing=noncritical_missing
        )
        prompt_tokens = 0
        completion_tokens = 0
        conflicts: list[str] = []
        try:
            response = await self._llm.structured(
                system_prompt=self._prompt,
                user_payload={
                    "verified_facts": board.verified_facts,
                    "authoritative_decision": deterministic.model_dump(
                        mode="json", exclude={"summary"}
                    ),
                },
                response_model=PolicyReview,
            )
            prompt_tokens = response.prompt_tokens
            completion_tokens = response.completion_tokens
            wanted = (
                deterministic.primary_issue,
                deterministic.ranked_causes[0].cause_code,
                deterministic.resolution_actions[0],
            )
            proposed = (
                response.value.primary_issue,
                response.value.root_cause,
                response.value.action,
            )
            if proposed != wanted:
                conflicts.append("LLM policy interpretation differed from deterministic policy")
                deterministic = self._engine.evaluate(
                    context,
                    repair_count=repair_count,
                    noncritical_missing=noncritical_missing,
                    warnings=True,
                )
                await self._trace.emit(
                    "policy_conflict_detected",
                    case_id=board.case_id,
                    agent=self.name,
                    message=conflicts[0],
                )
            deterministic.summary = response.value.summary
        except (LLMRequestError, LLMStructuredOutputError, TimeoutError) as exc:
            deterministic.summary = (
                f"Deterministic policy selected {deterministic.primary_issue} "
                f"with action {deterministic.resolution_actions[0]}."
            )
            await self._trace.emit(
                "agent_warning",
                case_id=board.case_id,
                agent=self.name,
                message=f"LLM policy review skipped: {type(exc).__name__}",
            )

        await self._trace.emit(
            "policy_evaluated",
            case_id=board.case_id,
            agent=self.name,
            evidence_ids=[f"policy:{deterministic.ranked_causes[0].cause_code}"],
            primary_issue=deterministic.primary_issue,
        )
        await self._trace.emit(
            "agent_completed",
            case_id=board.case_id,
            agent=self.name,
            model_id=OPENROUTER_MODEL_ID,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return deterministic, conflicts
