"""Write offers to Neon Postgres over its HTTP SQL endpoint.

All change detection happens in the database (`ingest_offers`), so a run only writes a
price_changes row when a price actually moved. After a run, `close_run` marks offers that a
provider stopped listing as inactive, so the index only reflects each provider's latest run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from gyrex.models import Offer
from gyrex.neon_http import NeonHttp

log = logging.getLogger(__name__)
CHUNK = 1000


class NeonSink:
    def __init__(self, database_url: str):
        self.db = NeonHttp(database_url)

    def begin_run(self) -> str:
        """Database clock at run start; close_run compares last_seen against it."""
        return self.db.query("select now()::text as t")[0]["t"]

    def write(self, source: str, offers: list[Offer]) -> dict[str, int]:
        totals = {"upserted": 0, "price_changes": 0}
        rows = [o.to_row() for o in offers]
        for i in range(0, len(rows), CHUNK):
            res = self.db.query(
                "select ingest_offers($1, $2::jsonb) as r", [source, json.dumps(rows[i : i + CHUNK], default=str)]
            )[0]["r"]
            for k in totals:
                totals[k] += int(res.get(k, 0))
        return totals

    def close_run(self, source: str, providers: list[str], started_at: str) -> int:
        if not providers:
            return 0
        return int(
            self.db.query(
                "select close_run($1, $2::text[], $3::timestamptz) as n", [source, providers, started_at]
            )[0]["n"]
        )

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
    """Writes JSON files instead of the database. Output is exactly what ingest_offers receives."""

    def __init__(self, out_dir: str | None):
        self.out = Path(out_dir) if out_dir else None
        if self.out:
            self.out.mkdir(parents=True, exist_ok=True)

    def begin_run(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def write(self, source: str, offers: list[Offer]) -> dict[str, int]:
        if self.out:
            (self.out / f"{source}_offers.json").write_text(
                json.dumps([o.to_row() for o in offers], indent=1, default=str)
            )
        return {"upserted": len(offers), "price_changes": 0}

    def close_run(self, source: str, providers: list[str], started_at: str) -> int:
        return 0

    def log_run(self, run: dict[str, Any]) -> None:
        log.info("run: %s", json.dumps(run, default=str))
