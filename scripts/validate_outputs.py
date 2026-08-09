"""Revalidate every production output against PostgreSQL and deterministic policy."""

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path

from scripts.validate_inputs import load_and_validate_inputs
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository
from src.errors import OutputValidationError
from src.finance.calculator import money
from src.policy.engine import PolicyEngine
from src.schemas.final_output import FinalCaseOutput
from src.verification.evidence_validator import parse_evidence_id, prioritize_evidence


async def validate_outputs(output_dir: Path, input_dir: Path) -> list[str]:
    expected_names = {f"EC_{index:03d}.json" for index in range(1, 51)}
    actual_names = {path.name for path in output_dir.glob("*.json")}
    errors: list[str] = []
    cases = {case.case_id: case for case in load_and_validate_inputs(input_dir)}
    if actual_names != expected_names:
        errors.append(
            f"Output set mismatch: missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)}"
        )
    unexpected = [
        path.name
        for path in output_dir.iterdir()
        if path.is_file() and path.name != ".gitkeep" and path.suffix.lower() != ".json"
    ]
    if unexpected:
        errors.append(f"Unexpected output files: {sorted(unexpected)}")

    engine = create_engine(get_settings(), read_only=True)
    repository = OlistRepository(create_session_factory(engine))
    policy = PolicyEngine()
    for name in sorted(actual_names & expected_names):
        path = output_dir / name
        try:
            output = FinalCaseOutput.model_validate_json(path.read_text(encoding="utf-8"))
            if output.case_id != path.stem:
                raise OutputValidationError("case_id does not match filename")
            order_id = output.affected_entities.order_ids[0]
            if order_id != cases[output.case_id].customer_request.claimed_order_id:
                raise OutputValidationError("order_id does not match the input claimed_order_id")
            context = await repository.get_policy_context(order_id)
            decision = policy.evaluate(context)
            expected_items = [
                f"{order_id}:{item.order_item_id}"
                for item in sorted(context.items, key=lambda row: row.order_item_id)[:5]
            ]
            expected_payments = [
                f"{order_id}:{payment.payment_sequential}"
                for payment in sorted(context.payments, key=lambda row: row.payment_sequential)[:5]
            ]
            expected_sellers = sorted({item.seller_id for item in context.items})[:5]
            expected_evidence = prioritize_evidence(
                order_id,
                decision.ranked_causes[0].cause_code,
                expected_items,
                expected_payments,
                [
                    party.party_id
                    for party in decision.responsible_parties
                    if party.party_type == "seller"
                ],
            )
            checks = {
                "primary_issue": output.assessment.primary_issue == decision.primary_issue,
                "case_status": output.assessment.case_status == decision.case_status,
                "ranked_causes": output.root_cause_analysis.ranked_causes == decision.ranked_causes,
                "responsible_parties": output.root_cause_analysis.responsible_parties
                == decision.responsible_parties,
                "actions": output.resolution_actions == decision.resolution_actions,
                "order_ids": output.affected_entities.order_ids == [order_id],
                "item_ids": output.affected_entities.item_ids == expected_items,
                "seller_ids": output.affected_entities.seller_ids == expected_sellers,
                "payment_ids": output.affected_entities.payment_ids == expected_payments,
                "evidence_ids": output.evidence_ids == expected_evidence,
                "item_total": money(output.financial_resolution.item_total_brl)
                == context.financials.item_total_brl,
                "freight_total": money(output.financial_resolution.freight_total_brl)
                == context.financials.freight_total_brl,
                "payment_total": money(output.financial_resolution.payment_total_brl)
                == context.financials.payment_total_brl,
                "refund": money(output.financial_resolution.recommended_refund_brl)
                == decision.recommended_refund_brl,
                "confidence": Decimal("0.90")
                <= Decimal(str(output.assessment.confidence))
                <= Decimal("0.98"),
            }
            for evidence_id in output.evidence_ids:
                parse_evidence_id(evidence_id)
            failed = [field for field, passed in checks.items() if not passed]
            if failed:
                errors.append(f"{name}: deterministic mismatch in {failed}")
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    await engine.dispose()
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    args = parser.parse_args()
    errors = asyncio.run(validate_outputs(args.output_dir, args.input_dir))
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: all 50 outputs match schema, PostgreSQL, evidence, finance, and policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
