import json
import stat
import subprocess
import sys

import pytest

from gyrex import collect, neon_http
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


def _offer(i, price=2.0, provider="aws"):
    return Offer("gpuhunt", provider, "on-demand", f"i{i}", "us-east-1", "H100", "H100", 1, 80.0, price)


def _stub(tmp_path, monkeypatch, body):
    """A stand-in gyrex-ingest: saves its stdin and environment, then runs `body`."""
    path = tmp_path / "gyrex-ingest"
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"d = pathlib.Path({str(tmp_path)!r})\n"
        "(d / 'stdin').write_text(sys.stdin.read())\n"
        "(d / 'argv').write_text(json.dumps(sys.argv[1:]))\n"
        "(d / 'url').write_text(os.environ['DATABASE_URL'])\n" + body
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GYREX_INGEST_BIN", str(path))
    return sink_mod.NeonSink(URL)


def test_sql_endpoint_matches_serverless_driver():
    assert neon_http.sql_endpoint(URL) == "https://api.c-8.us-east-2.aws.neon.tech/sql"
    with pytest.raises(ValueError):
        neon_http.sql_endpoint("postgresql://u:p@localhost/db")


def test_sql_errors_raise_without_retry(monkeypatch):
    db = neon_http.NeonHttp(URL)
    n = []
    monkeypatch.setattr(db.s, "post", lambda *a, **k: n.append(1) or FakeResp(400, {"message": "syntax error"}))
    with pytest.raises(neon_http.NeonHttpError, match="400: syntax error"):
        db.query("select 1")
    assert len(n) == 1


def test_ingest_streams_rows_and_keeps_the_url_off_the_command_line(tmp_path, monkeypatch):
    sink = _stub(tmp_path, monkeypatch, "print(json.dumps({'ok': True, 'upserted': 2, 'price_changes': 1}))\n")
    assert sink.ingest("gpuhunt", [_offer(0), _offer(1)])["upserted"] == 2
    rows = [json.loads(line) for line in (tmp_path / "stdin").read_text().splitlines()]
    assert [r["offer_key"] for r in rows] == ["gpuhunt|aws|i0|us-east-1|od|1", "gpuhunt|aws|i1|us-east-1|od|1"]
    assert json.loads((tmp_path / "argv").read_text()) == ["--source", "gpuhunt"]
    assert (tmp_path / "url").read_text() == URL


def test_ingest_failure_raises_with_the_reason(tmp_path, monkeypatch):
    body = "print(json.dumps({'ok': False, 'error': 'boom', 'upserted': 1000})); sys.exit(1)\n"
    sink = _stub(tmp_path, monkeypatch, body)
    with pytest.raises(sink_mod.IngestError, match=r"boom \(1000 offers already written\)"):
        sink.ingest("gpuhunt", [_offer(0)])


def test_ingest_crash_raises(tmp_path, monkeypatch):
    sink = _stub(tmp_path, monkeypatch, "sys.stderr.write('crashed'); sys.exit(3)\n")
    with pytest.raises(sink_mod.IngestError, match="exited 3: crashed"):
        sink.ingest("gpuhunt", [_offer(0)])


def test_missing_binary_is_an_error_not_a_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("GYREX_INGEST_BIN", str(tmp_path / "nope"))
    with pytest.raises(sink_mod.IngestError, match="cargo build"):
        sink_mod.NeonSink(URL)


class RecordingSink:
    def __init__(self, result):
        self.result, self.runs = result, []

    def ingest(self, source, offers):
        return self.result

    def log_run(self, run):
        self.runs.append(run)


def test_rejected_rows_make_the_run_partial(monkeypatch):
    monkeypatch.setattr(collect.gpuhunt_adapter, "fetch", lambda: ([_offer(0)], {}))
    sink = RecordingSink(
        {
            "upserted": 1, "price_changes": 0, "deactivated": 0, "started_at": "2026-11-18 00:00:00+00",
            "rejected": 1, "reject_reasons": {"price out of range": 1}, "reject_samples": [],
            "held_providers": ["aws"],
        }
    )
    assert collect.run(["gpuhunt"], sink) == 0
    run = sink.runs[0]
    assert run["status"] == "partial"
    assert run["started_at"] == "2026-11-18 00:00:00+00"
    assert run["errors"]["rejected_rows"]["held_providers"] == ["aws"]


@pytest.mark.skipif(not sink_mod.LOCAL_BUILD.is_file(), reason="gyrex-ingest not built")
def test_real_binary_accepts_our_rows_and_rejects_nan():
    rows = sink_mod.to_ndjson([_offer(i) for i in range(30)] + [_offer(99, float("nan"))])
    done = subprocess.run(
        [str(sink_mod.LOCAL_BUILD), "--source", "gpuhunt", "--validate-only"],
        input=rows, capture_output=True, text=True,
    )
    summary = json.loads(done.stdout)
    assert done.returncode == 0, summary
    assert (summary["valid"], summary["rejected"]) == (30, 1)
    assert summary["held_providers"] == ["aws"]


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
