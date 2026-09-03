import json

import httpx
import pytest

from meeting_pipeline.config import load_config
from meeting_pipeline.errors import ProviderError, SchemaRepairError
from meeting_pipeline.providers.openai_compatible import OpenAICompatibleProvider


def provider_with_responses(responses):
    queue = list(responses)

    def handler(request):
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["temperature"] == 0.1
        item = queue.pop(0)
        return httpx.Response(200, json={"choices": [{"message": {"content": item}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test/v1")
    settings = load_config(None).reasoning.model_copy(update={"base_url": "http://test/v1"})
    return OpenAICompatibleProvider(settings, client=client)


def test_complete_json_parses_json_and_code_fences():
    provider = provider_with_responses(['```json\n{"ok": true}\n```'])
    assert provider.complete_json([{"role": "user", "content": "x"}], {"type": "object"}) == {
        "ok": True
    }


def test_complete_json_ignores_qwen_thinking_prefix():
    provider = provider_with_responses(['<think>internal reasoning</think>\n{"ok": true}'])
    assert provider.complete_json([{"role": "user", "content": "x"}], {"type": "object"}) == {
        "ok": True
    }


def test_generate_typed_repairs_invalid_first_response():
    from pydantic import BaseModel

    class Answer(BaseModel):
        value: int

    provider = provider_with_responses(['{"wrong": 1}', '{"value": 2}'])
    assert provider.generate_typed([{"role": "user", "content": "x"}], Answer).value == 2


def test_generate_typed_fails_after_one_repair():
    from pydantic import BaseModel

    class Answer(BaseModel):
        value: int

    provider = provider_with_responses(["{}", "{}"])
    with pytest.raises(SchemaRepairError):
        provider.generate_typed([{"role": "user", "content": "x"}], Answer)


def test_http_error_is_provider_error():
    settings = load_config(None).reasoning.model_copy(
        update={"base_url": "http://test/v1", "max_retries": 0}
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="bad"))
    )
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider(settings, client=client).complete_json([], {"type": "object"})
