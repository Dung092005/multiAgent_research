"""Paired deliver/drop evaluation for one candidate message and one recipient state."""

from typing import Protocol
from uuid import uuid4

from experiments.pvoc_v0.metrics import (
    communication_cost,
    immediate_utility,
    terminal_utility,
    v_star,
)
from experiments.pvoc_v0.schemas import (
    CandidateMessage,
    CounterfactualOutcome,
    CounterfactualRecord,
    DecisionExecution,
    PrivateObservation,
    RootCauseCode,
)


class RecipientDecisionAgent(Protocol):
    async def decide(
        self,
        observation: PrivateObservation,
        received_message: CandidateMessage | None = None,
    ) -> DecisionExecution: ...


class CounterfactualRunner:
    def __init__(self, *, lambda_cost: float) -> None:
        if lambda_cost < 0:
            raise ValueError("lambda_cost must be non-negative")
        self.lambda_cost = lambda_cost

    async def run(
        self,
        *,
        message: CandidateMessage,
        recipient: RecipientDecisionAgent,
        recipient_observation: PrivateObservation,
        oracle_action: RootCauseCode,
    ) -> CounterfactualRecord:
        # The observation is copied for both branches. The sole intended input difference is message delivery.
        with_message = await recipient.decide(recipient_observation.model_copy(deep=True), message)
        without_message = await recipient.decide(recipient_observation.model_copy(deep=True), None)
        if with_message.observation_fingerprint != without_message.observation_fingerprint:
            raise RuntimeError("Counterfactual branches did not start from the same private state")

        with_outcome = CounterfactualOutcome(
            action=with_message.predicted_root_cause,
            immediate_utility=immediate_utility(with_message.predicted_root_cause, oracle_action),
            terminal_utility=terminal_utility(with_message.predicted_root_cause, oracle_action),
            prompt_tokens=with_message.prompt_tokens,
            completion_tokens=with_message.completion_tokens,
            latency_ms=with_message.latency_ms,
            observation_fingerprint=with_message.observation_fingerprint,
        )
        without_outcome = CounterfactualOutcome(
            action=without_message.predicted_root_cause,
            immediate_utility=immediate_utility(
                without_message.predicted_root_cause, oracle_action
            ),
            terminal_utility=terminal_utility(without_message.predicted_root_cause, oracle_action),
            prompt_tokens=without_message.prompt_tokens,
            completion_tokens=without_message.completion_tokens,
            latency_ms=without_message.latency_ms,
            observation_fingerprint=without_message.observation_fingerprint,
        )
        message_cost = communication_cost(message)
        return CounterfactualRecord(
            experiment_id=str(uuid4()),
            case_id=message.case_id,
            candidate_message=message,
            oracle_action=oracle_action,
            action_with_message=with_outcome.action,
            action_without_message=without_outcome.action,
            immediate_utility_with_message=with_outcome.immediate_utility,
            immediate_utility_without_message=without_outcome.immediate_utility,
            terminal_utility_with_message=with_outcome.terminal_utility,
            terminal_utility_without_message=without_outcome.terminal_utility,
            token_usage_with_message=with_outcome.prompt_tokens + with_outcome.completion_tokens,
            token_usage_without_message=without_outcome.prompt_tokens
            + without_outcome.completion_tokens,
            latency_with_message_ms=with_outcome.latency_ms,
            latency_without_message_ms=without_outcome.latency_ms,
            communication_cost=message_cost,
            lambda_cost=self.lambda_cost,
            v_star=v_star(
                with_outcome.terminal_utility,
                without_outcome.terminal_utility,
                self.lambda_cost,
                message_cost,
            ),
            with_message=with_outcome,
            without_message=without_outcome,
        )
