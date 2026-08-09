# Olist Dispute Desk

**Multi-agent e-commerce dispute investigation** with a deterministic refund policy and an internal ops console.

> Customer claims are not trusted by default. Agents investigate order / payment / delivery evidence from PostgreSQL, a policy engine computes refunds in code, and a verifier checks the result before it reaches the UI.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-Vite-61DAFB?logo=react&logoColor=black)](https://vitejs.dev/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![OpenRouter](https://img.shields.io/badge/OpenRouter-GPT--5--Nano-black)](https://openrouter.ai/openai/gpt-5-nano)

---

## Features

- **Dispute Desk UI** — create cases from live Olist orders, run investigations, review evidence, approve / reject recommendations
- **Multi-agent pipeline** — Coordinator + Order/Seller, Payment, Delivery specialists in parallel
- **Deterministic policy** — refunds and actions come from `EC_POLICY_V1` code, not LLM arithmetic
- **Grounded evidence IDs** — only IDs that resolve to PostgreSQL records
- **Run history** — snapshots stored in `dispute_desk.*` tables (timeline, reports, final JSON)
- **Batch mode** — optional `input/EC_001.json` … `EC_050.json` via the CLI runner

---

## Architecture

### System overview

Layout mirrors the end-to-end investigation pipeline: ingest → multi-agent graph → verified JSON output.

```mermaid
flowchart LR
    subgraph Inputs["Input data sources"]
        CSV["data/*.csv<br/>Olist dataset"]
        CASES["input/EC_001…EC_050.json<br/>investigation cases"]
        UI["Dispute Desk UI<br/>React · Vite"]
    end

    subgraph External["External services"]
        OR["OpenRouter<br/>openai/gpt-5-nano"]
        LF["Langfuse<br/>traces & metrics"]
    end

    subgraph Host["App runtime · Docker Compose + local services"]
        direction TB
        INGEST["CSV ingest<br/>scripts/import_olist_csv.py"]
        INTAKE["Case intake<br/>API / batch runner"]
        PG[("PostgreSQL 16<br/>olist.* · dispute_desk.*")]

        subgraph LG["LangGraph application"]
            direction TB
            COORD["Coordinator<br/>triage & dispatch"]
            DEL["Delivery Agent"]
            PAY["Payment Agent"]
            ORD["Order & Seller Agent"]
            MERGE["Merge evidence<br/>Evidence Board"]
            POL["Policy Agent<br/>+ deterministic EC_POLICY_V1"]
            VER["Verifier Agent"]
            FIX["Limited repair<br/>≤1 retry"]
            OUTN["Write output"]
        end

        ENV[".env / config<br/>DATABASE_URL · API keys"]
    end

    OUTF["output/EC_NNN.json<br/>+ Desk UI snapshot"]

    CSV --> INGEST --> PG
    CASES --> INTAKE
    UI --> INTAKE
    INTAKE --> COORD

    COORD --> DEL
    COORD --> PAY
    COORD --> ORD
    DEL --> PG
    PAY --> PG
    ORD --> PG
    DEL --> MERGE
    PAY --> MERGE
    ORD --> MERGE
    MERGE --> POL --> VER
    VER -->|valid| OUTN
    VER -->|invalid| FIX
    FIX --> VER
    OUTN --> OUTF

    OR -.->|model call| COORD
    OR -.->|model call| DEL
    OR -.->|model call| PAY
    OR -.->|model call| ORD
    OR -.->|model call| POL
    OR -.->|model call| VER
    OR -.->|model call| FIX
    LG -.->|trace| LF
    ENV -.-> Host

    classDef ext fill:#f5f5f5,stroke:#525252,color:#171717;
    classDef data fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a;
    classDef agent fill:#e0f2f1,stroke:#0f766e,color:#0b3d39;
    classDef gate fill:#ede9fe,stroke:#6d28d9,color:#3b0764;
    classDef det fill:#fef3c7,stroke:#b45309,color:#7c2d12;
    class OR,LF,ENV ext;
    class CSV,CASES,PG,OUTF data;
    class DEL,PAY,ORD,MERGE,COORD agent;
    class VER,FIX,INTAKE,INGEST,UI gate;
    class POL,OUTN det;
```

### Multi-agent investigation flow

```mermaid
flowchart LR
    A([Case Intake]) --> B{{Coordinator<br/>Triage}}
    B --> C[Order & Seller]
    B --> D[Payment]
    B --> E[Delivery]
    C --> F[[Evidence Board]]
    D --> F
    E --> F
    F --> G[/Policy Engine<br/>deterministic/]
    G --> H{Verifier}
    H -->|pass| I([Persist + UI Output])
    H -->|fail · ≤1 repair| J[Targeted Repair]
    J --> H

    classDef intake fill:#0f766e,stroke:#0b4f48,color:#fff;
    classDef spec fill:#e0f2f1,stroke:#0f766e,color:#0b3d39;
    classDef det fill:#fef3c7,stroke:#b45309,color:#7c2d12;
    classDef gate fill:#ede9fe,stroke:#6d28d9,color:#3b0764;
    class A,I intake;
    class C,D,E,F spec;
    class G det;
    class B,H,J gate;
```

### Deployment (local Docker + services)

```mermaid
flowchart LR
    subgraph Host["Developer Machine"]
        direction TB
        FE["Vite Dev Server<br/>:5173"]
        BE["FastAPI · Uvicorn<br/>:8000"]
        subgraph DC["Docker Compose"]
            PGC[("PostgreSQL 16<br/>:5432")]
        end
    end

    subgraph Cloud["External APIs"]
        ORC["OpenRouter<br/>openai/gpt-5-nano"]
        LFC["Langfuse<br/>(optional)"]
    end

    FE -->|REST /api| BE
    BE -->|SQLAlchemy async| PGC
    BE -->|structured JSON| ORC
    BE -.->|traces| LFC
    CSVF["data/*.csv"] -->|ingest once| PGC

    classDef svc fill:#e0f2f1,stroke:#0f766e,color:#0b3d39;
    classDef db fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a;
    classDef ext fill:#f5f5f5,stroke:#525252,color:#171717;
    class FE,BE svc;
    class PGC,CSVF db;
    class ORC,LFC ext;
```

### Data model (persistence)

```mermaid
erDiagram
    ORDERS ||--o{ ORDER_ITEMS : contains
    ORDERS ||--o{ ORDER_PAYMENTS : paid_by
    ORDERS ||--o{ ORDER_REVIEWS : reviewed_by
    ORDER_ITEMS }o--|| SELLERS : sold_by
    CASES ||--o{ INVESTIGATION_RUNS : has

    ORDERS {
        string order_id PK
        string order_status
        timestamp delivered_customer_date
        timestamp estimated_delivery_date
    }
    ORDER_ITEMS {
        string order_id FK
        int order_item_id
        string seller_id FK
        timestamp shipping_limit_date
        numeric price
        numeric freight_value
    }
    CASES {
        string case_id PK
        string order_id
        string decision
    }
    INVESTIGATION_RUNS {
        uuid run_id PK
        string case_id FK
        string status
        jsonb final_output
        jsonb timeline
    }
```

`olist.*` = read-only source of record. `dispute_desk.*` = app state written by the API.

### Verification state machine

```mermaid
stateDiagram-v2
    [*] --> Running
    Running --> Verifying: agents + policy done
    Verifying --> Completed: schema · evidence · finance OK
    Verifying --> Repairing: recoverable error (≤1)
    Repairing --> Verifying
    Verifying --> Failed: unrecoverable / retry exhausted
    Completed --> [*]
    Failed --> [*]
```

### What the LLM may vs may not do

| Allowed (LLM) | Forbidden (LLM) |
| --- | --- |
| Understand customer claim / intent | Invent orders, payments, tracking events |
| Write short investigation narratives | Run arbitrary SQL |
| Soft review of policy wording | Compute refunds or money totals |
| Compact structured JSON responses | Override deterministic policy results |

PostgreSQL is the **system of record**. Money, timestamps, and refund decisions are **code-owned**.

---

## Tech stack

| Layer | Choice |
| --- | --- |
| Frontend | React, Vite, TypeScript |
| Backend API | FastAPI, Uvicorn |
| Orchestration | LangGraph |
| LLM gateway | OpenRouter → `openai/gpt-5-nano` |
| Database | PostgreSQL 16 + SQLAlchemy async / psycopg |
| Infra local | Docker Compose |
| Observability | Local `trace.jsonl` + optional Langfuse |
| Config | `.env` for secrets · model slug in source (`src/config/model_config.py`) |

---

## Policy (`EC_POLICY_V1`)

Priority order. Money rounded to 2 decimals. Payment match tolerance **±0.10 BRL**.

| Primary issue | Condition | Responsible party | Refund | Action |
| --- | --- | --- | ---: | --- |
| `canceled_order_paid` | `canceled` + payment > 0 | `platform` | payment total | `issue_full_refund` |
| `unavailable_order_paid` | `unavailable` + payment > 0 | `platform` | payment total | `issue_full_refund` |
| `late_delivery_seller` | delivered late + carrier after `shipping_limit_date` | `seller` | freight | `refund_freight` |
| `late_delivery_logistics` | delivered late + carrier on time | `logistics_provider` | freight | `refund_freight` |
| `valid_split_payment` | ≥2 payments, totals reconcile | — | 0 | `explain_valid_split_payment` |
| `unsupported_late_claim` | not late + payment OK | — | 0 | `reject_late_refund` |

### Evidence ID contract

```text
order:<order_id>
item:<order_id>:<order_item_id>
payment:<order_id>:<payment_sequential>
seller:<seller_id>
policy:<root_cause_code>
```

---

## Project structure

```text
├── frontend/                  # Dispute Desk (React + Vite)
├── src/
│   ├── api/                   # FastAPI surface
│   ├── agents/                # Coordinator + specialists + policy + verifier
│   ├── graph.py               # LangGraph wiring
│   ├── policy/                # Deterministic EC_POLICY_V1
│   ├── finance/               # Money calculator
│   ├── database/              # Models, repository, schema
│   ├── tools/                 # Allowlisted DB tools
│   ├── verification/          # Schema / evidence / financial checks
│   ├── llm/                   # OpenRouter client
│   └── observability/         # Trace + metadata
├── scripts/                   # ingest, run_api, validate, smoke
├── config/                    # policy + model registry
├── input/                     # EC_001 … EC_050 case JSON
├── data/                      # Olist CSV (not committed if large)
├── logging/                   # traces / metadata
└── architecture.md            # Deep design notes
```

---

## Quick start

### 1. Environment

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"

copy .env.example .env
# set OPENROUTER_API_KEY=...
```

### 2. Database

```powershell
docker start olist_disputes_db
# or: docker compose up -d postgres

.\.venv\Scripts\python.exe -m scripts.import_olist_csv --data-dir data
```

Ingest is a **CSV → SQL COPY** (not RAG chunking). Run once unless you refresh data.

### 3. Backend

```powershell
.\.venv\Scripts\python.exe scripts\run_api.py
```

- API: http://127.0.0.1:8000  
- Docs: http://127.0.0.1:8000/docs  

> On Windows, use `scripts/run_api.py` so psycopg gets a SelectorEventLoop.

### 4. Frontend

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

- UI: http://127.0.0.1:5173  

### 5. Use the Desk

1. Open the UI  
2. **New case** → search/select an Olist `order_id` → write the customer claim  
3. **Create & run investigation**  
4. Wait for `completed`  
5. Review Assessment / Evidence / Agent reports / Timeline  
6. **Approve** or **Reject**

---

## API surface (MVP)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness + DB ping |
| `GET` | `/api/orders?search=` | Search Olist orders |
| `GET` | `/api/cases` | List desk cases |
| `POST` | `/api/cases` | Create case |
| `GET` | `/api/cases/{id}` | Case + runs |
| `POST` | `/api/cases/{id}/runs` | Start investigation |
| `GET` | `/api/runs/{run_id}` | Run snapshot |
| `POST` | `/api/cases/{id}/decision` | Approve / reject |

---

## Design principles

1. **Supervisor, not swarm** — handoffs go through the Coordinator / graph edges  
2. **Parallel specialists** — order, payment, delivery only read facts  
3. **Shared evidence board** — one place to merge grounded IDs and verified facts  
4. **Code owns money** — LLM never calculates refunds  
5. **Fail closed on verification** — invalid schema / evidence / finance does not ship as success  
6. **Secrets stay in `.env`** — model id stays in git for auditability  

More detail: [`architecture.md`](architecture.md)

---

## Data source

[Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)

---

## Roadmap

- [ ] Sample-case loader from `input/EC_*.json` in the UI  
- [ ] Live pipeline SSE / WebSocket progress  
- [ ] Lightweight auth for the desk  
- [ ] Deploy compose stack (API + UI + Postgres)

---

## License

Personal / educational project. Olist dataset retains its original license terms.
