import { getPhoto, isPhotoKey, photosConfigured } from "@/lib/photos";

export async function GET(request: Request) {
  if (!photosConfigured()) return new Response(null, { status: 503 });
  const key = new URL(request.url).searchParams.get("key") ?? "";
  if (!isPhotoKey(key)) return new Response(null, { status: 404 });
  try {
    const { body, type } = await getPhoto(key);
    // A photo never changes once uploaded
    return new Response(body, { headers: { "content-type": type, "cache-control": "private, max-age=31536000, immutable" } });
  } catch {
    return new Response(null, { status: 404 });
  }
}
