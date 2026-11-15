"use client";

import { useEffect, useState, type PointerEvent as ReactPointerEvent } from "react";
import { METRICS, seriesFor, type Metric, type MetricKey, type Overrides } from "@/lib/buoyData";

const TICK_MS = 5000;
const SPARK = { width: 200, height: 34, pad: 4 };

export function useNow(interval = TICK_MS) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(id);
  }, [interval]);
  return now;
}

type Point = { time: number; value: number };

type Edit = { value: string; changed: boolean; onChange(v: string): void };

export function Reading({ metric, points, edit }: { metric: Metric; points: Point[]; edit?: Edit }) {
  const [hover, setHover] = useState<number | null>(null);
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => SPARK.pad + (i / (points.length - 1)) * (SPARK.width - SPARK.pad * 2);
  const y = (v: number) => SPARK.pad + (1 - (v - min) / span) * (SPARK.height - SPARK.pad * 2);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");

  const current = values.at(-1)!;
  const fmt = (v: number) => v.toFixed(metric.decimals);
  const shown = hover ?? points.length - 1;

  const onMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const t = (e.clientX - box.left) / box.width;
    setHover(Math.round(Math.min(1, Math.max(0, t)) * (points.length - 1)));
  };

  return (
    <div className="reading">
      <div className="reading-label">{metric.label}</div>
      {edit ? (
        <label className="reading-value reading-edit" data-changed={edit.changed || undefined}>
          <input
            type="number"
            inputMode="decimal"
            step={10 ** -metric.decimals}
            min={0}
            value={edit.value}
            onChange={(e) => edit.onChange(e.target.value)}
            aria-label={`${metric.label} (${metric.unit})`}
          />
          <span className="reading-unit">{metric.unit}</span>
        </label>
      ) : (
        <div className="reading-value">
          {fmt(hover === null ? current : points[hover].value)}
          <span className="reading-unit">{metric.unit}</span>
        </div>
      )}
      <svg
        className="reading-spark"
        viewBox={`0 0 ${SPARK.width} ${SPARK.height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${metric.label} over the last 24 hours, from ${fmt(values[0])} to ${fmt(current)} ${metric.unit}`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {hover !== null && <line className="reading-cursor" x1={x(hover)} x2={x(hover)} y1={0} y2={SPARK.height} />}
        <path className="reading-line" d={path} vectorEffect="non-scaling-stroke" />
        <circle className="reading-dot" cx={x(shown)} cy={y(points[shown].value)} r={3.5} vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

type Values = Record<MetricKey, number>;
type SendState = "idle" | "sending" | "sent" | "failed";

type Props = {
  buoy: number;
  overrides?: Overrides;
  debug?: boolean;
  onSend?(values: Values): Promise<string>; // resolves to a status line
  onReset?(): void;
};

export default function BuoyReadings({ buoy, overrides, debug, onSend, onReset }: Props) {
  const now = useNow();
  const series = Object.fromEntries(METRICS.map((m) => [m.key, seriesFor(buoy, m.key, now, overrides)])) as Record<
    MetricKey,
    Point[]
  >;
  const current = (key: MetricKey) => series[key].at(-1)!.value;
  const format = (m: Metric, v: number) => v.toFixed(m.decimals);

  const [drafts, setDrafts] = useState<Record<MetricKey, string> | null>(null);
  const [send, setSend] = useState<SendState>("idle");
  const [note, setNote] = useState("");

  // Drafts start from what the buoy reads at the moment debug opens, or after a reset
  const values = drafts ?? (Object.fromEntries(METRICS.map((m) => [m.key, format(m, current(m.key))])) as Record<MetricKey, string>);
  const parsed = METRICS.map((m) => Number(values[m.key]));
  const valid = parsed.every((v) => Number.isFinite(v) && v >= 0 && v <= 1000);
  const changed = (m: Metric) => drafts !== null && values[m.key] !== format(m, current(m.key));

  const submit = async () => {
    if (!onSend || !valid) return;
    setSend("sending");
    try {
      setNote(await onSend(Object.fromEntries(METRICS.map((m, i) => [m.key, parsed[i]])) as Values));
      setDrafts(null);
      setSend("sent");
      setTimeout(() => setSend((s) => (s === "sent" ? "idle" : s)), 4000);
    } catch {
      setSend("failed");
    }
  };

  return (
    <div className="readings">
      <div className="readings-grid">
        {METRICS.map((m) => (
          <Reading
            key={m.key}
            metric={m}
            points={series[m.key]}
            edit={
              debug
                ? {
                    value: values[m.key],
                    changed: changed(m),
                    onChange: (v) => setDrafts({ ...values, [m.key]: v }),
                  }
                : undefined
            }
          />
        ))}
      </div>
      {debug && (
        <form
          className="debug-bar"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <span className="debug-bar-note" role="status">
            {send === "sending"
              ? "Publishing to AWS IoT…"
              : send === "sent"
                ? note
                : send === "failed"
                  ? "Couldn’t send it. Check the passcode or try again."
                  : `Sent as a live reading from Buoy ${buoy + 1}`}
          </span>
          <button
            type="button"
            className="debug-btn"
            onClick={() => {
              setDrafts(null);
              onReset?.();
            }}
          >
            Reset
          </button>
          <button type="submit" className="debug-btn debug-btn-primary" disabled={!valid || send === "sending"}>
            Send reading
          </button>
        </form>
      )}
    </div>
  );
}
