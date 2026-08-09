"""Concurrency-safe JSONL trace writer."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class TraceLogger:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    async def emit(
        self,
        event: str,
        *,
        case_id: str | None = None,
        agent: str | None = None,
        status: str = "success",
        message: str = "",
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        latency_ms: float | None = None,
        model_id: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        tool_name: str | None = None,
        evidence_ids: list[str] | None = None,
        task_id: str | None = None,
        **fields: Any,
    ) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "case_id": case_id,
            "trace_id": trace_id or (f"{self.run_id}:{case_id}" if case_id else self.run_id),
            "span_id": span_id or str(uuid4()),
            "parent_span_id": parent_span_id,
            "agent": agent,
            "event": event,
            "status": status,
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            "model_id": model_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "tool_name": tool_name,
            "evidence_ids": evidence_ids or [],
            "task_id": task_id,
            "message": message[:500],
            **fields,
        }
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        async with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
