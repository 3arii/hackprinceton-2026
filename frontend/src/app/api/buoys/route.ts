import { photosConfigured } from "@/lib/photos";
import { fleet } from "@/lib/readings";

// The hardware buoys' readings and newest camera photo, read from the bucket the Pi gateway uploads to
export async function GET() {
  if (!photosConfigured()) return Response.json({ error: "IMAGES_BUCKET is not set" }, { status: 503 });
  try {
    return Response.json(await fleet());
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 });
  }
}
