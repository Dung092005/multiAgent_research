"""Small stratified PVoC v0 pilot over six frozen-root-cause cases."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isclose
from pathlib import Path
from statistics import mean
from uuid import uuid4

from scripts.validate_inputs import load_and_validate_inputs
from src.config.model_config import OPENROUTER_MODEL_ID, validate_model_configuration
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
    ObservationBundle,
    PilotCondition,
    PilotMessageRecord,
    PilotTrialRecord,
    PVoCAgentName,
    RootCauseCode,
)

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
    """Raised when the pilot state or artifacts violate the pre-registered checks."""


@dataclass(frozen=True)
class PilotCaseState:
    case: CaseInput
    observations: ObservationBundle
    oracle_action: RootCauseCode


def load_pilot_cases(input_dir: Path) -> list[CaseInput]:
    """Load exactly the six pre-registered pilot cases from validated inputs."""

    available = {
        case.case_id: case for case in load_and_validate_inputs(input_dir, require_all=False)
    }
    missing = [case_id for case_id in PILOT_CASE_IDS if case_id not in available]
    if missing:
        raise PilotValidationError(
            f"PILOT_INVALID: missing validated pilot inputs: {', '.join(missing)}"
        )
    return [available[case_id] for case_id in PILOT_CASE_IDS]


def validate_expected_oracles(oracle_actions: Mapping[str, str]) -> None:
    """Fail before any LLM call unless all six expected classes match exactly."""

    mismatches = {
        case_id: (EXPECTED_ORACLE_ACTIONS[case_id], oracle_actions.get(case_id))
        for case_id in PILOT_CASE_IDS
        if oracle_actions.get(case_id) != EXPECTED_ORACLE_ACTIONS[case_id]
    }
    if mismatches:
        raise PilotValidationError(f"PILOT_INVALID: oracle mismatch: {mismatches}")


async def compute_oracle_actions(
    repository: OlistRepository, cases: list[CaseInput]
) -> dict[str, RootCauseCode]:
    policy_engine = PolicyEngine()
    actions: dict[str, RootCauseCode] = {}
    for case in cases:
        context = await repository.get_policy_context(case.customer_request.claimed_order_id)
        actions[case.case_id] = policy_engine.evaluate(context).primary_issue
    return actions


async def prepare_pilot_states(
    repository: OlistRepository, cases: list[CaseInput]
) -> dict[str, PilotCaseState]:
    """Build all observations and oracles before the first research-model call."""

    builder = PrivateObservationBuilder(repository)
    policy_engine = PolicyEngine()
    states: dict[str, PilotCaseState] = {}
    for case in cases:
        observations = await builder.build(case)
        context = await repository.get_policy_context(case.customer_request.claimed_order_id)
        oracle_action = policy_engine.evaluate(context).primary_issue
        states[case.case_id] = PilotCaseState(
            case=case,
            observations=observations,
            oracle_action=oracle_action,
        )
    validate_expected_oracles({case_id: state.oracle_action for case_id, state in states.items()})
    return states


def canonical_message_payload(message: CandidateMessage) -> str:
    return json.dumps(
        message.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def candidate_message_id(message: CandidateMessage) -> str:
    return hashlib.sha256(canonical_message_payload(message).encode("utf-8")).hexdigest()


def candidate_message_content_hash(message: CandidateMessage) -> str:
    return hashlib.sha256(message.content.encode("utf-8")).hexdigest()


def pilot_condition_order(trial_index: int) -> tuple[PilotCondition, PilotCondition]:
    if trial_index % 2:
        return ("without_message", "with_message")
    return ("with_message", "without_message")


def freeze_candidate_messages(
    *,
    pilot_run_id: str,
    case_id: str,
    oracle_action: RootCauseCode,
    observations: ObservationBundle,
    sender_decisions: Mapping[PVoCAgentName, object],
    lambda_cost: float,
) -> tuple[dict[AgentPair, CandidateMessage], list[PilotMessageRecord]]:
    """Construct each directed message once and return the frozen message set."""

    frozen: dict[AgentPair, CandidateMessage] = {}
    records: list[PilotMessageRecord] = []
    for sender, recipient in directed_pairs():
        message = build_candidate_message(
            case_id=case_id,
            sender=sender,
            recipient=recipient,
            sender_observation=observations.for_agent(sender),
            sender_decision=sender_decisions[sender],
        )
        frozen[(sender, recipient)] = message
        records.append(
            PilotMessageRecord(
                pilot_run_id=pilot_run_id,
                case_id=case_id,
                oracle_action=oracle_action,
                sender=sender,
                recipient=recipient,
                content=message.content,
                evidence_ids=list(message.evidence_ids),
                candidate_message_id=candidate_message_id(message),
                candidate_message_content_hash=candidate_message_content_hash(message),
                communication_cost=communication_cost(message),
                lambda_cost=lambda_cost,
            )
        )
    return frozen, records


def _action_counts(records: list[PilotTrialRecord]) -> dict[str, int]:
    counts = Counter(record.predicted_root_cause for record in records)
    return {action: counts[action] for action in PRIMARY_ISSUES if counts[action]}


def _modal_action(counts: dict[str, int]) -> str:
    if not counts:
        raise PilotValidationError("PILOT_INVALID: cannot compute a modal action from no trials")
    return min(counts, key=lambda action: (-counts[action], action))


def _condition_summary(records: list[PilotTrialRecord]) -> dict[str, object]:
    if len(records) != N_TRIALS_PER_CONDITION:
        raise PilotValidationError(
            f"PILOT_INVALID: expected {N_TRIALS_PER_CONDITION} trials, got {len(records)}"
        )
    counts = _action_counts(records)
    modal = _modal_action(counts)
    return {
        "action_counts": counts,
        "modal_action": modal,
        "modal_share": counts[modal] / N_TRIALS_PER_CONDITION,
        "oracle_accuracy_rate": mean(record.utility for record in records),
        "mean_confidence": mean(record.confidence for record in records),
        "mean_prompt_tokens": mean(record.prompt_tokens for record in records),
        "mean_completion_tokens": mean(record.completion_tokens for record in records),
        "mean_token_usage": mean(
            record.prompt_tokens + record.completion_tokens for record in records
        ),
        "mean_latency_ms": mean(record.latency_ms for record in records),
        "mean_utility": mean(record.utility for record in records),
    }


def classify_effect(delta_mean_utility: float) -> str:
    if delta_mean_utility > 0:
        return "POSITIVE OBSERVED EFFECT"
    if delta_mean_utility == 0:
        return "NO OBSERVED UTILITY EFFECT"
    return "NEGATIVE OBSERVED EFFECT"


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
    return [
        summarize_pair_records(grouped[key])
        for key in sorted(grouped, key=lambda value: (value[0], value[1], value[2]))
    ]


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
                summary["effect"] == "NO OBSERVED UTILITY EFFECT" for summary in grouped[case_id]
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
                summary["effect"] == "NO OBSERVED UTILITY EFFECT" for summary in grouped[root_cause]
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
    """Validate all per-case and global pilot invariants before aggregation."""

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
    expected_recipient_keys = {
        (case_id, recipient) for case_id in PILOT_CASE_IDS for recipient in AGENTS
    }
    if set(expected_fingerprints) != expected_recipient_keys:
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
        expected_message_id = candidate_message_id(message)
        expected_content_hash = candidate_message_content_hash(message)
        if {record.candidate_message_id for record in pair_records} != {expected_message_id}:
            raise PilotValidationError(f"PILOT_INVALID: message id changed for pair {key}")
        if {record.candidate_message_content_hash for record in pair_records} != {
            expected_content_hash
        }:
            raise PilotValidationError(f"PILOT_INVALID: message content changed for pair {key}")
        if any(record.oracle_action != expected_oracles[key[0]] for record in pair_records):
            raise PilotValidationError(f"PILOT_INVALID: oracle changed in pair {key}")
        expected_fingerprint = expected_fingerprints[(key[0], key[2])]
        if {record.observation_fingerprint for record in pair_records} != {expected_fingerprint}:
            raise PilotValidationError(f"PILOT_INVALID: observation changed for pair {key}")

    fingerprints_by_recipient: dict[tuple[str, PVoCAgentName], set[str]] = defaultdict(set)
    for record in records:
        fingerprints_by_recipient[(record.case_id, record.recipient)].add(
            record.observation_fingerprint
        )
    if any(len(fingerprints) != 1 for fingerprints in fingerprints_by_recipient.values()):
        raise PilotValidationError("PILOT_INVALID: recipient observation fingerprints are not stable")

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


def _write_jsonl(path: Path, records: list[object]) -> None:
    with path.open("x", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(record.model_dump_json())
            output_file.write("\n")


def write_pilot_artifacts(
    *,
    output_dir: Path,
    pilot_run_id: str,
    message_records: list[PilotMessageRecord],
    trial_records: list[PilotTrialRecord],
    summary: dict[str, object],
    oracle_actions: Mapping[str, RootCauseCode],
    lambda_cost: float,
    sender_execution_count: int,
    started_at: str,
    sanity_checks: Mapping[str, bool],
) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    messages_path = output_dir / f"{pilot_run_id}.messages.jsonl"
    trials_path = output_dir / f"{pilot_run_id}.trials.jsonl"
    summary_path = output_dir / f"{pilot_run_id}.summary.json"
    manifest_path = output_dir / f"{pilot_run_id}.manifest.json"
    _write_jsonl(messages_path, message_records)
    _write_jsonl(trials_path, trial_records)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    manifest = {
        "run_id": pilot_run_id,
        "experiment": "pvoc_v0_stratified_pilot",
        "status": "VALID",
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "pilot_case_ids": list(PILOT_CASE_IDS),
        "oracle_actions": dict(oracle_actions),
        "oracle_classes": list(EXPECTED_ORACLE_ACTIONS.values()),
        "model": OPENROUTER_MODEL_ID,
        "n_trials_per_condition": N_TRIALS_PER_CONDITION,
        "directed_pairs_per_case": EXPECTED_DIRECTED_PAIRS_PER_CASE,
        "expected_frozen_messages": EXPECTED_FROZEN_MESSAGE_COUNT,
        "actual_frozen_messages": len(message_records),
        "expected_sender_executions": EXPECTED_SENDER_EXECUTIONS,
        "actual_sender_executions": sender_execution_count,
        "expected_recipient_executions": EXPECTED_RECIPIENT_EXECUTIONS,
        "actual_recipient_executions": len(trial_records),
        "lambda_communication_cost": lambda_cost,
        "sanity_checks": dict(sanity_checks),
        "notes": "Research-only six-case stratified pilot; no production graph or learned estimator.",
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return messages_path, trials_path, summary_path, manifest_path


async def validate_oracles_only(input_dir: Path) -> dict[str, RootCauseCode]:
    settings = get_settings()
    cases = load_pilot_cases(input_dir)
    engine = create_engine(settings, read_only=True)
    try:
        actions = await compute_oracle_actions(OlistRepository(create_session_factory(engine)), cases)
        validate_expected_oracles(actions)
        return actions
    finally:
        await engine.dispose()


async def run_pilot(
    *,
    input_dir: Path = Path("data/input"),
    output_dir: Path = Path("experiments/pvoc_v0/results/pilot"),
    lambda_cost: float = 0.001,
) -> tuple[Path, Path, Path, Path]:
    if lambda_cost < 0:
        raise ValueError("lambda_cost must be non-negative")
    settings = get_settings()
    cases = load_pilot_cases(input_dir)
    pilot_run_id = f"pvoc_v0_pilot_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    started_at = datetime.now(UTC).isoformat()
    engine = create_engine(settings, read_only=True)
    try:
        repository = OlistRepository(create_session_factory(engine))
        states = await prepare_pilot_states(repository, cases)

        # Oracle validation is deliberately complete before model configuration or any LLM call.
        validate_model_configuration()
        llm = ResearchVertexStructuredLLM(settings)
        agents = {agent_name: PrivateDecisionAgent(agent_name, llm) for agent_name in AGENTS}

        frozen_messages: dict[CasePair, CandidateMessage] = {}
        message_records: list[PilotMessageRecord] = []
        trial_records: list[PilotTrialRecord] = []
        expected_fingerprints: dict[tuple[str, PVoCAgentName], str] = {}
        sender_execution_count = 0

        for case_id in PILOT_CASE_IDS:
            state = states[case_id]
            observations = state.observations
            for recipient in AGENTS:
                expected_fingerprints[(case_id, recipient)] = observation_fingerprint(
                    observations.for_agent(recipient)
                )
            sender_decisions = {}
            for sender in AGENTS:
                sender_decisions[sender] = await agents[sender].decide(
                    observations.for_agent(sender)
                )
                sender_execution_count += 1
            case_messages, case_message_records = freeze_candidate_messages(
                pilot_run_id=pilot_run_id,
                case_id=case_id,
                oracle_action=state.oracle_action,
                observations=observations,
                sender_decisions=sender_decisions,
                lambda_cost=lambda_cost,
            )
            message_records.extend(case_message_records)
            for (sender, recipient), message in case_messages.items():
                frozen_messages[(case_id, sender, recipient)] = message
                recipient_observation = observations.for_agent(recipient)
                for trial_index in range(1, N_TRIALS_PER_CONDITION + 1):
                    for condition in pilot_condition_order(trial_index):
                        received_message = (
                            message.model_copy(deep=True) if condition == "with_message" else None
                        )
                        execution = await agents[recipient].decide(
                            recipient_observation.model_copy(deep=True), received_message
                        )
                        expected_fingerprint = expected_fingerprints[(case_id, recipient)]
                        if execution.observation_fingerprint != expected_fingerprint:
                            raise PilotValidationError(
                                f"PILOT_INVALID: observation fingerprint changed for "
                                f"{case_id} {sender}->{recipient}"
                            )
                        trial_records.append(
                            PilotTrialRecord(
                                pilot_run_id=pilot_run_id,
                                case_id=case_id,
                                oracle_action=state.oracle_action,
                                sender=sender,
                                recipient=recipient,
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
                                candidate_message_id=candidate_message_id(message),
                                candidate_message_content_hash=candidate_message_content_hash(message),
                                communication_cost=communication_cost(message),
                                lambda_cost=lambda_cost,
                            )
                        )

        if sender_execution_count != EXPECTED_SENDER_EXECUTIONS:
            raise PilotValidationError(
                f"PILOT_INVALID: expected {EXPECTED_SENDER_EXECUTIONS} sender executions, "
                f"got {sender_execution_count}"
            )
        if len(message_records) != EXPECTED_FROZEN_MESSAGE_COUNT:
            raise PilotValidationError(
                f"PILOT_INVALID: expected {EXPECTED_FROZEN_MESSAGE_COUNT} frozen messages, "
                f"got {len(message_records)}"
            )
        sanity_checks = validate_pilot_trials(
            trial_records,
            frozen_messages,
            {case_id: state.oracle_action for case_id, state in states.items()},
            expected_fingerprints,
        )
        summary = build_pilot_summary(
            records=trial_records,
            oracle_actions={case_id: state.oracle_action for case_id, state in states.items()},
            sanity_checks=sanity_checks,
        )
        return write_pilot_artifacts(
            output_dir=output_dir,
            pilot_run_id=pilot_run_id,
            message_records=message_records,
            trial_records=trial_records,
            summary=summary,
            oracle_actions={case_id: state.oracle_action for case_id, state in states.items()},
            lambda_cost=lambda_cost,
            sender_execution_count=sender_execution_count,
            started_at=started_at,
            sanity_checks=sanity_checks,
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the six-case stratified PVoC v0 pilot.")
    parser.add_argument("--input-dir", type=Path, default=Path("data/input"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experiments/pvoc_v0/results/pilot"),
    )
    parser.add_argument("--lambda-cost", type=float, default=0.001)
    parser.add_argument(
        "--validate-oracles-only",
        action="store_true",
        help="Check the six PolicyEngine oracle classes without calling Vertex.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.lambda_cost < 0:
        raise SystemExit("--lambda-cost must be non-negative.")
    if sys.platform == "win32":
        runner = validate_oracles_only(args.input_dir) if args.validate_oracles_only else run_pilot(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            lambda_cost=args.lambda_cost,
        )
        result = asyncio.run(runner, loop_factory=asyncio.SelectorEventLoop)
    else:
        runner = validate_oracles_only(args.input_dir) if args.validate_oracles_only else run_pilot(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            lambda_cost=args.lambda_cost,
        )
        result = asyncio.run(runner)
    if args.validate_oracles_only:
        print(json.dumps(result, indent=2))
    else:
        print(f"Wrote pilot messages: {result[0]}")
        print(f"Wrote pilot trials: {result[1]}")
        print(f"Wrote pilot summary: {result[2]}")
        print(f"Wrote pilot manifest: {result[3]}")


if __name__ == "__main__":
    main()
