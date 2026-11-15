"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Check, Estimate, Photo } from "@/lib/photos";

type Feed = { total: number; photos: Photo[]; estimates: Estimate[] };
type Label = Check["label"];

const REFRESH_MS = 15_000;
const LABELS: [Label, string][] = [
  ["clean", "Clean"],
  ["microplastic", "Microplastic"],
  ["blocked", "Blocked"],
];
const NAME = Object.fromEntries(LABELS) as Record<Label, string>;
const src = (key: string) => `/api/images/file?key=${encodeURIComponent(key)}`;
const label = (p: Photo) => (p.buoy ? `Buoy ${p.buoy}` : p.node);
const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const per = (n: number) => (n === 0 ? "0" : n >= 10 ? n.toFixed(0) : n >= 1 ? n.toFixed(1) : n.toPrecision(2));
const span = (min: number) => (min < 90 ? `${Math.round(min)} min` : `${(min / 60).toFixed(1)} h`);

/** Microplastics per litre, by extrapolation from what the camera caught, with what it rests on. */
function Concentration({ e, named }: { e: Estimate; named: boolean }) {
  const [lo, hi] = e.ci95;
  const view = e.view_fraction >= 1 ? "all of it in view" : `${Math.round(e.view_fraction * 100)}% of it in view`;
  return (
    <p className="photos-estimate">
      {named ? `Buoy ${e.buoy} microplastics` : "Microplastics"} <b>{per(e.per_liter)} particles/L</b>
      {e.caught ? ` (95% confidence: ${per(lo)} to ${per(hi)})` : ` (95% confidence: at most ${per(hi)})`}. {e.caught} caught in{" "}
      {e.liters} L over {span(e.minutes)}, as of {clock(e.until)}.
      {!e.measured && (
        <span className="photos-assume">
          {" "}Assumes {e.flow_lpm} L/min through the mesh, {view}; measure the flow to firm this up.
        </span>
      )}
    </p>
  );
}

/** One line on what the model saw. */
function detail(c: Check | null) {
  if (!c) return "Not checked yet";
  if (c.label === "clean") return "Nothing on the mesh";
  if (c.label === "blocked") return capital(c.reason ?? "can't see the mesh");
  const n = c.spots.length;
  const [x0, y0, x1, y1] = [...c.spots].sort((a, b) => b.area_px - a.area_px)[0].bbox;
  return `${n} spot${n === 1 ? "" : "s"} on the mesh, the largest ${x1 - x0} × ${y1 - y0} px`;
}

function Verdict({ check }: { check: Check | null }) {
  return (
    <span className="photos-verdict" data-label={check?.label ?? "none"}>
      {check ? NAME[check.label] : "Not checked"}
    </span>
  );
}

/** The photo with a box around each spot the model found. */
function Frame({ photo, alt, lazy }: { photo: Photo; alt: string; lazy?: boolean }) {
  const c = photo.check;
  return (
    <div className="photos-frame">
      {/* eslint-disable-next-line @next/next/no-img-element -- private S3 photos through our own route */}
      <img src={src(photo.key)} alt={alt} loading={lazy ? "lazy" : undefined} />
      {c && c.spots.length > 0 && (
        <svg viewBox={`0 0 ${c.width || 640} ${c.height || 480}`} preserveAspectRatio="none" aria-hidden="true">
          {c.spots.map(({ bbox: [x0, y0, x1, y1] }, i) => (
            <rect key={i} x={x0 - 6} y={y0 - 6} width={x1 - x0 + 12} height={y1 - y0 + 12} rx={4} />
          ))}
        </svg>
      )}
    </div>
  );
}

export default function PhotoFeed() {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<string | null>(null);
  const [buoy, setBuoy] = useState<string>("all");
  const [verdict, setVerdict] = useState<Label | "all">("all");
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/images?limit=200", { cache: "no-store" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? res.statusText);
      setFeed(body);
      setError(null);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
    setChecked(new Date().toISOString());
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- first load, then poll
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  const sources = useMemo(() => [...new Set((feed?.photos ?? []).map(label))].sort(), [feed]);
  const fromBuoy = useMemo(
    () => (feed?.photos ?? []).filter((p) => buoy === "all" || label(p) === buoy),
    [feed, buoy],
  );
  const counts = useMemo(() => {
    const n: Record<string, number> = { all: fromBuoy.length };
    for (const p of fromBuoy) if (p.check) n[p.check.label] = (n[p.check.label] ?? 0) + 1;
    return n;
  }, [fromBuoy]);
  const shown = useMemo(
    () => fromBuoy.filter((p) => verdict === "all" || p.check?.label === verdict),
    [fromBuoy, verdict],
  );
  const current = open !== null ? shown[open] : null;

  useEffect(() => {
    if (open === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(null);
      if (e.key === "ArrowRight") setOpen((i) => (i === null ? i : Math.min(i + 1, shown.length - 1)));
      if (e.key === "ArrowLeft") setOpen((i) => (i === null ? i : Math.max(i - 1, 0)));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, shown.length]);

  return (
    <main className="photos">
      <header className="photos-head">
        <div>
          <h1>Buoy camera</h1>
          <p>
            {feed ? `${feed.total} photo${feed.total === 1 ? "" : "s"}` : "Loading"}
            {feed && feed.total > feed.photos.length ? `, newest ${feed.photos.length} shown` : ""}
            {checked ? ` · checked ${clock(checked)}` : ""}
          </p>
          {feed?.estimates
            .filter((e) => buoy === "all" || `Buoy ${e.buoy}` === buoy)
            .map((e) => <Concentration key={e.node} e={e} named={feed.estimates.length > 1} />)}
        </div>
        <div className="photos-filters">
          <div className="photos-filter" role="group" aria-label="What the model saw">
            {(["all", ...LABELS.map(([id]) => id)] as const).map((id) => (
              <button key={id} aria-pressed={verdict === id} onClick={() => setVerdict(id)}>
                {id !== "all" && <i className="photos-dot" data-label={id} aria-hidden="true" />}
                {id === "all" ? "All" : NAME[id]}
                <span className="photos-count">{counts[id] ?? 0}</span>
              </button>
            ))}
          </div>
          {sources.length > 1 && (
            <div className="photos-filter" role="group" aria-label="Buoy">
              {["all", ...sources].map((s) => (
                <button key={s} aria-pressed={buoy === s} onClick={() => setBuoy(s)}>
                  {s === "all" ? "All" : s}
                </button>
              ))}
            </div>
          )}
        </div>
      </header>

      {error && (
        <p className="photos-note" role="alert">
          Couldn&apos;t load photos: {error}
          {/expired|credential|token/i.test(error) ? " Run aws login --profile hackprinceton and refresh." : ""}
        </p>
      )}
      {feed && !fromBuoy.length && !error && (
        <p className="photos-note">No photos yet. The buoys upload one about every 30 seconds.</p>
      )}
      {feed && fromBuoy.length > 0 && !shown.length && (
        <p className="photos-note">No {NAME[verdict as Label].toLowerCase()} photos.</p>
      )}

      <ul className="photos-grid">
        {shown.map((p, i) => (
          <li key={p.key} data-label={p.check?.label}>
            <button
              onClick={() => setOpen(i)}
              aria-label={`${label(p)}, ${day(p.time)} ${clock(p.time)}, ${p.check ? NAME[p.check.label] : "not checked"}`}
            >
              <Frame photo={p} alt="" lazy />
              <span className="photos-caption">
                <Verdict check={p.check} />
                <span>
                  <b>{label(p)}</b> {clock(p.time)}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>

      {current && (
        <div className="photos-lightbox" role="dialog" aria-modal="true" aria-label="Photo" onClick={() => setOpen(null)}>
          <div className="photos-stage" onClick={(e) => e.stopPropagation()}>
            <Frame photo={current} alt={`${label(current)} at ${clock(current.time)}`} />
          </div>
          <p className="photos-finding">
            <Verdict check={current.check} /> {detail(current.check)}
          </p>
          <p>
            <b>{label(current)}</b> · {day(current.time)} {clock(current.time)} · {(current.size / 1024).toFixed(1)} KB ·{" "}
            {open! + 1} of {shown.length} · ← → to browse, Esc to close
          </p>
        </div>
      )}
    </main>
  );
}
