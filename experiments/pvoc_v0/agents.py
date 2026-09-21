"""Research-only agents that decide from one private observation and one optional message."""

from time import perf_counter

from experiments.pvoc_v0.observations import observation_fingerprint
from experiments.pvoc_v0.research_llm import ResearchStructuredLLM
from experiments.pvoc_v0.schemas import (
    CandidateMessage,
    DecisionExecution,
    DecisionResponse,
    PrivateObservation,
    PVoCAgentName,
)

DECISION_SYSTEM_PROMPT = """You are a research-only recipient in a controlled e-commerce experiment.
Use only the supplied private observation and optional received message. Do not infer missing
facts, calculate refunds, use external knowledge, or reveal chain-of-thought.

Return exactly one JSON object with exactly these keys:
{"predicted_root_cause":"<one allowed code>","confidence":<number from 0 to 1>,"short_reason":"<concise evidence-grounded reason>"}

The only allowed predicted_root_cause values are: canceled_order_paid,
unavailable_order_paid, late_delivery_seller, late_delivery_logistics,
valid_split_payment, unsupported_late_claim. Never use keys named primary_issue or reason,
and never use any ORDER_* code. short_reason must be an evidence-grounded plain-text phrase of
three to six words, with no quotation marks, newlines, or braces. End immediately after the
closing JSON brace."""


class PrivateDecisionAgent:
    """Calls the existing structured LLM client without touching production agent classes."""

    def __init__(self, name: PVoCAgentName, llm: ResearchStructuredLLM) -> None:
        self.name = name
        self._llm = llm

    async def decide(
        self,
        observation: PrivateObservation,
        received_message: CandidateMessage | None = None,
    ) -> DecisionExecution:
        if observation.agent != self.name:
            raise ValueError(f"{self.name} cannot consume {observation.agent} private observation")
        started = perf_counter()
        response = await self._llm.structured(
            system_prompt=DECISION_SYSTEM_PROMPT,
            user_payload={
                "recipient": self.name,
                "private_observation": observation.model_dump(mode="json"),
                "received_message": received_message.model_dump(mode="json")
                if received_message
                else None,
            },
            response_model=DecisionResponse,
        )
        return DecisionExecution(
            **response.value.model_dump(),
            agent=self.name,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=(perf_counter() - started) * 1000,
            observation_fingerprint=observation_fingerprint(observation),
        )
