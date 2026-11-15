import { NODE_BUOYS, nodePhotos, photosConfigured } from "@/lib/photos";
import { samples } from "@/lib/readings";

// A hardware buoy's raw samples and its camera's newest photos, from the bucket: GET ?buoy=1&minutes=30
const PHOTOS = 12;

export async function GET(request: Request) {
  if (!photosConfigured()) return Response.json({ error: "IMAGES_BUCKET is not set" }, { status: 503 });
  const q = new URL(request.url).searchParams;
  const buoy = Number(q.get("buoy") ?? 1);
  const node = Object.keys(NODE_BUOYS).find((n) => NODE_BUOYS[n] === buoy);
  if (!node) return Response.json({ error: `buoy ${buoy} has no hardware node` }, { status: 404 });
  const minutes = Math.min(Math.max(Number(q.get("minutes")) || 30, 1), 180);
  try {
    const buoys = Object.values(NODE_BUOYS).sort((a, b) => a - b);
    const [stream, photos] = await Promise.all([samples(node, minutes), nodePhotos(node, PHOTOS)]);
    return Response.json({ buoy, node, buoys, samples: stream, photos });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 });
  }
}
