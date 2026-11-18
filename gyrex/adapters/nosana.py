"""Nosana adapter (decentralized, Solana).

Endpoints come from the official SDK (@nosana/api 2.24.5, host-manager client):
  GET {host}/markets/           -> list of markets (what @nosana/kit's client.api.markets.list() calls)
  GET {host}/markets/price      -> {"price": <NOS in USD>}
  GET {host}/reservations/gpus  -> {"gpus": [{name, vramGb, nodes, available, working, markets[],
                                              price: {minUsdPerHour, maxUsdPerHour} | null}]}

The SDK types GET /markets/ as Record<string, unknown>, so the field names used below
(address, slug, name, type, usd_reward_per_hour, nos_reward_per_second) are taken from the
SDK's market update schema. Run `gyrex-collect --dry-run --dump out/` once against the live
API and check out/nosana_raw.json before trusting the numbers.

Price note: usd_reward_per_hour is what the HOST earns. If Nosana adds a network fee on top
for the buyer, the buyer price is higher; record it in NETWORK_FEE once confirmed.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from gyrex.models import Offer
from gyrex.normalize import canonical_gpu, default_vram, vram_from_name

log = logging.getLogger(__name__)

DEFAULT_API = "https://host-manager.k8s.prd.nosana.com"
NETWORK_FEE = 0.0  # fraction added to the host rate to get the buyer rate; unverified, so 0
TIMEOUT = 20


def _api() -> str:
    return os.environ.get("NOSANA_API_URL", DEFAULT_API).rstrip("/")


def _get(session: requests.Session, path: str) -> Any:
    r = session.get(f"{_api()}{path}", timeout=TIMEOUT, headers={"accept": "application/json"})
    r.raise_for_status()
    return r.json()


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def market_to_offer(
    m: dict[str, Any], nos_usd: float | None, gpu_info: dict[str, dict[str, Any]]
) -> Offer | None:
    market_id = str(m.get("address") or m.get("slug") or m.get("id") or "")
    name = str(m.get("name") or m.get("slug") or "")
    if not market_id or not name:
        return None

    price = _num(m.get("usd_reward_per_hour"))
    price_basis = "usd_reward_per_hour"
    if price is None:
        nos_per_s = _num(m.get("nos_reward_per_second"))
        if nos_per_s and nos_usd:
            price = nos_per_s * 3600 * nos_usd
            price_basis = "nos_reward_per_second*nos_usd"
    if price is None:
        return None
    price *= 1 + NETWORK_FEE

    info = gpu_info.get(market_id) or gpu_info.get(str(m.get("slug") or "")) or {}
    model = canonical_gpu(info.get("name") or name)
    vram = _num(info.get("vramGb")) or vram_from_name(name) or default_vram(model)
    market_type = str(m.get("type") or "").upper() or None

    return Offer(
        source="nosana",
        provider="nosana",
        offer_type="decentralized",
        instance_name=market_id,
        region="global",
        gpu_raw=name,
        gpu_model=model,
        gpu_count=1,
        vram_gb=vram,
        price_usd_hr=round(price, 6),
        available=info.get("available"),
        extra={
            "market_type": market_type,  # PREMIUM | COMMUNITY
            "slug": m.get("slug"),
            "price_basis": price_basis,
            "network_fee_applied": NETWORK_FEE,
            "nodes": info.get("nodes"),
            "working": info.get("working"),
        },
    )


def _gpu_info_by_market(payload: Any) -> dict[str, dict[str, Any]]:
    """Index /reservations/gpus entries by each market id they list."""
    out: dict[str, dict[str, Any]] = {}
    for g in (payload or {}).get("gpus", []) if isinstance(payload, dict) else []:
        for mid in g.get("markets") or []:
            out[str(mid)] = g
    return out


def fetch(dump: dict[str, Any] | None = None) -> tuple[list[Offer], dict[str, str]]:
    """Returns (offers, errors). `dump`, if given, receives the raw API payloads."""
    errors: dict[str, str] = {}
    with requests.Session() as s:
        try:
            markets = _get(s, "/markets/")
        except Exception as e:
            return [], {"markets": f"{type(e).__name__}: {e}"[:500]}
        if isinstance(markets, dict):  # tolerate {"markets": [...]} / {"data": [...]}
            markets = markets.get("markets") or markets.get("data") or []

        nos_usd = None
        try:
            nos_usd = _num(_get(s, "/markets/price").get("price"))
        except Exception as e:
            errors["nos_price"] = f"{type(e).__name__}: {e}"[:500]

        gpus_payload = None
        try:
            gpus_payload = _get(s, "/reservations/gpus")
        except Exception as e:  # enrichment only; prices still come from /markets/
            errors["reservations_gpus"] = f"{type(e).__name__}: {e}"[:500]

    if dump is not None:
        dump.update(markets=markets, nos_usd=nos_usd, reservations_gpus=gpus_payload)

    gpu_info = _gpu_info_by_market(gpus_payload)
    offers = [o for m in markets if isinstance(m, dict) and (o := market_to_offer(m, nos_usd, gpu_info))]
    skipped = len(markets) - len(offers)
    if skipped:
        log.info("nosana: skipped %d markets without a usable price", skipped)
    log.info("nosana: %d offers", len(offers))
    return offers, errors
