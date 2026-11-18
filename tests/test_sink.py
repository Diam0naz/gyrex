import json

import pytest

from gyrex import neon_http
from gyrex import sink as sink_mod
from gyrex.migrate import split_statements
from gyrex.models import Offer

URL = "postgresql://u:secret@ep-example-123456-pooler.c-8.us-east-2.aws.neon.tech/neondb?sslmode=require"


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _offer(i):
    return Offer("gpuhunt", "aws", "on-demand", f"i{i}", "us-east-1", "H100", "H100", 1, 80.0, 2.0)


def test_sql_endpoint_matches_serverless_driver():
    assert neon_http.sql_endpoint(URL) == "https://api.c-8.us-east-2.aws.neon.tech/sql"
    with pytest.raises(ValueError):
        neon_http.sql_endpoint("postgresql://u:p@localhost/db")


def test_write_chunks_sums_and_sends_connection_string(monkeypatch):
    s = sink_mod.NeonSink(URL)
    monkeypatch.setattr(sink_mod, "CHUNK", 2)
    calls = []

    def fake_post(url, data, headers, timeout):
        body = json.loads(data)
        calls.append((url, body))
        n = len(json.loads(body["params"][1]))
        return FakeResp(200, {"rows": [{"r": {"upserted": n, "price_changes": 1}}]})

    monkeypatch.setattr(s.db.s, "post", fake_post)
    assert s.write("gpuhunt", [_offer(i) for i in range(5)]) == {"upserted": 5, "price_changes": 3}
    assert [c[0] for c in calls] == ["https://api.c-8.us-east-2.aws.neon.tech/sql"] * 3
    assert calls[0][1]["query"] == "select ingest_offers($1, $2::jsonb) as r"
    assert json.loads(calls[0][1]["params"][1])[0]["offer_key"] == "gpuhunt|aws|i0|us-east-1|od|1"
    assert s.db.s.headers["Neon-Connection-String"] == URL


def test_close_run_skips_when_no_provider_answered(monkeypatch):
    s = sink_mod.NeonSink(URL)
    monkeypatch.setattr(s.db.s, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert s.close_run("gpuhunt", [], "2026-11-18T00:00:00Z") == 0


def test_sql_errors_raise_without_retry(monkeypatch):
    s = sink_mod.NeonSink(URL)
    n = []
    monkeypatch.setattr(s.db.s, "post", lambda *a, **k: n.append(1) or FakeResp(400, {"message": "syntax error"}))
    with pytest.raises(neon_http.NeonHttpError, match="400: syntax error"):
        s.write("gpuhunt", [_offer(0)])
    assert len(n) == 1


def test_split_statements_keeps_function_bodies_whole():
    sql = """
    -- a comment; with a semicolon
    create table t (a text default 'x;y');
    create function f() returns int language sql as $$ select 1; $$;
    create function g() returns int language plpgsql as $body$ begin return 2; end $body$;
    /* block; comment */
    """
    stmts = split_statements(sql)
    assert len(stmts) == 3
    assert stmts[1].endswith("$$ select 1; $$")
    assert "begin return 2; end" in stmts[2]
