"""A thin wrapper around the Anthropic SDK for structured calls.

Every call:
- sends only text the caller has already redacted,
- asks for output that matches a Pydantic model (`messages.parse`),
- sets the effort level explicitly (Opus 5.5 always thinks; effort is the depth control),
- opts into server-side refusal fallbacks, so a declined request is re-run on a fallback
  model chosen by refusal category instead of failing outright,
- writes one audit row with the model that actually answered, tokens, cost and latency,
- raises on refusal, truncation or output that does not parse.
"""

from __future__ import annotations

from typing import Any, TypeVar

import anthropic
from anthropic.types.beta import BetaMessageParam
from pydantic import BaseModel

from scribe.audit import AuditLog
from scribe.config import Effort
from scribe.pricing import Usage

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelRefusalError(RuntimeError):
    def __init__(self, category: str | None, explanation: str | None) -> None:
        self.category = category
        self.explanation = explanation
        super().__init__(f"model declined the request (category: {category or 'unknown'})")


class LLMOutputError(RuntimeError):
    pass


class ClaudeClient:
    def __init__(
        self,
        *,
        audit: AuditLog,
        api_key: str | None = None,
        fallbacks: bool = True,
        client: anthropic.Anthropic | None = None,
        timeout_s: float = 300.0,
    ) -> None:
        self.audit = audit
        self.fallbacks = fallbacks
        self.client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout_s, max_retries=3
        )

    def parse(
        self,
        *,
        purpose: str,
        model: str,
        effort: Effort,
        system: str,
        user: str,
        output_model: type[T],
        encounter_id: str | None = None,
        max_tokens: int = 16000,
    ) -> T:
        messages: list[BetaMessageParam] = [{"role": "user", "content": user}]
        prompt_record = {
            "model": model,
            "effort": effort,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "schema": output_model.model_json_schema(),
        }
        extra: dict[str, Any] = {}
        if self.fallbacks:
            extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}

        with self.audit.track(
            provider="anthropic",
            model=model,
            purpose=purpose,
            prompt=prompt_record,
            encounter_id=encounter_id,
        ) as call:
            response = self.client.beta.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_format=output_model,
                output_config={"effort": effort},
                **extra,
            )
            usage = response.usage
            call.model = response.model
            call.usage = Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            )
            if response.stop_reason == "refusal":
                details = response.stop_details
                category = getattr(details, "category", None)
                explanation = getattr(details, "explanation", None)
                call.status = "refusal"
                call.error = f"refusal: {category}"
                raise ModelRefusalError(category, explanation)
            if response.stop_reason == "max_tokens":
                call.status = "truncated"
                raise LLMOutputError(f"output hit max_tokens ({max_tokens})")
            parsed = response.parsed_output
            if parsed is None:
                call.status = "invalid_output"
                raise LLMOutputError("model output did not match the schema")
        return parsed
