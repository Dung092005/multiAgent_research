from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from experiments.pvoc_v0.core.io import append_jsonl, atomic_write_json, read_jsonl
from experiments.pvoc_v0.core.protocol import (
    candidate_message_content_hash,
    candidate_message_id,
    condition_order,
    directed_pairs,
)
from experiments.pvoc_v0.core.schemas import (
    CandidateMessage,
    DatasetV1MessageRecord,
    DatasetV1TrialRecord,
    RootCauseCode,
)
from experiments.pvoc_v0.studies.dataset_v1_records import (
    ALL_CASE_IDS,
    DATASET_V1_CONFIG_ID,
    EXPECTED_ROOT_CAUSE_DISTRIBUTION,
    LAMBDA_COST,
    N_TRIALS_PER_CONDITION,
    DatasetV1ValidationError,
    build_dataset_sample,
    build_dataset_summary,
    effect_label,
    expected_counts,
    validate_case_records,
    validate_oracle_distribution,
)
from experiments.pvoc_v0.studies.dataset_v1_store import artifact_paths as _artifact_paths
from experiments.pvoc_v0.studies.dataset_v1_store import (
    create_run_directory,
    inspect_and_clean_resume_state,
    load_manifest,
    record_resume,
    update_manifest,
    validate_completed_run_read_only,
)

RUN_ID = "dataset-v1-test"
FINGERPRINTS = {
    "order_seller_agent": "o" * 64,
    "payment_agent": "p" * 64,
    "delivery_agent": "d" * 64,
}


def all_oracles() -> dict[str, RootCauseCode]:
    actions: dict[str, RootCauseCode] = {}
    case_number = 1
    for root_cause, count in EXPECTED_ROOT_CAUSE_DISTRIBUTION.items():
        for _ in range(count):
            actions[f"EC_{case_number:03d}"] = root_cause  # type: ignore[assignment]
            case_number += 1
    return actions


def wrong_action(oracle: RootCauseCode) -> RootCauseCode:
    return "valid_split_payment" if oracle != "valid_split_payment" else "canceled_order_paid"


def make_message(
    case_id: str,
    sender: str,
    recipient: str,
    oracle: RootCauseCode,
    run_id: str = RUN_ID,
) -> DatasetV1MessageRecord:
    candidate = CandidateMessage(
        case_id=case_id,
        sender=sender,
        recipient=recipient,
        content=f"{sender} evidence for {case_id}.",
        evidence_ids=[f"evidence:{case_id}:{sender}"],
    )
    return DatasetV1MessageRecord(
        run_id=run_id,
        case_id=case_id,
        oracle_action=oracle,
        sender=sender,
        recipient=recipient,
        sender_predicted_root_cause=oracle,
        sender_confidence=0.8,
        candidate_message=candidate.content,
        evidence_ids=list(candidate.evidence_ids),
        candidate_message_id=candidate_message_id(candidate),
        candidate_message_content_hash=candidate_message_content_hash(candidate),
        communication_cost=4.0,
        lambda_cost=LAMBDA_COST,
        recipient_observation_fingerprint=FINGERPRINTS[recipient],
    )


def make_pair_records(
    message: DatasetV1MessageRecord,
    effect: str = "ZERO",
) -> list[DatasetV1TrialRecord]:
    oracle = message.oracle_action
    wrong = wrong_action(oracle)
    without_action = oracle if effect == "NEGATIVE" else wrong
    with_action = oracle if effect == "POSITIVE" else wrong
    records: list[DatasetV1TrialRecord] = []
    for trial_index in range(1, N_TRIALS_PER_CONDITION + 1):
        for condition in condition_order(trial_index):
            action = with_action if condition == "with_message" else without_action
            records.append(
                DatasetV1TrialRecord(
                    run_id=message.run_id,
                    case_id=message.case_id,
                    oracle_action=oracle,
                    sender=message.sender,
                    recipient=message.recipient,
                    candidate_message_id=message.candidate_message_id,
                    trial_index=trial_index,
                    condition=condition,
                    predicted_root_cause=action,
                    confidence=0.7 if condition == "without_message" else 0.8,
                    short_reason="Deterministic test decision",
                    utility=float(action == oracle),
                    prompt_tokens=10,
                    completion_tokens=5,
                    latency_ms=2.0,
                    observation_fingerprint=message.recipient_observation_fingerprint,
                    candidate_message_content_hash=message.candidate_message_content_hash,
                    communication_cost=message.communication_cost,
                    lambda_cost=message.lambda_cost,
                )
            )
    return records


def make_case_records(
    case_id: str,
    oracle: RootCauseCode,
    run_id: str = RUN_ID,
) -> tuple[list[DatasetV1MessageRecord], list[DatasetV1TrialRecord]]:
    messages: list[DatasetV1MessageRecord] = []
    trials: list[DatasetV1TrialRecord] = []
    effects = ("POSITIVE", "ZERO", "NEGATIVE", "ZERO", "ZERO", "ZERO")
    for (sender, recipient), effect in zip(directed_pairs(), effects, strict=True):
        message = make_message(case_id, sender, recipient, oracle, run_id)
        messages.append(message)
        trials.extend(make_pair_records(message, effect))
    return messages, trials


def test_dataset_case_set_is_exactly_ec_001_through_ec_050() -> None:
    assert ALL_CASE_IDS == tuple(f"EC_{index:03d}" for index in range(1, 51))
    assert expected_counts(ALL_CASE_IDS) == {
        "case_count": 50,
        "message_count": 300,
        "trial_count": 3000,
        "dataset_count": 300,
    }


def test_expected_root_cause_distribution_validation() -> None:
    actions = all_oracles()
    assert dict(Counter(actions.values())) == EXPECTED_ROOT_CAUSE_DISTRIBUTION
    assert validate_oracle_distribution(actions) == EXPECTED_ROOT_CAUSE_DISTRIBUTION

    invalid = dict(actions)
    invalid["EC_001"] = "valid_split_payment"
    with pytest.raises(DatasetV1ValidationError, match="oracle distribution mismatch"):
        validate_oracle_distribution(invalid)


def test_one_case_has_exactly_six_directed_pairs() -> None:
    messages, trials = make_case_records("EC_001", "canceled_order_paid")
    assert {(message.sender, message.recipient) for message in messages} == set(directed_pairs())
    assert len(messages) == 6
    assert len(trials) == 60


def test_fixed_candidate_messages_and_hashes_do_not_change() -> None:
    message = make_message(
        "EC_001", "order_seller_agent", "payment_agent", "canceled_order_paid"
    )
    copies = [message.model_copy(deep=True) for _ in range(10)]
    assert {copy.candidate_message_id for copy in copies} == {message.candidate_message_id}
    assert {copy.candidate_message_content_hash for copy in copies} == {
        message.candidate_message_content_hash
    }


def test_each_pair_has_five_with_and_five_without_trials() -> None:
    messages, trials = make_case_records("EC_001", "canceled_order_paid")
    validate_case_records(
        run_id=RUN_ID,
        case_id="EC_001",
        oracle_action="canceled_order_paid",
        messages=messages,
        trials=trials,
        expected_fingerprints=FINGERPRINTS,
    )
    for sender, recipient in directed_pairs():
        pair_trials = [
            record for record in trials if record.sender == sender and record.recipient == recipient
        ]
        assert len(pair_trials) == 10
        assert sum(record.condition == "with_message" for record in pair_trials) == 5
        assert sum(record.condition == "without_message" for record in pair_trials) == 5


def test_duplicate_trial_key_is_rejected() -> None:
    messages, trials = make_case_records("EC_001", "canceled_order_paid")
    duplicate_trials = [*trials[:-1], trials[0]]
    with pytest.raises(DatasetV1ValidationError, match="duplicate trial key"):
        validate_case_records(
            run_id=RUN_ID,
            case_id="EC_001",
            oracle_action="canceled_order_paid",
            messages=messages,
            trials=duplicate_trials,
        )


def test_message_hash_validation_rejects_changed_hash() -> None:
    messages, trials = make_case_records("EC_001", "canceled_order_paid")
    changed = trials[0].model_copy(update={"candidate_message_content_hash": "a" * 64})
    with pytest.raises(DatasetV1ValidationError, match="content hash changed"):
        validate_case_records(
            run_id=RUN_ID,
            case_id="EC_001",
            oracle_action="canceled_order_paid",
            messages=messages,
            trials=[changed, *trials[1:]],
        )


def test_observation_fingerprint_validation_rejects_changed_state() -> None:
    messages, trials = make_case_records("EC_001", "canceled_order_paid")
    changed = trials[0].model_copy(update={"observation_fingerprint": "x" * 64})
    with pytest.raises(DatasetV1ValidationError, match="observation fingerprint changed"):
        validate_case_records(
            run_id=RUN_ID,
            case_id="EC_001",
            oracle_action="canceled_order_paid",
            messages=messages,
            trials=[changed, *trials[1:]],
        )


def test_delta_repeated_value_and_effect_labels() -> None:
    messages, trials = make_case_records("EC_001", "canceled_order_paid")
    pair_messages = messages[:3]
    samples = []
    for message in pair_messages:
        pair_trials = [
            record
            for record in trials
            if record.sender == message.sender and record.recipient == message.recipient
        ]
        samples.append(build_dataset_sample(message, pair_trials))

    assert samples[0].delta_mean_utility == 1.0
    assert samples[0].repeated_mean_value == 1.0 - (LAMBDA_COST * 4.0)
    assert samples[0].effect_label == "POSITIVE"
    assert samples[1].delta_mean_utility == 0.0
    assert samples[1].effect_label == "ZERO"
    assert samples[2].delta_mean_utility == -1.0
    assert samples[2].effect_label == "NEGATIVE"
    assert effect_label(1.0) == "POSITIVE"
    assert effect_label(0.0) == "ZERO"
    assert effect_label(-1.0) == "NEGATIVE"


def test_summary_aggregates_root_cause_and_edge_statistics() -> None:
    messages, trials = make_case_records("EC_001", "canceled_order_paid")
    samples = [
        build_dataset_sample(
            message,
            [
                record
                for record in trials
                if record.sender == message.sender and record.recipient == message.recipient
            ],
        )
        for message in messages
    ]
    summary = build_dataset_summary(samples)
    assert summary["overall"]["message_sample_count"] == 6
    assert len(summary["by_root_cause"]) == 1
    assert len(summary["by_communication_edge"]) == 6
    assert summary["overall"]["positive_count"] == 1
    assert summary["overall"]["zero_count"] == 4
    assert summary["overall"]["negative_count"] == 1


def test_checkpoint_append_and_read_round_trip(tmp_path: Path) -> None:
    run_dir, manifest = create_run_directory(tmp_path, ("EC_001",))
    messages, trials = make_case_records("EC_001", "canceled_order_paid", manifest["run_id"])
    paths = _artifact_paths(run_dir)
    append_jsonl(paths["messages"], messages[:1])
    append_jsonl(paths["trials"], trials[:10])
    append_jsonl(paths["case_summaries"], [{"case_id": "EC_001", "status": "CHECKPOINT"}])
    assert len(read_jsonl(paths["messages"], DatasetV1MessageRecord)) == 1
    assert len(read_jsonl(paths["trials"], DatasetV1TrialRecord)) == 10


def test_resume_skips_completed_case_without_duplicates(tmp_path: Path) -> None:
    run_dir, manifest = create_run_directory(tmp_path, ("EC_001", "EC_010"))
    oracles = {"EC_001": "canceled_order_paid", "EC_010": "unavailable_order_paid"}
    update_manifest(run_dir, manifest, oracle_actions=oracles)
    messages, trials = make_case_records("EC_001", oracles["EC_001"], manifest["run_id"])
    paths = _artifact_paths(run_dir)
    append_jsonl(paths["messages"], messages)
    append_jsonl(paths["trials"], trials)
    case_summary = validate_case_records(
        run_id=manifest["run_id"],
        case_id="EC_001",
        oracle_action=oracles["EC_001"],
        messages=messages,
        trials=trials,
    )
    append_jsonl(paths["case_summaries"], [case_summary])

    assert inspect_and_clean_resume_state(run_dir, manifest) == {"EC_001"}
    first_counts = (
        len(read_jsonl(paths["messages"], DatasetV1MessageRecord)),
        len(read_jsonl(paths["trials"], DatasetV1TrialRecord)),
    )
    assert inspect_and_clean_resume_state(run_dir, manifest) == {"EC_001"}
    second_counts = (
        len(read_jsonl(paths["messages"], DatasetV1MessageRecord)),
        len(read_jsonl(paths["trials"], DatasetV1TrialRecord)),
    )
    assert first_counts == second_counts == (6, 60)


def test_resume_removes_incomplete_case_at_clean_boundary(tmp_path: Path) -> None:
    run_dir, manifest = create_run_directory(tmp_path, ("EC_010",))
    update_manifest(run_dir, manifest, oracle_actions={"EC_010": "unavailable_order_paid"})
    messages, trials = make_case_records("EC_010", "unavailable_order_paid", manifest["run_id"])
    paths = _artifact_paths(run_dir)
    append_jsonl(paths["messages"], messages[:1])
    append_jsonl(paths["trials"], trials[:1])

    assert inspect_and_clean_resume_state(run_dir, manifest) == set()
    assert read_jsonl(paths["messages"], DatasetV1MessageRecord) == []
    assert read_jsonl(paths["trials"], DatasetV1TrialRecord) == []


def test_resume_methodology_or_config_mismatch_stops(tmp_path: Path) -> None:
    run_dir, manifest = create_run_directory(tmp_path, ("EC_001",))
    manifest["config_id"] = "different-config"
    atomic_write_json(_artifact_paths(run_dir)["manifest"], manifest)
    with pytest.raises(DatasetV1ValidationError, match="configuration mismatch"):
        load_manifest(run_dir)
    assert DATASET_V1_CONFIG_ID != "different-config"


def test_record_resume_increments_future_manifest_metadata(tmp_path: Path) -> None:
    run_dir, manifest = create_run_directory(tmp_path, ("EC_001",))
    record_resume(run_dir, manifest)
    persisted = _artifact_paths(run_dir)["manifest"].read_text(encoding="utf-8")
    assert '"resume_count": 1' in persisted


def test_final_invariant_validation_completes_full_fake_dataset(tmp_path: Path) -> None:
    from experiments.pvoc_v0.studies.dataset_v1_store import finalize_run

    run_dir, manifest = create_run_directory(tmp_path, ALL_CASE_IDS)
    oracles = all_oracles()
    update_manifest(
        run_dir,
        manifest,
        oracle_actions=oracles,
        root_cause_distribution=EXPECTED_ROOT_CAUSE_DISTRIBUTION,
    )
    all_messages: list[DatasetV1MessageRecord] = []
    all_trials: list[DatasetV1TrialRecord] = []
    for case_id in ALL_CASE_IDS:
        messages, trials = make_case_records(case_id, oracles[case_id], manifest["run_id"])
        all_messages.extend(messages)
        all_trials.extend(trials)
    paths = _artifact_paths(run_dir)
    append_jsonl(paths["messages"], all_messages)
    append_jsonl(paths["trials"], all_trials)

    summary = finalize_run(run_dir, manifest)
    assert summary["overall"]["message_sample_count"] == 300
    assert manifest["status"] == "COMPLETE"
    assert manifest["actual_counts"] == {
        "case_count": 50,
        "message_count": 300,
        "trial_count": 3000,
        "dataset_count": 300,
    }
    assert len(paths["dataset"].read_text(encoding="utf-8").splitlines()) == 300
    assert manifest["sanity_checks"]["six_root_cause_classes"] is True
    assert validate_completed_run_read_only(run_dir)["unique_trial_keys"] is True
