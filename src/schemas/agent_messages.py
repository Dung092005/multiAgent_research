"""Structured handoff messages."""

from typing import Any, Literal

from pydantic import Field

from src.schemas.common import StrictModel

AgentName = Literal[
    "coordinator_agent",
    "order_seller_agent",
    "payment_agent",
    "delivery_agent",
    "policy_agent",
    "verifier_agent",
]


class AgentTask(StrictModel):
    task_id: str
    run_id: str
    case_id: str
    from_agent: AgentName
    to_agent: AgentName
    objective: str
    order_id: str
    allowed_tools: list[str] = Field(max_length=8)
    provided_context: dict[str, Any] = Field(default_factory=dict)


class VerifiedFact(StrictModel):
    fact_type: str
    value: Any
    source_table: str
    source_key: str
    evidence_id: str | None = None
    verified: bool = True


class AgentResult(StrictModel):
    task_id: str
    case_id: str
    agent_name: AgentName
    status: Literal["success", "failed", "needs_more_context"]
    summary: str
    verified_facts: list[VerifiedFact] = Field(default_factory=list)
    evidence_candidates: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
