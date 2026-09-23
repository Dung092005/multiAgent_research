"""Canonical PVoC protocol primitives shared by every research study."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from decimal import Decimal
from itertools import permutations
from statistics import mean
from time import perf_counter
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from src.database.repository import OlistRepository
from src.policy.rules import PRIMARY_ISSUES
from src.schemas.case_input import CaseInput
from src.tools.delivery_tools import DeliveryTools
from src.tools.order_tools import OrderTools
from src.tools.payment_tools import PaymentTools

from .llm import ResearchStructuredLLM
from .schemas import (
    CandidateMessage,
    CounterfactualOutcome,
    CounterfactualRecord,
    DecisionExecution,
    DecisionResponse,
    DeliveryObservation,
    DeliveryShippingEvidence,
    ObservationBundle,
    OrderSellerItemEvidence,
    OrderSellerObservation,
    PaymentObservation,
    PrivateObservation,
    PVoCAgentName,
    RootCauseCode,
)

AGENTS: tuple[PVoCAgentName, ...] = (
    "order_seller_agent",
    "payment_agent",
    "delivery_agent",
)
Condition = str
ErrorT = TypeVar("ErrorT", bound=Exception)


class _NoopTrace:
    async def emit(self, _event: str, **_fields: Any) -> None:
        return None


class PrivateObservationBuilder:
    """Build the three private views through existing read-only Olist tools."""

    def __init__(self, repository: OlistRepository) -> None:
        self._repository = repository

    async def build(self, case: CaseInput) -> ObservationBundle:
        case_id = case.case_id
        order_id = case.customer_request.claimed_order_id
        trace = _NoopTrace()
        order_tools = OrderTools(self._repository, trace, case_id, "pvoc_order_observation")
        order = await order_tools.get_order(order_id)
        order_items = await order_tools.get_order_items(order_id)
        sellers = await order_tools.get_order_sellers(order_id)
        payment_tools = PaymentTools(self._repository, trace, case_id, "pvoc_payment_observation")
        payments = await payment_tools.get_order_payments(order_id)
        delivery_tools = DeliveryTools(
            self._repository, trace, case_id, "pvoc_delivery_observation"
        )
        timeline = await delivery_tools.get_order_delivery_timeline(order_id)
        delivery_items = await delivery_tools.get_order_items(order_id)
        reviews = await delivery_tools.get_order_reviews(order_id)
        return ObservationBundle(
            order_seller=OrderSellerObservation(
                case_id=case_id,
                order_id=order.order_id,
                order_status=order.order_status,
                order_purchase_timestamp=order.order_purchase_timestamp,
                order_approved_at=order.order_approved_at,
                items=[
                    OrderSellerItemEvidence(
                        order_item_id=item.order_item_id,
                        seller_id=item.seller_id,
                        product_id=item.product_id,
                        shipping_limit_date=item.shipping_limit_date,
                        price=item.price,
                        freight_value=item.freight_value,
                    )
                    for item in order_items
                ],
                sellers=sellers,
            ),
            payment=PaymentObservation(
                case_id=case_id,
                order_id=order_id,
                payments=payments,
                payment_total_brl=sum(
                    (payment.payment_value for payment in payments), Decimal("0.00")
                ),
                payment_row_count=len(payments),
            ),
            delivery=DeliveryObservation(
                case_id=case_id,
                order_id=order_id,
                timeline=timeline,
                shipping_limits=[
                    DeliveryShippingEvidence(
                        order_item_id=item.order_item_id,
                        seller_id=item.seller_id,
                        shipping_limit_date=item.shipping_limit_date,
                    )
                    for item in delivery_items
                ],
                review_count=len(reviews),
            ),
        )


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def observation_fingerprint(observation: PrivateObservation) -> str:
    payload = canonical_json(observation.model_dump(mode="json"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def observation_evidence_ids(observation: PrivateObservation) -> list[str]:
    if isinstance(observation, OrderSellerObservation):
        evidence = [f"order:{observation.order_id}"]
        evidence.extend(
            f"item:{observation.order_id}:{item.order_item_id}" for item in observation.items
        )
        evidence.extend(f"seller:{seller.seller_id}" for seller in observation.sellers)
        return list(dict.fromkeys(evidence))
    if isinstance(observation, PaymentObservation):
        return [
            f"payment:{payment.order_id}:{payment.payment_sequential}"
            for payment in observation.payments
        ]
    evidence = [f"order:{observation.order_id}"]
    evidence.extend(
        f"item:{observation.order_id}:{item.order_item_id}"
        for item in observation.shipping_limits
    )
    return list(dict.fromkeys(evidence))


def directed_pairs() -> tuple[tuple[PVoCAgentName, PVoCAgentName], ...]:
    return tuple(permutations(AGENTS, 2))


def build_candidate_message(
    *,
    case_id: str,
    sender: PVoCAgentName,
    recipient: PVoCAgentName,
    sender_decision: DecisionExecution,
    sender_observation: PrivateObservation,
) -> CandidateMessage:
    if sender_decision.agent != sender or sender_observation.agent != sender:
        raise ValueError("Candidate message must be built from the sender's own decision and view")
    return CandidateMessage(
        case_id=case_id,
        sender=sender,
        recipient=recipient,
        content=(
            f"{sender} predicts {sender_decision.predicted_root_cause} "
            f"(confidence={sender_decision.confidence:.2f}). "
            f"Reason: {sender_decision.short_reason}"
        ),
        evidence_ids=observation_evidence_ids(sender_observation),
    )


def canonical_message_payload(message: CandidateMessage) -> str:
    return canonical_json(message.model_dump(mode="json"))


def candidate_message_id(message: CandidateMessage) -> str:
    return hashlib.sha256(canonical_message_payload(message).encode("utf-8")).hexdigest()


def candidate_message_content_hash(message: CandidateMessage) -> str:
    return hashlib.sha256(message.content.encode("utf-8")).hexdigest()


def condition_order(trial_index: int) -> tuple[Condition, Condition]:
    if trial_index % 2:
        return ("without_message", "with_message")
    return ("with_message", "without_message")


def immediate_utility(action: RootCauseCode, oracle_action: RootCauseCode) -> float:
    return 1.0 if action == oracle_action else 0.0


def terminal_utility(action: RootCauseCode, oracle_action: RootCauseCode) -> float:
    return immediate_utility(action, oracle_action)


def communication_cost(message: CandidateMessage) -> float:
    return float(len(re.findall(r"\S+", message.content)) + len(message.evidence_ids))


def v_star(
    terminal_with_message: float,
    terminal_without_message: float,
    lambda_cost: float,
    message_cost: float,
) -> float:
    return terminal_with_message - terminal_without_message - lambda_cost * message_cost


def utility_effect_label(delta_mean_utility: float) -> str:
    """Label the sign of utility change, not the cost-adjusted value."""

    if delta_mean_utility > 0:
        return "POSITIVE"
    if delta_mean_utility == 0:
        return "ZERO"
    return "NEGATIVE"


def value_sign_label(repeated_mean_value: float) -> str:
    """Pure sign label for a cost-adjusted repeated value."""

    if repeated_mean_value > 0:
        return "POSITIVE"
    if repeated_mean_value == 0:
        return "ZERO"
    return "NEGATIVE"


def action_counts(records: Iterable[object]) -> dict[str, int]:
    counts = Counter(record.predicted_root_cause for record in records)
    return {action: counts[action] for action in PRIMARY_ISSUES if counts[action]}


def modal_action(counts: dict[str, int]) -> str:
    if not counts:
        raise ValueError("Cannot compute a modal action from no trials")
    return min(counts, key=lambda action: (-counts[action], action))


def aggregate_condition(records: list[object], expected_count: int) -> dict[str, object]:
    if len(records) != expected_count:
        raise ValueError(f"Expected {expected_count} trials, got {len(records)}")
    counts = action_counts(records)
    modal = modal_action(counts)
    return {
        "action_counts": counts,
        "modal_action": modal,
        "modal_share": counts[modal] / expected_count,
        "accuracy_rate": mean(record.utility for record in records),
        "mean_confidence": mean(record.confidence for record in records),
        "mean_token_usage": mean(
            record.prompt_tokens + record.completion_tokens
            for record in records
        ),
        "mean_latency_ms": mean(record.latency_ms for record in records),
        "mean_utility": mean(record.utility for record in records),
    }


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
                "received_message": (
                    received_message.model_dump(mode="json") if received_message else None
                ),
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
        with_message = await recipient.decide(
            recipient_observation.model_copy(deep=True), message
        )
        without_message = await recipient.decide(
            recipient_observation.model_copy(deep=True), None
        )
        if with_message.observation_fingerprint != without_message.observation_fingerprint:
            raise RuntimeError("Counterfactual branches did not start from the same private state")
        outcomes = []
        for execution in (with_message, without_message):
            outcomes.append(
                CounterfactualOutcome(
                    action=execution.predicted_root_cause,
                    immediate_utility=immediate_utility(
                        execution.predicted_root_cause, oracle_action
                    ),
                    terminal_utility=terminal_utility(
                        execution.predicted_root_cause, oracle_action
                    ),
                    prompt_tokens=execution.prompt_tokens,
                    completion_tokens=execution.completion_tokens,
                    latency_ms=execution.latency_ms,
                    observation_fingerprint=execution.observation_fingerprint,
                )
            )
        with_outcome, without_outcome = outcomes
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
            token_usage_without_message=(
                without_outcome.prompt_tokens + without_outcome.completion_tokens
            ),
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
