"""Research-only agents that decide from one private observation and one optional message."""

from time import perf_counter

from experiments.pvoc_v0.observations import observation_fingerprint
from experiments.pvoc_v0.schemas import (
    CandidateMessage,
    DecisionExecution,
    DecisionResponse,
    PrivateObservation,
    PVoCAgentName,
)
from src.agents.base import StructuredLLM

DECISION_SYSTEM_PROMPT = """You are a research-only recipient in a controlled e-commerce experiment.
Use only the supplied private observation and optional received message. Do not infer missing
facts, calculate refunds, use external knowledge, or reveal chain-of-thought. Choose exactly one
EC_POLICY_V1 primary-issue code and give a concise evidence-grounded reason. Return JSON only."""


class PrivateDecisionAgent:
    """Calls the existing structured LLM client without touching production agent classes."""

    def __init__(self, name: PVoCAgentName, llm: StructuredLLM) -> None:
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
