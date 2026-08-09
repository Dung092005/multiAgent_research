"""FastAPI surface for the internal Olist Dispute Desk."""

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config.model_config import validate_model_configuration
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository
from src.graph import Workflow
from src.llm.openrouter_client import OpenRouterClient
from src.observability.trace_logger import TraceLogger
from src.schemas.case_input import CaseInput, CustomerRequest
from src.state import initial_state

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
RUN_OUTPUT_DIR = ROOT / "output" / "runs"

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS dispute_desk;
CREATE TABLE IF NOT EXISTS dispute_desk.cases (
 case_id VARCHAR(16) PRIMARY KEY, order_id VARCHAR(64) NOT NULL,
 customer_message TEXT NOT NULL, language VARCHAR(10) NOT NULL DEFAULT 'pt-BR',
 opened_at TIMESTAMPTZ NOT NULL, decision VARCHAR(16) NOT NULL DEFAULT 'pending',
 decision_note TEXT, decided_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS dispute_desk.investigation_runs (
 run_id UUID PRIMARY KEY, case_id VARCHAR(16) NOT NULL REFERENCES dispute_desk.cases(case_id) ON DELETE CASCADE,
 status VARCHAR(16) NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), completed_at TIMESTAMPTZ,
 final_output JSONB, evidence_board JSONB, reports JSONB, timeline JSONB NOT NULL DEFAULT '[]'::jsonb,
 error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_desk_runs_case_started ON dispute_desk.investigation_runs (case_id, started_at DESC);
"""


class CreateCase(BaseModel):
    order_id: str = Field(min_length=1, max_length=64)
    customer_message: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="pt-BR", min_length=2, max_length=10)


class Decision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=1000)


def model_json(value):
    return value.model_dump(mode="json") if value is not None else None


def row_json(row) -> dict:
    """Convert SQLAlchemy mapping rows into JSON-safe dicts."""
    payload = dict(row)
    for key, value in list(payload.items()):
        if hasattr(value, "isoformat"):
            payload[key] = value.isoformat()
        elif hasattr(value, "hex") and not isinstance(value, (bytes, bytearray, str)):
            payload[key] = str(value)
    return payload


def read_trace(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return items


class DeskRuntime:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.sessions = create_session_factory(engine)
        self.tasks: set[asyncio.Task] = set()

    async def initialize(self):
        async with self.engine.begin() as conn:
            for statement in SCHEMA_SQL.split(";"):
                if statement.strip():
                    await conn.execute(text(statement))

    def schedule(self, case_id: str, run_id: str):
        task = asyncio.create_task(self.execute(case_id, run_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def complete(self, run_id: str, status: str, timeline: list, error: str | None = None, output=None, evidence=None, reports=None):
        async with self.engine.begin() as conn:
            await conn.execute(text("""
                UPDATE dispute_desk.investigation_runs SET status=:status, completed_at=NOW(),
                final_output=CAST(:output AS jsonb), evidence_board=CAST(:evidence AS jsonb),
                reports=CAST(:reports AS jsonb), timeline=CAST(:timeline AS jsonb), error_message=:error
                WHERE run_id=CAST(:run_id AS uuid)
            """), {"run_id": run_id, "status": status, "error": error,
                   "output": json.dumps(output) if output is not None else None,
                   "evidence": json.dumps(evidence) if evidence is not None else None,
                   "reports": json.dumps(reports) if reports is not None else None,
                   "timeline": json.dumps(timeline)})

    async def execute(self, case_id: str, run_id: str):
        directory = RUN_OUTPUT_DIR / run_id
        trace_path = directory / "trace.jsonl"
        try:
            async with self.sessions() as session:
                row = (await session.execute(text("SELECT order_id, customer_message, language, opened_at FROM dispute_desk.cases WHERE case_id=:id"), {"id": case_id})).mappings().one()
            case = CaseInput(case_id=case_id, opened_at=row["opened_at"], policy_version="EC_POLICY_V1", customer_request=CustomerRequest(language=row["language"], message=row["customer_message"], claimed_order_id=row["order_id"]))
            validate_model_configuration()
            get_settings().require_api_key()
            trace = TraceLogger(trace_path, run_id)
            trace.reset()
            await trace.emit("run_started", case_id=case_id, message="Started from Dispute Desk")
            workflow = Workflow(llm=OpenRouterClient(get_settings()), repository=OlistRepository(self.sessions), trace=trace, output_dir=directory / "result")
            state = await asyncio.wait_for(
                workflow.graph.ainvoke(
                    initial_state(run_id, case),
                    {"configurable": {"thread_id": f"{run_id}:{case_id}"}, "max_concurrency": 3},
                ),
                timeout=180,
            )
            succeeded = bool(state.get("output_path")) and not state.get("errors")
            await trace.emit("run_completed", case_id=case_id, status="completed" if succeeded else "failed")
            await self.complete(run_id, "completed" if succeeded else "failed", read_trace(trace_path), "; ".join(state.get("errors", [])) or None, model_json(state.get("final_output")), model_json(state.get("evidence_board")), {name: model_json(state.get(name)) for name in ("coordinator_plan", "order_seller_report", "payment_report", "delivery_report", "policy_decision", "verification_result")})
        except Exception as exc:
            LOGGER.exception("Desk run failed: %s", run_id)
            await self.complete(run_id, "failed", read_trace(trace_path), f"{type(exc).__name__}: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = DeskRuntime(create_engine(get_settings(), read_only=False))
    await runtime.initialize()
    app.state.runtime = runtime
    yield
    if runtime.tasks:
        await asyncio.gather(*runtime.tasks, return_exceptions=True)
    await runtime.engine.dispose()


app = FastAPI(title="Olist Dispute Desk API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def runtime() -> DeskRuntime:
    return app.state.runtime


@app.get("/api/health")
async def health():
    async with runtime().engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/orders")
async def orders(search: str = Query(default="", max_length=64), limit: int = Query(default=20, ge=1, le=50)):
    async with runtime().engine.connect() as conn:
        rows = (await conn.execute(text("SELECT order_id, order_status, order_purchase_timestamp, order_delivered_customer_date FROM olist.orders WHERE order_id ILIKE :pattern ORDER BY order_purchase_timestamp DESC LIMIT :limit"), {"pattern": f"%{search}%", "limit": limit})).mappings().all()
    return {"items": [row_json(row) for row in rows]}


@app.get("/api/cases")
async def cases(limit: int = Query(default=50, ge=1, le=100)):
    async with runtime().engine.connect() as conn:
        rows = (await conn.execute(text("""SELECT c.*, r.run_id AS latest_run_id, r.status AS run_status, r.started_at AS last_run_at, r.final_output FROM dispute_desk.cases c LEFT JOIN LATERAL (SELECT * FROM dispute_desk.investigation_runs WHERE case_id=c.case_id ORDER BY started_at DESC LIMIT 1) r ON true ORDER BY c.created_at DESC LIMIT :limit"""), {"limit": limit})).mappings().all()
    return {"items": [row_json(row) for row in rows]}


@app.post("/api/cases", status_code=201)
async def create_case(payload: CreateCase):
    async with runtime().engine.begin() as conn:
        exists = await conn.execute(text("SELECT 1 FROM olist.orders WHERE order_id=:order_id"), {"order_id": payload.order_id})
        if exists.scalar_one_or_none() is None:
            raise HTTPException(404, "Order was not found in Olist data")
        number = (await conn.execute(text("SELECT COALESCE(MAX(CAST(SUBSTRING(case_id FROM 4) AS INTEGER)),0)+1 FROM dispute_desk.cases"))).scalar_one()
        if number > 999:
            raise HTTPException(409, "Case ID capacity reached")
        case_id = f"EC_{number:03d}"
        row = (await conn.execute(text("INSERT INTO dispute_desk.cases (case_id,order_id,customer_message,language,opened_at) VALUES (:case_id,:order_id,:message,:language,NOW()) RETURNING *"), {"case_id": case_id, "order_id": payload.order_id, "message": payload.customer_message, "language": payload.language})).mappings().one()
    return row_json(row)


@app.get("/api/cases/{case_id}")
async def case_detail(case_id: str):
    async with runtime().engine.connect() as conn:
        case = (await conn.execute(text("SELECT * FROM dispute_desk.cases WHERE case_id=:case_id"), {"case_id": case_id})).mappings().one_or_none()
        if case is None:
            raise HTTPException(404, "Case not found")
        runs = (await conn.execute(text("SELECT * FROM dispute_desk.investigation_runs WHERE case_id=:case_id ORDER BY started_at DESC"), {"case_id": case_id})).mappings().all()
    return {"case": row_json(case), "runs": [row_json(run) for run in runs]}


@app.post("/api/cases/{case_id}/runs", status_code=202)
async def run_case(case_id: str):
    run_id = str(uuid4())
    async with runtime().engine.begin() as conn:
        exists = await conn.execute(text("SELECT 1 FROM dispute_desk.cases WHERE case_id=:case_id"), {"case_id": case_id})
        if exists.scalar_one_or_none() is None:
            raise HTTPException(404, "Case not found")
        await conn.execute(text("INSERT INTO dispute_desk.investigation_runs (run_id,case_id,status) VALUES (CAST(:run_id AS uuid),:case_id,'running')"), {"run_id": run_id, "case_id": case_id})
    runtime().schedule(case_id, run_id)
    return {"run_id": run_id, "case_id": case_id, "status": "running"}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    async with runtime().engine.connect() as conn:
        row = (await conn.execute(text("SELECT * FROM dispute_desk.investigation_runs WHERE run_id=CAST(:run_id AS uuid)"), {"run_id": run_id})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Run not found")
    return row_json(row)


@app.post("/api/cases/{case_id}/decision")
async def decide(case_id: str, payload: Decision):
    async with runtime().engine.begin() as conn:
        row = (await conn.execute(text("UPDATE dispute_desk.cases SET decision=:decision, decision_note=:note, decided_at=NOW() WHERE case_id=:case_id RETURNING *"), {"case_id": case_id, "decision": payload.decision, "note": payload.note})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Case not found")
    return row_json(row)
