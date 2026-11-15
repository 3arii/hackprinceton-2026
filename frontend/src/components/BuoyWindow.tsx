"use client";

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

export type WindowState = {
  key: number;
  buoy: number;
  x: number;
  y: number;
  closing?: boolean;
};

type Props = {
  win: WindowState;
  title: string;
  subtitle: string;
  active: boolean;
  linked: boolean;
  z: number;
  onFocus(): void;
  onMove(x: number, y: number): void;
  onClose(): void;
  onHover(hovering: boolean): void;
  size?: { width: number; height: number };
  badge?: string;
  children?: ReactNode;
};

export const WINDOW_SIZE = { width: 480, height: 360 };
const EDGE = 8;

export default function BuoyWindow({
  win,
  title,
  subtitle,
  active,
  linked,
  z,
  onFocus,
  onMove,
  onClose,
  onHover,
  size: baseSize = WINDOW_SIZE,
  badge,
  children,
}: Props) {
  const [minimized, setMinimized] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const drag = useRef<{ dx: number; dy: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const titleId = `buoy-window-${win.key}`;

  const size = zoomed ? { width: baseSize.width * 1.5, height: baseSize.height * 1.4 } : baseSize;

  // Keep the window reachable when it grows or the viewport shrinks
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width } = el.getBoundingClientRect();
    const x = Math.min(Math.max(EDGE, win.x), window.innerWidth - width - EDGE);
    const y = Math.min(Math.max(EDGE, win.y), window.innerHeight - 52);
    if (x !== win.x || y !== win.y) onMove(x, y);
  }, [zoomed, win.x, win.y, onMove]);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    onFocus();
    if ((e.target as HTMLElement).closest("button")) return;
    drag.current = { dx: e.clientX - win.x, dy: e.clientY - win.y };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const width = ref.current?.offsetWidth ?? size.width;
    const x = Math.min(Math.max(EDGE, e.clientX - drag.current.dx), window.innerWidth - width - EDGE);
    const y = Math.min(Math.max(EDGE, e.clientY - drag.current.dy), window.innerHeight - 52);
    onMove(x, y);
  };

  const endDrag = () => (drag.current = null);

  return (
    <div
      ref={ref}
      role="dialog"
      aria-labelledby={titleId}
      className="mac-window"
      data-active={active || undefined}
      data-linked={linked || undefined}
      data-closing={win.closing || undefined}
      data-minimized={minimized || undefined}
      style={{
        transform: `translate3d(${win.x}px, ${win.y}px, 0)`,
        width: `min(${size.width}px, calc(100vw - ${EDGE * 2}px))`,
        height: minimized ? undefined : `min(${size.height}px, calc(100dvh - ${EDGE * 2}px))`,
        zIndex: z,
      }}
      onPointerDownCapture={onFocus}
      onPointerEnter={() => onHover(true)}
      onPointerLeave={() => onHover(false)}
      onKeyDown={(e) => e.key === "Escape" && onClose()}
    >
      <div
        className="mac-titlebar"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={(e) => !(e.target as HTMLElement).closest("button") && setZoomed((v) => !v)}
      >
        <div className="mac-lights">
          <button type="button" className="mac-light mac-close" aria-label={`Close ${title}`} onClick={onClose}>
            <svg viewBox="0 0 12 12" aria-hidden="true">
              <path d="M3.5 3.5l5 5M8.5 3.5l-5 5" />
            </svg>
          </button>
          <button
            type="button"
            className="mac-light mac-min"
            aria-label={minimized ? `Expand ${title}` : `Minimize ${title}`}
            aria-pressed={minimized}
            onClick={() => setMinimized((v) => !v)}
          >
            <svg viewBox="0 0 12 12" aria-hidden="true">
              <path d="M3 6h6" />
            </svg>
          </button>
          <button
            type="button"
            className="mac-light mac-zoom"
            aria-label={zoomed ? `Restore ${title}` : `Enlarge ${title}`}
            aria-pressed={zoomed}
            onClick={() => setZoomed((v) => !v)}
          >
            <svg viewBox="0 0 12 12" aria-hidden="true">
              <path d="M3.4 8.6V4.9l3.7 3.7zM8.6 3.4v3.7L4.9 3.4z" className="fill" />
            </svg>
          </button>
        </div>
        <div className="mac-title">
          <span id={titleId}>{title}</span>
          <span className="mac-subtitle">{subtitle}</span>
        </div>
        {badge && <span className="mac-badge">{badge}</span>}
      </div>
      {!minimized && <div className="mac-body">{children}</div>}
    </div>
  );
}
