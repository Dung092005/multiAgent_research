"""CLI runner for the research-only PVoC v0 counterfactual study.

This module is intentionally separate from ``src.graph`` and does not alter
the production investigation workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from scripts.validate_inputs import load_and_validate_inputs
from src.config.model_config import validate_model_configuration
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository
from src.llm.openrouter_client import OpenRouterClient
from src.policy.engine import PolicyEngine

from .agents import PrivateDecisionAgent
from .counterfactual import CounterfactualRunner
from .messages import AGENTS, build_candidate_message, directed_pairs
from .observations import PrivateObservationBuilder
from .schemas import CounterfactualRecord

SUPPORTED_CASE_IDS = tuple(f"EC_{number:03d}" for number in range(1, 36))


def load_supported_cases(input_dir: Path, case_id: str | None) -> list:
    """Load exactly the requested supported EC cases, failing on missing files."""
    if case_id is not None and case_id not in SUPPORTED_CASE_IDS:
        raise ValueError("PVoC v0 supports only EC_001 through EC_035.")

    available_cases = {
        case.case_id: case for case in load_and_validate_inputs(input_dir, require_all=False)
    }
    requested_ids = (case_id,) if case_id else SUPPORTED_CASE_IDS
    missing = [requested for requested in requested_ids if requested not in available_cases]
    if missing:
        raise ValueError(
            "Missing validated case inputs for: "
            f"{', '.join(missing)}. PVoC v0 requires all requested EC cases."
        )
    return [available_cases[requested] for requested in requested_ids]


def write_results(
    records: list[CounterfactualRecord],
    results_dir: Path,
    *,
    lambda_cost: float,
    case_ids: list[str],
) -> tuple[Path, Path]:
    """Persist append-safe JSONL records and a small reproducibility manifest."""
    results_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"pvoc_v0_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}"
    records_path = results_dir / f"{run_id}.jsonl"
    manifest_path = results_dir / f"{run_id}.manifest.json"

    with records_path.open("x", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(record.model_dump_json())
            output_file.write("\n")

    manifest = {
        "run_id": run_id,
        "experiment": "pvoc_v0",
        "created_at": datetime.now(UTC).isoformat(),
        "case_ids": case_ids,
        "case_count": len(case_ids),
        "candidate_message_count": len(records),
        "lambda_communication_cost": lambda_cost,
        "oracle": "EC_POLICY_V1 via PolicyEngine",
        "notes": "Research-only harness; production graph is not invoked or changed.",
    }
    with manifest_path.open("x", encoding="utf-8") as output_file:
        json.dump(manifest, output_file, indent=2)
        output_file.write("\n")

    return records_path, manifest_path


async def run_study(args: argparse.Namespace) -> tuple[Path, Path]:
    """Run all directed messages for the requested PVoC v0 cases."""
    settings = get_settings()
    validate_model_configuration()
    cases = load_supported_cases(args.input_dir, args.case_id)

    engine = create_engine(settings, read_only=True)
    try:
        repository = OlistRepository(create_session_factory(engine))
        observation_builder = PrivateObservationBuilder(repository)
        llm = OpenRouterClient(settings)
        agents = {agent_name: PrivateDecisionAgent(agent_name, llm) for agent_name in AGENTS}
        policy_engine = PolicyEngine()
        counterfactual_runner = CounterfactualRunner(lambda_cost=args.lambda_cost)
        records: list[CounterfactualRecord] = []

        for case in cases:
            observations = await observation_builder.build(case)
            oracle_decision = policy_engine.evaluate(
                await repository.get_policy_context(case.customer_request.claimed_order_id)
            )
            oracle_action = oracle_decision.primary_issue

            sender_decisions = {}
            for sender in AGENTS:
                sender_decisions[sender] = await agents[sender].decide(
                    observations.for_agent(sender)
                )

            for sender, recipient in directed_pairs():
                message = build_candidate_message(
                    case_id=case.case_id,
                    sender=sender,
                    recipient=recipient,
                    sender_observation=observations.for_agent(sender),
                    sender_decision=sender_decisions[sender],
                )
                record = await counterfactual_runner.run(
                    recipient=agents[recipient],
                    recipient_observation=observations.for_agent(recipient),
                    message=message,
                    oracle_action=oracle_action,
                )
                records.append(record)

        return write_results(
            records,
            args.results_dir,
            lambda_cost=args.lambda_cost,
            case_ids=[case.case_id for case in cases],
        )
    finally:
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the research-only PVoC v0 3-agent counterfactual study."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/input"),
        help="Directory containing validated EC case JSON files (default: data/input).",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("experiments/pvoc_v0/results"),
        help="Directory for JSONL records and the run manifest.",
    )
    parser.add_argument(
        "--case-id",
        choices=SUPPORTED_CASE_IDS,
        help="Run one supported case only; omit to run EC_001 through EC_035.",
    )
    parser.add_argument(
        "--lambda-cost",
        type=float,
        default=0.001,
        help="Non-negative communication-cost coefficient for V_star (default: 0.001).",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.lambda_cost < 0:
        raise SystemExit("--lambda-cost must be non-negative.")

    if sys.platform == "win32":
        records_path, manifest_path = asyncio.run(
            run_study(args), loop_factory=asyncio.SelectorEventLoop
        )
    else:
        records_path, manifest_path = asyncio.run(run_study(args))
    print(f"Wrote PVoC v0 records: {records_path}")
    print(f"Wrote PVoC v0 manifest: {manifest_path}")


if __name__ == "__main__":
    main()
