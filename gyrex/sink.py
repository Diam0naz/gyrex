"""Write offers to Supabase through PostgREST (no supabase-py dependency).

All change detection happens in the database (`ingest_offers` RPC), so a run only writes a
price_changes row when a price actually moved. That keeps the free tier's 500 MB for years
instead of weeks.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from gyrex.models import Offer

log = logging.getLogger(__name__)
CHUNK = 1000


class SupabaseSink:
    def __init__(self, url: str, service_key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.s = requests.Session()
        self.s.headers.update(
            {
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            }
        )

    def _post(self, path: str, body: Any, prefer: str | None = None) -> Any:
        headers = {"Prefer": prefer} if prefer else {}
        for attempt in range(4):
            r = self.s.post(f"{self.base}{path}", data=json.dumps(body), headers=headers, timeout=60)
            if r.status_code < 500:
                break
            time.sleep(2**attempt)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None

    def write(self, source: str, offers: list[Offer]) -> dict[str, int]:
        totals = {"upserted": 0, "price_changes": 0}
        rows = [o.to_row() for o in offers]
        for i in range(0, len(rows), CHUNK):
            res = self._post("/rpc/ingest_offers", {"p_source": source, "p_offers": rows[i : i + CHUNK]})
            for k in totals:
                totals[k] += int((res or {}).get(k, 0))
        return totals

    def log_run(self, run: dict[str, Any]) -> None:
        self._post("/collector_runs", run, prefer="return=minimal")


class DryRunSink:
    """Writes JSON files instead of the database. Output is exactly what the RPC receives."""

    def __init__(self, out_dir: str | None):
        self.out = Path(out_dir) if out_dir else None
        if self.out:
            self.out.mkdir(parents=True, exist_ok=True)

    def write(self, source: str, offers: list[Offer]) -> dict[str, int]:
        if self.out:
            (self.out / f"{source}_offers.json").write_text(
                json.dumps([o.to_row() for o in offers], indent=1, default=str)
            )
        return {"upserted": len(offers), "price_changes": 0}

    def log_run(self, run: dict[str, Any]) -> None:
        log.info("run: %s", json.dumps(run, default=str))
