"use client";

import { useEffect, useRef, useState } from "react";
import type { BuoyData } from "@/lib/readings";
import { when } from "@/lib/useFleet";

type Photo = NonNullable<BuoyData["photo"]>;
type Label = "clean" | "microplastic" | "blocked";

const NAME: Record<Label, string> = { clean: "Clean", microplastic: "Microplastic", blocked: "Blocked" };
const STALE_MS = 10 * 60_000; // a photo this much older than the buoy's newest reading isn't current
const src = (key: string, attempt: number) => `/api/images/file?key=${encodeURIComponent(key)}${attempt ? `&retry=${attempt}` : ""}`;
const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The model's label, or null for a photo it hasn't checked or a label this page doesn't know. */
const labelOf = (p: Photo): Label | null => (p.check && p.check.label in NAME ? p.check.label : null);
const spotsOf = (p: Photo) => (Array.isArray(p.check?.spots) ? p.check.spots.filter((s) => Array.isArray(s?.bbox) && s.bbox.length === 4) : []);

/** One line on what the model saw. */
function detail(p: Photo) {
  const label = labelOf(p);
  if (!label) return "Waiting for the model";
  if (label === "clean") return "Nothing on the mesh";
  if (label === "blocked") return capital(p.check?.reason || "can't see the mesh");
  const n = spotsOf(p).length;
  return n ? `${n} spot${n === 1 ? "" : "s"} on the mesh` : "Plastic on the mesh";
}

/** The photo with a box around each spot. Retries a failed load once, then says so instead of a broken image. */
function Frame({ photo, alt }: { photo: Photo; alt: string }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<"loading" | "shown" | "failed">("loading");
  const spots = spotsOf(photo);
  const w = photo.check?.width || 640;
  const h = photo.check?.height || 480;

  return (
    <div className="camera-frame" data-state={state} style={{ aspectRatio: `${w} / ${h}` }}>
      {state === "failed" ? (
        <span className="camera-missing">Photo unavailable</span>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element -- private S3 photo through our own route
        <img
          src={src(photo.key, attempt)}
          alt={alt}
          decoding="async"
          draggable={false}
          onLoad={() => setState("shown")}
          onError={() => (attempt ? setState("failed") : setAttempt(1))}
        />
      )}
      {state === "shown" && spots.length > 0 && (
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
          {spots.map(({ bbox: [x0, y0, x1, y1] }, i) => (
            <rect key={i} x={x0 - 6} y={y0 - 6} width={x1 - x0 + 12} height={y1 - y0 + 12} rx={4} />
          ))}
        </svg>
      )}
    </div>
  );
}

/** What the camera's model made of its newest photo, beside the photo. Opens to the photo at full size. */
export default function CameraTile({ photo, lastReading }: { photo: Photo; lastReading: string | null }) {
  const [open, setOpen] = useState(false);
  const close = useRef<HTMLButtonElement>(null);
  const label = labelOf(photo);
  const stale = !!lastReading && Date.parse(lastReading) - Date.parse(photo.time) > STALE_MS;
  const alt = `Newest photo of the intake mesh, ${label ? NAME[label].toLowerCase() : "not checked yet"}`;

  useEffect(() => {
    if (open) close.current?.focus();
  }, [open]);

  const verdict = (
    <div className="reading-value reading-verdict" data-label={label ?? "none"}>
      {label ? NAME[label] : "Not checked"}
    </div>
  );
  const taken = (
    <div className="reading-note" data-stale={stale || undefined}>
      {stale ? "Camera last reported " : "Photo from "}
      {when(photo.time)}
    </div>
  );

  return (
    <div className="reading reading-camera">
      <div className="reading-camera-text">
        <div className="reading-label">Microplastics</div>
        {verdict}
        <div className="reading-note reading-detail">{detail(photo)}</div>
        {taken}
      </div>
      <button type="button" className="reading-photo" aria-label="Enlarge the camera photo" onClick={() => setOpen(true)}>
        {/* A new photo starts over: its own load, its own retry */}
        <Frame key={photo.key} photo={photo} alt={alt} />
      </button>

      {open && (
        <div
          className="camera-view"
          role="group"
          aria-label="Camera photo"
          onKeyDown={(e) => {
            if (e.key !== "Escape") return;
            e.stopPropagation(); // close the photo, not the buoy's window
            setOpen(false);
          }}
        >
          <Frame key={photo.key} photo={photo} alt={alt} />
          <div className="camera-view-side">
            <div className="reading-label">Microplastics</div>
            {verdict}
            <div className="reading-note reading-detail">{detail(photo)}</div>
            {taken}
            <div className="camera-view-actions">
              <a className="debug-btn" href="/images" target="_blank" rel="noreferrer">
                All photos
              </a>
              <button ref={close} type="button" className="debug-btn debug-btn-primary" onClick={() => setOpen(false)}>
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
