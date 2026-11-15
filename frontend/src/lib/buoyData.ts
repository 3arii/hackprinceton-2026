// Simulated buoy readings. There is no sensor feed yet, so each value is a smooth,
// deterministic function of time: the same buoy shows the same reading in every
// window and across reopens. Baselines are plausible for a freshwater reservoir in
// late autumn.

export type MetricKey = "temperature" | "microplastics" | "salinity" | "turbidity";

export type Metric = {
  key: MetricKey;
  label: string;
  unit: string;
  decimals: number;
};

export const METRICS: Metric[] = [
  { key: "temperature", label: "Water temperature", unit: "°C", decimals: 1 },
  { key: "microplastics", label: "Microplastics", unit: "particles/L", decimals: 1 },
  { key: "salinity", label: "Salinity", unit: "PSU", decimals: 2 },
  { key: "turbidity", label: "Turbidity", unit: "NTU", decimals: 1 },
];

// The hardware's three sensors. Conductivity and the turbidity photodiode aren't calibrated yet, so
// they read in the sensor's own millivolts instead of PSU and NTU.
export const REAL_METRICS: Record<"temperature" | "salinity" | "turbidity", Metric> = {
  temperature: METRICS[0],
  salinity: { key: "salinity", label: "Salinity", unit: "mV", decimals: 0 },
  turbidity: { key: "turbidity", label: "Turbidity", unit: "mV", decimals: 0 },
};

type Profile = Record<MetricKey, number> & { seed: number };

// Buoy 1 sits off the stream mouth by Broadmead, so it runs murkier and dirtier
const PROFILES: Profile[] = [
  { seed: 1.3, temperature: 9.4, microplastics: 6.2, salinity: 0.17, turbidity: 13.5 },
  { seed: 2.9, temperature: 9.9, microplastics: 4.1, salinity: 0.14, turbidity: 8.2 },
  { seed: 4.4, temperature: 10.2, microplastics: 3.3, salinity: 0.13, turbidity: 6.9 },
  { seed: 6.1, temperature: 9.7, microplastics: 4.8, salinity: 0.15, turbidity: 10.4 },
];

const HOUR = 3_600_000;

// Smooth drift built from a few slow sine waves; roughly -1..1
function drift(hours: number, seed: number) {
  return (
    Math.sin(hours / 5.3 + seed) * 0.5 +
    Math.sin(hours / 2.1 + seed * 1.7) * 0.32 +
    Math.sin(hours / 0.83 + seed * 2.3) * 0.18
  );
}

export function readingAt(buoy: number, metric: MetricKey, time: number) {
  const p = PROFILES[buoy % PROFILES.length];
  const hours = time / HOUR;
  const seed = p.seed + METRICS.findIndex((m) => m.key === metric) * 0.9;
  switch (metric) {
    case "temperature": {
      const d = new Date(time);
      const hourOfDay = d.getHours() + d.getMinutes() / 60;
      // Surface water peaks mid-afternoon
      return p.temperature + 0.45 * Math.sin((2 * Math.PI * (hourOfDay - 9)) / 24) + 0.15 * drift(hours, seed);
    }
    case "microplastics":
      return Math.max(0, p.microplastics + 0.7 * drift(hours, seed));
    case "salinity":
      return Math.max(0, p.salinity + 0.008 * drift(hours, seed));
    case "turbidity":
      return Math.max(0, p.turbidity + 1.8 * drift(hours, seed));
  }
}

/** Readings over the last 24 hours, every 30 minutes, ending at `now`. */
export function history(buoy: number, metric: MetricKey, now: number) {
  return Array.from({ length: 49 }, (_, i) => {
    const time = now - (48 - i) * 0.5 * HOUR;
    return { time, value: readingAt(buoy, metric, time) };
  });
}

/** Values typed into a buoy in debug mode, standing in for its latest reading. */
export type Overrides = Partial<Record<number, Partial<Record<MetricKey, number>>>>;

/** `history`, with the latest point replaced by a debug override when there is one. */
export function seriesFor(buoy: number, metric: MetricKey, now: number, overrides?: Overrides) {
  const points = history(buoy, metric, now);
  const v = overrides?.[buoy]?.[metric];
  if (v !== undefined) points[points.length - 1] = { ...points[points.length - 1], value: v };
  return points;
}

/**
 * Environment index, 0–100, higher is healthier. Null when no buoy has reported.
 * Placeholder until the ML model is trained. It scores only what the hardware measures in real
 * units: water temperature against a healthy reference for a freshwater lake, and whether the
 * cameras see plastic on their meshes. Salinity and turbidity join once they are calibrated.
 */
export function environmentIndex(avg: { temperature?: number; plastic?: boolean }) {
  const clamp = (v: number) => Math.min(100, Math.max(0, v));
  const parts: [number, number][] = [];
  if (avg.temperature !== undefined) parts.push([0.2, clamp(100 - Math.abs(avg.temperature - 10) * 12)]);
  if (avg.plastic !== undefined) parts.push([0.35, avg.plastic ? 20 : 100]);
  if (!parts.length) return null;
  const weight = parts.reduce((sum, [w]) => sum + w, 0);
  return Math.round(clamp(parts.reduce((sum, [w, v]) => sum + w * v, 0) / weight));
}
