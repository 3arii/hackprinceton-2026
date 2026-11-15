"use client";

import { useEffect, useState, type PointerEvent as ReactPointerEvent } from "react";
import { METRICS, REAL_METRICS, seriesFor, type Metric, type MetricKey, type Overrides } from "@/lib/buoyData";
import type { BuoyData } from "@/lib/readings";
import { when } from "@/lib/useFleet";

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

/** A metric with nothing to show yet. */
function Empty({ label, note }: { label: string; note: string }) {
  return (
    <div className="reading">
      <div className="reading-label">{label}</div>
      <div className="reading-value reading-none">&mdash;</div>
      <div className="reading-note">{note}</div>
    </div>
  );
}

const VERDICT = { clean: "Clean", microplastic: "Microplastic", blocked: "Blocked" } as const;

/** What the camera's model made of its newest photo, beside the photo. No count: the flow isn't measured. */
function Camera({ photo }: { photo: BuoyData["photo"] }) {
  if (!photo) return <Empty label="Microplastics" note="No photo yet" />;
  const label = photo.check?.label;
  return (
    <div className="reading reading-camera">
      <div className="reading-camera-text">
        <div className="reading-label">Microplastics</div>
        <div className="reading-value reading-verdict" data-label={label ?? "none"}>
          {label ? VERDICT[label] : "Not checked"}
        </div>
        <div className="reading-note">Photo from {when(photo.time)}</div>
      </div>
      <a className="reading-photo" href="/images" target="_blank" rel="noreferrer" aria-label="Open the camera photos">
        {/* eslint-disable-next-line @next/next/no-img-element -- private S3 photo through our own route */}
        <img src={`/api/images/file?key=${encodeURIComponent(photo.key)}`} alt={`Newest mesh photo, ${label ?? "not checked"}`} />
      </a>
    </div>
  );
}

export function Reading({ metric, points, edit, span: period = "the last 24 hours" }: { metric: Metric; points: Point[]; edit?: Edit; span?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => SPARK.pad + (points.length > 1 ? i / (points.length - 1) : 0.5) * (SPARK.width - SPARK.pad * 2);
  const y = (v: number) => SPARK.pad + (1 - (v - min) / span) * (SPARK.height - SPARK.pad * 2);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");

  const current = values.at(-1)!;
  const fmt = (v: number) => v.toFixed(metric.decimals);
  const shown = Math.min(hover ?? points.length - 1, points.length - 1);

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
          {fmt(hover === null ? current : points[shown].value)}
          <span className="reading-unit">{metric.unit}</span>
        </div>
      )}
      <svg
        className="reading-spark"
        viewBox={`0 0 ${SPARK.width} ${SPARK.height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${metric.label} over ${period}, from ${fmt(values[0])} to ${fmt(current)} ${metric.unit}`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {hover !== null && <line className="reading-cursor" x1={x(shown)} x2={x(shown)} y1={0} y2={SPARK.height} />}
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
  data?: BuoyData; // the hardware buoy's readings; undefined while they load
  loading?: boolean;
  overrides?: Overrides;
  debug?: boolean;
  onSend?(values: Values): Promise<string>; // resolves to a status line
  onReset?(): void;
};

/** What the hardware reported: three sensors and the camera's verdict. */
function LiveReadings({ data, loading }: { data?: BuoyData; loading?: boolean }) {
  const none = loading ? "Loading" : "No reading yet";
  const tile = (key: "temperature" | "salinity" | "turbidity") => {
    const m = REAL_METRICS[key];
    const points = data?.series[key] ?? [];
    return points.length ? <Reading key={key} metric={m} points={points} span="its last run" /> : <Empty key={key} label={m.label} note={none} />;
  };
  return (
    <div className="readings">
      <div className="readings-grid">
        {tile("temperature")}
        {data ? <Camera photo={data.photo} /> : <Empty label="Microplastics" note={none} />}
        {tile("salinity")}
        {tile("turbidity")}
      </div>
    </div>
  );
}

export default function BuoyReadings(props: Props) {
  // Debug edits and sends a simulated reading; otherwise the window shows the hardware
  return props.debug ? <DebugReadings {...props} /> : <LiveReadings data={props.data} loading={props.loading} />;
}

function DebugReadings({ buoy, overrides, debug, onSend, onReset }: Props) {
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
