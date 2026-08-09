"""LLM semantic review plus authoritative deterministic verification."""

from pydantic import BaseModel, Field, ValidationError

from src.agents.base import BaseAgent, StructuredLLM
from src.config.model_config import OPENROUTER_MODEL_ID
from src.database.repository import OlistRepository
from src.errors import EvidenceValidationError
from src.observability.trace_logger import TraceLogger
from src.output.builder import build_final_output
from src.policy.engine import PolicyEngine
from src.schemas.agent_reports import (
    EvidenceBoard,
    PolicyDecision,
    VerificationError,
    VerificationResult,
)
from src.tools.evidence_tools import EvidenceTools
from src.verification.evidence_validator import parse_evidence_id
from src.verification.financial_validator import validate_financials
from src.verification.policy_validator import validate_policy


class VerifierReview(BaseModel):
    passed: bool
    summary: str = Field(min_length=1, max_length=1000)
    warnings: list[str] = Field(default_factory=list, max_length=5)


class VerifierAgent(BaseAgent):
    name = "verifier_agent"
    prompt_file = "verifier_agent.md"

    def __init__(self, llm: StructuredLLM, repository: OlistRepository, trace: TraceLogger) -> None:
        super().__init__(llm)
        self._repository = repository
        self._trace = trace
        self._engine = PolicyEngine()

    async def run(
        self, board: EvidenceBoard, decision: PolicyDecision, repair_count: int
    ) -> VerificationResult:
        await self._trace.emit("verification_started", case_id=board.case_id, agent=self.name)
        tools = EvidenceTools(self._repository, self._trace, board.case_id, self.name)
        database_context = await tools.get_policy_context(board.order_report.order.order_id)
        expected = self._engine.evaluate(
            database_context,
            repair_count=repair_count,
            noncritical_missing=board.delivery_report.missing_data == ["review"],
            warnings=bool(board.conflicts),
        )
        errors = validate_policy(decision, expected)
        errors.extend(
            validate_financials(
                board.payment_report.financials, database_context.financials, decision
            )
        )
        candidate = None
        try:
            candidate = build_final_output(board.case_id, board, decision)
        except ValidationError as exc:
            errors.append(
                VerificationError(
                    code="OUTPUT_SCHEMA_INVALID",
                    field="output",
                    message=str(exc),
                    repair_target="policy_agent",
                )
            )
        if candidate:
            references = await tools.validate_entity_references(
                database_context.order.order_id,
                candidate.affected_entities.item_ids,
                candidate.affected_entities.seller_ids,
                candidate.affected_entities.payment_ids,
            )
            if not references.valid:
                errors.append(
                    VerificationError(
                        code="ENTITY_REFERENCE_INVALID",
                        field="affected_entities",
                        message="One or more affected entity IDs are not grounded in PostgreSQL",
                        expected="all IDs exist and belong to this order",
                        actual=references.model_dump(),
                        repair_target="order_seller_agent",
                    )
                )
            for evidence_id in candidate.evidence_ids:
                try:
                    parsed = parse_evidence_id(evidence_id)
                    if parsed.order_id and parsed.order_id != database_context.order.order_id:
                        raise ValueError("evidence references a different order")
                except (EvidenceValidationError, ValueError) as exc:
                    errors.append(
                        VerificationError(
                            code="EVIDENCE_INVALID",
                            field="evidence_ids",
                            message=f"{evidence_id}: {exc}",
                            repair_target="policy_agent",
                        )
                    )
        response = None
        try:
            response = await self._llm.structured(
                system_prompt=self._prompt,
                user_payload={
                    "candidate": candidate.model_dump(mode="json") if candidate else None,
                    "deterministic_errors": [error.model_dump(mode="json") for error in errors],
                },
                response_model=VerifierReview,
            )
            warnings = list(response.value.warnings)
            if not response.value.passed and not errors:
                warnings.append(
                    "LLM verifier raised an unsupported warning; deterministic checks passed"
                )
        except Exception as exc:  # LLM is advisory only; deterministic errors already computed
            warnings = [f"LLM verifier skipped: {type(exc).__name__}"]
        result = VerificationResult(
            passed=not errors,
            errors=errors,
            warnings=warnings,
            repair_target=errors[0].repair_target if errors else None,
        )
        if result.passed:
            await self._trace.emit("verification_passed", case_id=board.case_id, agent=self.name)
        else:
            await self._trace.emit(
                "verification_failed",
                case_id=board.case_id,
                agent=self.name,
                status="failed",
                message=errors[0].message,
                repair_target=result.repair_target,
            )
        await self._trace.emit(
            "agent_completed",
            case_id=board.case_id,
            agent=self.name,
            model_id=OPENROUTER_MODEL_ID,
            prompt_tokens=response.prompt_tokens if response else 0,
            completion_tokens=response.completion_tokens if response else 0,
        )
        return result
