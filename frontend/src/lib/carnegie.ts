import type { Point, RealMetric } from "@/lib/readings";

// REAL DATA, NOT SIMULATED: measured in Lake Carnegie (Princeton, NJ) on 2026-11-15, by buoy number.
// The dashboard shows these as the buoys' readings, in place of the hardware feed where they exist.

export type CarnegieReading = {
  microplastics: number; // particles per litre
  temperature?: number; // °C
  salinity?: number; // mV, as the buoys' conductivity sensor reads it
  turbidity?: number; // mV, as the buoys' turbidity sensor reads it
  since: string; // when the measurement began, UTC
  until: string; // when it ended, UTC
  byHand?: boolean;
};

export const CARNEGIE: Partial<Record<number, CarnegieReading>> = {
  // Buoy 1, the hardware buoy off the Lake Carnegie shore, by its camera: its first calculation from
  // the water. The camera photographed the intake mesh nine times between 11:00:20 and 11:05:14 UTC
  // (6:00 to 6:05 AM ET), the first photos after the buoy went into the lake. The stack's checker
  // (aws/vision/microplastics/mesh.py) learnt the in-water view from the first four, and no particle
  // stayed on the mesh in the rest; concentration.py turned that into particles per litre: 0 caught
  // in 4.9 L, so 0 particles/L, and at most 0.75 with 95% confidence. The litres assume 1 L/min
  // through the mesh (aws/vision/models/2884855a1da8.json): the flow wasn't measured yet. Its
  // temperature, salinity and turbidity come live from the buoy.
  1: { microplastics: 0, since: "2026-11-15T11:00:20Z", until: "2026-11-15T11:05:14Z" },

  // Buoys 2, 3 and 4: recorded by hand in Lake Carnegie by the team, reported at 6:27 AM ET.
  2: { microplastics: 0.15, temperature: 17.2, salinity: 45, turbidity: 3, since: "2026-11-15T11:27:25Z", until: "2026-11-15T11:27:25Z", byHand: true },
  3: { microplastics: 0.12, temperature: 15.4, salinity: 42, turbidity: 2, since: "2026-11-15T11:27:25Z", until: "2026-11-15T11:27:25Z", byHand: true },
  4: { microplastics: 0.17, temperature: 16.2, salinity: 43, turbidity: 6, since: "2026-11-15T11:27:25Z", until: "2026-11-15T11:27:25Z", byHand: true },
};

export type BuoyMetric = RealMetric | "microplastics";

/** A buoy's points for each metric: what was recorded in the lake (a flat line over the time it was
 * measured) where there is a recording, else its hardware feed. */
export function buoySeries(buoy: number, hardware?: Record<RealMetric, Point[]>): Record<BuoyMetric, Point[]> {
  const r = CARNEGIE[buoy];
  const flat = (v?: number) =>
    r && v !== undefined
      ? [
          { time: Date.parse(r.since), value: v },
          { time: Date.parse(r.until), value: v },
        ]
      : null;
  return {
    temperature: flat(r?.temperature) ?? hardware?.temperature ?? [],
    microplastics: flat(r?.microplastics) ?? [],
    salinity: flat(r?.salinity) ?? hardware?.salinity ?? [],
    turbidity: flat(r?.turbidity) ?? hardware?.turbidity ?? [],
  };
}

/** Enough decimals to show every value as recorded: 0, 0.15. */
export const decimalsFor = (values: number[]) =>
  Math.min(2, Math.max(0, ...values.map((v) => (String(Math.round(v * 100) / 100).split(".")[1] ?? "").length)));
