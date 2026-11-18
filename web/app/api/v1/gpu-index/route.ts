import { latestSeen, REVALIDATE_SECONDS } from "@/lib/data";
import { getIndex } from "@/lib/load";

export const revalidate = 300;

/** Public read-only API: GET /api/v1/gpu-index -> { source, updated_at, rows: [...] } */
export async function GET() {
  try {
    const { rows, source } = await getIndex();
    return Response.json(
      { source, updated_at: latestSeen(rows), unit: "USD per GPU-hour", rows },
      {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": `public, s-maxage=${REVALIDATE_SECONDS}, stale-while-revalidate=600`,
        },
      },
    );
  } catch (e) {
    console.error(e);
    return Response.json({ error: "index temporarily unavailable" }, { status: 503 });
  }
}
