"""Context-budget preflight: overflow must fail before the network, never silently truncate."""

import httpx
import pytest
from pydantic import BaseModel

from meeting_pipeline.config import load_config
from meeting_pipeline.errors import ContextBudgetError
from meeting_pipeline.providers.openai_compatible import (
    OpenAICompatibleProvider,
    estimate_tokens,
)


class Answer(BaseModel):
    value: int


def provider(update=None, on_request=None):
    settings = load_config(None).reasoning.model_copy(
        update={"base_url": "http://test/v1", "max_retries": 0, **(update or {})}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if on_request is not None:
            on_request(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"value": 1}'}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(settings, client=client)


def test_estimate_tokens_is_a_documented_char_ratio_not_a_guess():
    assert estimate_tokens("", 3.6) == 0
    assert estimate_tokens("a" * 36, 3.6) == 10
    # Partial tokens round up so the estimate never understates the prompt.
    assert estimate_tokens("a" * 37, 3.6) == 11


def test_oversized_prompt_fails_before_any_network_call():
    calls = []
    client = provider(on_request=lambda request: calls.append(request))
    huge = "hola " * 200_000

    with pytest.raises(ContextBudgetError) as excinfo:
        client.complete_json([{"role": "user", "content": huge}], {"type": "object"})

    assert calls == []
    message = str(excinfo.value)
    assert "context_window" in message
    assert "max_output_tokens" in message


def test_budget_reserves_output_tokens_and_schema_instruction():
    settings_update = {"context_window": 4096, "max_output_tokens": 3500}
    client = provider(update=settings_update)
    # Comfortably under context_window on its own, but not once output is reserved.
    text = "a" * int(1000 * 3.6)

    with pytest.raises(ContextBudgetError):
        client.complete_json([{"role": "user", "content": text}], {"type": "object"})


def test_budget_error_names_the_schema_so_the_operator_knows_which_request_overflowed():
    client = provider(update={"context_window": 4096, "max_output_tokens": 3500})

    with pytest.raises(ContextBudgetError, match="Answer"):
        client.generate_typed([{"role": "user", "content": "a" * 40_000}], Answer)


def test_requests_that_fit_still_reach_the_endpoint():
    seen = []
    client = provider(on_request=lambda request: seen.append(request.url.path))

    assert client.complete_json([{"role": "user", "content": "hola"}], {"type": "object"}) == {
        "value": 1
    }
    assert seen == ["/v1/chat/completions"]
