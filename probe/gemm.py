"""Gyrex probe v1: FP16 GEMM throughput and device-to-device bandwidth.

Runs inside the probe container (any PyTorch CUDA image). Self-contained: no downloads, no
arguments. Prints exactly one line starting with GYREX_PROBE followed by JSON, also on failure,
and exits non-zero on failure so the job's exit code agrees with the payload.

Timing: every timestamp is Unix seconds from the container's clock, so the collector can line
them up against the job's on-chain start and end.
"""

import json
import os
import subprocess
import sys
import time

PROBE_VERSION = 1
GEMM_N = 8192          # 8192^3 * 2 = 1.1 TFLOP per matmul
WARMUP = 3
SUSTAIN_S = 20.0       # long enough to see clocks drop on a hot or power-capped card
COPY_BYTES = 1 << 30   # 1 GiB device-to-device copy

out = {"probe_version": PROBE_VERSION, "t_script_start": time.time(), "ok": False}


def emit(code):
    print("GYREX_PROBE " + json.dumps(out, separators=(",", ":")), flush=True)
    sys.exit(code)


def smi():
    fields = "name,uuid,driver_version,memory.total,power.limit,temperature.gpu,clocks.max.sm"
    try:
        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20,
        )
        first = r.stdout.strip().splitlines()[0]
        return dict(zip(fields.split(","), [v.strip() for v in first.split(",")]))
    except Exception as e:  # nvidia-smi is optional: torch still identifies the GPU
        return {"error": f"{type(e).__name__}: {e}"[:200]}


try:
    import torch

    out["t_import_done"] = time.time()
    if not torch.cuda.is_available():
        out["failure"] = "cuda_unavailable"
        emit(2)

    dev = torch.device("cuda:0")
    props = torch.cuda.get_device_properties(dev)
    out["gpu"] = {
        "name": props.name,
        "vram_mb": props.total_memory // (1 << 20),
        "sm_count": props.multi_processor_count,
        "capability": f"{props.major}.{props.minor}",
        "count": torch.cuda.device_count(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
    }
    out["smi_before"] = smi()

    a = torch.randn(GEMM_N, GEMM_N, device=dev, dtype=torch.float16)
    b = torch.randn(GEMM_N, GEMM_N, device=dev, dtype=torch.float16)
    torch.cuda.synchronize()
    out["t_first_kernel"] = time.time()

    for _ in range(WARMUP):
        a @ b
    torch.cuda.synchronize()

    flop = 2 * GEMM_N**3
    start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
    samples = []  # TFLOPS per matmul over the sustain window
    t0 = time.time()
    while time.time() - t0 < SUSTAIN_S:
        start.record()
        a @ b
        end.record()
        end.synchronize()
        samples.append(flop / (start.elapsed_time(end) / 1e3) / 1e12)

    def median(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2]

    n = len(samples)
    fifth = max(1, n // 5)
    ranked = sorted(samples)
    out["gemm"] = {
        "n": GEMM_N,
        "iters": n,
        "tflops_median": round(median(samples), 2),
        "tflops_p10": round(ranked[n // 10], 2),
        "tflops_max": round(ranked[-1], 2),
        # Throttling shows as the last fifth of the window running slower than the first.
        "tflops_first_fifth": round(median(samples[:fifth]), 2),
        "tflops_last_fifth": round(median(samples[-fifth:]), 2),
    }
    del a, b
    torch.cuda.empty_cache()

    src = torch.empty(COPY_BYTES, dtype=torch.uint8, device=dev)
    dst = torch.empty_like(src)
    dst.copy_(src)
    torch.cuda.synchronize()
    reps = 10
    start.record()
    for _ in range(reps):
        dst.copy_(src)
    end.record()
    end.synchronize()
    # A copy reads and writes every byte.
    out["copy_gbps"] = round(2 * COPY_BYTES * reps / (start.elapsed_time(end) / 1e3) / 1e9, 1)
    del src, dst

    out["smi_after"] = smi()
    out["t_done"] = time.time()
    out["ok"] = True
    emit(0)
except SystemExit:
    raise
except BaseException as e:
    out["failure"] = "oom" if "out of memory" in str(e).lower() else "exception"
    out["error"] = f"{type(e).__name__}: {e}"[:500]
    out["t_done"] = time.time()
    emit(1)
