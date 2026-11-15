"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { useNow } from "@/components/BuoyReadings";
import type { Check, Photo } from "@/lib/photos";
import type { RawSample } from "@/lib/readings";

type Feed = { buoy: number; node: string; buoys: number[]; samples: RawSample[]; photos: Photo[] };
type Tone = "good" | "warn" | "bad";
type Row = { at: number; sample?: RawSample; restart?: boolean; gap?: number };

const POLL_MS = 5000;
const WINDOW_MIN = 30; // minutes of samples shown
const QUIET_S = 150; // the Pi uploads a file a minute; longer than this and the buoy has gone quiet
const GAP_S = 30; // samples come every 5 s; a longer silence is marked in the stream
const ADC_MAX = 3300; // mV; the firmware reports more than this when a read failed
const TEMP_RANGE = [-2, 45]; // °C; outside it is a failed read, not water

const photoSrc = (key: string) => `/api/images/file?key=${encodeURIComponent(key)}`;
const VERDICT: Record<Check["label"], string> = { clean: "Clean", microplastic: "Microplastic", blocked: "Blocked" };
const verdict = (c: Check | null) =>
  !c ? "Not checked yet" : c.label === "blocked" ? `Blocked: ${c.reason ?? "can't see the mesh"}` : c.label === "microplastic" ? `Microplastic: ${c.spots.length} spot${c.spots.length === 1 ? "" : "s"}` : VERDICT[c.label];
const clock = (s: number) => new Date(s * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
const tempOk = (t: number | null): t is number => t !== null && t >= TEMP_RANGE[0] && t <= TEMP_RANGE[1];
const condOk = (c: number | null): c is number => c !== null && c >= 0 && c <= ADC_MAX;

function ago(s: number) {
  if (s < 90) return `${Math.max(0, Math.round(s))} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${(s / 3600).toFixed(1)} h ago`;
}

function duration(s: number) {
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

function uptime(ms: number | null) {
  if (ms === null) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** The stream newest first, with restarts (uptime went backwards) and silences marked between samples. */
function rows(samples: RawSample[]): Row[] {
  const out: Row[] = [];
  samples.forEach((s, i) => {
    const prev = samples[i - 1];
    if (prev && s.received_at - prev.received_at > GAP_S) out.push({ at: s.received_at, gap: s.received_at - prev.received_at });
    if (prev && s.uptime_ms !== null && prev.uptime_ms !== null && s.uptime_ms < prev.uptime_ms) out.push({ at: s.received_at, restart: true });
    out.push({ at: s.received_at, sample: s });
  });
  return out.reverse();
}

function Status({ label, tone, value, note }: { label: string; tone: Tone; value: string; note?: string }) {
  return (
    <div className="live-stat">
      <dt>
        <i className="live-dot" data-tone={tone} aria-hidden="true" />
        {label}
      </dt>
      <dd>
        {value}
        {note && <span>{note}</span>}
      </dd>
    </div>
  );
}

export default function LiveFeed() {
  const [buoy, setBuoy] = useState(1);
  // cut: the newest sample before this update, so only samples that just landed light up
  const [{ feed, cut }, setState] = useState<{ feed: Feed | null; cut: number | null }>({ feed: null, cut: null });
  const [error, setError] = useState<string | null>(null);
  const now = useNow(1000) / 1000;

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const res = await fetch(`/api/buoys/live?buoy=${buoy}&minutes=${WINDOW_MIN}`, { cache: "no-store" });
        const body: Feed = await res.json();
        if (!res.ok) throw new Error((body as unknown as { error?: string }).error ?? res.statusText);
        if (!stop) {
          setState((prev) => ({
            feed: body,
            cut: prev.feed?.buoy === body.buoy ? (prev.feed.samples.at(-1)?.received_at ?? null) : null,
          }));
          setError(null);
        }
      } catch (e) {
        if (!stop) setError(String(e instanceof Error ? e.message : e));
      }
    };
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [buoy]);

  const samples = useMemo(() => (feed?.buoy === buoy ? feed.samples : []), [feed, buoy]);
  const photos = feed?.buoy === buoy ? feed.photos : [];
  const stream = useMemo(() => rows(samples), [samples]);
  const newest = samples.at(-1);

  const status = useMemo(() => {
    if (!newest) return null;
    const age = now - newest.received_at;
    const recent = samples.filter((s) => s.received_at > now - 300).length;
    const restarts = stream.filter((r) => r.restart).length;
    const lastTemp = [...samples].reverse().find((s) => tempOk(s.temp_c));
    const light = newest.light_mv !== null && newest.ambient_mv !== null ? newest.light_mv - newest.ambient_mv : null;
    return { age, recent, restarts, lastTemp, light };
  }, [newest, samples, stream, now]);

  const rssiTone = (r: number | null): Tone => (r === null ? "bad" : r >= -70 ? "good" : r >= -85 ? "warn" : "bad");

  return (
    <main className="live">
      <header className="live-head">
        <div>
          <h1>Buoy {buoy} live</h1>
          <p>
            {feed?.buoy === buoy ? `Node ${feed.node} · ` : ""}every sample as the Pi gateway uploads it, newest first. Files land once a
            minute, so the newest row can be up to a minute old.
          </p>
        </div>
        {(feed?.buoys.length ?? 0) > 1 && (
          <div className="photos-filter" role="group" aria-label="Buoy">
            {feed!.buoys.map((b) => (
              <button key={b} aria-pressed={buoy === b} onClick={() => setBuoy(b)}>
                Buoy {b}
              </button>
            ))}
          </div>
        )}
      </header>

      {error && (
        <p className="live-note" role="alert">
          Couldn&apos;t load samples: {error}
          {/expired|credential|token/i.test(error) ? " Run aws login --profile hackprinceton and refresh." : ""}
        </p>
      )}
      {feed?.buoy === buoy && !samples.length && <p className="live-note">No samples from this buoy in the bucket yet.</p>}

      {newest && status && (
        <dl className="live-status" aria-live="polite">
          <Status
            label={status.age < QUIET_S ? "Streaming" : "Quiet"}
            tone={status.age < QUIET_S ? (status.restarts ? "warn" : "good") : "bad"}
            value={`Last sample ${ago(status.age)}`}
            note={`${status.recent} of 60 samples in 5 min · ${status.restarts ? `restarted ${status.restarts}× in ${WINDOW_MIN} min` : "no restarts"}`}
          />
          <Status
            label="Signal"
            tone={rssiTone(newest.rssi)}
            value={newest.rssi === null ? "No reading" : `${newest.rssi} dBm`}
            note={newest.level === 2 ? "relayed through the other buoy" : newest.level === 1 ? "direct to the gateway" : undefined}
          />
          <Status
            label="Temperature"
            tone={tempOk(newest.temp_c) ? "good" : "bad"}
            value={tempOk(newest.temp_c) ? `${newest.temp_c.toFixed(2)} °C` : "No reading"}
            note={tempOk(newest.temp_c) ? undefined : status.lastTemp ? `last ${status.lastTemp.temp_c} °C at ${clock(status.lastTemp.received_at)}` : `none in ${WINDOW_MIN} min`}
          />
          <Status
            label="Conductivity"
            tone={condOk(newest.cond_mv) ? "good" : "bad"}
            value={condOk(newest.cond_mv) ? `${newest.cond_mv} mV` : "Failed read"}
            note={condOk(newest.cond_mv) ? "raw, not yet salinity" : newest.cond_mv !== null ? `sends ${newest.cond_mv} mV` : "sends nothing"}
          />
          <Status
            label="Light − ambient"
            tone={status.light === null ? "bad" : "good"}
            value={status.light === null ? "No reading" : `${status.light} mV`}
            note="raw, not yet turbidity"
          />
        </dl>
      )}

      {photos.length > 0 && (
        <section className="live-camera" aria-label="Camera">
          <a className="live-camera-main" href={photoSrc(photos[0].key)} target="_blank" rel="noreferrer">
            {/* eslint-disable-next-line @next/next/no-img-element -- private S3 photos through our own route */}
            <img src={photoSrc(photos[0].key)} alt={`Newest photo from buoy ${buoy}`} />
          </a>
          <div>
            <p>
              <b>Camera</b>, newest photo {ago(now - Date.parse(photos[0].time) / 1000)} ({clock(Date.parse(photos[0].time) / 1000)}):{" "}
              {verdict(photos[0].check)}
            </p>
            <ul className="live-thumbs">
              {photos.slice(1).map((ph) => (
                <li key={ph.key}>
                  <a href={photoSrc(ph.key)} target="_blank" rel="noreferrer" title={verdict(ph.check)}>
                    {/* eslint-disable-next-line @next/next/no-img-element -- private S3 photos through our own route */}
                    <img src={photoSrc(ph.key)} alt={`Photo at ${clock(Date.parse(ph.time) / 1000)}, ${verdict(ph.check)}`} loading="lazy" />
                  </a>
                  <span>
                    {clock(Date.parse(ph.time) / 1000)} · {ph.check ? VERDICT[ph.check.label] : "not checked"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}

      {stream.length > 0 && (
        <div className="live-table">
          <table>
            <caption className="sr-only">Samples from buoy {buoy}, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Seq</th>
                <th scope="col">Up</th>
                <th scope="col">Temp °C</th>
                <th scope="col">Cond mV</th>
                <th scope="col">Light mV</th>
                <th scope="col">Ambient mV</th>
                <th scope="col">RSSI dBm</th>
                <th scope="col">Hop</th>
              </tr>
            </thead>
            <tbody>
              {stream.map((r) => (
                <Fragment key={`${r.at}-${r.gap !== undefined ? "gap" : r.restart ? "restart" : "sample"}`}>
                  {r.gap !== undefined && (
                    <tr className="live-divider">
                      <td colSpan={9}>No samples for {duration(r.gap)}</td>
                    </tr>
                  )}
                  {r.restart && (
                    <tr className="live-divider" data-tone="warn">
                      <td colSpan={9}>Node restarted</td>
                    </tr>
                  )}
                  {r.sample && (
                    <tr data-new={cut !== null && r.sample.received_at > cut ? "true" : undefined}>
                      <td>{clock(r.sample.received_at)}</td>
                      <td>{r.sample.seq ?? "—"}</td>
                      <td>{uptime(r.sample.uptime_ms)}</td>
                      <td data-bad={!tempOk(r.sample.temp_c) || undefined}>{r.sample.temp_c === null ? "null" : r.sample.temp_c.toFixed(2)}</td>
                      <td data-bad={!condOk(r.sample.cond_mv) || undefined}>{r.sample.cond_mv ?? "null"}</td>
                      <td>{r.sample.light_mv ?? "null"}</td>
                      <td>{r.sample.ambient_mv ?? "null"}</td>
                      <td data-bad={r.sample.rssi === null || r.sample.rssi < -85 || undefined}>{r.sample.rssi ?? "null"}</td>
                      <td>{r.sample.level ?? "—"}</td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
