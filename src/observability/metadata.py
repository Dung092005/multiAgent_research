"""Atomic metadata collection for a real production run."""

import importlib.metadata
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config.model_config import (
    LLM_TEMPERATURE,
    OPENROUTER_MODEL_ID,
    OPENROUTER_MODEL_PARAMETER_COUNT_B,
    OPENROUTER_PROVIDER,
)


def git_metadata() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        branch = run("branch", "--show-current")
        commit = run("rev-parse", "HEAD")
        dirty = bool(run("status", "--porcelain"))
    except (OSError, subprocess.CalledProcessError):
        return {"branch": "unknown", "commit": "unknown", "dirty_worktree": True}
    return {"branch": branch, "commit": commit, "dirty_worktree": dirty}


def build_metadata(
    run_id: str,
    started_at: datetime,
    expected: int,
    processed: int,
    succeeded: int,
    failed: int,
) -> dict[str, Any]:
    try:
        langgraph_version = importlib.metadata.version("langgraph")
    except importlib.metadata.PackageNotFoundError:
        langgraph_version = "unknown"
    return {
        "run_id": run_id,
        "started_at": started_at.astimezone(UTC).isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "git": git_metadata(),
        "framework": {"name": "LangGraph", "version": langgraph_version},
        "llm": {
            "provider": OPENROUTER_PROVIDER,
            "model_id": OPENROUTER_MODEL_ID,
            "parameter_count_b": OPENROUTER_MODEL_PARAMETER_COUNT_B,
            "temperature": LLM_TEMPERATURE,
        },
        "runtime": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "docker": os.path.exists("/.dockerenv"),
            "postgres_version": "16",
        },
        "cases": {
            "expected": expected,
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
        },
    }


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
