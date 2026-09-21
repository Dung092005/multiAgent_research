"""Deterministic construction of compact candidate messages from private agent decisions."""

from itertools import permutations

from experiments.pvoc_v0.observations import observation_evidence_ids
from experiments.pvoc_v0.schemas import (
    CandidateMessage,
    DecisionExecution,
    PrivateObservation,
    PVoCAgentName,
)

AGENTS: tuple[PVoCAgentName, ...] = (
    "order_seller_agent",
    "payment_agent",
    "delivery_agent",
)


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
    content = (
        f"{sender} predicts {sender_decision.predicted_root_cause} "
        f"(confidence={sender_decision.confidence:.2f}). "
        f"Reason: {sender_decision.short_reason}"
    )
    return CandidateMessage(
        case_id=case_id,
        sender=sender,
        recipient=recipient,
        content=content,
        evidence_ids=observation_evidence_ids(sender_observation),
    )
