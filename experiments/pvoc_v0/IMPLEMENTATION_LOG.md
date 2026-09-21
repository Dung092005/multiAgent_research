# PVoC v0 implementation log

## Scope

Added a standalone, research-only PVoC v0 experimental harness. No production
module, production graph, policy rule, database schema, or existing specialist
agent was changed.

## Reused project components

- `OlistRepository` with the existing read-only database connection.
- `OrderTools`, `PaymentTools`, and `DeliveryTools` to obtain source evidence.
- `PolicyEngine` / `EC_POLICY_V1` for the deterministic oracle action.
- `OpenRouterClient`, which is configured in this project to use Vertex AI, for
  structured research-agent decisions.
- Existing input validation in `scripts.validate_inputs`.

## Added components

- Private observation schemas and filtering for Order/Seller, Payment, and
  Delivery agents.
- Structured agent decision contract and directed candidate messages.
- Paired `deliver(message)` / `drop(message)` counterfactual runner with a
  private-observation fingerprint consistency check.
- One-step utility, communication-cost, and `V_star` metrics.
- CLI limited to `EC_001` through `EC_035`; timestamped JSONL and manifest
  outputs go under `experiments/pvoc_v0/results/`.
- Unit tests for data isolation, counterfactual consistency, and metric
  calculation.

## Deliberately out of scope for v0

- Learned value estimator.
- Entropy, mutual information, or Jensen-Shannon metrics.
- Any change to the production investigation graph or its behavior.

## Verification

- `python -m pytest tests/experiments/test_pvoc_v0.py -q`: **3 passed**.
- `ruff check experiments tests/experiments`: **passed**.
- `python -m compileall -q experiments`: **passed**.
- `python -m experiments.pvoc_v0.runner --help`: **passed**; confirms the CLI
  restricts selection to `EC_001`–`EC_035`.

The full pre-existing test suite could not complete in this local environment:

- E2E test collection requires `langgraph`, which is not installed in the
  current virtual environment.
- An existing input-generation unit test requires all 50 historical input JSON
  files under `input/`; this checkout contains none of those private inputs.

Neither limitation is changed by this research harness. No real PVoC run was
started, so this implementation did not contact Neon or Vertex AI.
