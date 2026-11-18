import json

from gyrex import sink as sink_mod
from gyrex.models import Offer


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = json.dumps(body).encode() if body is not None else b""
        self.text = self.content.decode()

    def json(self):
        return self._body


def _offer(i):
    return Offer("gpuhunt", "aws", "on-demand", f"i{i}", "us-east-1", "H100", "H100", 1, 80.0, 2.0)


def test_rpc_chunks_and_sums(monkeypatch):
    calls = []
    s = sink_mod.SupabaseSink("https://x.supabase.co/", "service-key")
    monkeypatch.setattr(sink_mod, "CHUNK", 2)

    def fake_post(url, data, headers, timeout):
        body = json.loads(data)
        calls.append((url, body))
        return FakeResp(200, {"upserted": len(body["p_offers"]), "price_changes": 1})

    monkeypatch.setattr(s.s, "post", fake_post)
    assert s.write("gpuhunt", [_offer(i) for i in range(5)]) == {"upserted": 5, "price_changes": 3}
    assert [c[0] for c in calls] == ["https://x.supabase.co/rest/v1/rpc/ingest_offers"] * 3
    assert calls[0][1]["p_offers"][0]["offer_key"] == "gpuhunt|aws|i0|us-east-1|od|1"
    assert s.s.headers["Authorization"] == "Bearer service-key"


def test_client_errors_raise_without_retry(monkeypatch):
    s = sink_mod.SupabaseSink("https://x.supabase.co", "k")
    n = []
    monkeypatch.setattr(s.s, "post", lambda *a, **k: n.append(1) or FakeResp(401, {"message": "bad key"}))
    try:
        s.write("gpuhunt", [_offer(0)])
    except RuntimeError as e:
        assert "401" in str(e)
    else:
        raise AssertionError("expected RuntimeError")
    assert len(n) == 1
