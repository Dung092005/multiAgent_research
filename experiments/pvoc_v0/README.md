# PVoC research harness

This folder is an isolated research harness for counterfactual communication in
the Olist dispute environment. It reuses read-only data tools and the
deterministic `EC_POLICY_V1` oracle, but it does not invoke or modify the
production graph or production agents.

## Experiment in one picture

```text
grounded Olist case
        |
        +-- order/seller private observation --> order_seller_agent
        +-- payment private observation ------> payment_agent
        +-- delivery private observation -----> delivery_agent
                                                     |
                           3 agents x 2 recipients = 6 directed messages
                                                     |
                         same recipient observation, WITH vs WITHOUT message
                                                     |
                       oracle accuracy, cost, and counterfactual value records
```

The recipient never receives the oracle, global state, or the sender's private
observation. The only intended branch difference is whether the frozen
`CandidateMessage` is present. Observation fingerprints enforce this invariant.

## Folder map

```text
pvoc_v0/
|-- README.md                 # start here
|-- cli.py                    # one CLI for every milestone
|-- core/
|   |-- schemas.py            # research-only Pydantic contracts
|   |-- protocol.py           # observations, agents, messages, metrics, hashes
|   |-- llm.py                # research-only Vertex structured-output adapter
|   `-- io.py                 # JSON/JSONL and checksum helpers
|-- studies/
|   |-- smoke.py              # original EC_001 paired run
|   |-- stability.py          # repeated EC_001 recipient decisions
|   |-- pilot.py              # six-case stratified pilot
|   |-- dataset_v1.py         # dataset orchestration
|   |-- dataset_v1_records.py # validation and aggregation
|   `-- dataset_v1_store.py   # checkpoint/resume and finalization
|-- *_runner.py               # tiny compatibility entry points only
`-- results/                  # frozen historical artifacts
```

## Completed milestones

1. **Smoke test** — one EC_001 run, six directed pairs, one paired
   WITH/WITHOUT evaluation per message.
2. **Stability test** — EC_001, 10 trials per condition, 120 recipient calls,
   fixed messages and fingerprints.
3. **Six-case pilot** — one case per root-cause class, 36 messages and 360
   recipient trials.
4. **Dataset v1** — **COMPLETE**: EC_001 through EC_050, 50 cases, 300 frozen
   messages, 3,000 raw trials, and 300 aggregated samples.

The final frozen dataset is:

```text
experiments/pvoc_v0/results/dataset_v1/
pvoc_v1_20260922T205457Z_483f9ca0/
```

Its validated utility-effect distribution is 74 POSITIVE, 209 ZERO, and 17
NEGATIVE samples. These are empirical dataset labels, not a generalization
claim.

## Unified commands

Run from the repository root with the existing virtual environment:

```powershell
..venv\Scripts\python.exe -m experiments.pvoc_v0.cli smoke --case-id EC_001
..venv\Scripts\python.exe -m experiments.pvoc_v0.cli stability
..venv\Scripts\python.exe -m experiments.pvoc_v0.cli pilot
..venv\Scripts\python.exe -m experiments.pvoc_v0.cli dataset-v1
..venv\Scripts\python.exe -m experiments.pvoc_v0.cli dataset-v1 --resume <run_dir>
```

Use `--help` after a study name for its options. Historical `*_runner.py`
module paths remain as small compatibility wrappers; new work should use
`cli.py`.

Research-only tests and lint:

```powershell
..venv\Scripts\python.exe -m pytest tests/experiments -q
..venv\Scripts\python.exe -m ruff check experiments/pvoc_v0 tests/experiments
```

## Metric names

The original paired study keeps:

```text
V_star = U_with - U_without - lambda * communication_cost
```

Repeated studies and Dataset v1 keep:

```text
delta_mean_utility = mean_U_with - mean_U_without
repeated_mean_value = delta_mean_utility - lambda * communication_cost
```

The frozen Dataset v1 field `effect_label` is specifically the sign of
`delta_mean_utility`; it is therefore a **utility-effect label**. It is not the
sign of `repeated_mean_value`. New code exposes the clearer
`utility_effect_label` property and the pure helper
`value_sign_label(repeated_mean_value)` without rewriting frozen artifacts.

## Provenance note: resume_count

The implementation log records that the final run was explicitly resumed after
a bounded Vertex `LengthFinishReasonError` at EC_026, and the resume path cleaned
the incomplete case before completion. The frozen final manifest nevertheless
contains `resume_count = 0`. Audit found that the old runner initialized this
field but never incremented it. Future runs now increment the field after a
resume manifest passes compatibility validation. The frozen manifest is left
unchanged, so this note documents the confirmed bookkeeping inconsistency.

## Scope boundary

No `V_hat` estimator exists yet. There is no learned router, SEND/DROP policy,
entropy, mutual information, or Jensen-Shannon metric in this harness.
