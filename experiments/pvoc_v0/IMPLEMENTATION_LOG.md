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

## Counterfactual message-value dataset v1

Added a separate crash-safe dataset runner in `dataset_v1_runner.py`. It keeps
the existing PVoC v0, stability, and pilot entry points unchanged and collects
exactly EC_001 through EC_050 with the existing six root-cause labels.

- One sender decision and six frozen directed messages are created per case.
- Each frozen message has five `without_message` and five `with_message`
  recipient trials.
- Raw messages and trials are flushed after every completed directed pair;
  completed cases are recorded in `case_summaries.jsonl`.
- `--resume <run_dir>` validates the run identifiers, skips complete cases,
  and removes incomplete cases before rerunning them from a clean boundary.
- The dataset sample uses repeated mean utility and `repeated_mean_value`; it
  does not rename or replace the existing single-run `V_star` metric.

The required two-case dry run (EC_001 and EC_010) completed after a deliberate
checkpoint/resume test with 2 cases, 12 messages, 120 trials, and 12 dataset
rows. The full run completed with 50 cases, 300 messages, 3000 trials, and 300
dataset rows. Final validation found 6 directed edges, 6 oracle classes, no
duplicate trial keys, and no message-hash or observation-fingerprint changes.

One bounded Vertex structured-response interruption occurred at EC_026 due to
`LengthFinishReasonError`. The research-only Vertex adapter was extended to
retry that provider error within its existing bounded retry policy. Resuming
the same run cleaned the incomplete EC_026 case and completed the full dataset.

Final artifacts are under
`experiments/pvoc_v0/results/dataset_v1/pvoc_v1_20260922T205457Z_483f9ca0/`.
The research test suite passed with 34 tests and Ruff passed for the research
module and its tests.
