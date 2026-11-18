"""Minimal client for Neon's SQL-over-HTTP endpoint (the transport @neondatabase/serverless uses).

One HTTPS POST per query, or per transaction batch, so it works anywhere outbound HTTPS does:
GitHub Actions, sandboxes with an HTTPS-only proxy, serverless runtimes. No libpq needed.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests

TIMEOUT = 120


class NeonHttpError(RuntimeError):
    pass


def sql_endpoint(database_url: str) -> str:
    """postgresql://u:p@ep-x-pooler.c-8.us-east-2.aws.neon.tech/db -> https://api.c-8.us-east-2.aws.neon.tech/sql"""
    host = urlparse(database_url).hostname or ""
    if not host.endswith(".neon.tech"):
        raise ValueError("not a Neon connection string")
    return "https://" + re.sub(r"^[^.]+\.", "api.", host) + "/sql"


class NeonHttp:
    def __init__(self, database_url: str):
        self.url = sql_endpoint(database_url)
        self.s = requests.Session()
        self.s.headers.update({"Neon-Connection-String": database_url, "Content-Type": "application/json"})

    def _post(self, body: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        for attempt in range(4):
            try:
                r = self.s.post(self.url, data=json.dumps(body, default=str), headers=headers, timeout=TIMEOUT)
            except requests.ConnectionError:
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
                continue
            if r.status_code < 500:
                break
            time.sleep(2**attempt)  # 5xx: compute waking from scale-to-zero, retry
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except ValueError:
                msg = r.text
            raise NeonHttpError(f"{r.status_code}: {msg[:500]}")
        return r.json()

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        return self._post({"query": sql, "params": params or []})["rows"]

    def transaction(self, queries: list[tuple[str, list[Any]]]) -> list[list[dict[str, Any]]]:
        """Run statements in one transaction: all commit or none do."""
        res = self._post(
            {"queries": [{"query": q, "params": p} for q, p in queries]},
            headers={"Neon-Batch-Isolation-Level": "ReadCommitted"},
        )
        return [r["rows"] for r in res["results"]]
