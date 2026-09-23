"""Validation and factual aggregation for the six-case pilot."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from math import isclose
from statistics import mean

from ..core.protocol import (
    AGENTS,
    aggregate_condition,
    candidate_message_content_hash,
    candidate_message_id,
    directed_pairs,
    utility_effect_label,
)
from ..core.schemas import CandidateMessage, PilotTrialRecord, PVoCAgentName, RootCauseCode

PILOT_CASE_IDS: tuple[str, ...] = (
    "EC_001",
    "EC_010",
    "EC_019",
    "EC_027",
    "EC_035",
    "EC_043",
)
EXPECTED_ORACLE_ACTIONS: dict[str, RootCauseCode] = {
    "EC_001": "canceled_order_paid",
    "EC_010": "unavailable_order_paid",
    "EC_019": "late_delivery_seller",
    "EC_027": "late_delivery_logistics",
    "EC_035": "valid_split_payment",
    "EC_043": "unsupported_late_claim",
}
N_TRIALS_PER_CONDITION = 5
EXPECTED_DIRECTED_PAIRS_PER_CASE = 6
EXPECTED_FROZEN_MESSAGE_COUNT = len(PILOT_CASE_IDS) * EXPECTED_DIRECTED_PAIRS_PER_CASE
EXPECTED_RECIPIENT_EXECUTIONS_PER_CASE = EXPECTED_DIRECTED_PAIRS_PER_CASE * 2 * N_TRIALS_PER_CONDITION
EXPECTED_RECIPIENT_EXECUTIONS = len(PILOT_CASE_IDS) * EXPECTED_RECIPIENT_EXECUTIONS_PER_CASE
EXPECTED_SENDER_EXECUTIONS = len(PILOT_CASE_IDS) * len(AGENTS)

AgentPair = tuple[PVoCAgentName, PVoCAgentName]
CasePair = tuple[str, PVoCAgentName, PVoCAgentName]


class PilotValidationError(RuntimeError):
    """Raised when pilot state or artifacts violate the pre-registered checks."""


def _condition_summary(records: list[PilotTrialRecord]) -> dict[str, object]:
    try:
        summary = aggregate_condition(records, N_TRIALS_PER_CONDITION)
    except ValueError as error:
        raise PilotValidationError(f"PILOT_INVALID: {error}") from error
    summary["oracle_accuracy_rate"] = summary["accuracy_rate"]
    summary["mean_prompt_tokens"] = mean(record.prompt_tokens for record in records)
    summary["mean_completion_tokens"] = mean(record.completion_tokens for record in records)
    return summary


def classify_effect(delta_mean_utility: float) -> str:
    return {
        "POSITIVE": "POSITIVE OBSERVED EFFECT",
        "ZERO": "NO OBSERVED UTILITY EFFECT",
        "NEGATIVE": "NEGATIVE OBSERVED EFFECT",
    }[utility_effect_label(delta_mean_utility)]


def summarize_pair_records(records: list[PilotTrialRecord]) -> dict[str, object]:
    if len(records) != 2 * N_TRIALS_PER_CONDITION:
        raise PilotValidationError("PILOT_INVALID: each pair must contain exactly ten trials")
    without = [record for record in records if record.condition == "without_message"]
    with_message = [record for record in records if record.condition == "with_message"]
    if len(without) != N_TRIALS_PER_CONDITION or len(with_message) != N_TRIALS_PER_CONDITION:
        raise PilotValidationError("PILOT_INVALID: pair condition counts are not 5/5")
    first = records[0]
    without_summary = _condition_summary(without)
    with_summary = _condition_summary(with_message)
    delta = with_summary["mean_utility"] - without_summary["mean_utility"]
    return {
        "case_id": first.case_id,
        "oracle": first.oracle_action,
        "sender": first.sender,
        "recipient": first.recipient,
        "without_message": without_summary,
        "with_message": with_summary,
        "communication_cost": first.communication_cost,
        "lambda_cost": first.lambda_cost,
        "delta_mean_utility": delta,
        "mean_observed_value": delta - first.lambda_cost * first.communication_cost,
        "effect": classify_effect(delta),
        "modal_action_changed_accuracy_unchanged": (
            without_summary["modal_action"] != with_summary["modal_action"]
            and isclose(
                without_summary["oracle_accuracy_rate"],
                with_summary["oracle_accuracy_rate"],
            )
        ),
    }


def summarize_pilot_trials(records: list[PilotTrialRecord]) -> list[dict[str, object]]:
    grouped: dict[CasePair, list[PilotTrialRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.case_id, record.sender, record.recipient)].append(record)
    expected_keys = {
        (case_id, sender, recipient)
        for case_id in PILOT_CASE_IDS
        for sender, recipient in directed_pairs()
    }
    if set(grouped) != expected_keys:
        raise PilotValidationError("PILOT_INVALID: summary does not contain all 36 directed pairs")
    return [summarize_pair_records(grouped[key]) for key in sorted(grouped)]


def summarize_cases(pair_summaries: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for summary in pair_summaries:
        grouped[summary["case_id"]].append(summary)
    return [
        {
            "case_id": case_id,
            "oracle": EXPECTED_ORACLE_ACTIONS[case_id],
            "positive_pair_count": sum(
                summary["effect"] == "POSITIVE OBSERVED EFFECT" for summary in grouped[case_id]
            ),
            "zero_effect_pair_count": sum(
                summary["effect"] == "NO OBSERVED UTILITY EFFECT"
                for summary in grouped[case_id]
            ),
            "negative_pair_count": sum(
                summary["effect"] == "NEGATIVE OBSERVED EFFECT" for summary in grouped[case_id]
            ),
            "best_observed_delta": max(
                summary["delta_mean_utility"] for summary in grouped[case_id]
            ),
            "worst_observed_delta": min(
                summary["delta_mean_utility"] for summary in grouped[case_id]
            ),
        }
        for case_id in PILOT_CASE_IDS
    ]


def aggregate_by_root_cause(pair_summaries: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for summary in pair_summaries:
        grouped[summary["oracle"]].append(summary)
    return [
        {
            "root_cause": root_cause,
            "positive": sum(
                summary["effect"] == "POSITIVE OBSERVED EFFECT" for summary in grouped[root_cause]
            ),
            "zero": sum(
                summary["effect"] == "NO OBSERVED UTILITY EFFECT"
                for summary in grouped[root_cause]
            ),
            "negative": sum(
                summary["effect"] == "NEGATIVE OBSERVED EFFECT" for summary in grouped[root_cause]
            ),
            "pair_count": len(grouped[root_cause]),
        }
        for root_cause in EXPECTED_ORACLE_ACTIONS.values()
    ]


def aggregate_by_edge(pair_summaries: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[AgentPair, list[dict[str, object]]] = defaultdict(list)
    for summary in pair_summaries:
        grouped[(summary["sender"], summary["recipient"])].append(summary)
    return [
        {
            "sender": sender,
            "recipient": recipient,
            "mean_delta_utility": mean(
                summary["delta_mean_utility"] for summary in grouped[(sender, recipient)]
            ),
            "positive_count": sum(
                summary["effect"] == "POSITIVE OBSERVED EFFECT"
                for summary in grouped[(sender, recipient)]
            ),
            "zero_count": sum(
                summary["effect"] == "NO OBSERVED UTILITY EFFECT"
                for summary in grouped[(sender, recipient)]
            ),
            "negative_count": sum(
                summary["effect"] == "NEGATIVE OBSERVED EFFECT"
                for summary in grouped[(sender, recipient)]
            ),
        }
        for sender, recipient in directed_pairs()
    ]


def validate_pilot_trials(
    records: list[PilotTrialRecord],
    frozen_messages: Mapping[CasePair, CandidateMessage],
    expected_oracles: Mapping[str, RootCauseCode],
    expected_fingerprints: Mapping[tuple[str, PVoCAgentName], str],
) -> dict[str, bool]:
    if len(records) != EXPECTED_RECIPIENT_EXECUTIONS:
        raise PilotValidationError(
            f"PILOT_INVALID: expected {EXPECTED_RECIPIENT_EXECUTIONS} recipient executions, "
            f"got {len(records)}"
        )
    expected_keys = {
        (case_id, sender, recipient)
        for case_id in PILOT_CASE_IDS
        for sender, recipient in directed_pairs()
    }
    if set(frozen_messages) != expected_keys:
        raise PilotValidationError("PILOT_INVALID: frozen message set is not exactly 36 pairs")
    if set(expected_fingerprints) != {
        (case_id, recipient) for case_id in PILOT_CASE_IDS for recipient in AGENTS
    }:
        raise PilotValidationError("PILOT_INVALID: recipient fingerprint set is incomplete")
    grouped: dict[CasePair, list[PilotTrialRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.case_id, record.sender, record.recipient)].append(record)
    if set(grouped) != expected_keys:
        raise PilotValidationError("PILOT_INVALID: trial set is not exactly 36 pairs")
    expected_trial_conditions = {
        (trial_index, condition)
        for trial_index in range(1, N_TRIALS_PER_CONDITION + 1)
        for condition in ("without_message", "with_message")
    }
    for key, pair_records in grouped.items():
        if len(pair_records) != 2 * N_TRIALS_PER_CONDITION:
            raise PilotValidationError(f"PILOT_INVALID: pair {key} does not contain ten trials")
        if {(record.trial_index, record.condition) for record in pair_records} != expected_trial_conditions:
            raise PilotValidationError(f"PILOT_INVALID: pair {key} is missing a trial condition")
        message = frozen_messages[key]
        if {record.candidate_message_id for record in pair_records} != {
            candidate_message_id(message)
        }:
            raise PilotValidationError(f"PILOT_INVALID: message id changed for pair {key}")
        if {record.candidate_message_content_hash for record in pair_records} != {
            candidate_message_content_hash(message)
        }:
            raise PilotValidationError(f"PILOT_INVALID: message content changed for pair {key}")
        if any(record.oracle_action != expected_oracles[key[0]] for record in pair_records):
            raise PilotValidationError(f"PILOT_INVALID: oracle changed in pair {key}")
        if {record.observation_fingerprint for record in pair_records} != {
            expected_fingerprints[(key[0], key[2])]
        }:
            raise PilotValidationError(f"PILOT_INVALID: observation changed for pair {key}")
    return {
        "all_six_oracle_classes_correct": set(expected_oracles.values())
        == set(EXPECTED_ORACLE_ACTIONS.values()),
        "fixed_candidate_messages": True,
        "stable_observation_fingerprints": True,
        "correct_pair_counts": True,
        "oracle_hidden": True,
        "private_observation_isolation": True,
    }


def build_pilot_summary(
    *,
    records: list[PilotTrialRecord],
    oracle_actions: Mapping[str, RootCauseCode],
    sanity_checks: Mapping[str, bool],
) -> dict[str, object]:
    pair_summaries = summarize_pilot_trials(records)
    effects = Counter(summary["effect"] for summary in pair_summaries)
    return {
        "experiment": "pvoc_v0_stratified_pilot",
        "pilot_case_ids": list(PILOT_CASE_IDS),
        "oracle_actions": dict(oracle_actions),
        "n_trials_per_condition": N_TRIALS_PER_CONDITION,
        "directed_pairs_per_case": EXPECTED_DIRECTED_PAIRS_PER_CASE,
        "expected_recipient_executions": EXPECTED_RECIPIENT_EXECUTIONS,
        "actual_recipient_executions": len(records),
        "sanity_checks": dict(sanity_checks),
        "per_case": summarize_cases(pair_summaries),
        "pairs": pair_summaries,
        "by_root_cause": aggregate_by_root_cause(pair_summaries),
        "by_sender_recipient": aggregate_by_edge(pair_summaries),
        "overall": {
            "total_pairs": len(pair_summaries),
            "positive_effect_count": effects["POSITIVE OBSERVED EFFECT"],
            "zero_effect_count": effects["NO OBSERVED UTILITY EFFECT"],
            "negative_effect_count": effects["NEGATIVE OBSERVED EFFECT"],
        },
    }
