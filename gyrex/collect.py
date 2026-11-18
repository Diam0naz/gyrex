"""Collector entry point: run every adapter, write to Neon Postgres (or JSON with --dry-run).

    gyrex-collect                       # all sources -> Neon (DATABASE_URL)
    gyrex-collect --dry-run --dump out  # no DB; writes out/<source>_offers.json + raw Nosana payload
    gyrex-collect --sources nosana
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from gyrex.adapters import gpuhunt_adapter, nosana
from gyrex.models import dedupe
from gyrex.sink import DryRunSink, IngestError, NeonSink

log = logging.getLogger("gyrex")
SOURCES = ("gpuhunt", "nosana")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(sources: list[str], sink, dump_dir: str | None = None) -> int:
    """Returns the number of sources that failed outright."""
    failed = 0
    for source in sources:
        started = _now()
        raw: dict = {}
        deactivated = 0
        try:
            if source == "gpuhunt":
                offers, errors = gpuhunt_adapter.fetch()
            else:
                offers, errors = nosana.fetch(dump=raw if dump_dir else None)
            offers = dedupe(offers)
            # Offers are retired only for providers that delivered a complete list this run: one
            # that errored, came back empty or had a row rejected keeps its last known offers.
            result = sink.ingest(source, offers) if offers else {"upserted": 0, "price_changes": 0}
            started = result.get("started_at") or started  # database clock, when there is one
            deactivated = result.get("deactivated", 0)
            if result.get("rejected"):
                errors["rejected_rows"] = {
                    "count": result["rejected"],
                    "reasons": result.get("reject_reasons"),
                    "samples": result.get("reject_samples"),
                    "held_providers": result.get("held_providers"),
                }
            status = "ok" if not errors else ("partial" if offers else "failed")
        except Exception as e:
            log.exception("%s failed", source)
            offers, errors, result, status = [], {"fatal": f"{type(e).__name__}: {e}"[:500]}, {}, "failed"

        if dump_dir and raw:
            Path(dump_dir, f"{source}_raw.json").write_text(json.dumps(raw, indent=1, default=str))

        unmapped = Counter(o.gpu_raw for o in offers if not o.gpu_model)
        log.info(
            "%s: %s, %d offers, %s price changes, %d delisted%s",
            source, status, len(offers), result.get("price_changes", "?"), deactivated,
            f", unmapped GPUs: {dict(unmapped)}" if unmapped else "",
        )
        try:
            sink.log_run(
                {
                    "source": source,
                    "status": status,
                    "started_at": started,
                    "finished_at": _now(),
                    "offers": len(offers),
                    "price_changes": result.get("price_changes", 0),
                    "deactivated": deactivated,
                    "errors": errors or None,
                    "unmapped_gpus": dict(unmapped) or None,
                }
            )
        except Exception:
            log.exception("could not record run for %s", source)
        failed += status == "failed"
    return failed


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sources", default=",".join(SOURCES), help="comma-separated: gpuhunt,nosana")
    p.add_argument("--dry-run", action="store_true", help="don't touch the database")
    p.add_argument("--dump", metavar="DIR", help="write normalized offers and raw payloads here")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # gpuhunt logs a warning per provider it skips for missing keys; that's expected noise.
    logging.getLogger("gpuhunt").setLevel(logging.ERROR)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    if bad := [s for s in sources if s not in SOURCES]:
        p.error(f"unknown sources: {bad}")

    if args.dry_run:
        sink = DryRunSink(args.dump)
    else:
        url = os.environ.get("DATABASE_URL")
        if not url:
            p.error("set DATABASE_URL (Neon, pooled), or use --dry-run")
        try:
            sink = NeonSink(url)
        except IngestError as e:
            p.error(str(e))
        if args.dump:
            Path(args.dump).mkdir(parents=True, exist_ok=True)

    # Non-zero if any source failed outright, so a scheduled run shows up red.
    sys.exit(1 if run(sources, sink, args.dump) else 0)


if __name__ == "__main__":
    main()
