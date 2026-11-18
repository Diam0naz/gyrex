"""Centralized clouds via dstack's gpuhunt.

gpuhunt.query() fans out to every provider and raises if ANY online provider fails
(missing key, network refusal), so we query one provider at a time and keep going.
"""

from __future__ import annotations

import logging
import os

import gpuhunt

from gyrex.models import Offer
from gyrex.normalize import canonical_gpu

log = logging.getLogger(__name__)

# Providers served from gpuhunt's pre-built offline catalog: no API keys needed.
DEFAULT_PROVIDERS = [
    "aws", "azure", "gcp", "lambdalabs", "nebius", "oci", "runpod", "verda", "cloudrift",
]


def configured_providers() -> list[str]:
    env = os.environ.get("GPUHUNT_PROVIDERS", "").strip()
    return [p.strip() for p in env.split(",") if p.strip()] if env else DEFAULT_PROVIDERS


# Smallest VRAM each NVIDIA model ships with. Less than that means a slice of a GPU
# (Azure NVadsA10 v5 at 1/6..1/2 of an A10, GCP g4 at 1/8..1/2 of an RTX PRO 6000), which
# isn't comparable to a whole GPU and would drag the index down.
_MIN_VRAM: dict[str, float] = {}
for _g in gpuhunt.KNOWN_NVIDIA_GPUS:
    _MIN_VRAM[_g.name] = min(_MIN_VRAM.get(_g.name, float("inf")), float(_g.memory))


def is_fractional(item: gpuhunt.CatalogItem) -> bool:
    floor = _MIN_VRAM.get(item.gpu_name or "")
    return bool(floor and item.gpu_memory and item.gpu_memory < floor)


# A GPU index: TPUs, Gaudi and Tenstorrent cards are left out.
GPU_VENDORS = {"nvidia", "amd"}


def item_to_offer(item: gpuhunt.CatalogItem) -> Offer | None:
    if not item.gpu_count or not item.gpu_name or item.price is None or is_fractional(item):
        return None
    vendor = getattr(item.gpu_vendor, "value", item.gpu_vendor)
    if vendor not in GPU_VENDORS:
        return None
    if vendor and vendor != "nvidia":
        model = item.gpu_name  # keep AMD names as gpuhunt gives them
    else:
        model = canonical_gpu(item.gpu_name) or item.gpu_name  # gpuhunt is already canonical
    return Offer(
        source="gpuhunt",
        provider=item.provider,
        offer_type="spot" if item.spot else "on-demand",
        instance_name=item.instance_name,
        region=item.location or "unknown",
        gpu_raw=item.gpu_name,
        gpu_model=model,
        gpu_count=int(item.gpu_count),
        vram_gb=float(item.gpu_memory) if item.gpu_memory else None,
        price_usd_hr=float(item.price),
        extra={
            "vendor": vendor,
            "cpu": item.cpu,
            "memory_gb": item.memory,
            "disk_gb": item.disk_size,
        },
    )


def fetch(providers: list[str] | None = None) -> tuple[list[Offer], dict[str, str]]:
    """Returns (offers, errors_by_provider)."""
    offers: list[Offer] = []
    errors: dict[str, str] = {}
    for provider in providers or configured_providers():
        try:
            items = gpuhunt.query(provider=[provider], min_gpu_count=1)
        except Exception as e:  # one bad provider must not sink the run
            errors[provider] = f"{type(e).__name__}: {e}"[:500]
            log.warning("gpuhunt provider %s failed: %s", provider, errors[provider])
            continue
        n = fractional = 0
        for item in items:
            if is_fractional(item):
                fractional += 1
            elif o := item_to_offer(item):
                offers.append(o)
                n += 1
        log.info("gpuhunt %s: %d offers (%d GPU slices skipped)", provider, n, fractional)
    return offers, errors
