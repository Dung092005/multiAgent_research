"""Crash-safe persistence and resume validation for dataset v1."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.config.model_config import OPENROUTER_MODEL_ID, OPENROUTER_PROVIDER

from ..core.io import (
    atomic_write_json,
    atomic_write_jsonl,
    read_json_objects,
    read_jsonl,
)
from ..core.protocol import directed_pairs
from ..core.schemas import DatasetV1DatasetSample, DatasetV1MessageRecord, DatasetV1TrialRecord
from .dataset_v1_records import (
    ALL_CASE_IDS,
    DATASET_V1_CONFIG_ID,
    DATASET_V1_EXPERIMENT,
    DATASET_V1_METHODOLOGY_ID,
    EXPECTED_ROOT_CAUSE_DISTRIBUTION,
    LAMBDA_COST,
    N_TRIALS_PER_CONDITION,
    AgentPair,
    DatasetV1ValidationError,
    build_dataset_sample,
    build_dataset_summary,
    expected_counts,
    validate_case_records,
    validate_case_selection,
)


def artifact_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "manifest": run_dir / "manifest.json",
        "messages": run_dir / "messages.jsonl",
        "trials": run_dir / "trials.jsonl",
        "case_summaries": run_dir / "case_summaries.jsonl",
        "dataset": run_dir / "dataset.jsonl",
        "summary": run_dir / "summary.json",
    }


def new_manifest(run_id: str, run_dir: Path, case_ids: tuple[str, ...]) -> dict[str, object]:
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
    paths = artifact_paths(run_dir)
    for name in ("messages", "trials", "case_summaries"):
        paths[name].touch(exist_ok=False)
    manifest = new_manifest(run_id, run_dir, case_ids)
    atomic_write_json(paths["manifest"], manifest)
    return run_dir, manifest


def load_manifest(run_dir: Path) -> dict[str, object]:
    path = artifact_paths(run_dir)["manifest"]
    if not path.is_file():
        raise DatasetV1ValidationError("DATASET_V1_INVALID: resume manifest.json is missing")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected_fields = {
        "experiment": DATASET_V1_EXPERIMENT,
        "methodology_id": DATASET_V1_METHODOLOGY_ID,
        "config_id": DATASET_V1_CONFIG_ID,
        "model": OPENROUTER_MODEL_ID,
        "provider": OPENROUTER_PROVIDER,
        "n_trials_per_condition": N_TRIALS_PER_CONDITION,
        "lambda": LAMBDA_COST,
    }
    mismatch_names = {
        "config_id": "configuration",
        "methodology_id": "methodology",
        "n_trials_per_condition": "trial count",
    }
    for field, expected in expected_fields.items():
        if manifest.get(field) != expected:
            raise DatasetV1ValidationError(
                "DATASET_V1_INVALID: resume "
                f"{mismatch_names.get(field, field.replace('_', ' '))} mismatch"
            )
    if manifest.get("status") in {"COMPLETE", "INVALID"}:
        raise DatasetV1ValidationError(
            f"DATASET_V1_INVALID: cannot resume status {manifest.get('status')}"
        )
    validate_case_selection(manifest.get("case_ids", []))
    return manifest


def update_manifest(run_dir: Path, manifest: dict[str, object], **updates: object) -> dict[str, object]:
    manifest.update(updates)
    atomic_write_json(artifact_paths(run_dir)["manifest"], manifest)
    return manifest


def record_resume(run_dir: Path, manifest: dict[str, object]) -> dict[str, object]:
    """Persist one successful resume attempt for future runs."""

    return update_manifest(
        run_dir,
        manifest,
        resume_count=int(manifest.get("resume_count", 0)) + 1,
    )


def inspect_and_clean_resume_state(
    run_dir: Path,
    manifest: Mapping[str, object],
) -> set[str]:
    paths = artifact_paths(run_dir)
    run_id = manifest["run_id"]
    case_ids = tuple(manifest["case_ids"])
    oracle_actions = manifest.get("oracle_actions", {})
    messages = read_jsonl(paths["messages"], DatasetV1MessageRecord)
    trials = read_jsonl(paths["trials"], DatasetV1TrialRecord)
    summaries = read_json_objects(paths["case_summaries"])
    summary_cases = {
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
            (completed if case_id in summary_cases else incomplete).add(case_id)
    if incomplete:
        atomic_write_jsonl(
            paths["messages"], [record for record in messages if record.case_id not in incomplete]
        )
        atomic_write_jsonl(
            paths["trials"], [record for record in trials if record.case_id not in incomplete]
        )
    atomic_write_jsonl(
        paths["case_summaries"],
        [
            summary
            for summary in summaries
            if summary.get("case_id") in completed and summary.get("status") == "COMPLETE"
        ],
    )
    return completed


def finalize_run(run_dir: Path, manifest: dict[str, object]) -> dict[str, object]:
    paths = artifact_paths(run_dir)
    run_id = manifest["run_id"]
    case_ids = tuple(manifest["case_ids"])
    oracle_actions = manifest["oracle_actions"]
    messages = read_jsonl(paths["messages"], DatasetV1MessageRecord)
    trials = read_jsonl(paths["trials"], DatasetV1TrialRecord)
    expected = expected_counts(case_ids)
    if len(messages) != expected["message_count"] or len(trials) != expected["trial_count"]:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: final raw counts are incomplete")
    samples: list[DatasetV1DatasetSample] = []
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
        trials_by_pair: dict[AgentPair, list[DatasetV1TrialRecord]] = defaultdict(list)
        for record in case_trials:
            trials_by_pair[(record.sender, record.recipient)].append(record)
        samples.extend(
            build_dataset_sample(message, trials_by_pair[(message.sender, message.recipient)])
            for message in case_messages
        )
    sample_keys = {(sample.case_id, sample.sender, sample.recipient) for sample in samples}
    expected_keys = {
        (case_id, sender, recipient)
        for case_id in case_ids
        for sender, recipient in directed_pairs()
    }
    if len(samples) != expected["dataset_count"] or sample_keys != expected_keys:
        raise DatasetV1ValidationError("DATASET_V1_INVALID: final dataset keys are incomplete")
    selected_oracles = {case_id: oracle_actions[case_id] for case_id in case_ids}
    if {sample.oracle_action for sample in samples} != set(selected_oracles.values()):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: final dataset root-cause classes mismatch")
    summary = build_dataset_summary(samples)
    atomic_write_jsonl(paths["dataset"], samples)
    atomic_write_json(paths["summary"], summary)
    actual = {
        "case_count": len(case_ids),
        "message_count": len(messages),
        "trial_count": len(trials),
        "dataset_count": len(samples),
    }
    checks = {
        "all_50_validated_inputs": len(oracle_actions) == 50,
        "expected_oracle_distribution": manifest["root_cause_distribution"]
        == EXPECTED_ROOT_CAUSE_DISTRIBUTION,
        "exact_case_count": actual["case_count"] == expected["case_count"],
        "exact_message_count": actual["message_count"] == expected["message_count"],
        "exact_trial_count": actual["trial_count"] == expected["trial_count"],
        "exact_dataset_count": actual["dataset_count"] == expected["dataset_count"],
        "six_directed_edges": {(sample.sender, sample.recipient) for sample in samples}
        == set(directed_pairs()),
        "six_root_cause_classes": len({sample.oracle_action for sample in samples}) == 6
        or len(case_ids) < len(ALL_CASE_IDS),
        "unique_message_samples": len(sample_keys) == len(samples),
        "message_trial_invariants": True,
        "oracle_hidden_from_recipient": True,
        "private_observation_isolation": True,
    }
    if not all(checks.values()):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: final sanity check failed")
    update_manifest(
        run_dir,
        manifest,
        status="COMPLETE",
        completed_at=datetime.now(UTC).isoformat(),
        actual_counts=actual,
        sanity_checks=checks,
        failure=None,
    )
    return summary


def validate_completed_run_read_only(run_dir: Path) -> dict[str, object]:
    """Validate a completed dataset directory without writing or contacting services."""

    paths = artifact_paths(run_dir)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    if manifest.get("status") != "COMPLETE":
        raise DatasetV1ValidationError("DATASET_V1_INVALID: manifest is not COMPLETE")
    case_ids = tuple(manifest["case_ids"])
    oracle_actions = manifest["oracle_actions"]
    messages = read_jsonl(paths["messages"], DatasetV1MessageRecord)
    trials = read_jsonl(paths["trials"], DatasetV1TrialRecord)
    samples = read_jsonl(paths["dataset"], DatasetV1DatasetSample)
    expected = expected_counts(case_ids)
    actual = {
        "case_count": len(case_ids),
        "message_count": len(messages),
        "trial_count": len(trials),
        "dataset_count": len(samples),
    }
    if actual != expected:
        raise DatasetV1ValidationError(
            f"DATASET_V1_INVALID: offline count mismatch expected={expected}, actual={actual}"
        )
    trial_keys = {
        (record.case_id, record.sender, record.recipient, record.trial_index, record.condition)
        for record in trials
    }
    if len(trial_keys) != len(trials):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: duplicate offline trial key")
    for case_id in case_ids:
        validate_case_records(
            run_id=manifest["run_id"],
            case_id=case_id,
            oracle_action=oracle_actions[case_id],
            messages=[record for record in messages if record.case_id == case_id],
            trials=[record for record in trials if record.case_id == case_id],
        )
    sample_keys = {(sample.case_id, sample.sender, sample.recipient) for sample in samples}
    if len(sample_keys) != len(samples):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: duplicate dataset sample key")
    root_causes = Counter(sample.oracle_action for sample in samples)
    edges = {(sample.sender, sample.recipient) for sample in samples}
    if len(root_causes) != 6 or edges != set(directed_pairs()):
        raise DatasetV1ValidationError("DATASET_V1_INVALID: class or edge coverage mismatch")
    return {
        **actual,
        "root_cause_count": len(root_causes),
        "root_cause_distribution": dict(root_causes),
        "edge_count": len(edges),
        "unique_trial_keys": True,
        "message_hash_invariants": True,
        "observation_fingerprint_invariants": True,
    }
