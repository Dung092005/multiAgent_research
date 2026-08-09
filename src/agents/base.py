"""Shared agent prompt and LLM helpers."""

from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, Field

from src.llm.openrouter_client import StructuredResponse

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(Protocol):
    async def structured(
        self, *, system_prompt: str, user_payload: dict, response_model: type[T]
    ) -> StructuredResponse[T]: ...


class Narrative(BaseModel):
    summary: str = Field(min_length=1, max_length=1000)
    warnings: list[str] = Field(default_factory=list, max_length=5)


class BaseAgent:
    name: str
    prompt_file: str

    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm
        path = Path(__file__).resolve().parents[1] / "prompts" / self.prompt_file
        self._prompt = path.read_text(encoding="utf-8")
