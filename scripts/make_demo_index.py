"""Build web/demo/gpu_index.json: the same aggregation as the gpu_index view, computed from a
live gpuhunt pull, so the page can run locally without Supabase. Centralized clouds only:
no Nosana numbers are invented for the demo."""

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from gyrex.adapters import gpuhunt_adapter
from gyrex.models import dedupe

offers, errors = gpuhunt_adapter.fetch()
if errors:
    print("provider errors:", errors, file=sys.stderr)
groups = defaultdict(list)
for o in dedupe(offers):
    if o.gpu_model:
        groups[(o.gpu_model, o.vram_gb, o.offer_type)].append(o)

now = datetime.now(timezone.utc).isoformat()
rows = []
for (model, vram, typ), os_ in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0, kv[0][2])):
    per_gpu = [o.price_usd_hr / o.gpu_count for o in os_]
    rows.append({
        "gpu_model": model, "vram_gb": vram, "offer_type": typ,
        "offers": len(os_), "providers": len({o.provider for o in os_}),
        "min_usd_hr": round(min(per_gpu), 4), "median_usd_hr": round(statistics.median(per_gpu), 4),
        "max_usd_hr": round(max(per_gpu), 4), "last_seen": now,
    })
out = Path(__file__).resolve().parent.parent / "web" / "demo" / "gpu_index.json"
out.write_text(json.dumps({"generated_at": now, "rows": rows}, indent=1))
print(f"wrote {len(rows)} rows to {out}")
