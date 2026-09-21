"""Minimal v0 utility and communication-cost metrics."""

import re

from experiments.pvoc_v0.schemas import CandidateMessage, RootCauseCode


def immediate_utility(action: RootCauseCode, oracle_action: RootCauseCode) -> float:
    """A one-step classification utility for the recipient's decision point."""

    return 1.0 if action == oracle_action else 0.0


def terminal_utility(action: RootCauseCode, oracle_action: RootCauseCode) -> float:
    """v0 has no later intervention; terminal utility equals the one-step oracle utility."""

    return immediate_utility(action, oracle_action)


def communication_cost(message: CandidateMessage) -> float:
    """Deterministic lexical-token proxy until provider-level message accounting is added."""

    return float(len(re.findall(r"\S+", message.content)) + len(message.evidence_ids))


def v_star(
    terminal_with_message: float,
    terminal_without_message: float,
    lambda_cost: float,
    message_cost: float,
) -> float:
    return terminal_with_message - terminal_without_message - lambda_cost * message_cost
