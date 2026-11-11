"""Stand-ins for the Anthropic SDK client, shaped like the parts the app reads."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel


def parsed_response(
    parsed: BaseModel | None,
    *,
    model: str = "claude-opus-5-5",
    stop_reason: str = "end_turn",
    input_tokens: int = 3000,
    output_tokens: int = 1200,
    category: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        stop_reason=stop_reason,
        stop_details=(
            SimpleNamespace(category=category, explanation="declined")
            if stop_reason == "refusal"
            else None
        ),
        parsed_output=parsed,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=None,
        ),
    )


class _Messages:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            outcome = outcome(kwargs)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeAnthropic:
    """`client.beta.messages.parse(...)` returns the queued outcomes in order.

    An outcome may be a response, an exception to raise, or a function of the request
    kwargs that returns either.
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self.beta = SimpleNamespace(messages=_Messages(outcomes))

    @property
    def calls(self) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = self.beta.messages.calls
        return calls
