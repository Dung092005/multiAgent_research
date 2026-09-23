# Value-Aware Selective Communication in LLM Multi-Agent Systems

Experimental study of prospective message value using controlled counterfactual
communication between specialized LLM agents.

## Current research status — September 2026

| Research component | Status |
| --- | --- |
| Three-agent research environment | **COMPLETE** |
| Private-information separation | **COMPLETE** |
| Counterfactual WITH vs WITHOUT apparatus | **COMPLETE** |
| Reliability / stability validation | **COMPLETE** |
| Six-class stratified pilot | **COMPLETE** |
| 50-case Counterfactual Message-Value Dataset v1 | **COMPLETE** |
| Prospective value estimator V_hat | **NOT YET IMPLEMENTED** |

Dataset v1 currently contains:

~~~text
50 cases · 300 candidate messages · 3,000 recipient trials · 300 aggregated samples
~~~

This repository is now primarily a research implementation and experimental
testbed. The original Olist dispute application is the environment from which
the research setup was constructed.

## 1. Research context

In a multi-agent system, agents can communicate or merge information without
knowing whether a particular message will improve another agent's future
decision. This project studies the question:

> Can an agent estimate whether a candidate message is worth sending before it
> is transmitted?

The current work measures message value empirically through controlled
counterfactual trials. The prospective estimator that would predict this value
before transmission is the next research stage; it does not exist yet.

## 2. What I started with

Before the research work, I had a working e-commerce dispute investigation
system built around the public Brazilian Olist dataset. A customer dispute was
investigated by three specialized agents, whose findings were combined and
checked by deterministic code.

The original high-level system was:

~~~text
Customer dispute
        |
        v
   Coordinator
        |
        +------------------+------------------+
        |                  |                  |
        v                  v                  v
 Order/Seller Agent    Payment Agent      Delivery Agent
        |                  |                  |
        +------------------+------------------+
                           |
                           v
                     Evidence Board
                           |
                           v
                      PolicyEngine
                           |
                           v
                        Verifier
                           |
                           v
                      Final result
~~~

The three specialists had distinct evidence responsibilities:

| Specialist | Evidence used |
| --- | --- |
| Order/Seller Agent | Order status, order items, and seller evidence |
| Payment Agent | Payment records, totals, and payment structure |
| Delivery Agent | Delivery timeline and shipping deadlines |

The deterministic PolicyEngine already defined six root-cause classes:

1. canceled_order_paid
2. unavailable_order_paid
3. late_delivery_seller
4. late_delivery_logistics
5. valid_split_payment
6. unsupported_late_claim

Because these decisions were defined in deterministic code, PolicyEngine could
later serve as the experimental oracle / ground truth. The original application
is therefore useful as a grounded environment, while the research question is
evaluated in a separate harness.

## 3. From application to research testbed

The original system was designed to complete an investigation. It did not
measure the value of an individual inter-agent message. To turn it into a
controlled communication experiment, I added the isolated research harness in
[experiments/pvoc_v0/](experiments/pvoc_v0/).

~~~text
Existing Olist application
        =
grounded research environment

PVoC v0 harness
        =
controlled experimental apparatus for measuring message value
~~~

The production application is not itself the research contribution. It provides
realistic cases, specialist evidence, existing agent roles, and the deterministic
oracle. The PVoC harness defines the private observations, frozen candidate
messages, counterfactual branches, and measurements needed to study communication
value.

## 4. Research testbed

For each grounded case, the harness constructs three private observations. Each
agent makes decisions from its own observation, and a sender can construct a
candidate message for one recipient.

~~~text
Olist case
   |
   +-------------------+-------------------+
   |                   |                   |
   v                   v                   v
Order/Seller        Payment            Delivery
private view        private view       private view
   |                   |                   |
   v                   v                   v
Agent A             Agent B            Agent C
   \                   |                   /
    +-------- candidate message --------+
                         |
                         v
                 WITH vs WITHOUT trial
~~~

For every directed sender-recipient pair, the experiment is:

~~~mermaid
flowchart TD
    S[Sender decision] --> M[Candidate message m]
    M --> W[DELIVER m]
    M --> X[DROP m]
    W --> SW[Same recipient private observation<br/>+ message]
    X --> SX[Same recipient private observation<br/>+ no message]
    SW --> DW[Decision WITH]
    SX --> DX[Decision WITHOUT]
    DW --> C[Compare utility<br/>after both decisions exist]
    DX --> C
    C --> V[Observed message value]
~~~

The experimental controls are:

- Each agent receives only its own private observation.
- The recipient does not see global state, the oracle, or the sender's private
  observation.
- WITH adds only the frozen CandidateMessage.
- WITHOUT uses the same recipient private observation without that message.
- Deep copies and observation fingerprints verify that the recipient state is
  otherwise equal.
- PolicyEngine is consulted only after the recipient decision, to calculate
  utility.

## 5. The three agents and communication edges

| Agent | Private evidence | Role in the experiment |
| --- | --- | --- |
| Order/Seller | Order status, items, seller records | Sends or receives order and seller evidence |
| Payment | Payment rows, totals, payment structure | Sends or receives payment evidence |
| Delivery | Delivery timeline and shipping deadlines | Sends or receives delivery evidence |

Each of the three agents can send to the other two, producing six directed
communication edges:

~~~text
Order/Seller -> Payment
Order/Seller -> Delivery
Payment     -> Order/Seller
Payment     -> Delivery
Delivery    -> Order/Seller
Delivery    -> Payment
~~~

Each candidate message contains a case identifier, sender, recipient, compact
content, and evidence identifiers. Sender messages are generated once and
frozen before repeated recipient trials.

## 6. How message value is currently measured

The original single paired run uses:

~~~text
V_star = U_with - U_without - lambda * communication_cost
~~~

Repeated trials use a separately named exploratory quantity:

~~~text
delta_mean_utility = mean(U_with) - mean(U_without)
repeated_mean_value = delta_mean_utility - lambda * communication_cost
~~~

Current utility is deliberately simple:

~~~text
correct root cause   = 1
incorrect root cause = 0
~~~

These are observed counterfactual values: both WITH and WITHOUT outcomes are
actually executed. They are not a prospective estimator. In particular, no
V_hat currently predicts value before a message is transmitted.

## 7. What has been completed

### Milestone 1 — Research harness and smoke test

**Status: COMPLETE**

The first real experiment established:

- three private agents;
- six directed candidate messages;
- a controlled WITH vs WITHOUT counterfactual branch;
- same-recipient-state fingerprint validation;
- the deterministic PolicyEngine oracle; and
- real Vertex/Gemini structured-output execution.

The EC_001 run produced meaningful message-value differences between
communication edges.

### Milestone 2 — Stability study

**Status: COMPLETE**

The stability study checked whether an apparent communication effect could be
explained only by repeated LLM sampling variability. For EC_001, every one of
the six edges was evaluated with:

~~~text
10 WITH trials
10 WITHOUT trials
~~~

This produced **120 recipient executions**. Fixed message hashes and
observation fingerprints passed validation. The lowest modal action share was
**0.80**, which was sufficient to proceed to a broader pilot while retaining
the observed variability in the record.

### Milestone 3 — Stratified six-case pilot

**Status: COMPLETE**

The pilot used one representative case for each of the six root-cause classes:

~~~text
6 cases × 6 communication edges × (5 WITH + 5 WITHOUT)
= 360 recipient executions
~~~

The pilot observed:

| Observed utility effect | Count |
| --- | ---: |
| Positive | 10 |
| Zero | 24 |
| Negative | 2 |

Communication was therefore not universally useful in this pilot. Some messages
helped, many did not change utility, and some reduced recipient correctness.
This is pilot evidence, not a generalization claim.

### Milestone 4 — Counterfactual Message-Value Dataset v1

**Status: COMPLETE**

Dataset v1 uses the full grounded case set, EC_001 through EC_050:

| Root-cause class | Cases |
| --- | ---: |
| canceled_order_paid | 9 |
| unavailable_order_paid | 9 |
| late_delivery_seller | 8 |
| late_delivery_logistics | 8 |
| valid_split_payment | 8 |
| unsupported_late_claim | 8 |

Each case contributes six frozen directed candidate messages. Each message is
evaluated with five WITH trials and five WITHOUT trials:

~~~text
50 cases
300 candidate messages
3,000 recipient counterfactual trials
300 aggregated message-value samples
~~~

The final empirical utility-effect labels are:

| Label | Count |
| --- | ---: |
| POSITIVE | 74 |
| ZERO | 209 |
| NEGATIVE | 17 |

These labels describe the observed utility effect
delta_mean_utility. They are not predictions from V_hat. Across the dataset, the
mean modal share was approximately **0.97** for both WITH and WITHOUT conditions.

## 8. One small example

Consider the EC_001 edge Order/Seller -> Payment.

- **WITHOUT message:** the Payment agent predicted valid_split_payment, which
  was incorrect for the canceled_order_paid oracle case.
- **WITH message:** the Order/Seller message included cancellation information,
  and the Payment agent predicted canceled_order_paid, which was correct.

For this candidate message, the observed utility improved from 0 to 1. It is a
positive observed downstream effect.

By contrast, on EC_001 the Payment -> Order/Seller recipient was already correct
without the message and remained correct with it. That message had no utility
improvement, although it still incurred communication cost. This is a redundant
observed message under the current utility definition.

## 9. What one Dataset v1 sample contains

Conceptually, one row represents one case, sender, recipient, and frozen
candidate message:

~~~text
(case, sender, recipient, candidate message)
        |
        +-- repeated WITHOUT outcomes
        |
        +-- repeated WITH outcomes
        |
        +-- delta_mean_utility
        |
        +-- communication cost
        +-- repeated_mean_value
~~~

The main aggregated artifact is:

~~~text
experiments/pvoc_v0/results/dataset_v1/
pvoc_v1_20260922T205457Z_483f9ca0/dataset.jsonl
~~~

The same run directory also contains frozen messages, raw recipient trials,
case summaries, a manifest, and a summary file for reproducibility.

## 10. Progress against the proposal

~~~mermaid
flowchart TD
    A[Research environment<br/>three grounded agents] --> B[Private information separation]
    B --> C[Counterfactual SEND vs DROP apparatus]
    C --> D[Stability / reliability validation]
    D --> E[Six-case pilot]
    E --> F[Counterfactual message-value dataset]
    F --> G[Prospective V_hat estimator<br/>NEXT]
    G --> H[Selective communication policy]
    H --> I[Baselines, budgets, ablations,<br/>generalization and final evaluation]
    classDef done fill:#dcfce7,stroke:#15803d,color:#14532d;
    classDef next fill:#fef3c7,stroke:#b45309,color:#78350f;
    classDef later fill:#f3f4f6,stroke:#6b7280,color:#374151;
    class A,B,C,D,E,F done;
    class G next;
    class H,I later;
~~~

The project has completed the measurement stage: it can estimate the true
observed effect of a candidate message by running both counterfactual
conditions. It has not completed the prospective prediction stage.

## 11. Current position in the research question

What exists now:

~~~text
candidate message
        |
        +--> actually run WITH
        |
        +--> actually run WITHOUT
        |
        v
observed counterfactual value
~~~

What the research ultimately needs:

~~~text
candidate message
        |
        v
prospective estimator V_hat
        |
        +---- high predicted value ---> SEND
        |
        +---- low predicted value ----> DROP
~~~

V_hat has **not** been implemented. There is no production selective router or
SEND/DROP policy yet.

## 12. Next steps

The next stage is to define a leakage-safe prediction problem from Dataset v1.
The intended order is:

1. **Define legal pre-send features.** Features must be available to the sender
   before communication. They must not contain downstream outcomes, oracle labels
   unavailable at send time, WITH-condition results, or other future information.
2. **Establish simple prospective-value baselines.** Possible baselines include
   a heuristic, a simple supervised predictor, or an LLM-based value estimator.
   The final method has not yet been selected.
3. **Train and evaluate V_hat.** The target is to predict observed message value
   before message transmission.
4. **Turn prediction into selective communication.** A future policy could send
   when V_hat(m) > threshold and otherwise drop the message.
5. **Compare against communication baselines:** full communication, no
   communication, random or budgeted communication, and existing merge-late
   behavior where appropriate.
6. **Evaluate trade-offs:** task correctness, message count, tokens, latency,
   communication cost, and performance retained under communication budgets.
7. **Later, test ablations and generalization** across recipients, root-cause
   types, team configurations, and—if resources permit—other models or tasks.

None of these prospective-estimation or selective-routing steps has been
claimed as completed.

## 13. Research-focused repository map

~~~text
multiAgent_research/
|
|-- experiments/pvoc_v0/
|   |-- core/          # shared research primitives
|   |-- studies/       # smoke, stability, pilot, Dataset v1
|   |-- results/       # frozen experiment artifacts
|   |-- cli.py         # unified study entry point
|
|-- data/input/        # grounded EC case inputs
|-- src/               # original Olist application/environment
|-- tests/experiments/ # research tests
~~~

A supervisor interested in the research should start at
[experiments/pvoc_v0/](experiments/pvoc_v0/). The internal README there
contains technical run commands and artifact details; this root README explains
the research progression and current position.

## Research boundaries

- Dataset v1 is an observed-value dataset, not a learned value estimator.
- Current utility is binary correctness against the deterministic oracle.
- The experiments use one grounded Olist environment and do not establish
  generalization beyond it.
- No learned V_hat estimator exists yet.
- No production selective communication router exists yet.
- The current work does not claim causal proof or a completed selective
  communication policy.
