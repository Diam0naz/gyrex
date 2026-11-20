import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from gyrex import probe
from gyrex.probe import BudgetError, Clock, NosanaError

MARKET = {
    "address": "97G9NnvBDQ2WpKu6fasoMsAKmfj63C9rhysJnkeWodAf",
    "slug": "nvidia-4090",
    "type": "PREMIUM",
    "usd_reward_per_hour": 0.3636,
    "network_fee_percentage": 10,
}
PRICE = 0.3636 * 1.1
NOW = 1_795_000_000.0  # fake epoch seconds

PROBE_OUT = {
    "probe_version": 1,
    "ok": True,
    "t_script_start": NOW + 95,
    "t_first_kernel": NOW + 100,
    "gpu": {"name": "NVIDIA GeForce RTX 4090", "vram_mb": 24210},
    "smi_before": {"uuid": "GPU-abc", "driver_version": "550.54"},
    "gemm": {"tflops_median": 152.3, "tflops_p10": 149.0, "tflops_first_fifth": 153.0, "tflops_last_fifth": 151.1},
    "copy_gbps": 880.5,
}


def finished_job(out=PROBE_OUT, exit_code=0, **docker):
    log = "loading\n" + (probe.MARKER + json.dumps(out) if out is not None else "")
    return {
        "state": "COMPLETED",
        "node": "NodeX",
        "listedAt": NOW + 2,
        "timeStart": NOW + 40,
        "timeEnd": NOW + 160,
        "jobResult": {
            "status": "success",
            "opStates": [
                {
                    "exitCode": exit_code,
                    "logs": [{"type": "stdout", "log": log}],
                    "diagnostics": {
                        "reason": {"hostShutDown": False, "jobStopped": False, "jobExpired": False},
                        "state": {"StartedAt": "2026-11-20T03:00:00.123456789Z", "OOMKilled": False, **docker},
                    },
                }
            ],
        },
    }


class FakeClock(Clock):
    def __init__(self):
        self.t = NOW
        super().__init__(now=lambda: self.t, sleep=self._sleep)

    def _sleep(self, s):
        self.t += s


class FakeNosana:
    """Scripted job states, one per poll; the last one repeats."""

    def __init__(self, states, create_error=None):
        self.states = list(states)
        self.create_error = create_error
        self.calls = []

    def create(self, body):
        self.calls.append(("create", body))
        if self.create_error:
            raise NosanaError(self.create_error)
        return {"id": "Dep1"}

    def jobs(self, dep):
        self.calls.append(("jobs", dep))
        return [] if self.states[0] is None else [{"job": "Job1", "node": "NodeX", "state": "QUEUED"}]

    def job(self, dep, job):
        self.calls.append(("job", job))
        s = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return s

    def _advance(self):
        if len(self.states) > 1 and self.states[0] is None:
            self.states.pop(0)

    def stop(self, dep):
        self.calls.append(("stop", dep))

    def archive(self, dep):
        self.calls.append(("archive", dep))


class ListThenJobs(FakeNosana):
    def jobs(self, dep):
        r = super().jobs(dep)
        self._advance()
        return r


def run(api, **kw):
    return probe.run_probe(api, MARKET, probe.job_definition("print(1)"), clock=FakeClock(), log=lambda m: None, **kw)


def test_job_definition_runs_the_script_on_a_gpu():
    d = probe.job_definition("print('hi')")
    op = d["ops"][0]
    assert d["version"] == "0.1" and d["type"] == "container"
    assert op["type"] == "container/run" and op["args"]["gpu"] is True
    assert op["args"]["cmd"] == ["python", "-c", "print('hi')"]
    assert op["args"]["image"] == probe.IMAGE


def test_successful_probe():
    api = ListThenJobs([None, {"state": "QUEUED"}, {"state": "RUNNING", "node": "NodeX"}, finished_job()])
    row = run(api)
    assert row["status"] == "ok" and row["failure"] is None
    assert row["tflops_median"] == 152.3 and row["copy_gbps"] == 880.5
    assert row["gpu_name"] == "NVIDIA GeForce RTX 4090" and row["gpu_uuid"] == "GPU-abc"
    assert row["node"] == "NodeX" and row["job_id"] == "Job1" and row["deployment_id"] == "Dep1"
    assert row["startup_s"] == 60.0  # job start (NOW+40) -> first kernel (NOW+100)
    assert row["cost_usd"] == pytest.approx(PRICE * 120 / 3600, abs=1e-6)
    assert row["cost_cap_usd"] == pytest.approx(PRICE * 10 / 60, abs=1e-6)
    assert row["container_started_at"].startswith("2026-11-20T03:00:00.123456")
    assert row["result"]["ok"] is True
    assert "jobDefinition" not in row["raw"]
    assert [c for c in api.calls if c[0] in ("stop", "archive")] == [("stop", "Dep1"), ("archive", "Dep1")]
    body = api.calls[0][1]
    assert body["market"] == MARKET["address"] and body["replicas"] == 1 and body["timeout"] == probe.TIMEOUT_MIN
    assert body["strategy"] == "SIMPLE" and body["autostart"] is True


def test_numeric_job_states_are_understood():
    api = ListThenJobs([{"state": 1}, {**finished_job(), "state": 2}])
    assert run(api)["status"] == "ok"


def test_queue_timeout_stops_the_deployment_and_costs_nothing():
    api = ListThenJobs([{"state": "QUEUED"}])
    row = run(api, queue_limit_s=60)
    assert row["status"] == "queue_timeout" and row["cost_usd"] == 0
    assert ("stop", "Dep1") in api.calls and ("archive", "Dep1") in api.calls


def test_run_timeout_keeps_the_cap_counted():
    api = ListThenJobs([{"state": "RUNNING", "timeStart": NOW + 20}])
    row = run(api, timeout_min=1)
    assert row["status"] == "run_timeout" and row["cost_usd"] is None


def test_submit_error_stores_an_attempt_without_cleanup():
    api = FakeNosana([None], create_error="POST /deployments/create: 402 insufficient credits")
    row = run(api)
    assert row["status"] == "submit_error" and "402" in row["failure"]
    assert not any(c[0] in ("stop", "archive") for c in api.calls)


def test_probe_reported_failure():
    out = {"probe_version": 1, "ok": False, "failure": "oom", "error": "CUDA out of memory"}
    row = run(ListThenJobs([finished_job(out, exit_code=1)]))
    assert row["status"] == "failed" and row["failure"] == "oom"


def test_failure_without_output_uses_the_container_state():
    row = run(ListThenJobs([finished_job(None, exit_code=137, OOMKilled=True)]))
    assert row["status"] == "failed" and row["failure"] == "oom_killed"
    row = run(ListThenJobs([finished_job(None, exit_code=125)]))
    assert row["failure"] == "exit_code 125"


def test_budget():
    probe.check_budget(1.0, [0.07, 0.07], 5.0, credits=1.0)
    with pytest.raises(BudgetError, match="weekly cap"):
        probe.check_budget(4.95, [0.07], 5.0, credits=10)
    with pytest.raises(BudgetError, match="credits"):
        probe.check_budget(0, [0.07], 5.0, credits=0)


def test_timestamps():
    assert probe._ts(1_795_000_000) == datetime.fromtimestamp(1_795_000_000, tz=timezone.utc)
    assert probe._ts(1_795_000_000_000) == probe._ts(1_795_000_000)
    assert probe._ts("0001-01-01T00:00:00Z") is None and probe._ts(None) is None
    assert probe._ts("2026-11-20T03:00:00.123456789Z").microsecond == 123456


def test_probe_output_is_found_in_any_log_line():
    jr = {"opStates": [{"logs": [{"log": "x"}, {"log": "a\nb " + probe.MARKER + '{"ok":true}\n'}]}]}
    assert probe.find_probe_output(jr) == {"ok": True}
    assert probe.find_probe_output({"opStates": [{"logs": [{"log": probe.MARKER + "{bad"}]}]}) is None


def test_gemm_script_reports_failure_as_json_without_torch(tmp_path):
    # The real benchmark needs a GPU; without torch it must still print one parseable line.
    r = subprocess.run(
        [sys.executable, "-I", "-S", str(probe.PROBE_SCRIPT)], capture_output=True, text=True, cwd=tmp_path
    )
    line = [l for l in r.stdout.splitlines() if l.startswith(probe.MARKER)]
    assert r.returncode == 1 and len(line) == 1
    out = json.loads(line[0][len(probe.MARKER):])
    assert out["ok"] is False and out["failure"] == "exception" and "torch" in out["error"]
