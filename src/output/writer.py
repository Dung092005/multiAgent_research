"""Atomic UTF-8 JSON writer."""

import json
import os
from pathlib import Path

from src.schemas.final_output import FinalCaseOutput


def write_output(output: FinalCaseOutput, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{output.case_id}.json"
    temporary = output_dir / f"{output.case_id}.tmp"
    payload = output.model_dump(mode="json")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(destination)
    return destination
