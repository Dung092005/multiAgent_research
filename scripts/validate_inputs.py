"""Validate the exact 50-file input contract without modifying inputs."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from src.errors import InputValidationError
from src.schemas.case_input import CaseInput


def load_and_validate_inputs(
    input_dir: Path, *, require_all: bool = True, case_id: str | None = None
) -> list[CaseInput]:
    allowed_non_json = {".gitkeep"}
    unexpected = [
        path.name
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() != ".json" and path.name not in allowed_non_json
    ]
    if unexpected:
        raise InputValidationError(f"Unexpected input files: {unexpected}")
    json_files = sorted(input_dir.glob("*.json"))
    if case_id:
        json_files = [input_dir / f"{case_id}.json"]
    elif require_all:
        expected = {f"EC_{index:03d}.json" for index in range(1, 51)}
        actual = {path.name for path in json_files}
        if actual != expected:
            raise InputValidationError(
                f"Input set mismatch; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
    cases: list[CaseInput] = []
    seen: set[str] = set()
    for path in json_files:
        if not path.is_file():
            raise InputValidationError(f"Missing input file: {path}")
        try:
            case = CaseInput.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, json.JSONDecodeError) as exc:
            raise InputValidationError(f"Invalid input {path.name}: {exc}") from exc
        if path.stem != case.case_id:
            raise InputValidationError(f"Filename/case_id mismatch: {path.name}/{case.case_id}")
        if case.case_id in seen:
            raise InputValidationError(f"Duplicate case_id: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if require_all and not case_id and len(cases) != 50:
        raise InputValidationError(f"Expected 50 inputs, received {len(cases)}")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    args = parser.parse_args()
    cases = load_and_validate_inputs(args.input_dir)
    print(f"PASS: validated {len(cases)} input files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
