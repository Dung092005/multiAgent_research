"""Common trace wrapper for typed tools."""

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import TypeVar

from src.observability.trace_logger import TraceLogger

T = TypeVar("T")


class TracedTools:
    def __init__(self, trace: TraceLogger, case_id: str, agent: str) -> None:
        self._trace = trace
        self._case_id = case_id
        self._agent = agent

    async def _call(self, name: str, operation: Callable[[], Awaitable[T]]) -> T:
        await self._trace.emit(
            "tool_called", case_id=self._case_id, agent=self._agent, tool_name=name
        )
        started = perf_counter()
        try:
            result = await operation()
        except Exception as exc:
            await self._trace.emit(
                "tool_completed",
                case_id=self._case_id,
                agent=self._agent,
                tool_name=name,
                status="failed",
                latency_ms=(perf_counter() - started) * 1000,
                message=str(exc),
            )
            raise
        await self._trace.emit(
            "tool_completed",
            case_id=self._case_id,
            agent=self._agent,
            tool_name=name,
            latency_ms=(perf_counter() - started) * 1000,
        )
        return result
