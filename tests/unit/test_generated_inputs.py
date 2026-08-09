from collections import Counter
from pathlib import Path

from scripts.generate_inputs import load_contexts
from scripts.validate_inputs import load_and_validate_inputs
from src.policy.engine import PolicyEngine


def test_generated_inputs_are_grounded_and_balanced():
    cases = load_and_validate_inputs(Path("input"))
    contexts = {context.order.order_id: context for context in load_contexts(Path("data"))}
    engine = PolicyEngine()
    distribution = Counter(
        engine.classify(contexts[case.customer_request.claimed_order_id]) for case in cases
    )
    assert distribution == {
        "canceled_order_paid": 9,
        "unavailable_order_paid": 9,
        "late_delivery_seller": 8,
        "late_delivery_logistics": 8,
        "valid_split_payment": 8,
        "unsupported_late_claim": 8,
    }
