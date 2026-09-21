# PVoC v0: private-observation counterfactual communication

This is a research-only experimental harness for a controlled three-agent
communication study. It is isolated from the production Olist dispute graph:
it does not import, invoke, or change `src.graph`.

## Study design

The harness reuses the existing read-only Olist repository and specialist
tools to construct three private observations for each EC case:

- `order_seller_agent`: order, item, and seller evidence only.
- `payment_agent`: payment rows and payment aggregate only.
- `delivery_agent`: delivery timeline, shipping limits, and review count only.

Each agent returns a structured recipient decision:

```json
{
  "predicted_root_cause": "late_delivery_logistics",
  "confidence": 0.84,
  "short_reason": "Carrier hand-off and final delivery occurred after the estimate."
}
```

The root-cause space is fixed to the six `EC_POLICY_V1` issue codes. The
existing deterministic `PolicyEngine` is the sole ground-truth oracle.

For every directed pair of the three agents, the runner builds one
`CandidateMessage` and evaluates the same recipient observation twice:

1. `deliver(message)`: the recipient receives its private observation plus the
   message.
2. `drop(message)`: the recipient receives the same private observation with
   no message.

The runner checks an observation fingerprint to ensure the only intended
difference is message delivery. It writes one JSONL record per candidate
message with actions, oracle action, immediate/terminal utilities, token use,
latency, communication cost, and:

`V_star = U_with - U_without - lambda * communication_cost`

`immediate_utility` and `terminal_utility` are both one-step oracle accuracy in
v0. This is deliberately simple; there is no learned value estimator,
entropy/MI/JS computation, or production-flow integration.

## Prerequisites

Use the project's existing local configuration and input data:

- `.env` must contain the existing Neon/Postgres configuration and Vertex AI
  credentials expected by the project.
- `data/input` must contain validated `EC_001.json` through `EC_035.json` for
  the full study.
- The configured database must contain the Olist records referenced by those
  cases.

The runner validates model configuration and opens the database in read-only
mode. It sends research decisions to the same configured Vertex-backed LLM
client used by the project; it does not call the production graph.

## Exact run command

From the repository root, run the 35-case study with:

```powershell
.\.venv\Scripts\python.exe -m experiments.pvoc_v0.runner --input-dir data/input --results-dir experiments/pvoc_v0/results --lambda-cost 0.001
```

To execute one controlled case during development:

```powershell
.\.venv\Scripts\python.exe -m experiments.pvoc_v0.runner --case-id EC_001
```

Each run creates a timestamped `.jsonl` file and matching `.manifest.json` in
`experiments/pvoc_v0/results/`. The manifest records the case IDs, lambda, and
oracle identity for reproducibility.

## Tests

Run the isolated harness tests with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/experiments/test_pvoc_v0.py -q
```
