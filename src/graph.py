"""LangGraph supervisor with parallel specialists and one targeted repair cycle."""

from pathlib import Path
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.agents.coordinator import CoordinatorAgent
from src.agents.delivery_agent import DeliveryAgent
from src.agents.order_seller_agent import OrderSellerAgent
from src.agents.payment_agent import PaymentAgent
from src.agents.policy_agent import PolicyAgent
from src.agents.verifier_agent import VerifierAgent
from src.database.repository import OlistRepository
from src.observability.trace_logger import TraceLogger
from src.output.builder import build_final_output
from src.output.writer import write_output
from src.schemas.agent_messages import AgentTask
from src.schemas.agent_reports import EvidenceBoard
from src.state import CaseState


class Workflow:
    def __init__(
        self,
        *,
        llm,
        repository: OlistRepository,
        trace: TraceLogger,
        output_dir: Path,
    ) -> None:
        self.trace = trace
        self.output_dir = output_dir
        self.coordinator = CoordinatorAgent(llm, trace)
        self.order_agent = OrderSellerAgent(llm, repository, trace)
        self.payment_agent = PaymentAgent(llm, repository, trace)
        self.delivery_agent = DeliveryAgent(llm, repository, trace)
        self.policy_agent = PolicyAgent(llm, trace)
        self.verifier_agent = VerifierAgent(llm, repository, trace)
        self.graph = self._build()

    @staticmethod
    def _task(state: CaseState, recipient: str) -> AgentTask:
        plan = state["coordinator_plan"]
        if plan is None:
            raise RuntimeError("Coordinator plan is missing")
        return next(task for task in plan.tasks if task.to_agent == recipient)

    async def validate_case(self, state: CaseState) -> dict:
        case = state["case_input"]
        await self.trace.emit("case_validation_completed", case_id=case.case_id)
        return {}

    async def coordinator_triage(self, state: CaseState) -> dict:
        return {
            "coordinator_plan": await self.coordinator.run(state["run_id"], state["case_input"])
        }

    async def order_seller(self, state: CaseState) -> dict:
        report = await self.order_agent.run(self._task(state, "order_seller_agent"))
        if state["repair_count"]:
            await self.trace.emit(
                "repair_completed",
                case_id=state["case_input"].case_id,
                agent="order_seller_agent",
            )
        return {"order_seller_report": report}

    async def payment(self, state: CaseState) -> dict:
        report = await self.payment_agent.run(self._task(state, "payment_agent"))
        if state["repair_count"]:
            await self.trace.emit(
                "repair_completed",
                case_id=state["case_input"].case_id,
                agent="payment_agent",
            )
        return {"payment_report": report}

    async def delivery(self, state: CaseState) -> dict:
        report = await self.delivery_agent.run(self._task(state, "delivery_agent"))
        if state["repair_count"]:
            await self.trace.emit(
                "repair_completed",
                case_id=state["case_input"].case_id,
                agent="delivery_agent",
            )
        return {"delivery_report": report}

    async def merge_evidence_board(self, state: CaseState) -> dict:
        order_report = state["order_seller_report"]
        payment_report = state["payment_report"]
        delivery_report = state["delivery_report"]
        if not (order_report and payment_report and delivery_report):
            raise RuntimeError("Specialist reports are incomplete")
        evidence = list(
            dict.fromkeys(
                order_report.evidence_candidates
                + payment_report.evidence_candidates
                + delivery_report.evidence_candidates
            )
        )
        financials = payment_report.financials
        board = EvidenceBoard(
            case_id=state["case_input"].case_id,
            customer_claim=state["case_input"].customer_request.message,
            order_report=order_report,
            payment_report=payment_report,
            delivery_report=delivery_report,
            verified_facts={
                "order_status": order_report.order.order_status,
                "item_total_brl": financials.item_total_brl,
                "freight_total_brl": financials.freight_total_brl,
                "payment_total_brl": financials.payment_total_brl,
                "payment_row_count": financials.payment_row_count,
                "payment_matches_order_total": financials.payment_matches_order_total,
                "delivered_after_estimate": delivery_report.delivered_after_estimate,
                "carrier_handoff_after_limit": delivery_report.carrier_handoff_after_limit,
                "late_item_ids": delivery_report.late_item_ids,
                "late_seller_ids": delivery_report.late_seller_ids,
            },
            evidence_ids=evidence,
            missing_data=list(
                dict.fromkeys(order_report.missing_data + delivery_report.missing_data)
            ),
        )
        await self.trace.emit(
            "evidence_board_updated",
            case_id=board.case_id,
            evidence_ids=evidence[:10],
        )
        await self.trace.emit(
            "handoff_created",
            case_id=board.case_id,
            agent="coordinator_agent",
            to_agent="policy_agent",
        )
        return {"evidence_board": board}

    async def policy(self, state: CaseState) -> dict:
        board = state["evidence_board"]
        if board is None:
            raise RuntimeError("Evidence board is missing")
        decision, conflicts = await self.policy_agent.run(board, state["repair_count"])
        if conflicts:
            board = board.model_copy(update={"conflicts": board.conflicts + conflicts})
        if state["repair_count"]:
            await self.trace.emit("repair_completed", case_id=board.case_id, agent="policy_agent")
        await self.trace.emit(
            "handoff_created",
            case_id=board.case_id,
            agent="policy_agent",
            to_agent="verifier_agent",
        )
        return {"policy_decision": decision, "evidence_board": board}

    async def verifier(self, state: CaseState) -> dict:
        board = state["evidence_board"]
        decision = state["policy_decision"]
        if board is None or decision is None:
            raise RuntimeError("Policy verification inputs are missing")
        result = await self.verifier_agent.run(board, decision, state["repair_count"])
        return {"verification_result": result}

    def route_verification(
        self, state: CaseState
    ) -> Literal["build_output", "repair_router", "case_failed"]:
        result = state["verification_result"]
        if result and result.passed:
            return "build_output"
        if result and result.repair_target and state["repair_count"] < 1:
            return "repair_router"
        return "case_failed"

    async def repair_router(
        self, state: CaseState
    ) -> Command[
        Literal[
            "order_seller_agent", "payment_agent", "delivery_agent", "policy_agent", "case_failed"
        ]
    ]:
        result = state["verification_result"]
        target = result.repair_target if result else None
        allowed = {"order_seller_agent", "payment_agent", "delivery_agent", "policy_agent"}
        if target not in allowed:
            return Command(goto="case_failed")
        await self.trace.emit(
            "repair_requested",
            case_id=state["case_input"].case_id,
            agent="coordinator_agent",
            repair_target=target,
        )
        return Command(update={"repair_count": state["repair_count"] + 1}, goto=target)

    async def build_output(self, state: CaseState) -> dict:
        board = state["evidence_board"]
        decision = state["policy_decision"]
        if board is None or decision is None:
            raise RuntimeError("Cannot build output from incomplete state")
        return {"final_output": build_final_output(state["case_input"].case_id, board, decision)}

    async def write_output(self, state: CaseState) -> dict:
        output = state["final_output"]
        if output is None:
            raise RuntimeError("Verified final output is missing")
        path = write_output(output, self.output_dir)
        await self.trace.emit("output_written", case_id=output.case_id, message=str(path))
        return {"output_path": str(path)}

    async def case_failed(self, state: CaseState) -> dict:
        result = state["verification_result"]
        errors = [error.message for error in result.errors] if result else ["Unknown case error"]
        await self.trace.emit(
            "case_failed",
            case_id=state["case_input"].case_id,
            status="failed",
            message="; ".join(errors),
        )
        return {"errors": errors}

    def _build(self):
        builder = StateGraph(CaseState)
        builder.add_node("validate_case", self.validate_case)
        builder.add_node("coordinator_triage", self.coordinator_triage)
        builder.add_node("order_seller_agent", self.order_seller)
        builder.add_node("payment_agent", self.payment)
        builder.add_node("delivery_agent", self.delivery)
        builder.add_node("merge_evidence_board", self.merge_evidence_board)
        builder.add_node("policy_agent", self.policy)
        builder.add_node("verifier_agent", self.verifier)
        builder.add_node("repair_router", self.repair_router)
        builder.add_node("build_output", self.build_output)
        builder.add_node("write_output", self.write_output)
        builder.add_node("case_failed", self.case_failed)
        builder.add_edge(START, "validate_case")
        builder.add_edge("validate_case", "coordinator_triage")
        builder.add_edge("coordinator_triage", "order_seller_agent")
        builder.add_edge("coordinator_triage", "payment_agent")
        builder.add_edge("coordinator_triage", "delivery_agent")
        builder.add_edge(
            ["order_seller_agent", "payment_agent", "delivery_agent"],
            "merge_evidence_board",
        )
        builder.add_edge("merge_evidence_board", "policy_agent")
        builder.add_edge("policy_agent", "verifier_agent")
        builder.add_conditional_edges("verifier_agent", self.route_verification)
        builder.add_edge("build_output", "write_output")
        builder.add_edge("write_output", END)
        builder.add_edge("case_failed", END)
        return builder.compile()
