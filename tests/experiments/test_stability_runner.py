from decimal import Decimal

import pytest

from experiments.pvoc_v0.core.protocol import observation_fingerprint
from experiments.pvoc_v0.core.schemas import (
    CandidateMessage,
    PaymentObservation,
    StabilityTrialRecord,
)
from experiments.pvoc_v0.studies.stability import (
    N_TRIALS,
    candidate_message_content_hash,
    candidate_message_id,
    condition_order,
    summarize_pair_records,
)


def test_fixed_message_hashes_remain_unchanged_across_trials() -> None:
    message = CandidateMessage(
        case_id="EC_001",
        sender="order_seller_agent",
        recipient="payment_agent",
        content="The order was canceled before approval.",
        evidence_ids=["order:order-1"],
    )

    copies = [message.model_copy(deep=True) for _ in range(N_TRIALS)]

    assert len({candidate_message_id(copy) for copy in copies}) == 1
    assert len({candidate_message_content_hash(copy) for copy in copies}) == 1
    assert [copy.content for copy in copies] == [message.content] * N_TRIALS


def test_observation_fingerprint_remains_constant_for_deep_copies() -> None:
    observation = PaymentObservation(
        case_id="EC_001",
        order_id="order-1",
        payments=[],
        payment_total_brl=Decimal("0.00"),
        payment_row_count=0,
    )

    fingerprints = {
        observation_fingerprint(observation.model_copy(deep=True)) for _ in range(N_TRIALS * 2)
    }

    assert len(fingerprints) == 1


def test_condition_order_balances_ten_trials_and_alternates_order() -> None:
    orders = [condition_order(index) for index in range(1, N_TRIALS + 1)]

    assert orders[0] == ("without_message", "with_message")
    assert orders[1] == ("with_message", "without_message")
    assert sum(order.count("with_message") for order in orders) == N_TRIALS
    assert sum(order.count("without_message") for order in orders) == N_TRIALS


def test_summary_counts_modal_share_and_repeated_mean_value() -> None:
    records = []
    for index in range(1, N_TRIALS + 1):
        for condition in ("without_message", "with_message"):
            action = (
                "canceled_order_paid"
                if condition == "with_message" or index > 7
                else "valid_split_payment"
            )
            records.append(
                StabilityTrialRecord(
                    case_id="EC_001",
                    sender="order_seller_agent",
                    recipient="payment_agent",
                    trial_index=index,
                    condition=condition,
                    predicted_root_cause=action,
                    confidence=0.8,
                    short_reason="Controlled test decision",
                    oracle_action="canceled_order_paid",
                    utility=float(action == "canceled_order_paid"),
                    prompt_tokens=100,
                    completion_tokens=20,
                    latency_ms=10.0,
                    observation_fingerprint="a" * 64,
                    candidate_message_id="b" * 64,
                    candidate_message_content_hash="c" * 64,
                    communication_cost=5.0,
                    lambda_cost=0.001,
                )
            )

    summary = summarize_pair_records(records)

    assert summary["without_message"]["action_counts"] == {
        "canceled_order_paid": 3,
        "valid_split_payment": 7,
    }
    assert summary["without_message"]["modal_share"] == 0.7
    assert summary["without_message"]["accuracy_rate"] == 0.3
    assert summary["with_message"]["modal_action"] == "canceled_order_paid"
    assert summary["with_message"]["modal_share"] == 1.0
    assert summary["delta_mean_utility"] == 0.7
    assert summary["mean_observed_value"] == pytest.approx(0.695)
