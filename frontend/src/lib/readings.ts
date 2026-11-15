import { GetObjectCommand, ListObjectsV2Command, S3Client } from "@aws-sdk/client-s3";
import { NODE_BUOYS, latestPhotos, type Check } from "@/lib/photos";

// Hardware buoy readings, uploaded by the Pi gateway to readings/<node>/yyyy/mm/dd/<stamp>.jsonl:
// about a minute of samples per file, one JSON line each (node, seq, temp_c, light_mv, ambient_mv,
// cond_mv, received_at). Each file becomes one point per metric, the median of its samples.
//
// Salinity and turbidity are the sensors' raw millivolts: neither is calibrated to PSU or NTU yet.

const BUCKET = process.env.IMAGES_BUCKET;
const FILES = 120; // newest files read per buoy, about two hours of running
const TEMP_RANGE = [-2, 45]; // outside this it's a failed read, not water (same as the stack)
const ADC_MAX = 3300; // mV; the firmware reports more than this when the read failed

let client: S3Client | null = null;
const s3 = () => (client ??= new S3Client({ region: process.env.AWS_REGION ?? "us-east-2" }));

export type Point = { time: number; value: number };
export type RealMetric = "temperature" | "salinity" | "turbidity";
export type BuoyData = {
  buoy: number; // 1-based, as everywhere outside the map
  node: string;
  lastReading: string | null; // when the newest sample arrived
  series: Record<RealMetric, Point[]>;
  photo: { key: string; time: string; check: Check | null } | null;
};
export type Fleet = { fetchedAt: string; buoys: BuoyData[] };

type Minute = { time: number } & Partial<Record<RealMetric, number>>;
type Sample = { temp_c?: number | null; light_mv?: number | null; ambient_mv?: number | null; cond_mv?: number | null; received_at?: number };

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function median(values: number[]) {
  if (!values.length) return undefined;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** One file of samples as a single point per metric. Null if it holds no timed sample. */
export function summarize(body: string): Minute | null {
  const samples: Sample[] = [];
  for (const line of body.split("\n")) {
    if (!line.trim()) continue;
    try {
      samples.push(JSON.parse(line));
    } catch {
      // a torn line: skip it
    }
  }
  const times = samples.map((s) => s.received_at).filter(num);
  if (!times.length) return null;
  return {
    time: Math.max(...times) * 1000,
    temperature: median(samples.map((s) => s.temp_c).filter(num).filter((t) => t >= TEMP_RANGE[0] && t <= TEMP_RANGE[1])),
    salinity: median(samples.map((s) => s.cond_mv).filter(num).filter((v) => v >= 0 && v <= ADC_MAX)),
    turbidity: median(
      samples.filter((s) => num(s.light_mv) && num(s.ambient_mv)).map((s) => (s.light_mv as number) - (s.ambient_mv as number)),
    ),
  };
}

// A file never changes once uploaded, so each is read once
const minutes = new Map<string, Minute | null>();

async function minute(key: string) {
  if (!minutes.has(key)) {
    const res = await s3().send(new GetObjectCommand({ Bucket: BUCKET, Key: key }));
    minutes.set(key, summarize(await res.Body!.transformToString()));
  }
  return minutes.get(key)!;
}

/** A node's newest reading files, oldest first. Keys sort by time. */
async function readingKeys(node: string) {
  const keys: string[] = [];
  let token: string | undefined;
  do {
    const res = await s3().send(new ListObjectsV2Command({ Bucket: BUCKET, Prefix: `readings/${node}/`, ContinuationToken: token }));
    for (const o of res.Contents ?? []) if (o.Key?.endsWith(".jsonl")) keys.push(o.Key);
    token = res.IsTruncated ? res.NextContinuationToken : undefined;
  } while (token);
  return keys.sort().slice(-FILES);
}

async function buoyData(node: string, buoy: number, photo: BuoyData["photo"]): Promise<BuoyData> {
  const all = (await Promise.all((await readingKeys(node)).map(minute))).filter((m): m is Minute => !!m);
  all.sort((a, b) => a.time - b.time);
  const series = (k: RealMetric) => all.filter((m) => num(m[k])).map((m) => ({ time: m.time, value: m[k] as number }));
  return {
    buoy,
    node,
    lastReading: all.length ? new Date(all[all.length - 1].time).toISOString() : null,
    series: { temperature: series("temperature"), salinity: series("salinity"), turbidity: series("turbidity") },
    photo,
  };
}

let cached: { at: number; fleet: Promise<Fleet> } | null = null;

/** Every hardware buoy's readings and newest photo. Shared between viewers for a few seconds. */
export function fleet(): Promise<Fleet> {
  if (cached && Date.now() - cached.at < 8000) return cached.fleet;
  const load = (async () => {
    const photos = await latestPhotos();
    const buoys = await Promise.all(Object.entries(NODE_BUOYS).map(([node, b]) => buoyData(node, b, photos.get(node) ?? null)));
    return { fetchedAt: new Date().toISOString(), buoys: buoys.sort((a, b) => a.buoy - b.buoy) };
  })();
  cached = { at: Date.now(), fleet: load };
  load.catch(() => (cached = null));
  return load;
}
