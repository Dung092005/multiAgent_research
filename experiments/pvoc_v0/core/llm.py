"""Vertex structured-output client used only by the PVoC studies."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    LengthFinishReasonError,
    OpenAIError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from src.config.model_config import LLM_MAX_TOKENS, LLM_TEMPERATURE, OPENROUTER_MODEL_ID
from src.config.settings import Settings
from src.errors import ConfigurationError, LLMRequestError

T = TypeVar("T", bound=BaseModel)
LOGGER = logging.getLogger(__name__)
_CLOUD_PLATFORM_SCOPE = ("https://www.googleapis.com/auth/cloud-platform",)
_THINKING_CONFIG = {
    "google": {
        "thinking_config": {
            "thinking_budget": 0,
            "include_thoughts": False,
        }
    }
}


class ResearchStructuredLLMError(LLMRequestError):
    """Raised after the bounded research-only structured-output retries fail."""


@dataclass(frozen=True)
class ResearchStructuredResponse(Generic[T]):
    value: T
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ResearchStructuredLLM(Protocol):
    async def structured(
        self, *, system_prompt: str, user_payload: dict, response_model: type[T]
    ) -> ResearchStructuredResponse[T]: ...


def _refresh_vertex_access_token() -> str:
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as exc:  # pragma: no cover - declared project dependency
        raise ResearchStructuredLLMError("google-auth is required for the PVoC Vertex adapter") from exc

    credentials, _ = google.auth.default(scopes=_CLOUD_PLATFORM_SCOPE)
    credentials.refresh(google.auth.transport.requests.Request())
    token = getattr(credentials, "token", None)
    if not token:
        raise ResearchStructuredLLMError("Unable to refresh Vertex access token")
    return str(token)


async def _vertex_access_token() -> str:
    return await asyncio.to_thread(_refresh_vertex_access_token)


class ResearchVertexStructuredLLM:
    """Bounded Pydantic parsing through Vertex's OpenAI-compatible endpoint."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
    ) -> None:
        if settings.llm_provider.strip().lower() != "vertex":
            raise ConfigurationError("PVoC v0 research adapter requires LLM_PROVIDER=vertex")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")

        settings.require_api_key()
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._timeout = max(15.0, float(settings.llm_request_timeout_seconds))
        self._client = client or AsyncOpenAI(
            api_key=_vertex_access_token,
            base_url=settings.vertex_openai_base_url,
            timeout=self._timeout,
        )

    async def structured(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        response_model: type[T],
    ) -> ResearchStructuredResponse[T]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
        ]
        prompt_tokens = 0
        completion_tokens = 0
        last_error: BaseException | None = None

        for attempt in range(1, self._max_attempts + 1):
            completion: Any | None = None
            try:
                completion = await asyncio.wait_for(
                    self._client.beta.chat.completions.parse(
                        model=OPENROUTER_MODEL_ID,
                        messages=messages,
                        response_format=response_model,
                        max_tokens=LLM_MAX_TOKENS,
                        temperature=LLM_TEMPERATURE,
                        extra_body=_THINKING_CONFIG,
                    ),
                    timeout=self._timeout,
                )
                usage_prompt, usage_completion = self._usage(completion)
                prompt_tokens += usage_prompt
                completion_tokens += usage_completion
                parsed = self._parsed_value(completion, response_model)
                return ResearchStructuredResponse(
                    value=parsed,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            except (
                OpenAIError,
                ResearchStructuredLLMError,
                TimeoutError,
                TypeError,
                ValidationError,
                ValueError,
            ) as exc:
                last_error = exc
                self._log_failed_attempt(attempt, completion, exc)
                if attempt == self._max_attempts or not self._retryable(exc):
                    break
                await asyncio.sleep(min(self._backoff_seconds * (2 ** (attempt - 1)), 2.0))

        error_name = type(last_error).__name__ if last_error else "UnknownError"
        raise ResearchStructuredLLMError(
            f"PVoC Vertex structured response failed after {self._max_attempts} attempts: {error_name}"
        ) from last_error

    @staticmethod
    def _usage(completion: Any) -> tuple[int, int]:
        usage = getattr(completion, "usage", None)
        return (
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        )

    @staticmethod
    def _parsed_value(completion: Any, response_model: type[T]) -> T:
        choices = getattr(completion, "choices", None)
        if not choices:
            raise ResearchStructuredLLMError("Vertex returned no completion choices")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise ResearchStructuredLLMError("Vertex returned a choice without a message")
        if hasattr(message, "content") and not message.content:
            raise ResearchStructuredLLMError("Vertex returned an empty structured-response content")
        parsed = getattr(message, "parsed", None)
        if parsed is None:
            raise ResearchStructuredLLMError("Vertex returned no parsed structured response")
        return parsed if isinstance(parsed, response_model) else response_model.model_validate(parsed)

    @staticmethod
    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, ResearchStructuredLLMError):
            return True
        if isinstance(exc, ValidationError):
            return True
        if isinstance(exc, LengthFinishReasonError):
            return True
        if isinstance(exc, (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)):
            return True
        if isinstance(exc, TimeoutError):
            return True
        return isinstance(exc, APIStatusError) and exc.status_code >= 500

    def _log_failed_attempt(self, attempt: int, completion: Any | None, exc: BaseException) -> None:
        finish_reason = None
        choices = getattr(completion, "choices", None) if completion is not None else None
        if choices:
            finish_reason = getattr(choices[0], "finish_reason", None)
        usage_prompt, usage_completion = self._usage(completion)
        LOGGER.warning(
            "PVoC Vertex attempt=%s model=%s finish_reason=%s prompt_tokens=%s "
            "completion_tokens=%s error_class=%s",
            attempt,
            OPENROUTER_MODEL_ID,
            finish_reason,
            usage_prompt,
            usage_completion,
            type(exc).__name__,
        )
