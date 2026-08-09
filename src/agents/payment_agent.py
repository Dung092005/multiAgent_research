"""Payment reconciliation specialist."""

from src.agents.base import BaseAgent, Narrative, StructuredLLM
from src.config.model_config import OPENROUTER_MODEL_ID
from src.database.repository import OlistRepository
from src.errors import LLMRequestError, LLMStructuredOutputError
from src.observability.trace_logger import TraceLogger
from src.schemas.agent_messages import AgentTask
from src.schemas.agent_reports import PaymentReport
from src.tools.payment_tools import PaymentTools


class PaymentAgent(BaseAgent):
    name = "payment_agent"
    prompt_file = "payment_agent.md"

    def __init__(self, llm: StructuredLLM, repository: OlistRepository, trace: TraceLogger) -> None:
        super().__init__(llm)
        self._repository = repository
        self._trace = trace

    async def run(self, task: AgentTask) -> PaymentReport:
        if set(task.allowed_tools) != PaymentTools.allowed_tools:
            raise ValueError("Payment agent task has an invalid tool allowlist")
        await self._trace.emit(
            "agent_started", case_id=task.case_id, agent=self.name, task_id=task.task_id
        )
        tools = PaymentTools(self._repository, self._trace, task.case_id, self.name)
        payments = await tools.get_order_payments(task.order_id)
        financials = await tools.calculate_order_financials(task.order_id)
        prompt_tokens = 0
        completion_tokens = 0
        try:
            response = await self._llm.structured(
                system_prompt=self._prompt,
                user_payload={
                    "objective": task.objective,
                    "payments": [row.model_dump(mode="json") for row in payments],
                    "financials": financials.model_dump(mode="json"),
                },
                response_model=Narrative,
            )
            summary = response.value.summary
            warnings = list(response.value.warnings)
            prompt_tokens = response.prompt_tokens
            completion_tokens = response.completion_tokens
        except (LLMRequestError, LLMStructuredOutputError, TimeoutError) as exc:
            summary = (
                f"Payments={len(payments)}; total={financials.payment_total_brl}; "
                f"matched={financials.payment_matches_order_total}."
            )
            warnings = [f"LLM narrative skipped: {type(exc).__name__}"]
        evidence = [f"payment:{row.order_id}:{row.payment_sequential}" for row in payments]
        report = PaymentReport(
            task_id=task.task_id,
            payments=payments,
            financials=financials,
            is_split_payment=len(payments) >= 2,
            evidence_candidates=evidence,
            summary=summary,
            warnings=warnings,
        )
        await self._trace.emit(
            "agent_completed",
            case_id=task.case_id,
            agent=self.name,
            task_id=task.task_id,
            evidence_ids=evidence,
            model_id=OPENROUTER_MODEL_ID,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return report
