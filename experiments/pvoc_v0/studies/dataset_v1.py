"""Collect or resume the frozen-methodology counterfactual dataset v1."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scripts.validate_inputs import load_and_validate_inputs
from src.config.model_config import validate_model_configuration
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository
from src.policy.engine import PolicyEngine
from src.schemas.case_input import CaseInput

from ..core.io import append_jsonl
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
    DatasetV1MessageRecord,
    DatasetV1TrialRecord,
    DecisionExecution,
    ObservationBundle,
    PVoCAgentName,
    RootCauseCode,
)
from .dataset_v1_records import (
    ALL_CASE_IDS,
    LAMBDA_COST,
    N_TRIALS_PER_CONDITION,
    DatasetV1ValidationError,
    validate_case_records,
    validate_case_selection,
    validate_oracle_distribution,
    validate_pair_records,
)
from .dataset_v1_store import (
    artifact_paths,
    create_run_directory,
    finalize_run,
    inspect_and_clean_resume_state,
    load_manifest,
    record_resume,
    update_manifest,
)

DIRECTED_PAIR_COUNT = 6
_artifact_paths = artifact_paths


@dataclass(frozen=True)
class DatasetCaseState:
    case: CaseInput
    observations: ObservationBundle
    oracle_action: RootCauseCode


def load_all_validated_cases(input_dir: Path) -> dict[str, CaseInput]:
    cases = load_and_validate_inputs(input_dir, require_all=True)
    by_id = {case.case_id: case for case in cases}
    if set(by_id) != set(ALL_CASE_IDS):
        raise DatasetV1ValidationError(
            "DATASET_V1_INVALID: validated input IDs are not exactly EC_001 through EC_050"
        )
    return by_id


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
    fingerprints = {
        recipient: observation_fingerprint(observations.for_agent(recipient))
        for recipient in AGENTS
    }
    sender_decisions = {
        sender: await agents[sender].decide(observations.for_agent(sender))
        for sender in AGENTS
    }
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
            recipient_observation_fingerprint=fingerprints[recipient],
            lambda_cost=lambda_cost,
        )
        pair_trials: list[DatasetV1TrialRecord] = []
        recipient_observation = observations.for_agent(recipient)
        for trial_index in range(1, N_TRIALS_PER_CONDITION + 1):
            for condition in condition_order(trial_index):
                received = message.model_copy(deep=True) if condition == "with_message" else None
                execution = await agents[recipient].decide(
                    recipient_observation.model_copy(deep=True), received
                )
                if execution.observation_fingerprint != fingerprints[recipient]:
                    raise DatasetV1ValidationError(
                        "DATASET_V1_INVALID: observation fingerprint changed for "
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
                        candidate_message_content_hash=(
                            message_record.candidate_message_content_hash
                        ),
                        communication_cost=message_record.communication_cost,
                        lambda_cost=lambda_cost,
                    )
                )
        validate_pair_records(
            message_record,
            pair_trials,
            expected_fingerprint=fingerprints[recipient],
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
        expected_fingerprints=fingerprints,
    )
    append_jsonl(paths["case_summaries"], [summary])
    return summary


def _safe_failure(
    error: BaseException, *, phase: str, case_id: str | None = None
) -> dict[str, object]:
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
        record_resume(run_dir, manifest)
        selected_case_ids = tuple(manifest["case_ids"])
    else:
        selected_case_ids = validate_case_selection(case_ids)
        run_dir, manifest = create_run_directory(output_dir, selected_case_ids)

    paths = artifact_paths(run_dir)
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
        update_manifest(
            run_dir,
            manifest,
            oracle_actions=oracle_actions,
            root_cause_distribution=validate_oracle_distribution(oracle_actions),
            validated_input_case_count=len(all_cases),
            status="RUNNING",
            failure=None,
        )
        completed_cases = inspect_and_clean_resume_state(run_dir, manifest)
        pending_cases = [case_id for case_id in selected_case_ids if case_id not in completed_cases]
        if not pending_cases:
            finalize_run(run_dir, manifest)
            return run_dir

        validate_model_configuration()
        llm = ResearchVertexStructuredLLM(settings)
        agents = {agent: PrivateDecisionAgent(agent, llm) for agent in AGENTS}
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
                completed_count = len(completed_cases) + newly_completed
                update_manifest(
                    run_dir,
                    manifest,
                    status="INTERRUPTED",
                    actual_counts={
                        "case_count": completed_count,
                        "message_count": completed_count * DIRECTED_PAIR_COUNT,
                        "trial_count": completed_count
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
        "--output-dir", type=Path, default=Path("experiments/pvoc_v0/results/dataset_v1")
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
        coroutine = run_dataset_v1(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            case_ids=selected,
            resume_dir=args.resume,
            stop_after_cases=args.stop_after_cases,
            lambda_cost=args.lambda_cost,
        )
        run_dir = (
            asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
            if sys.platform == "win32"
            else asyncio.run(coroutine)
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
