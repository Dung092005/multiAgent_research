"""Repeated-recipient stability study for the frozen EC_001 PVoC artifact."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from scripts.validate_inputs import load_and_validate_inputs
from src.config.model_config import OPENROUTER_MODEL_ID, validate_model_configuration
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository

from ..core.llm import ResearchVertexStructuredLLM
from ..core.protocol import (
    PrivateDecisionAgent,
    PrivateObservationBuilder,
    aggregate_condition,
    candidate_message_content_hash,
    candidate_message_id,
    condition_order,
    directed_pairs,
    immediate_utility,
    observation_fingerprint,
)
from ..core.schemas import (
    CounterfactualRecord,
    PVoCAgentName,
    StabilityTrialRecord,
)

N_TRIALS = 10
EXPECTED_PAIR_COUNT = 6
EXPECTED_EXECUTION_COUNT = EXPECTED_PAIR_COUNT * 2 * N_TRIALS


class StabilityValidationError(RuntimeError):
    """Raised when the frozen source or repeated-study state is not valid."""


def _source_manifest_candidates(results_dir: Path) -> list[tuple[Path, Path]]:
    candidates = []
    for manifest_path in results_dir.glob("*.manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("case_ids") == ["EC_001"]
            and manifest.get("case_count") == 1
            and manifest.get("candidate_message_count") == EXPECTED_PAIR_COUNT
        ):
            jsonl_path = manifest_path.with_name(
                manifest_path.name.removesuffix(".manifest.json") + ".jsonl"
            )
            if jsonl_path.is_file():
                candidates.append((manifest_path, jsonl_path))
    return sorted(candidates, key=lambda paths: paths[0].stat().st_mtime)


def load_frozen_ec001_records(results_dir: Path) -> tuple[Path, list[CounterfactualRecord]]:
    candidates = _source_manifest_candidates(results_dir)
    if not candidates:
        raise StabilityValidationError(
            "No successful EC_001 manifest with exactly six candidate messages was found."
        )
    _, jsonl_path = candidates[-1]
    records = [
        CounterfactualRecord.model_validate(json.loads(line))
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expected_pairs = set(directed_pairs())
    actual_pairs = {(record.candidate_message.sender, record.candidate_message.recipient) for record in records}
    if len(records) != EXPECTED_PAIR_COUNT or actual_pairs != expected_pairs:
        raise StabilityValidationError("Frozen EC_001 artifact does not contain exactly six directed pairs.")
    if any(record.case_id != "EC_001" for record in records):
        raise StabilityValidationError("Frozen stability source contains a non-EC_001 record.")
    return jsonl_path, records


def load_ec001_case(input_dir: Path):
    cases = load_and_validate_inputs(input_dir, require_all=False)
    matches = [case for case in cases if case.case_id == "EC_001"]
    if len(matches) != 1:
        raise StabilityValidationError("Expected exactly one valid EC_001 input case.")
    return matches[0]


def _condition_summary(records: list[StabilityTrialRecord]) -> dict[str, object]:
    try:
        return aggregate_condition(records, N_TRIALS)
    except ValueError as error:
        raise StabilityValidationError(str(error)) from error


def summarize_pair_records(records: list[StabilityTrialRecord]) -> dict[str, object]:
    if len(records) != 2 * N_TRIALS:
        raise StabilityValidationError("Each directed pair must contain exactly 20 trial records.")
    without = [record for record in records if record.condition == "without_message"]
    with_message = [record for record in records if record.condition == "with_message"]
    if len(without) != N_TRIALS or len(with_message) != N_TRIALS:
        raise StabilityValidationError("Each directed pair must contain 10 records per condition.")
    first = records[0]
    without_summary = _condition_summary(without)
    with_summary = _condition_summary(with_message)
    delta = with_summary["mean_utility"] - without_summary["mean_utility"]
    return {
        "case_id": first.case_id,
        "sender": first.sender,
        "recipient": first.recipient,
        "without_message": without_summary,
        "with_message": with_summary,
        "communication_cost": first.communication_cost,
        "lambda_cost": first.lambda_cost,
        "delta_mean_utility": delta,
        "mean_observed_value": delta - first.lambda_cost * first.communication_cost,
    }


def summarize_trials(records: list[StabilityTrialRecord]) -> list[dict[str, object]]:
    grouped: dict[tuple[PVoCAgentName, PVoCAgentName], list[StabilityTrialRecord]] = {}
    for record in records:
        grouped.setdefault((record.sender, record.recipient), []).append(record)
    if len(grouped) != EXPECTED_PAIR_COUNT:
        raise StabilityValidationError("Stability output does not contain six directed pairs.")
    return [summarize_pair_records(grouped[pair]) for pair in sorted(grouped)]


def validate_trial_records(
    records: list[StabilityTrialRecord],
    frozen_by_pair: dict[tuple[PVoCAgentName, PVoCAgentName], CounterfactualRecord],
) -> None:
    """Validate that repeated trials stayed on the frozen paired experimental state."""

    if len(records) != EXPECTED_EXECUTION_COUNT:
        raise StabilityValidationError("Stability execution count is not 120.")
    grouped: dict[tuple[PVoCAgentName, PVoCAgentName], list[StabilityTrialRecord]] = {}
    for record in records:
        grouped.setdefault((record.sender, record.recipient), []).append(record)
    if set(grouped) != set(frozen_by_pair):
        raise StabilityValidationError("Stability output pairs differ from the frozen source pairs.")

    expected_trials = {
        (trial_index, condition)
        for trial_index in range(1, N_TRIALS + 1)
        for condition in ("without_message", "with_message")
    }
    for pair, pair_records in grouped.items():
        if len(pair_records) != 2 * N_TRIALS:
            raise StabilityValidationError(f"Pair {pair} does not contain 20 trial records.")
        source_record = frozen_by_pair[pair]
        expected_message_id = candidate_message_id(source_record.candidate_message)
        expected_content_hash = candidate_message_content_hash(source_record.candidate_message)
        if {(record.trial_index, record.condition) for record in pair_records} != expected_trials:
            raise StabilityValidationError(f"Pair {pair} is missing a trial/condition combination.")
        if {record.candidate_message_id for record in pair_records} != {expected_message_id}:
            raise StabilityValidationError(f"Candidate message changed for pair {pair}.")
        if {record.candidate_message_content_hash for record in pair_records} != {
            expected_content_hash
        }:
            raise StabilityValidationError(f"Candidate message content changed for pair {pair}.")
        expected_fingerprint = source_record.with_message.observation_fingerprint
        if {record.observation_fingerprint for record in pair_records} != {expected_fingerprint}:
            raise StabilityValidationError(f"Observation fingerprint changed for pair {pair}.")


def _write_artifacts(
    records: list[StabilityTrialRecord],
    summaries: list[dict[str, object]],
    output_dir: Path,
    *,
    source_jsonl: Path,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"pvoc_v0_stability_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    raw_path = output_dir / f"{run_id}.jsonl"
    summary_path = output_dir / f"{run_id}.summary.json"
    manifest_path = output_dir / f"{run_id}.manifest.json"
    with raw_path.open("x", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(record.model_dump_json())
            output_file.write("\n")
    summary = {
        "experiment": "pvoc_v0_stability",
        "case_id": "EC_001",
        "n_trials_per_condition": N_TRIALS,
        "directed_pair_count": EXPECTED_PAIR_COUNT,
        "total_recipient_executions": len(records),
        "sanity_checks": {
            "fixed_candidate_messages": True,
            "all_observation_fingerprints_stable": True,
            "all_pair_counts_correct": True,
            "oracle_hidden_from_recipient": True,
        },
        "pairs": summaries,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "experiment": "pvoc_v0_stability",
        "created_at": datetime.now(UTC).isoformat(),
        "case_id": "EC_001",
        "source_jsonl": str(source_jsonl),
        "frozen_candidate_message_count": EXPECTED_PAIR_COUNT,
        "n_trials_per_condition": N_TRIALS,
        "expected_recipient_executions": EXPECTED_EXECUTION_COUNT,
        "actual_recipient_executions": len(records),
        "model": OPENROUTER_MODEL_ID,
        "oracle": "EC_POLICY_V1 action from frozen successful artifact",
        "sanity_checks": {
            "fixed_candidate_messages": True,
            "all_observation_fingerprints_stable": True,
            "all_pair_counts_correct": True,
            "oracle_hidden_from_recipient": True,
        },
        "notes": "Research-only repeated-decision stability run; no sender calls.",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return raw_path, summary_path, manifest_path


async def run_stability(
    *,
    input_dir: Path = Path("data/input"),
    results_dir: Path = Path("experiments/pvoc_v0/results"),
    output_dir: Path = Path("experiments/pvoc_v0/results/stability"),
) -> tuple[Path, Path, Path]:
    validate_model_configuration()
    source_jsonl, frozen_records = load_frozen_ec001_records(results_dir)
    case = load_ec001_case(input_dir)
    settings = get_settings()
    engine = create_engine(settings, read_only=True)
    try:
        repository = OlistRepository(create_session_factory(engine))
        observations = await PrivateObservationBuilder(repository).build(case)
        observation_by_agent = {
            agent: observations.for_agent(agent) for agent in (
                "order_seller_agent",
                "payment_agent",
                "delivery_agent",
            )
        }
        frozen_by_pair = {
            (record.candidate_message.sender, record.candidate_message.recipient): record
            for record in frozen_records
        }
        for pair, source_record in frozen_by_pair.items():
            current_fingerprint = observation_fingerprint(observation_by_agent[pair[1]])
            expected_fingerprint = source_record.with_message.observation_fingerprint
            if current_fingerprint != expected_fingerprint:
                raise StabilityValidationError(
                    f"Recipient observation fingerprint changed for {pair[0]} -> {pair[1]}"
                )

        llm = ResearchVertexStructuredLLM(settings)
        agents = {
            agent: PrivateDecisionAgent(agent, llm)
            for agent in ("order_seller_agent", "payment_agent", "delivery_agent")
        }
        trial_records: list[StabilityTrialRecord] = []
        for pair in sorted(frozen_by_pair):
            source_record = frozen_by_pair[pair]
            message = source_record.candidate_message
            recipient_observation = observation_by_agent[pair[1]]
            message_id = candidate_message_id(message)
            content_hash = candidate_message_content_hash(message)
            for trial_index in range(1, N_TRIALS + 1):
                for condition in condition_order(trial_index):
                    received_message = message.model_copy(deep=True) if condition == "with_message" else None
                    execution = await agents[pair[1]].decide(
                        recipient_observation.model_copy(deep=True), received_message
                    )
                    if execution.observation_fingerprint != source_record.with_message.observation_fingerprint:
                        raise StabilityValidationError(
                            f"Trial observation fingerprint changed for {pair[0]} -> {pair[1]}"
                        )
                    trial_records.append(
                        StabilityTrialRecord(
                            case_id="EC_001",
                            sender=pair[0],
                            recipient=pair[1],
                            trial_index=trial_index,
                            condition=condition,
                            predicted_root_cause=execution.predicted_root_cause,
                            confidence=execution.confidence,
                            short_reason=execution.short_reason,
                            oracle_action=source_record.oracle_action,
                            utility=immediate_utility(
                                execution.predicted_root_cause, source_record.oracle_action
                            ),
                            prompt_tokens=execution.prompt_tokens,
                            completion_tokens=execution.completion_tokens,
                            latency_ms=execution.latency_ms,
                            observation_fingerprint=execution.observation_fingerprint,
                            candidate_message_id=message_id,
                            candidate_message_content_hash=content_hash,
                            communication_cost=source_record.communication_cost,
                            lambda_cost=source_record.lambda_cost,
                        )
                    )

        validate_trial_records(trial_records, frozen_by_pair)
        summaries = summarize_trials(trial_records)
        return _write_artifacts(
            trial_records, summaries, output_dir, source_jsonl=source_jsonl
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repeated EC_001 PVoC v0 stability trials.")
    parser.add_argument("--input-dir", type=Path, default=Path("data/input"))
    parser.add_argument("--results-dir", type=Path, default=Path("experiments/pvoc_v0/results"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("experiments/pvoc_v0/results/stability"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if sys.platform == "win32":
        paths = asyncio.run(
            run_stability(
                input_dir=args.input_dir,
                results_dir=args.results_dir,
                output_dir=args.output_dir,
            ),
            loop_factory=asyncio.SelectorEventLoop,
        )
    else:
        paths = asyncio.run(
            run_stability(
                input_dir=args.input_dir,
                results_dir=args.results_dir,
                output_dir=args.output_dir,
            )
        )
    print(f"Wrote stability raw trials: {paths[0]}")
    print(f"Wrote stability summary: {paths[1]}")
    print(f"Wrote stability manifest: {paths[2]}")


if __name__ == "__main__":
    main()
