"""List prices used to put a dollar figure on each audited model call.

Prices are USD per million tokens. A model missing from the table gets a NULL cost in
the audit log rather than a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

MILLION = Decimal(1_000_000)
COST_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class ModelPrice:
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


def _anthropic(input_: str, output: str, cache_read: str) -> ModelPrice:
    inp = Decimal(input_)
    # Five-minute cache writes are billed at 1.25x the input rate.
    return ModelPrice(inp, Decimal(output), Decimal(cache_read), inp * Decimal("1.25"))


def _gemini(input_: str, output: str) -> ModelPrice:
    # Context caching is not used here, so cached-token rates are left at zero.
    return ModelPrice(Decimal(input_), Decimal(output), Decimal(0), Decimal(0))


PRICES: dict[str, ModelPrice] = {
    # Anthropic first-party API list prices (checked 2026-11-10).
    "claude-opus-5-5": _anthropic("4.00", "20.00", "0.20"),
    "claude-sonnet-5-5": _anthropic("2.00", "10.00", "0.10"),
    # Gemini API paid-tier list prices (checked 2026-11-10; audio input is billed at the
    # same rate as text for these models). The free tier bills nothing, so for a free key
    # these figures are what the same calls would cost on a paid key.
    "gemini-3.8-flash": _gemini("0.75", "3.75"),  # introductory rate to 2026-12-31
    "gemini-3.5-flash-lite": _gemini("0.30", "2.50"),
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def cost_usd(model: str, usage: Usage) -> Decimal | None:
    """Dollar cost of one call, or None when the model has no price entry."""
    price = PRICES.get(model)
    if price is None:
        return None
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    ) / MILLION
    return total.quantize(COST_PLACES)
