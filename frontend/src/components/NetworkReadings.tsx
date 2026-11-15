"use client";

import { useEffect, useState } from "react";
import { METRICS, REAL_METRICS, environmentIndex } from "@/lib/buoyData";
import { Reading } from "@/components/BuoyReadings";
import { decimalsFor, type BuoyMetric } from "@/lib/carnegie";
import type { Point } from "@/lib/readings";

const G = { size: 240, cx: 120, cy: 118, r: 92, sweep: 240 };

const polar = (deg: number, r: number) => {
  const a = (deg * Math.PI) / 180;
  return { x: G.cx + r * Math.sin(a), y: G.cy - r * Math.cos(a) };
};
const angleFor = (score: number) => -G.sweep / 2 + (score / 100) * G.sweep;

const start = polar(-G.sweep / 2, G.r);
const end = polar(G.sweep / 2, G.r);
const ARC = `M${start.x},${start.y} A${G.r},${G.r} 0 1 1 ${end.x},${end.y}`;

function band(score: number) {
  if (score >= 70) return { label: "Good", color: "#30d158" };
  if (score >= 40) return { label: "Fair", color: "#ffd60a" };
  return { label: "Poor", color: "#ff453a" };
}

function Gauge({ score: real }: { score: number | null }) {
  const score = real ?? 0;
  // Sweep up from zero on open, like a speedometer waking up
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(score));
    return () => cancelAnimationFrame(id);
  }, [score]);
  const { label, color } = real === null ? { label: "No data", color: "#636366" } : band(score);

  return (
    <div className="gauge">
      <svg viewBox={`0 0 ${G.size} 182`} role="img" aria-label={real === null ? "Environment index, no data yet" : `Environment index ${score} out of 100, ${label}`}>
        <path d={ARC} className="gauge-track" pathLength={100} />
        <path
          d={ARC}
          className="gauge-value"
          pathLength={100}
          style={{ stroke: color, strokeDasharray: `${shown} 100` }}
        />
        {Array.from({ length: 21 }, (_, i) => {
          const deg = angleFor(i * 5);
          const major = i % 2 === 0;
          const a = polar(deg, G.r - 14);
          const b = polar(deg, G.r - (major ? 22 : 18));
          return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={major ? "gauge-tick major" : "gauge-tick"} />;
        })}
        {[0, 50, 100].map((v) => {
          // 50 sits inside the dial; the ends sit just below the arc's tips, clear of the score
          const p = v === 50 ? polar(0, G.r - 34) : polar(angleFor(v), G.r);
          return (
            <text
              key={v}
              x={p.x}
              y={v === 50 ? p.y : p.y + 18}
              className="gauge-scale"
              textAnchor="middle"
              dominantBaseline="central"
            >
              {v}
            </text>
          );
        })}
        <g
          className="gauge-needle"
          style={{ transform: `rotate(${angleFor(shown)}deg)`, transformOrigin: `${G.cx}px ${G.cy}px` }}
        >
          <line x1={G.cx} y1={G.cy + 10} x2={G.cx} y2={G.cy - G.r + 18} />
        </g>
        <circle cx={G.cx} cy={G.cy} r={6} className="gauge-hub" />
        <text x={G.cx} y={G.cy + 44} className="gauge-score" textAnchor="middle">
          {real ?? "\u2014"}
          <tspan className="gauge-outof" dx={3}>
            /100
          </tspan>
        </text>
      </svg>
      <div className="gauge-caption">
        <span className="gauge-name">Environment index</span>
        <span className="gauge-band">
          <span className="gauge-dot" style={{ background: color }} />
          {label}
        </span>
      </div>
    </div>
  );
}

/** A metric averaged across the buoys that report it, over the stretch they all cover. */
function average(buoys: Record<BuoyMetric, Point[]>[], key: BuoyMetric): Point[] {
  const series = buoys.map((b) => b[key]).filter((p) => p.length);
  if (!series.length) return [];
  const n = Math.min(...series.map((p) => p.length));
  const tails = series.map((p) => p.slice(-n));
  return tails[0].map((p, i) => ({ time: p.time, value: tails.reduce((sum, t) => sum + t[i].value, 0) / tails.length }));
}

/** Every buoy's readings (each recorded in the lake or live, see lib/carnegie.ts), averaged. */
export default function NetworkReadings({ buoys, loading }: { buoys: Record<BuoyMetric, Point[]>[]; loading?: boolean }) {
  const keys: BuoyMetric[] = ["temperature", "microplastics", "salinity", "turbidity"];
  const series = Object.fromEntries(keys.map((k) => [k, average(buoys, k)])) as Record<BuoyMetric, Point[]>;
  const none = loading ? "Loading" : "No reading yet";

  const tile = (key: BuoyMetric) => {
    const base =
      key === "microplastics"
        ? { ...METRICS.find((m) => m.key === "microplastics")!, decimals: decimalsFor(series[key].map((p) => p.value)) }
        : REAL_METRICS[key];
    const label = `Avg. ${base.label.toLowerCase()}`;
    return series[key].length ? (
      <Reading key={key} metric={{ ...base, label }} points={series[key]} span="the last run" />
    ) : (
      <div className="reading" key={key}>
        <div className="reading-label">{label}</div>
        <div className="reading-value reading-none">&mdash;</div>
        <div className="reading-note">{none}</div>
      </div>
    );
  };

  return (
    <div className="network-readings">
      <Gauge
        score={environmentIndex({
          temperature: series.temperature.at(-1)?.value,
          turbidity: series.turbidity.at(-1)?.value,
          microplastics: series.microplastics.at(-1)?.value,
        })}
      />
      <div className="readings-grid">
        {tile("temperature")}
        {tile("microplastics")}
        {tile("salinity")}
        {tile("turbidity")}
      </div>
    </div>
  );
}
