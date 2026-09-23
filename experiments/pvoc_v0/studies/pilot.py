"""Run the six-case stratified PVoC v0 pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from scripts.validate_inputs import load_and_validate_inputs
from src.config.model_config import OPENROUTER_MODEL_ID, validate_model_configuration
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository
from src.policy.engine import PolicyEngine
from src.schemas.case_input import CaseInput

from ..core.llm import ResearchVertexStructuredLLM
from ..core.protocol import (
    AGENTS,
    PrivateDecisionAgent,
    PrivateObservationBuilder,
    build_candidate_message,
    candidate_message_content_hash,
    candidate_message_id,
    communication_cost,
    condition_order,
    directed_pairs,
    immediate_utility,
    observation_fingerprint,
)
from ..core.schemas import (
    CandidateMessage,
    ObservationBundle,
    PilotCondition,
    PilotMessageRecord,
    PilotTrialRecord,
    PVoCAgentName,
    RootCauseCode,
)
from .pilot_analysis import (
    EXPECTED_DIRECTED_PAIRS_PER_CASE,
    EXPECTED_FROZEN_MESSAGE_COUNT,
    EXPECTED_ORACLE_ACTIONS,
    EXPECTED_RECIPIENT_EXECUTIONS,
    EXPECTED_SENDER_EXECUTIONS,
    N_TRIALS_PER_CONDITION,
    PILOT_CASE_IDS,
    AgentPair,
    CasePair,
    PilotValidationError,
    build_pilot_summary,
    validate_pilot_trials,
)


@dataclass(frozen=True)
class PilotCaseState:
    case: CaseInput
    observations: ObservationBundle
    oracle_action: RootCauseCode


def load_pilot_cases(input_dir: Path) -> list[CaseInput]:
    cases = load_and_validate_inputs(input_dir, require_all=False)
    by_id = {case.case_id: case for case in cases}
    missing = [case_id for case_id in PILOT_CASE_IDS if case_id not in by_id]
    if missing:
        raise PilotValidationError(f"PILOT_INVALID: missing cases {', '.join(missing)}")
    return [by_id[case_id] for case_id in PILOT_CASE_IDS]


def validate_expected_oracles(oracle_actions: Mapping[str, str]) -> None:
    if dict(oracle_actions) != EXPECTED_ORACLE_ACTIONS:
        raise PilotValidationError(
            "PILOT_INVALID: expected one pre-registered case per root-cause class; "
            f"expected={EXPECTED_ORACLE_ACTIONS}, actual={dict(oracle_actions)}"
        )


async def compute_oracle_actions(
    repository: OlistRepository, cases: list[CaseInput]
) -> dict[str, RootCauseCode]:
    policy_engine = PolicyEngine()
    actions = {}
    for case in cases:
        context = await repository.get_policy_context(case.customer_request.claimed_order_id)
        actions[case.case_id] = policy_engine.evaluate(context).primary_issue
    return actions


async def prepare_pilot_states(
    repository: OlistRepository, cases: list[CaseInput]
) -> dict[str, PilotCaseState]:
    builder = PrivateObservationBuilder(repository)
    policy_engine = PolicyEngine()
    states = {}
    for case in cases:
        observations = await builder.build(case)
        context = await repository.get_policy_context(case.customer_request.claimed_order_id)
        states[case.case_id] = PilotCaseState(
            case=case,
            observations=observations,
            oracle_action=policy_engine.evaluate(context).primary_issue,
        )
    validate_expected_oracles({case_id: state.oracle_action for case_id, state in states.items()})
    return states


def pilot_condition_order(trial_index: int) -> tuple[PilotCondition, PilotCondition]:
    return condition_order(trial_index)


def freeze_candidate_messages(
    *,
    pilot_run_id: str,
    case_id: str,
    oracle_action: RootCauseCode,
    observations: ObservationBundle,
    sender_decisions: Mapping[PVoCAgentName, object],
    lambda_cost: float,
) -> tuple[dict[AgentPair, CandidateMessage], list[PilotMessageRecord]]:
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
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
        actions = await compute_oracle_actions(
            OlistRepository(create_session_factory(engine)), cases
        )
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
        validate_model_configuration()
        llm = ResearchVertexStructuredLLM(settings)
        agents = {agent: PrivateDecisionAgent(agent, llm) for agent in AGENTS}
        frozen_messages: dict[CasePair, CandidateMessage] = {}
        message_records: list[PilotMessageRecord] = []
        trial_records: list[PilotTrialRecord] = []
        fingerprints: dict[tuple[str, PVoCAgentName], str] = {}
        sender_execution_count = 0
        for case_id in PILOT_CASE_IDS:
            state = states[case_id]
            observations = state.observations
            for recipient in AGENTS:
                fingerprints[(case_id, recipient)] = observation_fingerprint(
                    observations.for_agent(recipient)
                )
            sender_decisions = {}
            for sender in AGENTS:
                sender_decisions[sender] = await agents[sender].decide(
                    observations.for_agent(sender)
                )
                sender_execution_count += 1
            case_messages, case_records = freeze_candidate_messages(
                pilot_run_id=pilot_run_id,
                case_id=case_id,
                oracle_action=state.oracle_action,
                observations=observations,
                sender_decisions=sender_decisions,
                lambda_cost=lambda_cost,
            )
            message_records.extend(case_records)
            for (sender, recipient), message in case_messages.items():
                frozen_messages[(case_id, sender, recipient)] = message
                recipient_observation = observations.for_agent(recipient)
                for trial_index in range(1, N_TRIALS_PER_CONDITION + 1):
                    for condition in pilot_condition_order(trial_index):
                        received = (
                            message.model_copy(deep=True) if condition == "with_message" else None
                        )
                        execution = await agents[recipient].decide(
                            recipient_observation.model_copy(deep=True), received
                        )
                        if execution.observation_fingerprint != fingerprints[(case_id, recipient)]:
                            raise PilotValidationError(
                                "PILOT_INVALID: observation fingerprint changed for "
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
                                candidate_message_content_hash=(
                                    candidate_message_content_hash(message)
                                ),
                                communication_cost=communication_cost(message),
                                lambda_cost=lambda_cost,
                            )
                        )
        if sender_execution_count != EXPECTED_SENDER_EXECUTIONS:
            raise PilotValidationError("PILOT_INVALID: sender execution count mismatch")
        if len(message_records) != EXPECTED_FROZEN_MESSAGE_COUNT:
            raise PilotValidationError("PILOT_INVALID: frozen message count mismatch")
        oracles = {case_id: state.oracle_action for case_id, state in states.items()}
        checks = validate_pilot_trials(
            trial_records, frozen_messages, oracles, fingerprints
        )
        summary = build_pilot_summary(
            records=trial_records, oracle_actions=oracles, sanity_checks=checks
        )
        return write_pilot_artifacts(
            output_dir=output_dir,
            pilot_run_id=pilot_run_id,
            message_records=message_records,
            trial_records=trial_records,
            summary=summary,
            oracle_actions=oracles,
            lambda_cost=lambda_cost,
            sender_execution_count=sender_execution_count,
            started_at=started_at,
            sanity_checks=checks,
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the six-case stratified PVoC v0 pilot.")
    parser.add_argument("--input-dir", type=Path, default=Path("data/input"))
    parser.add_argument("--output-dir", type=Path, default=Path("experiments/pvoc_v0/results/pilot"))
    parser.add_argument("--lambda-cost", type=float, default=0.001)
    parser.add_argument("--validate-oracles-only", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.lambda_cost < 0:
        raise SystemExit("--lambda-cost must be non-negative.")
    coroutine = (
        validate_oracles_only(args.input_dir)
        if args.validate_oracles_only
        else run_pilot(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            lambda_cost=args.lambda_cost,
        )
    )
    result = (
        asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
        if sys.platform == "win32"
        else asyncio.run(coroutine)
    )
    if args.validate_oracles_only:
        print(json.dumps(result, indent=2))
    else:
        print(f"Wrote pilot messages: {result[0]}")
        print(f"Wrote pilot trials: {result[1]}")
        print(f"Wrote pilot summary: {result[2]}")
        print(f"Wrote pilot manifest: {result[3]}")


if __name__ == "__main__":
    main()
