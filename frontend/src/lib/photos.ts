import { GetObjectCommand, ListObjectsV2Command, S3Client } from "@aws-sdk/client-s3";

// Buoy camera photos, uploaded by the Pi gateway to images/<node>/yyyy/mm/dd/<file>.jpg. The stack
// labels each one (clean, microplastic or blocked) with the camera's clean-mesh model and turns the
// labels into microplastics per litre; both come from the dashboard API.

const BUCKET = process.env.IMAGES_BUCKET;
const PREFIX = "images/";
// Mesh node id -> dashboard buoy number (same map as the buoy-monitor stack's NodeBuoys)
export const NODE_BUOYS: Record<string, number> = JSON.parse(process.env.NODE_BUOYS ?? '{"2884855a1da8":1,"2884855a1cf0":2}');

let client: S3Client | null = null;
const s3 = () => (client ??= new S3Client({ region: process.env.AWS_REGION ?? "us-east-2" }));

/** Something on the mesh, in the photo's pixels. strength is the score over its threshold. */
export type Spot = { x: number; y: number; bbox: [number, number, number, number]; area_px: number; strength: number };
export type Check = {
  label: "clean" | "microplastic" | "blocked";
  reason: string | null; // why it's blocked
  score: number | null; // 1 or more is flagged
  spots: Spot[];
  width: number;
  height: number;
};
export type Photo = { key: string; node: string; buoy: number | null; time: string; size: number; check: Check | null };
/** Particles caught on the mesh over the water filtered in the camera's newest run of photos. */
export type Estimate = {
  buoy: number;
  node: string;
  per_liter: number;
  ci95: [number, number];
  caught: number;
  liters: number;
  minutes: number;
  until: string;
  flow_lpm: number;
  view_fraction: number;
  measured: boolean; // false while flow and view fraction are guesses
};

export const photosConfigured = () => !!BUCKET;

/** GET from the dashboard API, or the fallback if it's down or unset: photos still show, unlabelled. */
async function api<T>(path: string, fallback: T): Promise<T> {
  const base = process.env.BUOY_API_URL;
  if (!base) return fallback;
  try {
    const res = await fetch(`${base.replace(/\/$/, "")}${path}`, { cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return await res.json();
  } catch (e) {
    console.warn(`dashboard API ${path} unavailable:`, e);
    return fallback;
  }
}

/** Labels by photo key. */
async function checks(): Promise<Map<string, Check>> {
  const items = await api<(Check & { key: string })[]>("/photos?limit=500", []);
  return new Map(items.map(({ key, label, reason, score, spots, width, height }) => [key, { label, reason, score, spots, width, height }]));
}

/** Every photo in the bucket, newest first. */
async function allPhotos() {
  const all: Omit<Photo, "check">[] = [];
  let token: string | undefined;
  do {
    const res = await s3().send(new ListObjectsV2Command({ Bucket: BUCKET, Prefix: PREFIX, ContinuationToken: token }));
    for (const o of res.Contents ?? []) {
      if (!o.Key?.toLowerCase().endsWith(".jpg") || !o.LastModified) continue;
      const node = o.Key.split("/")[1];
      all.push({ key: o.Key, node, buoy: NODE_BUOYS[node] ?? null, time: o.LastModified.toISOString(), size: o.Size ?? 0 });
    }
    token = res.IsTruncated ? res.NextContinuationToken : undefined;
  } while (token && all.length < 20_000);
  return all.sort((a, b) => b.time.localeCompare(a.time));
}

/** The newest `limit` photos across all buoys, newest first, and how many there are in total. */
export async function listPhotos(limit: number) {
  const labels = checks();
  const estimates = api<Estimate[]>("/camera", []);
  const all = await allPhotos();
  const byKey = await labels;
  const photos: Photo[] = all.slice(0, limit).map((p) => ({ ...p, check: byKey.get(p.key) ?? null }));
  return { total: all.length, photos, estimates: await estimates };
}

/** One camera's newest `limit` photos with their labels, newest first. */
export async function nodePhotos(node: string, limit: number): Promise<Photo[]> {
  const labels = checks();
  const mine = (await allPhotos()).filter((p) => p.node === node).slice(0, limit);
  const byKey = await labels;
  return mine.map((p) => ({ ...p, check: byKey.get(p.key) ?? null }));
}

/** Each camera's newest photo and its label, by node. */
export async function latestPhotos() {
  const labels = checks();
  const newest = new Map<string, { key: string; time: string; check: Check | null }>();
  for (const p of await allPhotos()) if (!newest.has(p.node)) newest.set(p.node, { key: p.key, time: p.time, check: null });
  const byKey = await labels;
  for (const p of newest.values()) p.check = byKey.get(p.key) ?? null;
  return newest;
}

/** Only photos are served: the bucket also holds readings and server builds. */
export const isPhotoKey = (key: string) => /^images\/[\w-]+\/\d{4}\/\d{2}\/\d{2}\/[\w.-]+\.jpe?g$/i.test(key);

export async function getPhoto(key: string) {
  const res = await s3().send(new GetObjectCommand({ Bucket: BUCKET, Key: key }));
  return { body: res.Body!.transformToWebStream(), type: res.ContentType ?? "image/jpeg" };
}
