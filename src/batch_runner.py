"""CLI batch runner for isolated Olist dispute workflows."""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from sqlalchemy import text

from scripts.validate_inputs import load_and_validate_inputs
from src.config.model_config import validate_model_configuration
from src.config.settings import get_settings
from src.database.connection import create_engine, create_session_factory
from src.database.repository import OlistRepository
from src.graph import Workflow
from src.llm.openrouter_client import OpenRouterClient
from src.observability.metadata import build_metadata, write_metadata
from src.observability.trace_logger import TraceLogger
from src.schemas.case_input import CaseInput
from src.state import initial_state

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaseRunResult:
    case_id: str
    succeeded: bool
    error: str | None = None


async def database_preflight(engine, cases: list[CaseInput]) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        order_ids = [case.customer_request.claimed_order_id for case in cases]
        found = set(
            (
                await connection.execute(
                    text("SELECT order_id FROM olist.orders WHERE order_id = ANY(:ids)"),
                    {"ids": order_ids},
                )
            ).scalars()
        )
    missing = sorted(set(order_ids) - found)
    if missing:
        raise RuntimeError(f"Input orders are missing from PostgreSQL: {missing}")


def prepare_outputs(output_dir: Path, cases: list[CaseInput], overwrite: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = [output_dir / f"{case.case_id}.json" for case in cases]
    target_names = {path.name for path in targets}
    unexpected_json = sorted(
        path.name for path in output_dir.glob("*.json") if path.name not in target_names
    )
    if unexpected_json:
        raise RuntimeError(f"Unexpected JSON files in output directory: {unexpected_json}")
    existing = [path for path in targets if path.exists()]
    if existing and not overwrite:
        raise FileExistsError("Output files already exist; pass --overwrite")
    if overwrite:
        for path in existing:
            path.unlink()
        for case in cases:
            temporary = output_dir / f"{case.case_id}.tmp"
            if temporary.exists():
                temporary.unlink()


async def run_batch(args: argparse.Namespace) -> int:
    started_wall = datetime.now(UTC)
    started = perf_counter()
    validate_model_configuration()
    settings = get_settings()
    cases = load_and_validate_inputs(
        args.input_dir, require_all=args.case_id is None, case_id=args.case_id
    )
    engine = create_engine(settings, read_only=True)
    await database_preflight(engine, cases)
    if args.validate_only:
        await engine.dispose()
        print(f"PASS: {len(cases)} inputs and PostgreSQL records validated")
        return 0

    settings.require_api_key()
    prepare_outputs(args.output_dir, cases, args.overwrite)
    run_id = str(uuid4())
    trace = TraceLogger(args.trace_file, run_id)
    trace.reset()
    await trace.emit("run_started", message=f"Processing {len(cases)} cases")
    await trace.emit("database_check_started")
    await database_preflight(engine, cases)
    await trace.emit("database_check_completed")

    sessions = create_session_factory(engine)
    repository = OlistRepository(sessions)
    llm = OpenRouterClient(settings)
    workflow = Workflow(llm=llm, repository=repository, trace=trace, output_dir=args.output_dir)
    case_limit = args.max_concurrent_cases or settings.max_concurrent_cases
    semaphore = asyncio.Semaphore(case_limit)

    async def run_case(case: CaseInput) -> CaseRunResult:
        async with semaphore:
            await trace.emit("case_started", case_id=case.case_id)
            try:
                final_state = await workflow.graph.ainvoke(
                    initial_state(run_id, case),
                    {
                        "configurable": {"thread_id": f"{run_id}:{case.case_id}"},
                        "max_concurrency": 3,
                    },
                )
                succeeded = bool(final_state.get("output_path")) and not final_state.get("errors")
                if not succeeded:
                    return CaseRunResult(
                        case.case_id, False, "; ".join(final_state.get("errors", []))
                    )
                await trace.emit("case_completed", case_id=case.case_id)
                return CaseRunResult(case.case_id, True)
            except Exception as exc:
                LOGGER.exception("Case failed: %s", case.case_id)
                await trace.emit(
                    "case_failed",
                    case_id=case.case_id,
                    status="failed",
                    message=f"{type(exc).__name__}: {exc}",
                )
                return CaseRunResult(case.case_id, False, str(exc))

    results = await asyncio.gather(*(run_case(case) for case in cases))
    succeeded = sum(result.succeeded for result in results)
    failed_ids = [result.case_id for result in results if not result.succeeded]
    expected_output_names = {f"{case.case_id}.json" for case in cases}
    actual_output_names = {path.name for path in args.output_dir.glob("*.json")}
    if actual_output_names != expected_output_names:
        failed_ids.append("OUTPUT_SET_MISMATCH")
    await trace.emit(
        "run_completed",
        status="success" if not failed_ids else "failed",
        processed=len(results),
        succeeded=succeeded,
        failed=len(failed_ids),
    )
    write_metadata(
        args.metadata_file,
        build_metadata(run_id, started_wall, len(cases), len(results), succeeded, len(failed_ids)),
    )
    await engine.dispose()
    duration = perf_counter() - started
    print(
        f"Total={len(results)} Success={succeeded} Failed={len(failed_ids)} "
        f"FailedCaseIDs={failed_ids} DurationSeconds={duration:.2f}"
    )
    return 0 if not failed_ids and succeeded == len(cases) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--trace-file", type=Path, default=Path("trace.jsonl"))
    parser.add_argument("--metadata-file", type=Path, default=Path("metadata.json"))
    parser.add_argument("--case-id")
    parser.add_argument("--max-concurrent-cases", type=int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=getattr(logging, get_settings().log_level.upper(), logging.INFO))
    return asyncio.run(run_batch(args))


if __name__ == "__main__":
    raise SystemExit(main())
