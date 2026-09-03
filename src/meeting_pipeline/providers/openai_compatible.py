"""OpenAI-compatible JSON client for llama.cpp and vLLM."""

from __future__ import annotations

import json
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import ReasoningSettings
from ..errors import ProviderError, SchemaRepairError
from .base import Usage

T = TypeVar("T", bound=BaseModel)


def _strip_fence(text: str) -> str:
    value = text.strip()
    # Qwen reasoning variants may prepend a private thinking block even when JSON is requested.
    value = re.sub(r"^\s*<think>.*?</think>\s*", "", value, flags=re.DOTALL)
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


class OpenAICompatibleProvider:
    def __init__(self, settings: ReasoningSettings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.timeout_seconds)
        self._last_usage = Usage(model=settings.model)

    @property
    def last_usage(self) -> Usage:
        return self._last_usage

    def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        schema_name: str = "response",
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        key = self.settings.resolve_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        schema_instruction = (
            "Return exactly one JSON object conforming to this JSON Schema; no markdown: "
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        )
        payload = {
            "model": self.settings.model,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_output_tokens,
            "messages": [
                {"role": "system", "content": schema_instruction},
                *messages,
            ],
            "response_format": {"type": "json_object"},
        }
        url = f"{self.settings.base_url.rstrip('/')}/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self.client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                if len(response.content) > self.settings.max_response_bytes:
                    raise ProviderError("reasoning response exceeds configured byte limit")
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                usage = body.get("usage") or {}
                self._last_usage = Usage(
                    model=body.get("model", self.settings.model),
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    attempts=attempt + 1,
                )
                parsed = json.loads(_strip_fence(content))
                if not isinstance(parsed, dict):
                    raise ProviderError("reasoning response must be one JSON object")
                return parsed
            except (
                httpx.HTTPError,
                KeyError,
                IndexError,
                TypeError,
                json.JSONDecodeError,
                ProviderError,
            ) as exc:
                last_error = exc
                if attempt < self.settings.max_retries:
                    time.sleep(min(0.25 * (2**attempt), 2.0))
        raise ProviderError(f"reasoning request failed: {last_error}") from last_error

    def generate_typed(self, messages: list[dict[str, str]], model_type: type[T]) -> T:
        schema = model_type.model_json_schema()
        first_error: Exception | None = None
        try:
            data = self.complete_json(messages, schema)
            return model_type.model_validate(data)
        except (ProviderError, ValidationError) as exc:
            first_error = exc
        repair = [
            *messages,
            {
                "role": "user",
                "content": (
                    "Your previous response was invalid. Correct it once. "
                    "Return only a JSON object "
                    f"matching the schema. Validation error: {first_error}"
                ),
            },
        ]
        try:
            result = model_type.model_validate(self.complete_json(repair, schema))
            self._last_usage.repaired = True
            return result
        except (ProviderError, ValidationError) as exc:
            raise SchemaRepairError(f"structured output failed after one repair: {exc}") from exc
