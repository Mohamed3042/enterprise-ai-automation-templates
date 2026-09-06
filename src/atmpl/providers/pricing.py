"""Cost estimation from a committed price table.

A model the table does not know returns ``None``, and the dashboard renders that as
"not priced" rather than as zero — a cost we did not compute is not a cost of nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from atmpl.providers.base import Usage

PRICES_PATH = Path(__file__).resolve().parent / "prices.yaml"


@dataclass(frozen=True)
class PriceTable:
    as_of: str
    currency: str
    source: str
    prices: dict[str, dict[str, float]]
    valid_through: str | None = None

    def key(self, provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def knows(self, provider: str, model: str) -> bool:
        return self.key(provider, model) in self.prices

    def estimate(self, provider: str, model: str, usage: Usage) -> float | None:
        """USD estimate, or ``None`` when the model is unpriced or usage is unreported."""
        entry = self.prices.get(self.key(provider, model))
        if entry is None or not usage.measured:
            return None
        million = 1_000_000
        cost = (usage.input_tokens or 0) / million * entry.get("input", 0.0)
        cost += (usage.output_tokens or 0) / million * entry.get("output", 0.0)
        return round(cost, 8)

    def basis(self, provider: str, model: str) -> str:
        if self.knows(provider, model):
            return f"list price {self.as_of}"
        return "not priced"


@lru_cache(maxsize=1)
def load_prices(path: Path | None = None) -> PriceTable:
    raw = yaml.safe_load((path or PRICES_PATH).read_text(encoding="utf-8"))
    return PriceTable(
        as_of=str(raw["as_of"]),
        currency=str(raw.get("currency", "USD")),
        source=str(raw.get("source", "")),
        prices={str(key): dict(value) for key, value in (raw.get("models") or {}).items()},
        valid_through=str(raw["valid_through"]) if raw.get("valid_through") else None,
    )
