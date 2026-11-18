"""Normalized offer row shared by every adapter.

Matches the plan's schema (gpu_model, vram, count, region, price_usd_hr, source, type, ts);
`ts` is set by the database (first_seen / last_seen / last_changed) rather than by adapters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

OfferType = Literal["on-demand", "spot", "decentralized"]


@dataclass(frozen=True)
class Offer:
    source: str  # adapter that produced it: "gpuhunt" | "nosana"
    provider: str  # who actually sells it: "aws", "runpod", "nosana", ...
    offer_type: OfferType
    instance_name: str
    region: str
    gpu_raw: str  # GPU name exactly as the provider reports it
    gpu_model: str | None  # canonical name (gpuhunt vocabulary), None if unmapped
    gpu_count: int
    vram_gb: float | None  # per GPU
    price_usd_hr: float  # whole instance, USD per hour
    available: int | None = None  # nodes/instances available right now, if known
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def offer_key(self) -> str:
        """Stable identity of an offer across runs. Price is deliberately not part of it."""
        spot = "spot" if self.offer_type == "spot" else "od"
        return "|".join(
            [self.source, self.provider, self.instance_name, self.region, spot, str(self.gpu_count)]
        )

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["offer_key"] = self.offer_key
        return row


def dedupe(offers: list[Offer]) -> list[Offer]:
    """Keep the cheapest offer per key (gpuhunt can list the same instance more than once)."""
    best: dict[str, Offer] = {}
    for o in offers:
        cur = best.get(o.offer_key)
        if cur is None or o.price_usd_hr < cur.price_usd_hr:
            best[o.offer_key] = o
    return list(best.values())
