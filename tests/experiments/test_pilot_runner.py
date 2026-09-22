from datetime import UTC, datetime
from decimal import Decimal

import pytest

from experiments.pvoc_v0.agents import PrivateDecisionAgent
from experiments.pvoc_v0.messages import AGENTS, directed_pairs
from experiments.pvoc_v0.observations import observation_fingerprint
from experiments.pvoc_v0.pilot_runner import (
    EXPECTED_ORACLE_ACTIONS,
    EXPECTED_RECIPIENT_EXECUTIONS,
    EXPECTED_RECIPIENT_EXECUTIONS_PER_CASE,
    N_TRIALS_PER_CONDITION,
    PILOT_CASE_IDS,
    PilotValidationError,
    aggregate_by_edge,
    aggregate_by_root_cause,
    build_pilot_summary,
    candidate_message_content_hash,
    candidate_message_id,
    freeze_candidate_messages,
    pilot_condition_order,
    summarize_pair_records,
    summarize_pilot_trials,
    validate_pilot_trials,
)
from experiments.pvoc_v0.research_llm import ResearchStructuredResponse
from experiments.pvoc_v0.schemas import (
    CandidateMessage,
    DecisionExecution,
    DecisionResponse,
    DeliveryObservation,
    DeliveryShippingEvidence,
    ObservationBundle,
    OrderSellerItemEvidence,
    OrderSellerObservation,
    PaymentObservation,
    PilotTrialRecord,
)
from src.schemas.records import (
    DeliveryTimeline,
    PaymentRecord,
    SellerRecord,
)


def fake_observations(case_id: str = "EC_001") -> ObservationBundle:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    return ObservationBundle(
        order_seller=OrderSellerObservation(
            case_id=case_id,
            order_id=f"order-{case_id}",
            order_status="canceled",
            order_purchase_timestamp=timestamp,
            order_approved_at=timestamp,
            items=[
                OrderSellerItemEvidence(
                    order_item_id=1,
                    seller_id="seller-1",
                    product_id="product-1",
                    shipping_limit_date=timestamp,
                    price=Decimal("10.00"),
                    freight_value=Decimal("2.00"),
                )
            ],
            sellers=[
                SellerRecord(
                    seller_id="seller-1",
                    seller_zip_code_prefix=12345,
                    seller_city="Sao Paulo",
                    seller_state="SP",
                )
            ],
        ),
        payment=PaymentObservation(
            case_id=case_id,
            order_id=f"order-{case_id}",
            payments=[
                PaymentRecord(
                    order_id=f"order-{case_id}",
                    payment_sequential=1,
                    payment_type="credit_card",
                    payment_installments=1,
                    payment_value=Decimal("12.00"),
                )
            ],
            payment_total_brl=Decimal("12.00"),
            payment_row_count=1,
        ),
        delivery=DeliveryObservation(
            case_id=case_id,
            order_id=f"order-{case_id}",
            timeline=DeliveryTimeline(
                order_delivered_carrier_date=timestamp,
                order_delivered_customer_date=timestamp,
                order_estimated_delivery_date=timestamp,
            ),
            shipping_limits=[
                DeliveryShippingEvidence(
                    order_item_id=1,
                    seller_id="seller-1",
                    shipping_limit_date=timestamp,
                )
            ],
            review_count=0,
        ),
    )


def fake_sender_decisions(observations: ObservationBundle) -> dict[str, DecisionExecution]:
    return {
        agent: DecisionExecution(
            agent=agent,
            predicted_root_cause="canceled_order_paid",
            confidence=0.8,
            short_reason="Controlled fake sender decision",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=1.0,
            observation_fingerprint=observation_fingerprint(observations.for_agent(agent)),
        )
        for agent in AGENTS
    }


def fake_message_map() -> dict[tuple[str, str, str], CandidateMessage]:
    messages = {}
    for case_id in PILOT_CASE_IDS:
        for sender, recipient in directed_pairs():
            messages[(case_id, sender, recipient)] = CandidateMessage(
                case_id=case_id,
                sender=sender,
                recipient=recipient,
                content=f"{sender} evidence for {case_id}.",
                evidence_ids=[f"order:{case_id}"],
            )
    return messages


def wrong_action(oracle: str) -> str:
    actions = tuple(EXPECTED_ORACLE_ACTIONS.values())
    return next(action for action in actions if action != oracle)


def fake_trial_records() -> tuple[list[PilotTrialRecord], dict, dict, dict]:
    messages = fake_message_map()
    expected_fingerprints = {
        (case_id, recipient): f"{case_id}-{recipient}".replace("_", "")[:8] + "f" * 56
        for case_id in PILOT_CASE_IDS
        for recipient in AGENTS
    }
    records: list[PilotTrialRecord] = []
    for case_id in PILOT_CASE_IDS:
        oracle = EXPECTED_ORACLE_ACTIONS[case_id]
        for sender, recipient in directed_pairs():
            message = messages[(case_id, sender, recipient)]
            for trial_index in range(1, N_TRIALS_PER_CONDITION + 1):
                for condition in pilot_condition_order(trial_index):
                    action = oracle if condition == "with_message" and case_id == "EC_001" else wrong_action(oracle)
                    records.append(
                        PilotTrialRecord(
                            pilot_run_id="pilot-test",
                            case_id=case_id,
                            oracle_action=oracle,
                            sender=sender,
                            recipient=recipient,
                            trial_index=trial_index,
                            condition=condition,
                            predicted_root_cause=action,
                            confidence=0.8,
                            short_reason="Controlled fake recipient decision",
                            utility=float(action == oracle),
                            prompt_tokens=20,
                            completion_tokens=10,
                            latency_ms=2.0,
                            observation_fingerprint=expected_fingerprints[(case_id, recipient)],
                            candidate_message_id=candidate_message_id(message),
                            candidate_message_content_hash=candidate_message_content_hash(message),
                            communication_cost=5.0,
                            lambda_cost=0.001,
                        )
                    )
    return records, messages, expected_fingerprints, dict(EXPECTED_ORACLE_ACTIONS)


def test_pilot_case_set_is_exactly_six_pre_registered_ids() -> None:
    assert PILOT_CASE_IDS == ("EC_001", "EC_010", "EC_019", "EC_027", "EC_035", "EC_043")
    assert set(EXPECTED_ORACLE_ACTIONS) == set(PILOT_CASE_IDS)
    assert len(set(EXPECTED_ORACLE_ACTIONS.values())) == 6


def test_one_case_freezes_exactly_six_directed_messages() -> None:
    observations = fake_observations()
    frozen, records = freeze_candidate_messages(
        pilot_run_id="pilot-test",
        case_id="EC_001",
        oracle_action="canceled_order_paid",
        observations=observations,
        sender_decisions=fake_sender_decisions(observations),
        lambda_cost=0.001,
    )

    assert set(frozen) == set(directed_pairs())
    assert len(frozen) == 6
    assert len(records) == 6


def test_frozen_candidate_message_hashes_remain_constant_across_trials() -> None:
    message = fake_message_map()[("EC_001", "order_seller_agent", "payment_agent")]
    copies = [message.model_copy(deep=True) for _ in range(N_TRIALS_PER_CONDITION * 2)]

    assert len({candidate_message_id(copy) for copy in copies}) == 1
    assert len({candidate_message_content_hash(copy) for copy in copies}) == 1


def test_each_pair_has_five_with_and_five_without_and_each_case_has_sixty() -> None:
    records, messages, fingerprints, oracles = fake_trial_records()
    validate_pilot_trials(records, messages, oracles, fingerprints)

    assert len(records) == EXPECTED_RECIPIENT_EXECUTIONS
    for case_id in PILOT_CASE_IDS:
        case_records = [record for record in records if record.case_id == case_id]
        assert len(case_records) == EXPECTED_RECIPIENT_EXECUTIONS_PER_CASE
        for sender, recipient in directed_pairs():
            pair_records = [
                record
                for record in case_records
                if record.sender == sender and record.recipient == recipient
            ]
            assert len(pair_records) == 10
            assert sum(record.condition == "with_message" for record in pair_records) == 5
            assert sum(record.condition == "without_message" for record in pair_records) == 5


def test_observation_fingerprint_validation_rejects_changed_state() -> None:
    records, messages, fingerprints, oracles = fake_trial_records()
    changed = records[0].model_copy(update={"observation_fingerprint": "z" * 64})

    with pytest.raises(PilotValidationError, match="observation changed"):
        validate_pilot_trials([changed, *records[1:]], messages, oracles, fingerprints)


def test_delta_and_mean_observed_value_calculation() -> None:
    message = fake_message_map()[("EC_001", "order_seller_agent", "payment_agent")]
    fingerprint = "f" * 64
    records = []
    for trial_index in range(1, N_TRIALS_PER_CONDITION + 1):
        for condition in pilot_condition_order(trial_index):
            action = "canceled_order_paid" if condition == "with_message" else "valid_split_payment"
            records.append(
                PilotTrialRecord(
                    pilot_run_id="pilot-test",
                    case_id="EC_001",
                    oracle_action="canceled_order_paid",
                    sender="order_seller_agent",
                    recipient="payment_agent",
                    trial_index=trial_index,
                    condition=condition,
                    predicted_root_cause=action,
                    confidence=0.8,
                    short_reason="Controlled fake recipient decision",
                    utility=float(action == "canceled_order_paid"),
                    prompt_tokens=20,
                    completion_tokens=10,
                    latency_ms=2.0,
                    observation_fingerprint=fingerprint,
                    candidate_message_id=candidate_message_id(message),
                    candidate_message_content_hash=candidate_message_content_hash(message),
                    communication_cost=5.0,
                    lambda_cost=0.001,
                )
            )

    summary = summarize_pair_records(records)

    assert summary["delta_mean_utility"] == pytest.approx(1.0)
    assert summary["mean_observed_value"] == pytest.approx(0.995)


def test_aggregation_by_root_cause_and_sender_recipient() -> None:
    records, messages, fingerprints, oracles = fake_trial_records()
    validate_pilot_trials(records, messages, oracles, fingerprints)
    pair_summaries = summarize_pilot_trials(records)
    by_root_cause = aggregate_by_root_cause(pair_summaries)
    by_edge = aggregate_by_edge(pair_summaries)
    summary = build_pilot_summary(
        records=records,
        oracle_actions=oracles,
        sanity_checks={"all": True},
    )

    assert len(pair_summaries) == 36
    assert len(by_root_cause) == 6
    assert len(by_edge) == 6
    assert sum(row["pair_count"] for row in by_root_cause) == 36
    assert sum(row["positive"] for row in by_root_cause) == 6
    assert summary["overall"]["positive_effect_count"] == 6
    assert all(row["positive_count"] == 1 for row in by_edge)


class FakeResearchLLM:
    def __init__(self) -> None:
        self.last_payload = None

    async def structured(self, *, system_prompt, user_payload, response_model):
        self.last_payload = user_payload
        return ResearchStructuredResponse(
            value=DecisionResponse(
                predicted_root_cause="canceled_order_paid",
                confidence=0.8,
                short_reason="Controlled fake recipient decision",
            ),
            prompt_tokens=10,
            completion_tokens=5,
        )


@pytest.mark.asyncio
async def test_recipient_prompt_contains_only_private_observation_and_message() -> None:
    observations = fake_observations()
    llm = FakeResearchLLM()
    agent = PrivateDecisionAgent("payment_agent", llm)
    message = CandidateMessage(
        case_id="EC_001",
        sender="order_seller_agent",
        recipient="payment_agent",
        content="Sender message only.",
        evidence_ids=["order:EC_001"],
    )

    await agent.decide(observations.payment.model_copy(deep=True), message)

    assert set(llm.last_payload) == {"recipient", "private_observation", "received_message"}
    assert "oracle_action" not in str(llm.last_payload)
    assert "order_status" not in str(llm.last_payload["private_observation"])
    assert "payments" not in str(llm.last_payload["received_message"])
    assert "payment_total_brl" not in str(llm.last_payload["received_message"])
