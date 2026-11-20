"""Probe jobs on Nosana: post the GEMM benchmark (probe/gemm.py) to a market and record what happened.

    gyrex-probe --check                      # free: validate the job, show credits, budget and cost
    gyrex-probe                              # one probe each on the Premium and Community 4090 markets
    gyrex-probe --market nvidia-4090         # one market (by slug)

Needs NOSANA_API_KEY (credits) and DATABASE_URL. Spending is capped: before submitting, the
worst-case cost of every probe in the last 7 days (its actual cost once known) plus the new ones
must fit in PROBE_WEEKLY_CAP_USD (default 5), and in the account's credits.

Each probe is a one-replica SIMPLE deployment with a hard timeout. Whatever happens, the
deployment is stopped and archived afterwards, and the attempt is stored in probe_runs: a job
that never left the queue is a reliability result too.

Clocks: queue_s uses our clock against Nosana's job start; startup_s uses Nosana's job start
against the container's clock. Both are good to a few seconds, which is the scale that matters.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

CLIENT_MANAGER = "https://client-manager.k8s.prd.nosana.com"
HOST_MANAGER = "https://host-manager.k8s.prd.nosana.com"
PROBE_SCRIPT = Path(__file__).resolve().parent.parent / "probe" / "gemm.py"
PROBE_VERSION = 1
# CUDA 12.1 runs on any driver from 530 up. A common image, so many hosts already have it cached.
IMAGE = "pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime"
DEFAULT_MARKETS = ("nvidia-4090", "nvidia-4090-community")
GPU_MODEL = {"nvidia-4090": "RTX4090", "nvidia-4090-community": "RTX4090"}

TIMEOUT_MIN = 10        # job hard limit; the probe itself needs about 1-2 minutes after the pull
QUEUE_LIMIT_S = 15 * 60  # give up if no node has picked the job up by then
POLL_S = 10
MARKER = "GYREX_PROBE "
TERMINAL = {"COMPLETED", "STOPPED"}
# The on-chain job state is an enum; the API returns its name or its number.
STATE_NAMES = {0: "QUEUED", 1: "RUNNING", 2: "COMPLETED", 3: "STOPPED"}


class NosanaError(RuntimeError):
    pass


class BudgetError(RuntimeError):
    pass


# ---------------------------------------------------------------------------- Nosana API


class Nosana:
    """The few client-manager routes a probe needs. With an API key the client manager proxies
    /deployments/* to the deployment manager (see @nosana/api's deployment-manager client)."""

    def __init__(self, api_key: str, session: requests.Session | None = None):
        if not api_key:
            raise NosanaError("NOSANA_API_KEY is not set")
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})

    def _call(self, method: str, url: str, body: Any = None) -> Any:
        for attempt in range(4):
            try:
                r = self.s.request(method, url, data=None if body is None else json.dumps(body), timeout=60)
            except requests.ConnectionError as e:
                if attempt == 3:
                    raise NosanaError(f"{method} {url}: {type(e).__name__}") from None
                time.sleep(2**attempt)
                continue
            if r.status_code in (429, 502, 503, 504) and attempt < 3:
                time.sleep(2**attempt)
                continue
            break
        if r.status_code >= 400:
            # The key is only ever in the request headers, never in this message.
            raise NosanaError(f"{method} {url.replace(CLIENT_MANAGER, '')}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    def markets(self) -> list[dict[str, Any]]:
        return requests.get(f"{HOST_MANAGER}/markets/", timeout=60).json()

    def credits(self) -> float:
        b = self._call("GET", f"{CLIENT_MANAGER}/credits/balance")
        return float(b["assignedCredits"]) - float(b["reservedCredits"]) - float(b["settledCredits"])

    def validate(self, definition: dict[str, Any]) -> list[str]:
        r = self._call("POST", f"{CLIENT_MANAGER}/deployments/validate", {"job_definition": definition})
        return [] if r.get("valid") else list(r.get("errors") or ["rejected without a reason"])

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"{CLIENT_MANAGER}/deployments/create", body)

    def jobs(self, deployment: str) -> list[dict[str, Any]]:
        return self._call("GET", f"{CLIENT_MANAGER}/deployments/{deployment}/jobs?sort_order=asc")["jobs"]

    def job(self, deployment: str, job: str) -> dict[str, Any]:
        return self._call("GET", f"{CLIENT_MANAGER}/deployments/{deployment}/jobs/{job}")

    def stop(self, deployment: str) -> None:
        self._call("POST", f"{CLIENT_MANAGER}/deployments/{deployment}/stop")

    def archive(self, deployment: str) -> None:
        self._call("POST", f"{CLIENT_MANAGER}/deployments/{deployment}/archive")


# ---------------------------------------------------------------------------- job and budget


def job_definition(script: str | None = None, image: str = IMAGE) -> dict[str, Any]:
    script = script if script is not None else PROBE_SCRIPT.read_text()
    return {
        "version": "0.1",
        "type": "container",
        "meta": {"trigger": "api", "gyrex_probe_version": PROBE_VERSION},
        "ops": [
            {
                "type": "container/run",
                "id": "gyrex-probe",
                "args": {"image": image, "gpu": True, "cmd": ["python", "-c", script]},
            }
        ],
    }


def buyer_price(market: dict[str, Any]) -> float:
    """Same rule as the index (gyrex/adapters/nosana.py): host rate plus the network fee."""
    from gyrex.adapters.nosana import _network_fee

    return float(market["usd_reward_per_hour"]) * (1 + _network_fee(market))


def cost_cap(price_usd_hr: float, timeout_min: int = TIMEOUT_MIN) -> float:
    return round(price_usd_hr * timeout_min / 60, 6)


def check_budget(spent_7d: float, new_caps: list[float], weekly_cap: float, credits: float | None) -> None:
    need = sum(new_caps)
    if spent_7d + need > weekly_cap:
        raise BudgetError(
            f"weekly cap ${weekly_cap:.2f}: ${spent_7d:.4f} used in the last 7 days, "
            f"${need:.4f} more needed (worst case)"
        )
    if credits is not None and credits < need:
        raise BudgetError(f"${credits:.4f} of Nosana credits left, ${need:.4f} needed (worst case)")


def spent_last_7d(db) -> float:
    rows = db.query(
        "select coalesce(sum(coalesce(cost_usd, cost_cap_usd)), 0) as spent "
        "from probe_runs where submitted_at > now() - interval '7 days'"
    )
    return float(rows[0]["spent"])


# ---------------------------------------------------------------------------- result parsing


def _ts(v: Any) -> datetime | None:
    """Unix seconds or milliseconds, or an ISO string (Docker's zero time means never)."""
    if v in (None, "", 0):
        return None
    if isinstance(v, str):
        if v.startswith("0001-"):
            return None
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")[:32])
        except ValueError:
            # Docker writes nanoseconds; Python takes at most microseconds.
            head, _, tail = v.partition(".")
            frac = "".join(c for c in tail if c.isdigit())[:6]
            return datetime.fromisoformat(f"{head}.{frac or '0'}+00:00")
    x = float(v)
    if x <= 0:
        return None
    return datetime.fromtimestamp(x / 1000 if x > 1e12 else x, tz=timezone.utc)


def state_name(v: Any) -> str:
    if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
        return STATE_NAMES.get(int(v), str(v))
    return str(v or "").upper()


def find_probe_output(job_result: dict[str, Any] | None) -> dict[str, Any] | None:
    for op in (job_result or {}).get("opStates") or []:
        for entry in op.get("logs") or []:
            for line in str(entry.get("log") or "").splitlines():
                i = line.find(MARKER)
                if i != -1:
                    try:
                        return json.loads(line[i + len(MARKER):])
                    except ValueError:
                        continue
    return None


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    return round((b - a).total_seconds(), 1) if a and b else None


def build_row(
    *,
    market: dict[str, Any],
    price: float,
    submitted_at: datetime,
    deployment_id: str | None,
    job_list_item: dict[str, Any] | None,
    job: dict[str, Any] | None,
    outcome: str | None,
    failure: str | None,
    image: str = IMAGE,
    timeout_min: int = TIMEOUT_MIN,
) -> dict[str, Any]:
    """One probe_runs row. `outcome` is set when we gave up (queue_timeout, run_timeout,
    submit_error); otherwise the status comes from the job and the probe's own output."""
    job = job or {}
    jr = job.get("jobResult") or {}
    ops = jr.get("opStates") or []
    op = ops[0] if ops else {}
    docker = (op.get("diagnostics") or {}).get("state") or {}
    out = find_probe_output(jr)

    listed = _ts(job.get("listedAt"))
    started = _ts(job.get("timeStart")) or _ts((job_list_item or {}).get("time_start"))
    ended = _ts(job.get("timeEnd"))
    container = _ts(docker.get("StartedAt"))
    first_kernel = _ts((out or {}).get("t_first_kernel"))

    if outcome:
        status = outcome
    elif out and out.get("ok"):
        status = "ok"
    else:
        status = "failed"
        if not failure:
            if out:
                failure = out.get("failure") or "probe_failed"
            elif docker.get("OOMKilled"):
                failure = "oom_killed"
            elif (op.get("error") or {}).get("message"):
                failure = f"op_error: {op['error']['message']}"[:200]
            elif op.get("exitCode") not in (None, 0):
                failure = f"exit_code {op['exitCode']}"
            elif ((op.get("diagnostics") or {}).get("reason") or {}).get("hostShutDown"):
                failure = "host_shutdown"
            else:
                failure = f"no_output (job {state_name(job.get('state'))})"

    gpu = (out or {}).get("gpu") or {}
    smi = (out or {}).get("smi_before") or {}
    gemm = (out or {}).get("gemm") or {}
    billed_s = _secs(started, ended)
    if billed_s is not None:
        cost = round(price * billed_s / 3600, 6)
    elif started is None and outcome != "run_timeout":
        cost = 0.0  # never left the queue: assumed unbilled
    else:
        cost = None  # ran, end unknown: the cap stays counted against the budget
    return {
        "source": "nosana",
        "market": market["address"],
        "market_slug": market.get("slug"),
        "market_type": str(market.get("type") or "").upper() or None,
        "gpu_model": GPU_MODEL.get(str(market.get("slug"))),
        "probe_version": PROBE_VERSION,
        "image": image,
        "deployment_id": deployment_id,
        "job_id": (job_list_item or {}).get("job"),
        "node": job.get("node") or (job_list_item or {}).get("node"),
        "status": status,
        "failure": failure,
        "price_usd_hr": round(price, 6),
        "cost_cap_usd": cost_cap(price, timeout_min),
        "cost_usd": cost,
        "submitted_at": submitted_at.isoformat(),
        "listed_at": listed and listed.isoformat(),
        "job_started_at": started and started.isoformat(),
        "container_started_at": container and container.isoformat(),
        "first_kernel_at": first_kernel and first_kernel.isoformat(),
        "job_ended_at": ended and ended.isoformat(),
        "queue_s": _secs(submitted_at, started),
        "startup_s": _secs(started, first_kernel),
        "total_s": _secs(submitted_at, ended),
        "tflops_median": gemm.get("tflops_median"),
        "tflops_p10": gemm.get("tflops_p10"),
        "tflops_first_fifth": gemm.get("tflops_first_fifth"),
        "tflops_last_fifth": gemm.get("tflops_last_fifth"),
        "copy_gbps": (out or {}).get("copy_gbps"),
        "gpu_name": gpu.get("name"),
        "gpu_uuid": smi.get("uuid"),
        "driver_version": smi.get("driver_version"),
        "vram_mb": gpu.get("vram_mb"),
        "result": out,
        "raw": _trim_job(job, job_list_item),
    }


def _trim_job(job: dict[str, Any], item: dict[str, Any] | None) -> dict[str, Any]:
    """The job record without the definition (it holds the whole script) and with logs cut short."""
    keep = {k: v for k, v in job.items() if k not in ("jobDefinition", "jobResult")}
    jr = job.get("jobResult") or {}
    keep["jobResult"] = {
        "status": jr.get("status"),
        "startTime": jr.get("startTime"),
        "endTime": jr.get("endTime"),
        "errors": jr.get("errors"),
        "opStates": [
            {
                **{k: op.get(k) for k in ("operationId", "status", "startTime", "endTime", "exitCode", "error")},
                "diagnostics": op.get("diagnostics"),
                "logs_tail": [str(e.get("log") or "")[-500:] for e in (op.get("logs") or [])[-20:]],
            }
            for op in jr.get("opStates") or []
        ],
    }
    keep["list_item"] = item
    return keep


# ---------------------------------------------------------------------------- one probe


@dataclass
class Clock:
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


def run_probe(
    api: Nosana,
    market: dict[str, Any],
    definition: dict[str, Any],
    *,
    timeout_min: int = TIMEOUT_MIN,
    queue_limit_s: float = QUEUE_LIMIT_S,
    clock: Clock = Clock(),
    log: Callable[[str], None] = lambda m: print(m, file=sys.stderr, flush=True),
) -> dict[str, Any]:
    price = buyer_price(market)
    slug = market.get("slug") or market["address"]
    submitted = datetime.now(timezone.utc)
    t0 = clock.now()
    dep_id = item = job = outcome = failure = None
    try:
        dep = api.create(
            {
                "name": f"gyrex-probe-{slug}-{submitted:%Y%m%dT%H%M%S}",
                "market": market["address"],
                "replicas": 1,
                "timeout": timeout_min,
                "strategy": "SIMPLE",
                "autostart": True,
                "job_definition": definition,
            }
        )
        dep_id = dep["id"]
        log(f"{slug}: deployment {dep_id} created")
        run_started: float | None = None
        while True:
            clock.sleep(POLL_S)
            jobs = api.jobs(dep_id)
            item = jobs[-1] if jobs else None
            if item:
                job = api.job(dep_id, item["job"])
                state = state_name(job.get("state") or item.get("state"))
                if state != "QUEUED" and run_started is None:
                    run_started = clock.now()
                    log(f"{slug}: job {item['job']} running on {job.get('node') or item.get('node')}")
                if state in TERMINAL:
                    # Results can land a little after the state flips; give them one more poll.
                    if not job.get("jobResult"):
                        clock.sleep(POLL_S)
                        job = api.job(dep_id, item["job"])
                    break
            elapsed = clock.now() - t0
            if run_started is None and elapsed > queue_limit_s:
                outcome, failure = "queue_timeout", f"not picked up in {queue_limit_s / 60:.0f} min"
                break
            if run_started is not None and clock.now() - run_started > timeout_min * 60 + 300:
                outcome, failure = "run_timeout", f"still running {timeout_min + 5} min after start"
                break
    except NosanaError as e:
        # Before the deployment exists nothing ran; after, the job's own state decides the status.
        outcome = "submit_error" if dep_id is None else None
        failure = f"api_error: {e}"[:300]
    finally:
        if dep_id:
            for action in (api.stop, api.archive):
                try:
                    action(dep_id)
                except NosanaError as e:
                    log(f"{slug}: {action.__name__} {dep_id} failed: {e}")
    row = build_row(
        market=market, price=price, submitted_at=submitted, deployment_id=dep_id,
        job_list_item=item, job=job, outcome=outcome, failure=failure, timeout_min=timeout_min,
    )
    log(f"{slug}: {row['status']}" + (f" ({row['failure']})" if row["failure"] else "")
        + (f", {row['tflops_median']} TFLOPS" if row["tflops_median"] else ""))
    return row


def store(db, row: dict[str, Any]) -> None:
    cols = [k for k in row]
    db.query(
        f"insert into probe_runs ({', '.join(cols)}) "
        f"select {', '.join(cols)} from jsonb_populate_record(null::probe_runs, $1::jsonb)",
        [json.dumps(row, default=str)],
    )


# ---------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gyrex-probe", description=__doc__.split("\n")[0])
    ap.add_argument("--market", action="append", help="market slug (repeatable); default: Premium and Community 4090")
    ap.add_argument("--check", action="store_true", help="validate and show the plan; spends nothing")
    args = ap.parse_args(argv)

    api = Nosana(os.environ.get("NOSANA_API_KEY", ""))
    slugs = args.market or list(DEFAULT_MARKETS)
    by_slug = {m.get("slug"): m for m in api.markets()}
    missing = [s for s in slugs if s not in by_slug]
    if missing:
        print(f"unknown market(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    markets = [by_slug[s] for s in slugs]

    definition = job_definition()
    errors = api.validate(definition)
    if errors:
        print("Nosana rejects the job definition:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 2

    from gyrex.neon_http import NeonHttp

    db = NeonHttp(os.environ["DATABASE_URL"])
    caps = [cost_cap(buyer_price(m)) for m in markets]
    weekly_cap = float(os.environ.get("PROBE_WEEKLY_CAP_USD", "5"))
    spent, credits = spent_last_7d(db), api.credits()
    for m, c in zip(markets, caps):
        print(f"{m['slug']:24} {m.get('type'):10} ${buyer_price(m):.3f}/hr  at most ${c:.4f} ({TIMEOUT_MIN} min)")
    print(f"budget: ${spent:.4f} of ${weekly_cap:.2f} used in 7 days; credits ${credits:.4f}; image {IMAGE}")
    try:
        check_budget(spent, caps, weekly_cap, credits)
    except BudgetError as e:
        print(f"not running: {e}", file=sys.stderr)
        return 3
    if args.check:
        print("job definition valid; --check spends nothing")
        return 0

    with ThreadPoolExecutor(len(markets)) as pool:
        rows = list(pool.map(lambda m: run_probe(api, m, definition), markets))
    for row in rows:
        store(db, row)
    print(json.dumps([{k: r[k] for k in ("market_slug", "status", "failure", "queue_s", "startup_s",
                                         "total_s", "tflops_median", "copy_gbps", "cost_usd")} for r in rows],
                     indent=2, default=str))
    return 0 if all(r["status"] == "ok" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
