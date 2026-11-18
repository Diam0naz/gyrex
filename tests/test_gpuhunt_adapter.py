import gpuhunt
from gpuhunt._internal.models import AcceleratorVendor

from gyrex.adapters import gpuhunt_adapter
from gyrex.models import dedupe


def _item(**kw):
    base = dict(
        provider="aws", instance_name="p5.48xlarge", location="us-east-1", price=98.32, cpu=192,
        memory=2048.0, gpu_count=8, gpu_name="H100", gpu_memory=80.0, spot=False, disk_size=None,
        gpu_vendor=AcceleratorVendor.NVIDIA,
    )
    base.update(kw)
    return gpuhunt.CatalogItem(**base)


def test_item_to_offer():
    o = gpuhunt_adapter.item_to_offer(_item())
    assert (o.gpu_model, o.gpu_count, o.vram_gb, o.offer_type) == ("H100", 8, 80.0, "on-demand")
    assert gpuhunt_adapter.item_to_offer(_item(spot=True)).offer_type == "spot"
    assert gpuhunt_adapter.item_to_offer(_item(gpu_count=0)) is None


def test_gpu_slices_are_skipped():
    a10_sixth = _item(provider="azure", instance_name="Standard_NV6ads_A10_v5", gpu_count=1,
                      gpu_name="A10", gpu_memory=4.0, price=0.45)
    assert gpuhunt_adapter.item_to_offer(a10_sixth) is None
    assert gpuhunt_adapter.item_to_offer(_item(gpu_name="A10", gpu_memory=24.0, gpu_count=1)) is not None


def test_non_gpu_accelerators_are_skipped():
    tpu = _item(provider="gcp", instance_name="v6e-8", gpu_name="v6e", gpu_memory=32.0,
                gpu_vendor=AcceleratorVendor.GOOGLE)
    assert gpuhunt_adapter.item_to_offer(tpu) is None


def test_dedupe_keeps_cheapest():
    a = gpuhunt_adapter.item_to_offer(_item(price=10.0))
    b = gpuhunt_adapter.item_to_offer(_item(price=9.0))
    assert [o.price_usd_hr for o in dedupe([a, b])] == [9.0]


def test_one_failing_provider_does_not_sink_the_run(monkeypatch):
    def fake_query(provider, **_):
        if provider == ["broken"]:
            raise RuntimeError("proxy said no")
        return [_item(provider=provider[0])]

    monkeypatch.setattr(gpuhunt, "query", fake_query)
    offers, errors = gpuhunt_adapter.fetch(["aws", "broken", "runpod"])
    assert sorted(o.provider for o in offers) == ["aws", "runpod"]
    assert list(errors) == ["broken"]
