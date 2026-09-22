"""Crash-safe empirical counterfactual message-value dataset v1 runner."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isclose
from pathlib import Path
from statistics import mean, median
from uuid import uuid4

from scripts.validate_inputs import load_and_validate_inputs
from src.config.model_config import (
    LLM_TEMPERATURE,
    OPENROUTER_MODEL_ID,
    OPENROUTER_PROVIDER,
    validate_model_configuration,
)
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository
from src.policy.engine import PolicyEngine
from src.policy.rules import PRIMARY_ISSUES
from src.schemas.case_input import CaseInput

from .agents import PrivateDecisionAgent
from .messages import AGENTS, build_candidate_message, directed_pairs
from .metrics import communication_cost, immediate_utility
from .observations import PrivateObservationBuilder, observation_fingerprint
from .research_llm import ResearchVertexStructuredLLM
from .schemas import (
    CandidateMessage,
    DatasetV1DatasetSample,
    DatasetV1MessageRecord,
    DatasetV1TrialRecord,
    DecisionExecution,
    ObservationBundle,
    PVoCAgentName,
    RootCauseCode,
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
CasePair = tuple[str, PVoCAgentName, PVoCAgentName]


class DatasetV1ValidationError(RuntimeError):
    """Raised when dataset v1 state or artifacts are not valid."""


@dataclass(frozen=True)
class DatasetCaseState:
    case: CaseInput
    observations: ObservationBundle
    oracle_action: RootCauseCode


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


def load_all_validated_cases(input_dir: Path) -> dict[str, CaseInput]:
    """Validate the complete 50-case input contract before any LLM call."""

    cases = load_and_validate_inputs(input_dir, require_all=True)
    by_id = {case.case_id: case for case in cases}
    if set(by_id) != set(ALL_CASE_IDS):
        raise DatasetV1ValidationError(
            "DATASET_V1_INVALID: validated input IDs are not exactly EC_001 through EC_050"
        )
    return by_id


def validate_oracle_distribution(oracle_actions: Mapping[str, str]) -> dict[str, int]:
    actual = dict(Counter(oracle_actions.values()))
    if actual != EXPECTED_ROOT_CAUSE_DISTRIBUTION:
        raise DatasetV1ValidationError(
            "DATASET_V1_INVALID: oracle distribution mismatch; "
            f"expected={EXPECTED_ROOT_CAUSE_DISTRIBUTION}, actual={actual}"
        )
    return actual


async def compute_all_oracles(
    repository: OlistRepository, cases: Mapping[str, CaseInput]
) -> dict[str, RootCauseCode]:
    policy_engine = PolicyEngine()
    actions: dict[str, RootCauseCode] = {}
    for case_id in ALL_CASE_IDS:
        case = cases[case_id]
        context = await repository.get_policy_context(case.customer_request.claimed_order_id)
        actions[case_id] = policy_engine.evaluate(context).primary_issue
    validate_oracle_distribution(actions)
    return actions


async def build_case_state(
    repository: OlistRepository, case: CaseInput, oracle_action: RootCauseCode
) -> DatasetCaseState:
    observations = await PrivateObservationBuilder(repository).build(case)
    return DatasetCaseState(case=case, observations=observations, oracle_action=oracle_action)


def canonical_message_payload(message: CandidateMessage) -> str:
    return json.dumps(
        message.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def candidate_message_id(message: CandidateMessage) -> str:
    return hashlib.sha256(canonical_message_payload(message).encode("utf-8")).hexdigest()


def candidate_message_content_hash(message: CandidateMessage) -> str:
    return hashlib.sha256(message.content.encode("utf-8")).hexdigest()


def condition_order(trial_index: int) -> tuple[str, str]:
    if trial_index % 2:
        return ("without_message", "with_message")
    return ("with_message", "without_message")


def _action_counts(records: list[DatasetV1TrialRecord]) -> dict[str, int]:
    counts = Counter(record.predicted_root_cause for record in records)
    return {action: counts[action] for action in PRIMARY_ISSUES if counts[action]}


def _modal_action(counts: Mapping[str, int]) -> str:
    if not counts:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: no actions to summarize")
    return min(counts, key=lambda action: (-counts[action], action))


def condition_metrics(records: list[DatasetV1TrialRecord]) -> dict[str, object]:
    if len(records) != N_TRIALS_PER_CONDITION:
        raise DatasetV1ValidationError(
            f"DATASET_V1_INVALID: expected {N_TRIALS_PER_CONDITION} trials per condition"
        )
    counts = _action_counts(records)
    modal = _modal_action(counts)
    return {
        "action_counts": counts,
        "modal_action": modal,
        "modal_share": counts[modal] / N_TRIALS_PER_CONDITION,
        "accuracy_rate": mean(record.utility for record in records),
        "mean_confidence": mean(record.confidence for record in records),
        "mean_token_usage": mean(
            record.prompt_tokens + record.completion_tokens for record in records
        ),
        "mean_latency_ms": mean(record.latency_ms for record in records),
        "mean_utility": mean(record.utility for record in records),
    }


def effect_label(delta_mean_utility: float) -> str:
    if delta_mean_utility > 0:
        return "POSITIVE"
    if delta_mean_utility == 0:
        return "ZERO"
    return "NEGATIVE"


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
    action_changed = without["modal_action"] != with_message["modal_action"]
    accuracy_changed = not isclose(without["accuracy_rate"], with_message["accuracy_rate"])
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
        action_changed=action_changed,
        accuracy_changed=accuracy_changed,
    )


def _message_to_candidate(message: DatasetV1MessageRecord) -> CandidateMessage:
    return CandidateMessage(
        case_id=message.case_id,
        sender=message.sender,
        recipient=message.recipient,
        content=message.candidate_message,
        evidence_ids=list(message.evidence_ids),
    )


def _validate_pair_records(
    message: DatasetV1MessageRecord,
    trials: list[DatasetV1TrialRecord],
    *,
    expected_fingerprint: str | None = None,
) -> None:
    if len(trials) != 2 * N_TRIALS_PER_CONDITION:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: pair does not have ten trials")
    trial_keys = {(record.trial_index, record.condition) for record in trials}
    expected_keys = {
        (trial_index, condition)
        for trial_index in range(1, N_TRIALS_PER_CONDITION + 1)
        for condition in ("without_message", "with_message")
    }
    if trial_keys != expected_keys:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: pair trial keys are incomplete")
    expected_message_id = candidate_message_id(_message_to_candidate(message))
    expected_content_hash = candidate_message_content_hash(_message_to_candidate(message))
    if {record.candidate_message_id for record in trials} != {expected_message_id}:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: candidate message ID changed")
    if {record.candidate_message_content_hash for record in trials} != {expected_content_hash}:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: candidate message content hash changed")
    if {record.observation_fingerprint for record in trials} != {
        message.recipient_observation_fingerprint
    }:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: observation fingerprint changed")
    if expected_fingerprint is not None and message.recipient_observation_fingerprint != expected_fingerprint:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: rebuilt observation fingerprint changed")
    if any(record.oracle_action != message.oracle_action for record in trials):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: oracle action changed within pair")


def validate_case_records(
    *,
    run_id: str,
    case_id: str,
    oracle_action: RootCauseCode,
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
    if message_keys != expected_pair_keys:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: case directed message set is invalid")
    if len(message_keys) != len(messages):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: duplicate case message key")
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
        expected_fingerprint = (
            expected_fingerprints.get(message.recipient) if expected_fingerprints else None
        )
        _validate_pair_records(
            message,
            pair_trials,
            expected_fingerprint=expected_fingerprint,
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


def append_jsonl(path: Path, records: Iterable[object]) -> None:
    with path.open("a", encoding="utf-8") as output_file:
        for record in records:
            if hasattr(record, "model_dump_json"):
                serialized = record.model_dump_json()
            else:
                serialized = json.dumps(record, ensure_ascii=False)
            output_file.write(serialized)
            output_file.write("\n")
        output_file.flush()
        os.fsync(output_file.fileno())


def read_jsonl(path: Path, model: type) -> list[object]:
    if not path.exists():
        return []
    records: list[object] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(model.model_validate_json(line))
    return records


def read_json_objects(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def atomic_write_jsonl(path: Path, records: Iterable[object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as output_file:
        for record in records:
            if hasattr(record, "model_dump_json"):
                output_file.write(record.model_dump_json())
            else:
                output_file.write(json.dumps(record, ensure_ascii=False))
            output_file.write("\n")
        output_file.flush()
        os.fsync(output_file.fileno())
    temporary.replace(path)


def _artifact_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "manifest": run_dir / "manifest.json",
        "messages": run_dir / "messages.jsonl",
        "trials": run_dir / "trials.jsonl",
        "case_summaries": run_dir / "case_summaries.jsonl",
        "dataset": run_dir / "dataset.jsonl",
        "summary": run_dir / "summary.json",
    }


def _new_manifest(run_id: str, run_dir: Path, case_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "run_id": run_id,
        "experiment": DATASET_V1_EXPERIMENT,
        "methodology_id": DATASET_V1_METHODOLOGY_ID,
        "config_id": DATASET_V1_CONFIG_ID,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "RUNNING",
        "run_directory": str(run_dir),
        "case_ids": list(case_ids),
        "validated_input_case_count": 50,
        "oracle_actions": {},
        "root_cause_distribution": {},
        "model": OPENROUTER_MODEL_ID,
        "provider": OPENROUTER_PROVIDER,
        "n_trials_per_condition": N_TRIALS_PER_CONDITION,
        "lambda": LAMBDA_COST,
        "expected_counts": expected_counts(case_ids),
        "actual_counts": {
            "case_count": 0,
            "message_count": 0,
            "trial_count": 0,
            "dataset_count": 0,
        },
        "sanity_checks": {},
        "failure": None,
        "resume_count": 0,
    }


def create_run_directory(output_dir: Path, case_ids: tuple[str, ...]) -> tuple[Path, dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"pvoc_v1_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    run_dir = output_dir / run_id
    run_dir.mkdir(exist_ok=False)
    paths = _artifact_paths(run_dir)
    for name in ("messages", "trials", "case_summaries"):
        paths[name].touch(exist_ok=False)
    manifest = _new_manifest(run_id, run_dir, case_ids)
    atomic_write_json(paths["manifest"], manifest)
    return run_dir, manifest


def load_manifest(run_dir: Path) -> dict[str, object]:
    path = _artifact_paths(run_dir)["manifest"]
    if not path.is_file():
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume manifest.json is missing")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("experiment") != DATASET_V1_EXPERIMENT:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume experiment identifier mismatch")
    if manifest.get("methodology_id") != DATASET_V1_METHODOLOGY_ID:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume methodology mismatch")
    if manifest.get("config_id") != DATASET_V1_CONFIG_ID:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume configuration mismatch")
    if manifest.get("model") != OPENROUTER_MODEL_ID or manifest.get("provider") != OPENROUTER_PROVIDER:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume model/provider mismatch")
    if manifest.get("n_trials_per_condition") != N_TRIALS_PER_CONDITION:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume trial count mismatch")
    if manifest.get("lambda") != LAMBDA_COST:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume lambda mismatch")
    if manifest.get("status") in {"COMPLETE", "INVALID"}:
        raise DatasetV1ValidationError(
            f"DATASET_V1_INVALID: cannot resume status {manifest.get('status')}"
        )
    validate_case_selection(manifest.get("case_ids", []))
    return manifest


def update_manifest(run_dir: Path, manifest: dict[str, object], **updates: object) -> dict[str, object]:
    manifest.update(updates)
    atomic_write_json(_artifact_paths(run_dir)["manifest"], manifest)
    return manifest


def inspect_and_clean_resume_state(
    run_dir: Path,
    manifest: Mapping[str, object],
) -> set[str]:
    paths = _artifact_paths(run_dir)
    run_id = manifest["run_id"]
    case_ids = tuple(manifest["case_ids"])
    oracle_actions = manifest.get("oracle_actions", {})
    messages = read_jsonl(paths["messages"], DatasetV1MessageRecord)
    trials = read_jsonl(paths["trials"], DatasetV1TrialRecord)
    summaries = read_json_objects(paths["case_summaries"])
    complete_summary_cases = {
        summary.get("case_id")
        for summary in summaries
        if summary.get("run_id") == run_id and summary.get("status") == "COMPLETE"
    }
    if any(record.run_id != run_id for record in [*messages, *trials]):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume artifact run_id mismatch")
    allowed_cases = set(case_ids)
    if any(record.case_id not in allowed_cases for record in [*messages, *trials]):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume artifact contains an unknown case")

    completed: set[str] = set()
    incomplete: set[str] = set()
    for case_id in case_ids:
        case_messages = [record for record in messages if record.case_id == case_id]
        case_trials = [record for record in trials if record.case_id == case_id]
        if not case_messages and not case_trials:
            continue
        try:
            validate_case_records(
                run_id=run_id,
                case_id=case_id,
                oracle_action=oracle_actions[case_id],
                messages=case_messages,
                trials=case_trials,
            )
        except (DatasetV1ValidationError, KeyError):
            incomplete.add(case_id)
        else:
            if case_id in complete_summary_cases:
                completed.add(case_id)
            else:
                incomplete.add(case_id)

    if incomplete:
        atomic_write_jsonl(
            paths["messages"], [record for record in messages if record.case_id not in incomplete]
        )
        atomic_write_jsonl(
            paths["trials"], [record for record in trials if record.case_id not in incomplete]
        )
    retained_summaries = [
        summary
        for summary in summaries
        if summary.get("case_id") in completed and summary.get("status") == "COMPLETE"
    ]
    atomic_write_jsonl(paths["case_summaries"], retained_summaries)
    return completed


def build_message_record(
    *,
    run_id: str,
    oracle_action: RootCauseCode,
    sender: PVoCAgentName,
    recipient: PVoCAgentName,
    sender_decision: DecisionExecution,
    sender_observation,
    recipient_observation_fingerprint: str,
    lambda_cost: float,
) -> tuple[CandidateMessage, DatasetV1MessageRecord]:
    message = build_candidate_message(
        case_id=sender_observation.case_id,
        sender=sender,
        recipient=recipient,
        sender_observation=sender_observation,
        sender_decision=sender_decision,
    )
    return message, DatasetV1MessageRecord(
        run_id=run_id,
        case_id=message.case_id,
        oracle_action=oracle_action,
        sender=sender,
        recipient=recipient,
        sender_predicted_root_cause=sender_decision.predicted_root_cause,
        sender_confidence=sender_decision.confidence,
        candidate_message=message.content,
        evidence_ids=list(message.evidence_ids),
        candidate_message_id=candidate_message_id(message),
        candidate_message_content_hash=candidate_message_content_hash(message),
        communication_cost=communication_cost(message),
        lambda_cost=lambda_cost,
        recipient_observation_fingerprint=recipient_observation_fingerprint,
    )


async def run_case(
    *,
    run_id: str,
    state: DatasetCaseState,
    agents: Mapping[PVoCAgentName, PrivateDecisionAgent],
    paths: Mapping[str, Path],
    lambda_cost: float,
) -> dict[str, object]:
    observations = state.observations
    recipient_fingerprints = {
        recipient: observation_fingerprint(observations.for_agent(recipient))
        for recipient in AGENTS
    }
    sender_decisions: dict[PVoCAgentName, DecisionExecution] = {}
    for sender in AGENTS:
        sender_decisions[sender] = await agents[sender].decide(observations.for_agent(sender))

    case_messages: list[DatasetV1MessageRecord] = []
    case_trials: list[DatasetV1TrialRecord] = []
    for sender, recipient in directed_pairs():
        message, message_record = build_message_record(
            run_id=run_id,
            oracle_action=state.oracle_action,
            sender=sender,
            recipient=recipient,
            sender_decision=sender_decisions[sender],
            sender_observation=observations.for_agent(sender),
            recipient_observation_fingerprint=recipient_fingerprints[recipient],
            lambda_cost=lambda_cost,
        )
        pair_trials: list[DatasetV1TrialRecord] = []
        recipient_observation = observations.for_agent(recipient)
        for trial_index in range(1, N_TRIALS_PER_CONDITION + 1):
            for condition in condition_order(trial_index):
                received_message = (
                    message.model_copy(deep=True) if condition == "with_message" else None
                )
                execution = await agents[recipient].decide(
                    recipient_observation.model_copy(deep=True), received_message
                )
                if execution.observation_fingerprint != recipient_fingerprints[recipient]:
                    raise DatasetV1ValidationError(
                        f"DATASET_V1_INVALID: observation fingerprint changed for "
                        f"{state.case.case_id} {sender}->{recipient}"
                    )
                pair_trials.append(
                    DatasetV1TrialRecord(
                        run_id=run_id,
                        case_id=state.case.case_id,
                        oracle_action=state.oracle_action,
                        sender=sender,
                        recipient=recipient,
                        candidate_message_id=message_record.candidate_message_id,
                        trial_index=trial_index,
                        condition=condition,
                        predicted_root_cause=execution.predicted_root_cause,
                        confidence=execution.confidence,
                        short_reason=execution.short_reason,
                        utility=immediate_utility(
                            execution.predicted_root_cause, state.oracle_action
                        ),
                        prompt_tokens=execution.prompt_tokens,
                        completion_tokens=execution.completion_tokens,
                        latency_ms=execution.latency_ms,
                        observation_fingerprint=execution.observation_fingerprint,
                        candidate_message_content_hash=message_record.candidate_message_content_hash,
                        communication_cost=message_record.communication_cost,
                        lambda_cost=lambda_cost,
                    )
                )
        _validate_pair_records(
            message_record,
            pair_trials,
            expected_fingerprint=recipient_fingerprints[recipient],
        )
        append_jsonl(paths["messages"], [message_record])
        append_jsonl(paths["trials"], pair_trials)
        case_messages.append(message_record)
        case_trials.extend(pair_trials)

    summary = validate_case_records(
        run_id=run_id,
        case_id=state.case.case_id,
        oracle_action=state.oracle_action,
        messages=case_messages,
        trials=case_trials,
        expected_fingerprints=recipient_fingerprints,
    )
    append_jsonl(paths["case_summaries"], [summary])
    return summary


def _summary_group_counts(samples: list[DatasetV1DatasetSample], field: str) -> list[dict[str, object]]:
    grouped: dict[str, list[DatasetV1DatasetSample]] = defaultdict(list)
    for sample in samples:
        grouped[getattr(sample, field)].append(sample)
    return [
        {
            "root_cause": root_cause,
            "sample_count": len(grouped[root_cause]),
            "positive": sum(sample.effect_label == "POSITIVE" for sample in grouped[root_cause]),
            "zero": sum(sample.effect_label == "ZERO" for sample in grouped[root_cause]),
            "negative": sum(sample.effect_label == "NEGATIVE" for sample in grouped[root_cause]),
            "mean_delta": mean(sample.delta_mean_utility for sample in grouped[root_cause]),
        }
        for root_cause in sorted(grouped)
    ]


def _summary_edge_counts(samples: list[DatasetV1DatasetSample]) -> list[dict[str, object]]:
    grouped: dict[AgentPair, list[DatasetV1DatasetSample]] = defaultdict(list)
    for sample in samples:
        grouped[(sample.sender, sample.recipient)].append(sample)
    return [
        {
            "sender": sender,
            "recipient": recipient,
            "sample_count": len(grouped[(sender, recipient)]),
            "positive": sum(
                sample.effect_label == "POSITIVE" for sample in grouped[(sender, recipient)]
            ),
            "zero": sum(sample.effect_label == "ZERO" for sample in grouped[(sender, recipient)]),
            "negative": sum(
                sample.effect_label == "NEGATIVE" for sample in grouped[(sender, recipient)]
            ),
            "mean_delta": mean(
                sample.delta_mean_utility for sample in grouped[(sender, recipient)]
            ),
        }
        for sender, recipient in directed_pairs()
    ]


def build_dataset_summary(samples: list[DatasetV1DatasetSample]) -> dict[str, object]:
    if not samples:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: cannot summarize empty dataset")
    with_shares = [sample.with_message["modal_share"] for sample in samples]
    without_shares = [sample.without_message["modal_share"] for sample in samples]

    def share_stats(values: list[float]) -> dict[str, object]:
        return {
            "values": values,
            "min": min(values),
            "mean": mean(values),
            "median": median(values),
        }

    action_changed = [sample for sample in samples if sample.action_changed]
    return {
        "experiment": DATASET_V1_EXPERIMENT,
        "overall": {
            "message_sample_count": len(samples),
            "positive_count": sum(sample.effect_label == "POSITIVE" for sample in samples),
            "zero_count": sum(sample.effect_label == "ZERO" for sample in samples),
            "negative_count": sum(sample.effect_label == "NEGATIVE" for sample in samples),
            "mean_delta_utility": mean(sample.delta_mean_utility for sample in samples),
            "mean_repeated_mean_value": mean(sample.repeated_mean_value for sample in samples),
        },
        "by_root_cause": _summary_group_counts(samples, "oracle_action"),
        "by_communication_edge": _summary_edge_counts(samples),
        "stability": {
            "with_modal_share": share_stats(with_shares),
            "without_modal_share": share_stats(without_shares),
        },
        "action_change": {
            "modal_action_changed_count": len(action_changed),
            "modal_action_changed_utility_not_improved_count": sum(
                sample.delta_mean_utility <= 0 for sample in action_changed
            ),
        },
    }


def finalize_run(
    run_dir: Path,
    manifest: dict[str, object],
) -> dict[str, object]:
    paths = _artifact_paths(run_dir)
    run_id = manifest["run_id"]
    case_ids = tuple(manifest["case_ids"])
    oracle_actions = manifest["oracle_actions"]
    messages = read_jsonl(paths["messages"], DatasetV1MessageRecord)
    trials = read_jsonl(paths["trials"], DatasetV1TrialRecord)
    expected = expected_counts(case_ids)
    if len(messages) != expected["message_count"] or len(trials) != expected["trial_count"]:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: final raw counts are incomplete")

    all_samples: list[DatasetV1DatasetSample] = []
    for case_id in case_ids:
        case_messages = [record for record in messages if record.case_id == case_id]
        case_trials = [record for record in trials if record.case_id == case_id]
        validate_case_records(
            run_id=run_id,
            case_id=case_id,
            oracle_action=oracle_actions[case_id],
            messages=case_messages,
            trials=case_trials,
        )
        messages_by_pair = {(record.sender, record.recipient): record for record in case_messages}
        trials_by_pair: dict[AgentPair, list[DatasetV1TrialRecord]] = defaultdict(list)
        for record in case_trials:
            trials_by_pair[(record.sender, record.recipient)].append(record)
        all_samples.extend(
            build_dataset_sample(message, trials_by_pair[pair])
            for pair, message in messages_by_pair.items()
        )

    if len(all_samples) != expected["dataset_count"]:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: final dataset row count is incomplete")
    sample_keys = {(sample.case_id, sample.sender, sample.recipient) for sample in all_samples}
    expected_keys = {
        (case_id, sender, recipient)
        for case_id in case_ids
        for sender, recipient in directed_pairs()
    }
    if sample_keys != expected_keys or len(sample_keys) != len(all_samples):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: final dataset keys are not unique")
    selected_oracle_actions = {case_id: oracle_actions[case_id] for case_id in case_ids}
    if {sample.oracle_action for sample in all_samples} != set(selected_oracle_actions.values()):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: final dataset root-cause classes mismatch")

    summary = build_dataset_summary(all_samples)
    atomic_write_jsonl(paths["dataset"], all_samples)
    atomic_write_json(paths["summary"], summary)
    actual_counts = {
        "case_count": len(case_ids),
        "message_count": len(messages),
        "trial_count": len(trials),
        "dataset_count": len(all_samples),
    }
    sanity_checks = {
        "all_50_validated_inputs": len(oracle_actions) == 50,
        "expected_oracle_distribution": manifest["root_cause_distribution"]
        == EXPECTED_ROOT_CAUSE_DISTRIBUTION,
        "exact_case_count": actual_counts["case_count"] == expected["case_count"],
        "exact_message_count": actual_counts["message_count"] == expected["message_count"],
        "exact_trial_count": actual_counts["trial_count"] == expected["trial_count"],
        "exact_dataset_count": actual_counts["dataset_count"] == expected["dataset_count"],
        "six_directed_edges": {(sample.sender, sample.recipient) for sample in all_samples}
        == set(directed_pairs()),
        "six_root_cause_classes": (
            len({sample.oracle_action for sample in all_samples}) == 6
            or len(case_ids) < len(ALL_CASE_IDS)
        ),
        "unique_message_samples": len(sample_keys) == len(all_samples),
        "message_trial_invariants": True,
        "oracle_hidden_from_recipient": True,
        "private_observation_isolation": True,
    }
    if not all(sanity_checks.values()):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: final sanity check failed")
    update_manifest(
        run_dir,
        manifest,
        status="COMPLETE",
        completed_at=datetime.now(UTC).isoformat(),
        actual_counts=actual_counts,
        sanity_checks=sanity_checks,
        failure=None,
    )
    return summary


def _safe_failure(error: BaseException, *, phase: str, case_id: str | None = None) -> dict[str, object]:
    return {
        "error_class": type(error).__name__,
        "phase": phase,
        "case_id": case_id,
        "message": "Run stopped; inspect the run directory and use --resume after review.",
    }


async def run_dataset_v1(
    *,
    input_dir: Path = Path("data/input"),
    output_dir: Path = Path("experiments/pvoc_v0/results/dataset_v1"),
    case_ids: tuple[str, ...] = ALL_CASE_IDS,
    resume_dir: Path | None = None,
    stop_after_cases: int | None = None,
    lambda_cost: float = LAMBDA_COST,
) -> Path:
    if lambda_cost != LAMBDA_COST:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: lambda is fixed at 0.001")
    if stop_after_cases is not None and stop_after_cases < 1:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: stop_after_cases must be positive")
    if resume_dir is not None:
        if case_ids != ALL_CASE_IDS:
            raise DatasetV1ValidationError("DATASET_V1_INVALID: do not pass case IDs with --resume")
        run_dir = resume_dir
        manifest = load_manifest(run_dir)
        selected_case_ids = tuple(manifest["case_ids"])
    else:
        selected_case_ids = validate_case_selection(case_ids)
        run_dir, manifest = create_run_directory(output_dir, selected_case_ids)

    paths = _artifact_paths(run_dir)
    settings = get_settings()
    current_case: str | None = None
    engine = None
    try:
        all_cases = load_all_validated_cases(input_dir)
        engine = create_engine(settings, read_only=True)
        repository = OlistRepository(create_session_factory(engine))
        oracle_actions = await compute_all_oracles(repository, all_cases)
        if manifest.get("oracle_actions") and manifest["oracle_actions"] != oracle_actions:
            raise DatasetV1ValidationError("DATASET_V1_INVALID: resume oracle mapping changed")
        root_distribution = validate_oracle_distribution(oracle_actions)
        update_manifest(
            run_dir,
            manifest,
            oracle_actions=oracle_actions,
            root_cause_distribution=root_distribution,
            validated_input_case_count=len(all_cases),
            status="RUNNING",
            failure=None,
        )
        completed_cases = inspect_and_clean_resume_state(run_dir, manifest)
        if not completed_cases.issubset(set(selected_case_ids)):
            raise DatasetV1ValidationError("DATASET_V1_INVALID: completed case set is out of scope")
        pending_cases = [case_id for case_id in selected_case_ids if case_id not in completed_cases]
        if not pending_cases:
            finalize_run(run_dir, manifest)
            return run_dir

        validate_model_configuration()
        llm = ResearchVertexStructuredLLM(settings)
        agents = {agent_name: PrivateDecisionAgent(agent_name, llm) for agent_name in AGENTS}
        for newly_completed, case_id in enumerate(pending_cases, start=1):
            current_case = case_id
            state = await build_case_state(repository, all_cases[case_id], oracle_actions[case_id])
            await run_case(
                run_id=manifest["run_id"],
                state=state,
                agents=agents,
                paths=paths,
                lambda_cost=lambda_cost,
            )
            if stop_after_cases is not None and newly_completed >= stop_after_cases:
                update_manifest(
                    run_dir,
                    manifest,
                    status="INTERRUPTED",
                    actual_counts={
                        "case_count": len(completed_cases) + newly_completed,
                        "message_count": (len(completed_cases) + newly_completed)
                        * DIRECTED_PAIR_COUNT,
                        "trial_count": (len(completed_cases) + newly_completed)
                        * DIRECTED_PAIR_COUNT
                        * 2
                        * N_TRIALS_PER_CONDITION,
                        "dataset_count": 0,
                    },
                    failure={
                        "error_class": "ControlledStop",
                        "phase": "checkpoint",
                        "case_id": case_id,
                        "message": "Controlled stop requested; resume this run directory.",
                    },
                )
                return run_dir
        finalize_run(run_dir, manifest)
        return run_dir
    except DatasetV1ValidationError as error:
        update_manifest(
            run_dir,
            manifest,
            status="INVALID",
            failure=_safe_failure(error, phase="validation", case_id=current_case),
        )
        raise
    except Exception as error:
        update_manifest(
            run_dir,
            manifest,
            status="INTERRUPTED",
            failure=_safe_failure(error, phase="execution", case_id=current_case),
        )
        raise
    finally:
        if engine is not None:
            await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or resume the research-only PVoC counterfactual dataset v1 collection."
    )
    parser.add_argument("--input-dir", type=Path, default=Path("data/input"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experiments/pvoc_v0/results/dataset_v1"),
    )
    parser.add_argument("--case-ids", nargs="+", choices=ALL_CASE_IDS)
    parser.add_argument("--stop-after-cases", type=int)
    parser.add_argument("--lambda-cost", type=float, default=LAMBDA_COST)
    parser.add_argument("--resume", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.resume is not None and args.stop_after_cases is not None:
        raise SystemExit("--resume cannot be combined with --stop-after-cases")
    selected = tuple(args.case_ids) if args.case_ids else ALL_CASE_IDS
    try:
        if sys.platform == "win32":
            run_dir = asyncio.run(
                run_dataset_v1(
                    input_dir=args.input_dir,
                    output_dir=args.output_dir,
                    case_ids=selected,
                    resume_dir=args.resume,
                    stop_after_cases=args.stop_after_cases,
                    lambda_cost=args.lambda_cost,
                ),
                loop_factory=asyncio.SelectorEventLoop,
            )
        else:
            run_dir = asyncio.run(
                run_dataset_v1(
                    input_dir=args.input_dir,
                    output_dir=args.output_dir,
                    case_ids=selected,
                    resume_dir=args.resume,
                    stop_after_cases=args.stop_after_cases,
                    lambda_cost=args.lambda_cost,
                )
            )
    except DatasetV1ValidationError as error:
        print(f"Dataset v1 stopped: {error}")
        raise SystemExit(2) from None
    except Exception as error:  # noqa: BLE001 - CLI must hide raw failure details
        print(f"Dataset v1 interrupted: error_class={type(error).__name__}")
        raise SystemExit(1) from None
    print(f"Dataset v1 run directory: {run_dir}")


if __name__ == "__main__":
    main()
