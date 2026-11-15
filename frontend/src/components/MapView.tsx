"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { mapStyle } from "@/lib/mapStyle";
import { BUOY_BODY_CENTER, createBuoyLayer, pxPerUnitAt, type BuoyLayer } from "@/lib/buoyLayer";
import BuoyWindow, { WINDOW_SIZE, type WindowState } from "@/components/BuoyWindow";
import BuoyReadings from "@/components/BuoyReadings";
import NetworkReadings from "@/components/NetworkReadings";
import NetworkPanel from "@/components/NetworkPanel";
import type { MetricKey, Overrides } from "@/lib/buoyData";

// The buoy cluster on Lake Carnegie, by Lake Drive
const HOME = { center: [-74.64015, 40.34295] as [number, number], zoom: 17.5, pitch: 60, bearing: -18 };

// Four buoys mid-lake, each at least 20m from shore. Every pair is linked.
const BUOYS: [number, number][] = [
  [-74.6413, 40.3433],
  [-74.6398, 40.34338],
  [-74.6389, 40.34295],
  [-74.6409, 40.34262],
];

const MAX_WINDOWS = 2; // buoy windows; the network window is extra
const NETWORK = -1; // the `buoy` of the mesh network's window
const NETWORK_SIZE = { width: 640, height: 360 };
const PANEL_RECT = { x: 0, y: 0, w: 290, h: 130 }; // the mesh networks panel, top left
const DEBUG_SIZE = { width: WINDOW_SIZE.width, height: WINDOW_SIZE.height + 52 }; // room for the send bar
// The agent panel, bottom right
const AGENT_WIDTH = 452;
const agentRect = () => ({ x: window.innerWidth - AGENT_WIDTH - 24, y: 280, w: AGENT_WIDTH + 24, h: window.innerHeight - 280 });
const ALL_BUOYS = BUOYS.map((_, i) => i);
const sizeOf = (w: WindowState) => (w.buoy === NETWORK ? NETWORK_SIZE : WINDOW_SIZE);
const isNarrow = () => window.innerWidth < 900;
let agentPanelOpen = false; // read by window placement
const agentOpenNow = () => agentPanelOpen;
const CLOSE_MS = 180;

const formatCoord = ([lng, lat]: [number, number]) =>
  `${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lng).toFixed(4)}° ${lng >= 0 ? "E" : "W"}`;

// Nearest buoy under a screen point, or -1. Buoys are drawn in 3D, so aim at the body's
// center above the water rather than the anchor point.
function hitBuoy(map: maplibregl.Map, point: { x: number; y: number }) {
  const ppu = pxPerUnitAt(map.getZoom());
  const lift = BUOY_BODY_CENTER * ppu * Math.sin((map.getPitch() * Math.PI) / 180);
  const radius = Math.max(18, ppu * 0.55);
  let best = -1;
  let bestDist = radius;
  BUOYS.forEach((b, i) => {
    const p = map.project(b);
    const d = Math.hypot(point.x - p.x, point.y - (p.y - lift));
    if (d < bestDist) {
      best = i;
      bestDist = d;
    }
  });
  return best;
}

type Rect = { x: number; y: number; w: number; h: number };
const overlap = (a: Rect, b: Rect) =>
  Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) *
  Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));

// Opens a window beside its buoy, in whichever spot hides the fewest buoys and
// overlaps the other window least.
function placeWindow(
  at: { x: number; y: number },
  others: WindowState[],
  map: maplibregl.Map | null,
  size = WINDOW_SIZE,
) {
  const { innerWidth: vw, innerHeight: vh } = window;
  const w = Math.min(size.width, vw - 16);
  const h = Math.min(size.height, vh - 16);
  const gap = 44;
  const top = 16;
  const right = vw - w - 72; // clear of the map controls
  const candidates = [
    { x: at.x + gap, y: at.y - h / 2 },
    { x: at.x - gap - w, y: at.y - h / 2 },
    { x: at.x - w / 2, y: at.y - gap - h },
    { x: at.x - w / 2, y: at.y + gap },
    { x: 16, y: PANEL_RECT.h + 8 },
    { x: right, y: top },
    { x: 16, y: vh - h - 16 },
    { x: right, y: vh - h - 16 },
    { x: (vw - w) / 2, y: top },
    { x: (vw - w) / 2, y: vh - h - 16 },
    { x: 16, y: (vh - h) / 2 },
    { x: right, y: (vh - h) / 2 },
  ].map((c) => ({
    x: Math.min(Math.max(8, c.x), vw - w - 8),
    y: Math.min(Math.max(8, c.y), vh - h - 8),
  }));
  const buoys = map ? BUOYS.map((b) => map.project(b)) : [];
  const score = (c: { x: number; y: number }) => {
    const r = { x: c.x - 12, y: c.y - 12, w: w + 24, h: h + 24 };
    const hidden = buoys.filter((p) => p.x > r.x && p.x < r.x + r.w && p.y > r.y && p.y < r.y + r.h).length;
    const covered =
      others.reduce((sum, o) => sum + overlap(r, { x: o.x, y: o.y, w: sizeOf(o).width, h: sizeOf(o).height }), 0) +
      overlap(r, PANEL_RECT) +
      (isNarrow() || !agentOpenNow() ? 0 : overlap(r, agentRect()));
    // Stacking on the other window is worse than hiding one buoy
    return hidden * 1e6 + covered * 8 + Math.hypot(c.x + w / 2 - at.x, c.y + h / 2 - at.y);
  };
  return candidates.reduce((best, c) => (score(c) < score(best) ? c : best));
}

// Live data, so it renders in the browser only
const AgentPanel = dynamic(() => import("@/components/AgentPanel"), { ssr: false });

// Served from public/ (copied on postinstall); the bundler can't resolve it itself.
maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

export default function MapView() {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [bearing, setBearing] = useState(HOME.bearing);
  const [failed, setFailed] = useState(false);
  const layerRef = useRef<BuoyLayer | null>(null);
  const [windows, setWindows] = useState<WindowState[]>([]); // last is frontmost
  const [hoverMap, setHoverMap] = useState(-1);
  const [hoverWindow, setHoverWindow] = useState(-1);
  const [hoverNetwork, setHoverNetwork] = useState(false);
  const [debug, setDebug] = useState(false);
  const [overrides, setOverrides] = useState<Overrides>({});
  const nextKey = useRef(1);

  // Debug injects readings and starts paid agent runs, so it sits behind a passcode
  const [passcode, setPasscode] = useState<string | null>(null);
  const [askPasscode, setAskPasscode] = useState(false);

  const unlock = useCallback(async (code: string) => {
    const res = await fetch("/api/debug", { headers: { "x-debug-passcode": code } });
    if (!res.ok) return false;
    setPasscode(code);
    try {
      localStorage.setItem("debug-passcode", code);
    } catch {
      // private mode: ask again next time
    }
    return true;
  }, []);

  const toggleDebug = useCallback(async () => {
    if (debug) {
      setDebug(false);
      setOverrides({});
      return;
    }
    let saved = passcode ?? "";
    if (!saved) {
      try {
        saved = localStorage.getItem("debug-passcode") ?? "";
      } catch {
        saved = "";
      }
    }
    if (await unlock(saved)) setDebug(true);
    else setAskPasscode((v) => !v);
  }, [debug, passcode, unlock]);

  // Publish the edited values through AWS IoT Core as a live reading from that buoy
  const sendReading = useCallback(
    async (buoy: number, values: Record<MetricKey, number>) => {
      const res = await fetch("/api/debug", {
        method: "POST",
        headers: { "Content-Type": "application/json", "x-debug-passcode": passcode ?? "" },
        body: JSON.stringify({ buoy: buoy + 1, values }),
      });
      if (!res.ok) throw new Error(`debug publish failed: ${res.status}`);
      const body: { anomalies: unknown[]; cases: unknown[]; limited?: boolean } = await res.json();
      setOverrides((o) => ({ ...o, [buoy]: values }));
      if (body.limited) return "Sent. The investigation budget for this demo is used up.";
      if (body.cases.length) return "Sent. The Lake Steward is investigating.";
      return body.anomalies.length ? "Sent. Anomaly detected." : "Sent. Within normal range, no investigation.";
    },
    [passcode],
  );

  // An open agent panel takes the right side; shift the lake left so no buoy hides behind it
  const agentOpen = useRef(false);
  const onAgentExpand = useCallback((open: boolean) => {
    if (agentOpen.current === open) return;
    agentOpen.current = open;
    agentPanelOpen = open;
    const wide = window.innerWidth >= 900;
    // Lower too, so windows along the top edge clear the lake
    mapRef.current?.easeTo({
      padding: { right: open && wide ? AGENT_WIDTH + 32 : 0, left: 0, top: open && wide ? 240 : 0, bottom: 0 },
      duration: 600,
      easing: (t) => 1 - Math.pow(1 - t, 3),
    });
    if (!open || !wide) return;
    // Slide any window the panel would cover to its left
    const panel = agentRect();
    setWindows((ws) =>
      ws.map((w) => {
        const size = sizeOf(w);
        const covered = overlap({ x: w.x, y: w.y, w: size.width, h: size.height }, panel) > 0;
        return covered ? { ...w, x: Math.max(16, panel.x - size.width - 12), y: 16 } : w;
      }),
    );
  }, []);

  const resetBuoy = useCallback((buoy: number) => {
    setOverrides((o) => {
      const next = { ...o };
      delete next[buoy];
      return next;
    });
  }, []);

  const closeWindow = useCallback((key: number) => {
    setWindows((ws) => ws.map((w) => (w.key === key ? { ...w, closing: true } : w)));
    setTimeout(() => setWindows((ws) => ws.filter((w) => w.key !== key)), CLOSE_MS);
    setHoverWindow(-1);
    setHoverNetwork(false);
  }, []);

  const focusWindow = useCallback((key: number) => {
    setWindows((ws) => {
      const i = ws.findIndex((w) => w.key === key);
      if (i < 0 || i === ws.length - 1) return ws;
      return [...ws.slice(0, i), ...ws.slice(i + 1), ws[i]];
    });
  }, []);

  const moveWindow = useCallback((key: number, x: number, y: number) => {
    setWindows((ws) => ws.map((w) => (w.key === key ? { ...w, x, y } : w)));
  }, []);

  const openBuoy = useCallback(
    (buoy: number, at: { x: number; y: number }) => {
      const open = windows.filter((w) => !w.closing);
      const existing = open.find((w) => w.buoy === buoy);
      if (existing) return focusWindow(existing.key);
      const buoyWindows = open.filter((w) => w.buoy !== NETWORK);
      const replaced = buoyWindows.length >= MAX_WINDOWS ? buoyWindows[0] : null; // the least recent
      if (replaced) closeWindow(replaced.key);

      const keep = open.filter((w) => w !== replaced);
      const { x, y } = placeWindow(at, keep, mapRef.current, debug ? DEBUG_SIZE : WINDOW_SIZE);
      const key = nextKey.current++;
      setWindows((ws) => [...ws, { key, buoy, x, y }]);
    },
    [windows, focusWindow, closeWindow, debug],
  );
  const networkWindow = windows.find((w) => w.buoy === NETWORK && !w.closing);

  // Selecting the network toggles its window
  const toggleNetwork = useCallback(() => {
    if (networkWindow) return closeWindow(networkWindow.key);
    const map = mapRef.current;
    const pts = map ? BUOYS.map((b) => map.project(b)) : [{ x: window.innerWidth / 2, y: window.innerHeight / 2 }];
    const at = { x: pts.reduce((s, p) => s + p.x, 0) / pts.length, y: pts.reduce((s, p) => s + p.y, 0) / pts.length };
    const { x, y } = placeWindow(at, windows.filter((w) => !w.closing), map, NETWORK_SIZE);
    const key = nextKey.current++;
    setWindows((ws) => [...ws, { key, buoy: NETWORK, x, y }]);
  }, [networkWindow, windows, closeWindow]);

  const openRef = useRef(openBuoy);
  useEffect(() => {
    openRef.current = openBuoy;
  }, [openBuoy]);

  // Selected buoys glow fully, the whole mesh when the network is open; hovering a
  // buoy, its window, or the network lights them partially
  useEffect(() => {
    const open = new Set(windows.filter((w) => !w.closing).map((w) => w.buoy));
    const network = open.has(NETWORK);
    layerRef.current?.setLevels(
      BUOYS.map((_, i) =>
        open.has(i) || network ? 1 : i === hoverMap || i === hoverWindow || hoverNetwork ? 0.5 : 0,
      ),
    );
  }, [windows, hoverMap, hoverWindow, hoverNetwork]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const top = windows.filter((w) => !w.closing).at(-1);
      if (top) closeWindow(top.key);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [windows, closeWindow]);

  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: mapStyle,
      ...HOME,
      maxPitch: 80,
      attributionControl: false,
      canvasContextAttributes: { antialias: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-left");

    map.on("load", () => {
      const layer = createBuoyLayer("buoys", BUOYS);
      layerRef.current = layer;
      map.addLayer(layer);
    });

    map.on("mousemove", (e) => {
      const i = hitBuoy(map, e.point);
      map.getCanvas().style.cursor = i >= 0 ? "pointer" : "";
      setHoverMap(i);
    });
    map.on("mouseout", () => setHoverMap(-1));
    map.on("click", (e) => {
      const i = hitBuoy(map, e.point);
      if (i >= 0) openRef.current(i, e.point);
    });

    map.on("rotate", () => setBearing(map.getBearing()));
    let loaded = false;
    map.once("load", () => (loaded = true));
    map.on("error", () => {
      if (!loaded) setFailed(true);
    });

    return () => map.remove();
  }, []);

  const map = () => mapRef.current;
  const ease = { duration: 700, easing: (t: number) => 1 - Math.pow(1 - t, 4) };

  return (
    <div className="map-shell">
      <div ref={container} className="map-canvas" aria-label="Map of Princeton" role="region" />

      {failed && (
        <p className="map-error" role="alert">
          The map couldn&rsquo;t load. Check your connection and reload the page.
        </p>
      )}

      <div className="desktop" aria-live="polite">
        {windows.map((w, z) =>
          w.buoy === NETWORK ? (
            <BuoyWindow
              key={w.key}
              win={w}
              title="Mesh Network 1"
              subtitle={`${BUOYS.length} buoys · Lake Carnegie`}
              size={NETWORK_SIZE}
              active={z === windows.length - 1}
              linked={hoverNetwork}
              z={10 + z}
              onFocus={() => focusWindow(w.key)}
              onMove={(x, y) => moveWindow(w.key, x, y)}
              onClose={() => closeWindow(w.key)}
              onHover={setHoverNetwork}
            >
              <NetworkReadings buoys={ALL_BUOYS} overrides={overrides} />
            </BuoyWindow>
          ) : (
            <BuoyWindow
              key={w.key}
              win={w}
              title={`Buoy ${w.buoy + 1}`}
              subtitle={`Lake Carnegie · ${formatCoord(BUOYS[w.buoy])}`}
              size={debug ? DEBUG_SIZE : WINDOW_SIZE}
              badge={debug ? "Debug" : undefined}
              active={z === windows.length - 1}
              linked={hoverMap === w.buoy}
              z={10 + z}
              onFocus={() => focusWindow(w.key)}
              onMove={(x, y) => moveWindow(w.key, x, y)}
              onClose={() => closeWindow(w.key)}
              onHover={(on) => setHoverWindow(on ? w.buoy : -1)}
            >
              <BuoyReadings
                buoy={w.buoy}
                overrides={overrides}
                debug={debug}
                onSend={(values) => sendReading(w.buoy, values)}
                onReset={() => resetBuoy(w.buoy)}
              />
            </BuoyWindow>
          ),
        )}
      </div>

      <NetworkPanel
        selected={!!networkWindow}
        buoyCount={BUOYS.length}
        onSelect={toggleNetwork}
        onHover={setHoverNetwork}
      />

      <AgentPanel onExpand={onAgentExpand} />

      <div className="map-controls">
        <button
          type="button"
          className="ctl ctl-solo"
          aria-label="Reset to north"
          onClick={() => map()?.easeTo({ bearing: 0, ...ease })}
        >
          <svg
            width="22"
            height="22"
            viewBox="0 0 22 22"
            aria-hidden="true"
            style={{ transform: `rotate(${-bearing}deg)` }}
          >
            <path d="M11 2.5 14 11H8z" fill="var(--signal)" />
            <path d="M11 19.5 8 11h6z" fill="var(--ink-3)" />
          </svg>
        </button>

        <div className="ctl-group">
          <button type="button" className="ctl" aria-label="Zoom in" onClick={() => map()?.zoomIn(ease)}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
          <button type="button" className="ctl" aria-label="Zoom out" onClick={() => map()?.zoomOut(ease)}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3 8h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <button
          type="button"
          className="ctl ctl-solo"
          aria-label="Return to Princeton"
          onClick={() => map()?.flyTo({ ...HOME, duration: 1600, essential: true })}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M14 2 2 7.2l5.1 1.7L8.8 14z"
              fill="currentColor"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinejoin="round"
            />
          </svg>
        </button>

        {askPasscode && (
          <form
            className="passcode"
            onSubmit={async (e) => {
              e.preventDefault();
              const input = e.currentTarget.elements.namedItem("code") as HTMLInputElement;
              if (await unlock(input.value.trim())) {
                setAskPasscode(false);
                setDebug(true);
              } else {
                input.setCustomValidity("Wrong passcode");
                input.reportValidity();
              }
            }}
          >
            <label htmlFor="debug-code">Debug passcode</label>
            <input
              id="debug-code"
              name="code"
              type="password"
              autoComplete="off"
              autoFocus
              onChange={(e) => e.currentTarget.setCustomValidity("")}
            />
            <button type="submit" className="debug-btn debug-btn-primary">
              Unlock
            </button>
          </form>
        )}

        <button
          type="button"
          className="ctl ctl-solo ctl-debug"
          aria-pressed={debug}
          aria-label="Debug: edit buoy readings and send them to AWS"
          title={debug ? "Debug on: open a buoy to edit its readings" : "Debug"}
          onClick={toggleDebug}
        >
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path
              d="M9 6.2c2.2 0 3.8 1.9 3.8 4.6S11.2 15.6 9 15.6s-3.8-2-3.8-4.8S6.8 6.2 9 6.2zM9 6.2v9.4M6.7 6.6 5.3 4.6M11.3 6.6l1.4-2M5.2 9.3H2.6M5.2 12.6 3 13.8M12.8 9.3h2.6M12.8 12.6l2.2 1.2"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}
