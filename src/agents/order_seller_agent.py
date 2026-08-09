"""Order and seller specialist."""

from src.agents.base import BaseAgent, Narrative, StructuredLLM
from src.config.model_config import OPENROUTER_MODEL_ID
from src.database.repository import OlistRepository
from src.errors import LLMRequestError, LLMStructuredOutputError
from src.observability.trace_logger import TraceLogger
from src.schemas.agent_messages import AgentTask
from src.schemas.agent_reports import OrderSellerReport
from src.tools.order_tools import OrderTools


class OrderSellerAgent(BaseAgent):
    name = "order_seller_agent"
    prompt_file = "order_seller_agent.md"

    def __init__(self, llm: StructuredLLM, repository: OlistRepository, trace: TraceLogger) -> None:
        super().__init__(llm)
        self._repository = repository
        self._trace = trace

    async def run(self, task: AgentTask) -> OrderSellerReport:
        if set(task.allowed_tools) != OrderTools.allowed_tools:
            raise ValueError("Order agent task has an invalid tool allowlist")
        await self._trace.emit(
            "agent_started", case_id=task.case_id, agent=self.name, task_id=task.task_id
        )
        tools = OrderTools(self._repository, self._trace, task.case_id, self.name)
        order = await tools.get_order(task.order_id)
        items = await tools.get_order_items(task.order_id)
        sellers = await tools.get_order_sellers(task.order_id)
        prompt_tokens = 0
        completion_tokens = 0
        try:
            response = await self._llm.structured(
                system_prompt=self._prompt,
                user_payload={
                    "objective": task.objective,
                    "order": order.model_dump(mode="json"),
                    "items": [item.model_dump(mode="json") for item in items],
                    "sellers": [seller.model_dump(mode="json") for seller in sellers],
                },
                response_model=Narrative,
            )
            summary = response.value.summary
            warnings = list(response.value.warnings)
            prompt_tokens = response.prompt_tokens
            completion_tokens = response.completion_tokens
        except (LLMRequestError, LLMStructuredOutputError, TimeoutError) as exc:
            summary = f"Order {order.order_id} status={order.order_status}; items={len(items)}."
            warnings = [f"LLM narrative skipped: {type(exc).__name__}"]
        evidence = [f"order:{order.order_id}"]
        evidence.extend(f"item:{item.order_id}:{item.order_item_id}" for item in items)
        evidence.extend(f"seller:{seller.seller_id}" for seller in sellers)
        report = OrderSellerReport(
            task_id=task.task_id,
            order=order,
            items=items,
            seller_ids=[seller.seller_id for seller in sellers],
            missing_data=["items"] if not items else [],
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
