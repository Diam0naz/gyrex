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


def _market(name, slug="m1", price=1.0):
    return {"address": slug, "slug": slug, "name": name, "type": "PREMIUM", "usd_reward_per_hour": price}


def _gpus(*entries):
    return nosana._gpu_info_by_market({"gpus": [{**e, "markets": ["m1"]} for e in entries]})


def test_market_name_decides_between_variants():
    info = _gpus(
        {"name": "NVIDIA GeForce RTX 4070 Ti", "vramGb": 12, "available": 1},
        {"name": "NVIDIA GeForce RTX 4070", "vramGb": 12, "available": 2},
        {"name": "NVIDIA GeForce RTX 4070 SUPER", "vramGb": 12, "available": 3},
    )
    o = nosana.market_to_offer(_market("NVIDIA 4070"), None, info)
    assert (o.gpu_model, o.available) == ("RTX4070", 2)
    assert len(o.extra["market_gpus"]) == 3


def test_entry_for_another_model_is_ignored():
    info = _gpus({"name": "NVIDIA GeForce RTX 3060 Ti", "vramGb": 8, "available": 4})
    o = nosana.market_to_offer(_market("NVIDIA 3060"), None, info)
    assert (o.gpu_model, o.vram_gb, o.available) == ("RTX3060", None, None)


def test_single_entry_names_a_market_with_an_opaque_name():
    info = _gpus({"name": "NVIDIA GeForce RTX 4090", "vramGb": 24})
    assert nosana.market_to_offer(_market("Fast Pool"), None, info).gpu_model == "RTX4090"


def test_several_entries_do_not_guess_for_an_opaque_name():
    info = _gpus({"name": "NVIDIA GeForce RTX 4080"}, {"name": "NVIDIA GeForce RTX 4080 SUPER"})
    assert nosana.market_to_offer(_market("Fast Pool"), None, info).gpu_model is None


def test_multi_gpu_market_is_priced_per_gpu():
    o = nosana.market_to_offer(_market("NVIDIA Pro 6000 (6x)", price=12.0), None, {})
    assert (o.gpu_model, o.gpu_count, o.vram_gb) == ("RTXPRO6000", 6, 96.0)
    assert o.to_row()["offer_key"].endswith("|od|6")


def test_reported_vram_snaps_to_the_marketed_size():
    info = _gpus({"name": "NVIDIA RTX PRO 6000 Blackwell Server Edition", "vramGb": 95})
    o = nosana.market_to_offer(_market("NVIDIA Pro 6000 (SOC2)"), None, info)
    assert (o.gpu_model, o.vram_gb) == ("RTXPRO6000", 96.0)


def test_fetch_handles_api_down(monkeypatch):
    monkeypatch.setenv("NOSANA_API_URL", "http://127.0.0.1:9")  # nothing listens here
    offers, errors = nosana.fetch()
    assert offers == [] and "markets" in errors
