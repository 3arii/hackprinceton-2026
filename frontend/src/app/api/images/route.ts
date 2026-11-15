import { listPhotos, photosConfigured } from "@/lib/photos";

export async function GET(request: Request) {
  if (!photosConfigured()) return Response.json({ error: "IMAGES_BUCKET is not set" }, { status: 503 });
  const limit = Math.min(Number(new URL(request.url).searchParams.get("limit")) || 120, 500);
  try {
    return Response.json(await listPhotos(limit));
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 });
  }
}
