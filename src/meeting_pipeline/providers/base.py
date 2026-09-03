"""The provider contract the pipeline depends on — nothing model-specific leaks past it."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

Message = dict[str, str]
T = TypeVar("T", bound=BaseModel)


class Usage(BaseModel):
    model: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    attempts: int = 0
    repaired: bool = False


@runtime_checkable
class ReasoningProvider(Protocol):
    """Any OpenAI-compatible endpoint: llama.cpp `llama-server`, vLLM, or a stub."""

    def complete_json(
        self, messages: list[Message], schema: dict[str, Any], *, schema_name: str = ...
    ) -> dict[str, Any]:
        """Return the parsed JSON object the model produced."""
        ...

    def generate_typed(self, messages: list[Message], model: type[T]) -> T:
        """Return a validated Pydantic object, with at most one schema-repair attempt."""
        ...

    @property
    def last_usage(self) -> Usage: ...
