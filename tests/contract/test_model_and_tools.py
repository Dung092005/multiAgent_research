from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError, RateLimitError
from pydantic import BaseModel
from tenacity import wait_none

import src.llm.openrouter_client as client_module
from src.config.model_config import (
    OPENROUTER_MODEL_ID,
    validate_model_configuration,
)
from src.config.settings import get_settings
from src.errors import ConfigurationError, LLMRequestError, LLMStructuredOutputError
from src.llm.openrouter_client import OpenRouterClient, parse_json_payload
from src.tools.delivery_tools import DeliveryTools
from src.tools.order_tools import OrderTools
from src.tools.payment_tools import PaymentTools


class Answer(BaseModel):
    value: int


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response))], usage=None
        )


def settings_with_key():
    return replace(get_settings(), openrouter_api_key="test-key", llm_max_retries=2)


def test_model_registry_matches_source():
    validate_model_configuration()
    assert OPENROUTER_MODEL_ID == "openai/gpt-5-nano"


def test_missing_api_key_fails_fast():
    with pytest.raises(ConfigurationError):
        OpenRouterClient(replace(get_settings(), openrouter_api_key=""))


def test_agent_tool_allowlists_have_no_arbitrary_sql():
    all_tools = OrderTools.allowed_tools | PaymentTools.allowed_tools | DeliveryTools.allowed_tools
    assert "execute_sql" not in all_tools
    assert OrderTools.allowed_tools == {"get_order", "get_order_items", "get_order_sellers"}


def test_json_fence_parser():
    assert parse_json_payload('```json\n{"value": 1}\n```') == {"value": 1}


@pytest.mark.asyncio
async def test_invalid_json_is_repaired_once():
    fake = FakeClient(["not json", '{"value": 7}'])
    client = OpenRouterClient(settings_with_key(), client=fake)
    result = await client.structured(system_prompt="test", user_payload={}, response_model=Answer)
    assert result.value.value == 7
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_invalid_json_repair_failure_is_not_defaulted():
    fake = FakeClient(["bad", "still bad"])
    client = OpenRouterClient(settings_with_key(), client=fake)
    with pytest.raises(LLMStructuredOutputError):
        await client.structured(system_prompt="test", user_payload={}, response_model=Answer)


@pytest.mark.asyncio
async def test_429_is_retried(monkeypatch):
    monkeypatch.setattr(client_module, "wait_exponential", lambda **kwargs: wait_none())
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(429, request=request)
    error = RateLimitError("rate limited", response=response, body=None)
    fake = FakeClient([error, '{"value": 2}'])
    client = OpenRouterClient(settings_with_key(), client=fake)
    result = await client.structured(system_prompt="test", user_payload={}, response_model=Answer)
    assert result.value.value == 2
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_timeout_stops_after_retry_limit(monkeypatch):
    monkeypatch.setattr(client_module, "wait_exponential", lambda **kwargs: wait_none())
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    fake = FakeClient(
        [
            APITimeoutError(request=request),
            APITimeoutError(request=request),
            APITimeoutError(request=request),
        ]
    )
    client = OpenRouterClient(settings_with_key(), client=fake)
    with pytest.raises(LLMRequestError):
        await client.structured(system_prompt="test", user_payload={}, response_model=Answer)
    assert fake.calls == 3
