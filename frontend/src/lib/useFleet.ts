"use client";

import { useEffect, useState } from "react";
import type { BuoyData, Fleet } from "@/lib/readings";

const REFRESH_MS = 15_000;

export type FleetState = {
  /** By map index: buoy 1 is 0. Undefined until the first fetch lands or for a buoy with no node. */
  buoys: (BuoyData | undefined)[];
  fetchedAt: string | null; // the last fetch that worked
  error: string | null;
};

/** The hardware buoys' readings and photos, re-fetched every few seconds. */
export function useFleet(): FleetState {
  const [state, setState] = useState<FleetState>({ buoys: [], fetchedAt: null, error: null });

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const res = await fetch("/api/buoys", { cache: "no-store" });
        const body = await res.json();
        if (!res.ok) throw new Error(body.error ?? res.statusText);
        const fleet = body as Fleet;
        const buoys: FleetState["buoys"] = [];
        for (const b of fleet.buoys) buoys[b.buoy - 1] = b;
        if (!stop) setState({ buoys, fetchedAt: fleet.fetchedAt, error: null });
      } catch (e) {
        if (!stop) setState((s) => ({ ...s, error: String(e instanceof Error ? e.message : e) }));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, []);

  return state;
}

/** "3:06 AM" today, "Nov 15, 3:06 AM" on another day. */
export function when(iso: string) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === new Date().toDateString()) return time;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}
