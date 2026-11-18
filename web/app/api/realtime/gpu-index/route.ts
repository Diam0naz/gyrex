import { sealGpuIndex } from "@/lib/realtime";

export const dynamic = "force-dynamic";

/**
 * Renews the sealed live query for the index (sealed queries expire after ~60 s).
 * The index is public, so there is no user to authorize: this always seals the same
 * parameterless query, and the client can't change what it subscribes to.
 */
export async function POST() {
  const sealed = await sealGpuIndex();
  if (!sealed) return Response.json({ error: "realtime not configured" }, { status: 404 });
  return Response.json(sealed, { headers: { "Cache-Control": "no-store" } });
}
