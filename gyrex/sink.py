"""Hand offers to the database write path.

Writes go through `gyrex-ingest` (Rust, see ingest/): it validates each row, writes batches
through `ingest_offers`, then runs `close_run` for providers whose list arrived complete. All
change detection stays in the database, so a price_changes row appears only when a price moved.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from gyrex.models import Offer
from gyrex.neon_http import NeonHttp

log = logging.getLogger(__name__)
LOCAL_BUILD = Path(__file__).resolve().parent.parent / "ingest" / "target" / "release" / "gyrex-ingest"
INGEST_TIMEOUT = 600


class IngestError(RuntimeError):
    pass


def ingest_binary() -> str:
    """GYREX_INGEST_BIN, else the local release build, else gyrex-ingest on PATH.

    There is deliberately no Python fallback: a second write path would skip the row checks.
    """
    configured = os.environ.get("GYREX_INGEST_BIN")
    found = configured or (str(LOCAL_BUILD) if LOCAL_BUILD.is_file() else shutil.which("gyrex-ingest"))
    if not found or not os.access(found, os.X_OK):
        raise IngestError(
            "gyrex-ingest not found: run `cargo build --release --manifest-path ingest/Cargo.toml`"
            " or set GYREX_INGEST_BIN"
        )
    return found


def to_ndjson(offers: list[Offer]) -> str:
    return "".join(json.dumps(o.to_row(), default=str) + "\n" for o in offers)


class NeonSink:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.binary = ingest_binary()
        self.db = NeonHttp(database_url)

    def ingest(self, source: str, offers: list[Offer]) -> dict[str, Any]:
        done = subprocess.run(
            [self.binary, "--source", source],
            input=to_ndjson(offers),
            capture_output=True,
            text=True,
            timeout=INGEST_TIMEOUT,
            env={**os.environ, "DATABASE_URL": self.database_url},
        )
        try:
            summary = json.loads(done.stdout)
        except ValueError:
            raise IngestError(f"gyrex-ingest exited {done.returncode}: {done.stderr[:500]}") from None
        if done.returncode != 0 or not summary.get("ok"):
            raise IngestError(f"{summary.get('error')} ({summary.get('upserted', 0)} offers already written)")
        return summary

    def log_run(self, run: dict[str, Any]) -> None:
        self.db.query(
            "insert into collector_runs (source, status, started_at, offers, price_changes, deactivated,"
            " errors, unmapped_gpus) values ($1, $2, $3::timestamptz, $4, $5, $6, $7::jsonb, $8::jsonb)",
            [
                run["source"], run["status"], run["started_at"], run["offers"], run["price_changes"],
                run.get("deactivated", 0),
                json.dumps(run["errors"]) if run.get("errors") else None,
                json.dumps(run["unmapped_gpus"]) if run.get("unmapped_gpus") else None,
            ],
        )


class DryRunSink:
    """Writes JSON files instead of the database. Rows are exactly what gyrex-ingest receives."""

    def __init__(self, out_dir: str | None):
        self.out = Path(out_dir) if out_dir else None
        if self.out:
            self.out.mkdir(parents=True, exist_ok=True)

    def ingest(self, source: str, offers: list[Offer]) -> dict[str, Any]:
        if self.out:
            (self.out / f"{source}_offers.json").write_text(
                json.dumps([o.to_row() for o in offers], indent=1, default=str)
            )
        return {"upserted": len(offers), "price_changes": 0, "deactivated": 0}

    def log_run(self, run: dict[str, Any]) -> None:
        log.info("run: %s", json.dumps(run, default=str))
