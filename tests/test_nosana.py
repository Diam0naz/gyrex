import json
from pathlib import Path

from gyrex.adapters import nosana

FIX = json.loads((Path(__file__).parent / "fixtures" / "nosana_markets.json").read_text())


def _offers():
    info = nosana._gpu_info_by_market(FIX["reservations_gpus"])
    out = [nosana.market_to_offer(m, FIX["nos_usd"], info) for m in FIX["markets"]]
    return {o.extra["slug"]: o for o in out if o}


def test_usd_price_and_enrichment():
    o = _offers()["nvidia-4090"]
    assert (o.gpu_model, o.price_usd_hr, o.vram_gb, o.available) == ("RTX4090", 0.4, 24.0, 37)
    assert o.offer_type == "decentralized" and o.extra["market_type"] == "PREMIUM"
    assert o.offer_key == "nosana|nosana|97G9NnvBDQ2WpKu6fasoMsAKmfj63C9rhysJnkeWodAf|global|od|1"


def test_nos_price_fallback():
    o = _offers()["nvidia-3060"]
    assert o.price_usd_hr == round(0.00004 * 3600 * 0.5, 6)
    assert o.extra["price_basis"] == "nos_reward_per_second*nos_usd"
    assert o.vram_gb is None  # 3060 ships as 8 and 12 GB: no guess


def test_vram_from_name_and_alias():
    offers = _offers()
    assert offers["nvidia-a100-80gb"].vram_gb == 80.0
    assert offers["nvidia-rtx-a6000"].gpu_model == "A6000"


def test_unmapped_kept_unpriced_dropped():
    offers = _offers()
    assert offers["mystery"].gpu_model is None and offers["mystery"].gpu_raw == "Mystery Accelerator X"
    assert "nvidia-h100" not in offers  # no usd price and no NOS rate


def test_fetch_handles_api_down(monkeypatch):
    monkeypatch.setenv("NOSANA_API_URL", "http://127.0.0.1:9")  # nothing listens here
    offers, errors = nosana.fetch()
    assert offers == [] and "markets" in errors
