"""Dataset-v1 record validation and factual aggregation."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from math import isclose
from statistics import mean, median

from src.config.model_config import LLM_TEMPERATURE, OPENROUTER_MODEL_ID, OPENROUTER_PROVIDER

from ..core.protocol import (
    aggregate_condition,
    candidate_message_content_hash,
    candidate_message_id,
    directed_pairs,
    utility_effect_label,
)
from ..core.schemas import (
    CandidateMessage,
    DatasetV1DatasetSample,
    DatasetV1MessageRecord,
    DatasetV1TrialRecord,
    PVoCAgentName,
)

DATASET_V1_EXPERIMENT = "pvoc_counterfactual_dataset_v1"
DATASET_V1_METHODOLOGY_ID = "pvoc_counterfactual_dataset_v1_n5_fixed_messages"
DATASET_V1_CONFIG_ID = (
    f"{OPENROUTER_PROVIDER}|{OPENROUTER_MODEL_ID}|temperature={LLM_TEMPERATURE}|"
    "thinking_budget=0|lambda=0.001"
)
ALL_CASE_IDS: tuple[str, ...] = tuple(f"EC_{index:03d}" for index in range(1, 51))
EXPECTED_ROOT_CAUSE_DISTRIBUTION: dict[str, int] = {
    "canceled_order_paid": 9,
    "unavailable_order_paid": 9,
    "late_delivery_seller": 8,
    "late_delivery_logistics": 8,
    "valid_split_payment": 8,
    "unsupported_late_claim": 8,
}
N_TRIALS_PER_CONDITION = 5
LAMBDA_COST = 0.001
DIRECTED_PAIR_COUNT = 6

AgentPair = tuple[PVoCAgentName, PVoCAgentName]


class DatasetV1ValidationError(RuntimeError):
    """Raised when dataset-v1 state or artifacts violate the frozen protocol."""


def expected_counts(case_ids: Iterable[str]) -> dict[str, int]:
    case_count = len(tuple(case_ids))
    return {
        "case_count": case_count,
        "message_count": case_count * DIRECTED_PAIR_COUNT,
        "trial_count": case_count * DIRECTED_PAIR_COUNT * 2 * N_TRIALS_PER_CONDITION,
        "dataset_count": case_count * DIRECTED_PAIR_COUNT,
    }


def validate_case_selection(case_ids: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(case_ids)
    if not selected:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: no case IDs selected")
    if len(set(selected)) != len(selected):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: duplicate case IDs selected")
    unknown = [case_id for case_id in selected if case_id not in ALL_CASE_IDS]
    if unknown:
        raise DatasetV1ValidationError(
            f"DATASET_V1_INVALID: unsupported case IDs: {', '.join(unknown)}"
        )
    return selected


def validate_oracle_distribution(oracle_actions: Mapping[str, str]) -> dict[str, int]:
    actual = dict(Counter(oracle_actions.values()))
    if actual != EXPECTED_ROOT_CAUSE_DISTRIBUTION:
        raise DatasetV1ValidationError(
            "DATASET_V1_INVALID: oracle distribution mismatch; "
            f"expected={EXPECTED_ROOT_CAUSE_DISTRIBUTION}, actual={actual}"
        )
    return actual


def condition_metrics(records: list[DatasetV1TrialRecord]) -> dict[str, object]:
    try:
        return aggregate_condition(records, N_TRIALS_PER_CONDITION)
    except ValueError as error:
        raise DatasetV1ValidationError(f"DATASET_V1_INVALID: {error}") from error


def effect_label(delta_mean_utility: float) -> str:
    """Compatibility name: frozen ``effect_label`` is the utility-delta sign."""

    return utility_effect_label(delta_mean_utility)


def _message_to_candidate(message: DatasetV1MessageRecord) -> CandidateMessage:
    return CandidateMessage(
        case_id=message.case_id,
        sender=message.sender,
        recipient=message.recipient,
        content=message.candidate_message,
        evidence_ids=list(message.evidence_ids),
    )


def build_dataset_sample(
    message: DatasetV1MessageRecord, trials: list[DatasetV1TrialRecord]
) -> DatasetV1DatasetSample:
    if len(trials) != 2 * N_TRIALS_PER_CONDITION:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: message does not have ten trials")
    without_records = [record for record in trials if record.condition == "without_message"]
    with_records = [record for record in trials if record.condition == "with_message"]
    if len(without_records) != N_TRIALS_PER_CONDITION or len(with_records) != N_TRIALS_PER_CONDITION:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: message does not have a 5/5 split")
    without = condition_metrics(without_records)
    with_message = condition_metrics(with_records)
    delta = with_message["mean_utility"] - without["mean_utility"]
    return DatasetV1DatasetSample(
        run_id=message.run_id,
        case_id=message.case_id,
        oracle_action=message.oracle_action,
        sender=message.sender,
        recipient=message.recipient,
        candidate_message=message.candidate_message,
        candidate_message_id=message.candidate_message_id,
        message_content_hash=message.candidate_message_content_hash,
        evidence_ids=list(message.evidence_ids),
        communication_cost=message.communication_cost,
        lambda_cost=message.lambda_cost,
        without_message=without,
        with_message=with_message,
        delta_mean_utility=delta,
        repeated_mean_value=delta - message.lambda_cost * message.communication_cost,
        effect_label=effect_label(delta),
        action_changed=without["modal_action"] != with_message["modal_action"],
        accuracy_changed=not isclose(without["accuracy_rate"], with_message["accuracy_rate"]),
    )


def validate_pair_records(
    message: DatasetV1MessageRecord,
    trials: list[DatasetV1TrialRecord],
    *,
    expected_fingerprint: str | None = None,
) -> None:
    if len(trials) != 2 * N_TRIALS_PER_CONDITION:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: pair does not have ten trials")
    expected_keys = {
        (trial_index, condition)
        for trial_index in range(1, N_TRIALS_PER_CONDITION + 1)
        for condition in ("without_message", "with_message")
    }
    if {(record.trial_index, record.condition) for record in trials} != expected_keys:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: pair trial keys are incomplete")
    candidate = _message_to_candidate(message)
    if {record.candidate_message_id for record in trials} != {candidate_message_id(candidate)}:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: candidate message ID changed")
    if {record.candidate_message_content_hash for record in trials} != {
        candidate_message_content_hash(candidate)
    }:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: candidate message content hash changed")
    if {record.observation_fingerprint for record in trials} != {
        message.recipient_observation_fingerprint
    }:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: observation fingerprint changed")
    if expected_fingerprint is not None and (
        message.recipient_observation_fingerprint != expected_fingerprint
    ):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: rebuilt observation fingerprint changed")
    if any(record.oracle_action != message.oracle_action for record in trials):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: oracle action changed within pair")


def validate_case_records(
    *,
    run_id: str,
    case_id: str,
    oracle_action: str,
    messages: list[DatasetV1MessageRecord],
    trials: list[DatasetV1TrialRecord],
    expected_fingerprints: Mapping[PVoCAgentName, str] | None = None,
) -> dict[str, object]:
    if len(messages) != DIRECTED_PAIR_COUNT:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: case does not have six messages")
    if len(trials) != DIRECTED_PAIR_COUNT * 2 * N_TRIALS_PER_CONDITION:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: case does not have sixty trials")
    message_keys = {(record.case_id, record.sender, record.recipient) for record in messages}
    expected_pair_keys = {
        (case_id, sender, recipient) for sender, recipient in directed_pairs()
    }
    if message_keys != expected_pair_keys or len(message_keys) != len(messages):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: case directed message set is invalid")
    trial_keys = {
        (record.case_id, record.sender, record.recipient, record.trial_index, record.condition)
        for record in trials
    }
    if len(trial_keys) != len(trials):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: duplicate trial key")
    messages_by_pair = {(record.sender, record.recipient): record for record in messages}
    trials_by_pair: dict[AgentPair, list[DatasetV1TrialRecord]] = defaultdict(list)
    for record in trials:
        trials_by_pair[(record.sender, record.recipient)].append(record)
    if set(trials_by_pair) != set(messages_by_pair):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: trial/message pair mismatch")
    for message in messages:
        if message.run_id != run_id or message.case_id != case_id:
            raise DatasetV1ValidationError("DATASET_V1_INVALID: message run/case mismatch")
        if message.oracle_action != oracle_action:
            raise DatasetV1ValidationError("DATASET_V1_INVALID: message oracle mismatch")
        candidate = _message_to_candidate(message)
        if message.candidate_message_id != candidate_message_id(candidate):
            raise DatasetV1ValidationError("DATASET_V1_INVALID: message ID does not match content")
        if message.candidate_message_content_hash != candidate_message_content_hash(candidate):
            raise DatasetV1ValidationError("DATASET_V1_INVALID: message content hash mismatch")
        pair_trials = trials_by_pair[(message.sender, message.recipient)]
        if any(
            record.run_id != run_id
            or record.case_id != case_id
            or record.sender != message.sender
            or record.recipient != message.recipient
            for record in pair_trials
        ):
            raise DatasetV1ValidationError("DATASET_V1_INVALID: trial metadata mismatch")
        validate_pair_records(
            message,
            pair_trials,
            expected_fingerprint=(
                expected_fingerprints.get(message.recipient) if expected_fingerprints else None
            ),
        )
    return {
        "run_id": run_id,
        "case_id": case_id,
        "oracle_action": oracle_action,
        "status": "COMPLETE",
        "message_count": len(messages),
        "trial_count": len(trials),
        "completed_at": datetime.now(UTC).isoformat(),
    }


def _effect_counts(samples: list[DatasetV1DatasetSample]) -> dict[str, int]:
    return {
        label.lower(): sum(sample.utility_effect_label == label for sample in samples)
        for label in ("POSITIVE", "ZERO", "NEGATIVE")
    }


def build_dataset_summary(samples: list[DatasetV1DatasetSample]) -> dict[str, object]:
    if not samples:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: cannot summarize empty dataset")
    grouped_causes: dict[str, list[DatasetV1DatasetSample]] = defaultdict(list)
    grouped_edges: dict[AgentPair, list[DatasetV1DatasetSample]] = defaultdict(list)
    for sample in samples:
        grouped_causes[sample.oracle_action].append(sample)
        grouped_edges[(sample.sender, sample.recipient)].append(sample)

    def group_row(key: str, group: list[DatasetV1DatasetSample]) -> dict[str, object]:
        return {
            "root_cause": key,
            "sample_count": len(group),
            **_effect_counts(group),
            "mean_delta": mean(sample.delta_mean_utility for sample in group),
        }

    def share_stats(values: list[float]) -> dict[str, object]:
        return {"values": values, "min": min(values), "mean": mean(values), "median": median(values)}

    changed = [sample for sample in samples if sample.action_changed]
    counts = _effect_counts(samples)
    return {
        "experiment": DATASET_V1_EXPERIMENT,
        "overall": {
            "message_sample_count": len(samples),
            "positive_count": counts["positive"],
            "zero_count": counts["zero"],
            "negative_count": counts["negative"],
            "mean_delta_utility": mean(sample.delta_mean_utility for sample in samples),
            "mean_repeated_mean_value": mean(sample.repeated_mean_value for sample in samples),
        },
        "by_root_cause": [
            group_row(root_cause, grouped_causes[root_cause])
            for root_cause in sorted(grouped_causes)
        ],
        "by_communication_edge": [
            {
                "sender": sender,
                "recipient": recipient,
                "sample_count": len(grouped_edges[(sender, recipient)]),
                **_effect_counts(grouped_edges[(sender, recipient)]),
                "mean_delta": mean(
                    sample.delta_mean_utility for sample in grouped_edges[(sender, recipient)]
                ),
            }
            for sender, recipient in directed_pairs()
        ],
        "stability": {
            "with_modal_share": share_stats(
                [sample.with_message["modal_share"] for sample in samples]
            ),
            "without_modal_share": share_stats(
                [sample.without_message["modal_share"] for sample in samples]
            ),
        },
        "action_change": {
            "modal_action_changed_count": len(changed),
            "modal_action_changed_utility_not_improved_count": sum(
                sample.delta_mean_utility <= 0 for sample in changed
            ),
        },
    }
