from types import SimpleNamespace

import pytest
from openai import LengthFinishReasonError

from experiments.pvoc_v0.research_llm import (
    ResearchStructuredLLMError,
    ResearchVertexStructuredLLM,
)
from experiments.pvoc_v0.schemas import DecisionResponse


class FakeSettings:
    llm_provider = "vertex"
    llm_request_timeout_seconds = 15.0
    vertex_openai_base_url = "https://example.invalid/openapi"

    def require_api_key(self) -> None:
        return None


class FakeParseClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(parse=self.parse))
        )

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def parsed_completion(action: str = "late_delivery_logistics") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content='{"predicted_root_cause":"late_delivery_logistics"}',
                    parsed=DecisionResponse(
                        predicted_root_cause=action,
                        confidence=0.8,
                        short_reason="Timeline supports late delivery",
                    ),
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
    )


@pytest.mark.asyncio
async def test_research_adapter_returns_vertex_parsed_pydantic_response() -> None:
    client = FakeParseClient([parsed_completion()])
    adapter = ResearchVertexStructuredLLM(FakeSettings(), client=client, backoff_seconds=0)

    response = await adapter.structured(
        system_prompt="Return a decision.",
        user_payload={"probe": True},
        response_model=DecisionResponse,
    )

    assert response.value.predicted_root_cause == "late_delivery_logistics"
    assert response.prompt_tokens == 12
    assert response.completion_tokens == 8
    assert client.calls[0]["response_format"] is DecisionResponse
    assert client.calls[0]["extra_body"] == {
        "google": {"thinking_config": {"thinking_budget": 0, "include_thoughts": False}}
    }


@pytest.mark.asyncio
async def test_research_adapter_retries_message_none_without_attribute_error() -> None:
    missing_message = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=None)],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=0),
    )
    client = FakeParseClient([missing_message, parsed_completion("valid_split_payment")])
    adapter = ResearchVertexStructuredLLM(FakeSettings(), client=client, backoff_seconds=0)

    response = await adapter.structured(
        system_prompt="Return a decision.",
        user_payload={"probe": True},
        response_model=DecisionResponse,
    )

    assert response.value.predicted_root_cause == "valid_split_payment"
    assert len(client.calls) == 2
    assert response.prompt_tokens == 15


@pytest.mark.asyncio
async def test_research_adapter_retries_are_bounded() -> None:
    no_choices = SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0))
    client = FakeParseClient([no_choices, no_choices, no_choices])
    adapter = ResearchVertexStructuredLLM(
        FakeSettings(), client=client, max_attempts=3, backoff_seconds=0
    )

    with pytest.raises(ResearchStructuredLLMError, match="after 3 attempts"):
        await adapter.structured(
            system_prompt="Return a decision.",
            user_payload={"probe": True},
            response_model=DecisionResponse,
        )

    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_research_adapter_retries_length_finish_reason() -> None:
    completion = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3),
    )
    client = FakeParseClient(
        [LengthFinishReasonError(completion=completion), parsed_completion("valid_split_payment")]
    )
    adapter = ResearchVertexStructuredLLM(FakeSettings(), client=client, backoff_seconds=0)

    response = await adapter.structured(
        system_prompt="Return a decision.",
        user_payload={"probe": "length-retry"},
        response_model=DecisionResponse,
    )

    assert response.value.predicted_root_cause == "valid_split_payment"
    assert len(client.calls) == 2
