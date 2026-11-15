"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNow } from "@/components/BuoyReadings";
import type { CaseDetail, CaseSummary, Lane, Step, Todo, Verdict } from "@/lib/steward";

const SPECIALISTS = ["hydrology", "satellite", "permits", "community", "history"] as const;

const AGENT_INFO: Record<string, { label: string; role: string }> = {
  hydrology: { label: "Hydrology", role: "USGS river gauge · weather" },
  satellite: { label: "Satellite", role: "Sentinel-2 · AWS Open Data" },
  permits: { label: "Permits", role: "EPA discharge permits" },
  community: { label: "Community", role: "Bluesky posts · who links to whom" },
  history: { label: "History", role: "Buoy network · past cases" },
};

const VERDICT: Record<Verdict["verdict"], { label: string; color: string }> = {
  benign: { label: "Benign", color: "#30d158" },
  monitor: { label: "Monitor", color: "#ffd60a" },
  warn: { label: "Warn", color: "#ff453a" },
};

const TOOL_LABEL: Record<string, string> = {
  river_gauge: "Pulled the USGS river gauge",
  weather: "Pulled weather and 10-year normals",
  buoy_network: "Compared all four buoys",
  analyze_lake_imagery: "Measured the lake in Sentinel-2 scenes",
  satellite_scenes: "Listed Sentinel-2 scenes",
  permitted_dischargers: "Listed EPA-permitted dischargers",
  facility_compliance: "Checked a facility’s compliance",
  past_incidents: "Looked up past incidents",
  search_bluesky: "Searched Bluesky",
  analyze_conversation: "Mapped the conversation network",
  list_async_tasks: "Checked on the specialists",
  check_async_task: "Checked a specialist",
  send_alert: "Alerted the lake manager",
  read_file: "Read a file",
  write_file: "Wrote a file",
  edit_file: "Edited a file",
};

const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const elapsed = (from: string, to: number) => {
  const s = Math.max(0, Math.round((to - Date.parse(from)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const fileUrl = (caseId: string, key: string) =>
  `/api/steward/file?case=${encodeURIComponent(caseId)}&key=${encodeURIComponent(key)}`;

// --- Data -----------------------------------------------------------------------------

function useSteward() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null); // null follows the newest case
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [offline, setOffline] = useState(false);
  const current = selected ?? cases[0]?.threadId ?? null;
  const settled = useRef(new Set<string>());

  useEffect(() => {
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const res = await fetch("/api/steward/cases");
        if (!res.ok) throw new Error(res.statusText);
        const body: CaseSummary[] = await res.json();
        if (stop) return;
        setCases(body);
        setOffline(false);
      } catch {
        if (!stop) setOffline(true);
      }
      if (!stop) timer = window.setTimeout(tick, 2500);
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, []);

  // The open case, polled every second while anything in it runs
  useEffect(() => {
    if (!current) return;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      let busy = true;
      if (!settled.current.has(current)) {
        try {
          const res = await fetch(`/api/steward/case?thread=${current}`);
          const body: CaseDetail = await res.json();
          if (stop) return;
          if (res.ok) {
            setDetail(body);
            busy = body.busy;
            if (!body.busy && body.verdict) settled.current.add(current);
          }
        } catch {
          // keep the last good view
        }
      }
      if (!stop) timer = window.setTimeout(tick, busy ? 1000 : 4000);
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [current]);

  const shown = detail && detail.threadId === current ? detail : null;
  return { cases, current, selected, setSelected, detail: shown, offline };
}

// --- Small pieces ---------------------------------------------------------------------

function Markdownish({ text }: { text: string }) {
  // Enough markdown for agent notes: headings, bullets, bold
  const inline = (s: string) =>
    s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : part,
    );
  // Group consecutive table rows so they render as one table
  const blocks: (string | string[][])[] = [];
  for (const line of text.split("\n")) {
    if (/^\s*\|.*\|\s*$/.test(line)) {
      if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) continue; // header separator
      const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim());
      const last = blocks.at(-1);
      if (Array.isArray(last)) last.push(cells);
      else blocks.push([cells]);
    } else blocks.push(line);
  }
  return (
    <div className="md">
      {blocks.map((line, i) => {
        if (Array.isArray(line)) {
          const [head, ...rows] = line;
          return (
            <div key={i} className="md-table-wrap">
              <table className="md-table">
                <thead>
                  <tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr>
                </thead>
                <tbody>
                  {rows.map((r, k) => (
                    <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        if (/^#{1,6}\s/.test(line)) return <p key={i} className="md-h">{inline(line.replace(/^#+\s/, ""))}</p>;
        if (/^\s*[-*]\s/.test(line)) return <p key={i} className="md-li">{inline(line.replace(/^\s*[-*]\s/, ""))}</p>;
        if (!line.trim()) return <span key={i} className="md-gap" />;
        return <p key={i}>{inline(line)}</p>;
      })}
    </div>
  );
}

function Disclosure({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="disc" data-open={open || undefined}>
      <button type="button" className="disc-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="disc-summary">{summary}</span>
        <svg viewBox="0 0 10 10" aria-hidden="true" className="disc-chevron">
          <path d="M3 2l3 3-3 3" />
        </svg>
      </button>
      {open && <div className="disc-body">{children}</div>}
    </div>
  );
}

function prettyJson(s?: string) {
  if (!s) return "";
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

function VerdictCard({ v }: { v: Verdict }) {
  const meta = VERDICT[v.verdict] ?? VERDICT.monitor;
  return (
    <div className="verdict" style={{ ["--verdict" as string]: meta.color }}>
      <div className="verdict-head">
        <span className="verdict-label">
          <span className="verdict-dot" />
          {meta.label}
        </span>
        <span className="verdict-conf">{Math.round(Number(v.confidence) * 100)}% confident</span>
      </div>
      <p className="verdict-cause">{v.cause}</p>
      <p className="verdict-summary">{v.summary}</p>
      {v.evidence?.length > 0 && (
        <ul className="verdict-evidence">
          {v.evidence.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
      <p className="verdict-foot">{v.alerted ? "Lake manager alerted by email (Amazon SNS)" : "No alert sent"}</p>
    </div>
  );
}

// --- Timelines ------------------------------------------------------------------------

function ToolView({ step, caseId, live }: { step: Extract<Step, { kind: "tool" }>; caseId: string; live: boolean }) {
  const path = String(step.args.file_path ?? "");
  const pending = step.result === undefined;
  const state = pending ? (live ? "running" : "stopped") : step.ok === false ? "failed" : "ok";

  if (step.name === "write_todos" || step.name === "submit_verdict") return null;

  if (step.name === "start_async_task" || step.name === "update_async_task") {
    const agent = String(step.args.subagent_type ?? "");
    const launch = step.name === "start_async_task";
    return (
      <li className="tl tl-launch" data-state={state}>
        <Disclosure
          summary={
            <>
              <span className="tl-kicker">{launch ? "Launched" : "Steered"}</span>
              <strong>{launch ? (AGENT_INFO[agent]?.label ?? agent) : "a running specialist"}</strong>
              {launch && <span className="tl-note">async</span>}
            </>
          }
        >
          <p className="tl-task">{String(step.args.description ?? step.args.message ?? "")}</p>
        </Disclosure>
      </li>
    );
  }

  let label = TOOL_LABEL[step.name] ?? step.name;
  let body: ReactNode = (
    <>
      <code className="tl-call">
        {step.name}({Object.entries(step.args).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(", ")})
      </code>
      {step.result && <pre className="tl-output">{prettyJson(step.result)}</pre>}
    </>
  );
  let image: string | null = null;

  if (step.name === "search_bluesky") label = `Searched Bluesky for “${String(step.args.query ?? "")}”`;
  else if (step.name === "read_file" && path.startsWith("/skills/")) label = `Loaded the ${path.split("/")[2]} playbook`;
  else if (step.name === "read_file" && path.startsWith("/memories/")) label = "Read long-term memory";
  else if (step.name === "read_file" && path.endsWith(".png")) {
    label = "Looked at the satellite image";
    image = fileUrl(caseId, path.replace(/^\/case/, ""));
  } else if (step.name === "read_file" && path.startsWith("/case/evidence/")) {
    label = `Read the ${path.split("/").pop()?.replace(".md", "")} evidence`;
  } else if ((step.name === "write_file" || step.name === "edit_file") && path.startsWith("/case/")) {
    label = `Wrote ${path.replace("/case/", "")}`;
    body = <Markdownish text={String(step.args.content ?? step.args.new_string ?? "")} />;
  } else if ((step.name === "write_file" || step.name === "edit_file") && path.startsWith("/memories/")) {
    label = "Saved a lesson to long-term memory";
    body = <p className="tl-task">{String(step.args.new_string ?? step.args.content ?? "")}</p>;
  }

  return (
    <li className="tl tl-tool" data-state={state}>
      <Disclosure
        summary={
          <>
            <span className="tl-tool-label">{label}</span>
            {state === "running" && <span className="tl-spin" aria-label="running" />}
          </>
        }
      >
        {body}
      </Disclosure>
      {image && !pending && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="tl-image" src={image} alt="Sentinel-2 true-color image of Lake Carnegie, water outlined in blue, buoys in red" />
      )}
    </li>
  );
}

function StepView({ step, caseId, live }: { step: Step; caseId: string; live: boolean }) {
  switch (step.kind) {
    case "user":
      return null;
    case "thinking":
      return (
        <li className="tl tl-thinking">
          <Disclosure
            summary={
              <>
                <span className="tl-kicker">Thinking</span>
                <span className="tl-clamp">{step.text}</span>
              </>
            }
          >
            <p className="tl-thought">{step.text}</p>
          </Disclosure>
        </li>
      );
    case "text":
      return (
        <li className="tl tl-text">
          <Markdownish text={step.text} />
        </li>
      );
    case "notification":
      return (
        <li className="tl tl-notify" data-status={step.status}>
          <Disclosure
            summary={
              <>
                <span className="tl-kicker">{step.status === "success" ? "Reported back" : cap(step.status)}</span>
                <strong>{AGENT_INFO[step.agent]?.label ?? step.agent}</strong>
              </>
            }
          >
            <Markdownish text={step.text} />
          </Disclosure>
        </li>
      );
    case "tool":
      return <ToolView step={step} caseId={caseId} live={live} />;
  }
}

function Plan({ todos }: { todos: Todo[] }) {
  if (!todos.length) return null;
  const done = todos.filter((t) => t.status === "completed").length;
  return (
    <section className="sec" aria-label="Plan">
      <div className="sec-head">
        <span>Plan</span>
        <span className="sec-meta">
          {done}/{todos.length}
        </span>
      </div>
      <ul className="plan">
        {todos.map((t, i) => (
          <li key={i} data-status={t.status}>
            <span className="plan-mark" aria-hidden="true" />
            <span>{t.content}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function LaneTile({ name, lane, active, onSelect }: { name: string; lane?: Lane; active: boolean; onSelect(): void }) {
  const info = AGENT_INFO[name];
  const tools = (lane?.steps ?? []).filter((s): s is Extract<Step, { kind: "tool" }> => s.kind === "tool");
  const last = tools.at(-1);
  const status = !lane ? "idle" : lane.busy ? "running" : lane.status;
  const doing = last ? (TOOL_LABEL[last.name] ?? last.name) : "Starting…";
  return (
    <button type="button" className="lane" data-status={status} aria-pressed={active} onClick={onSelect}>
      <span className="lane-top">
        <span className="lane-dot" aria-hidden="true" />
        <span className="lane-name">{info.label}</span>
        {lane && <span className="lane-count">{tools.length}</span>}
      </span>
      <span className="lane-role">
        {!lane ? info.role : status === "running" ? doing : status === "success" ? "Reported back" : cap(status)}
      </span>
    </button>
  );
}


// --- Conversation network (community specialist) -------------------------------------

type NetNode = { id: string; handle: string; cluster: number; posts: number; followers: number; local: boolean };
type Network = { nodes: NetNode[]; edges: { source: string; target: string; kinds: string[] }[] };
const CLUSTER_COLORS = ["#0a84ff", "#ff9f0a", "#30d158", "#bf5af2", "#64d2ff", "#ff375f", "#ffd60a", "#ac8e68"];

function layout(net: Network, w: number, h: number) {
  // Small deterministic force layout: repulsion, springs on edges, gentle pull to the centre
  const n = net.nodes.length;
  const pos = net.nodes.map((_, i) => {
    const a = (i / Math.max(1, n)) * Math.PI * 2;
    return { x: w / 2 + Math.cos(a) * w * 0.12, y: h / 2 + Math.sin(a) * h * 0.12 };
  });
  const index = new Map(net.nodes.map((nd, i) => [nd.id, i]));
  const links = net.edges.map((e) => [index.get(e.source), index.get(e.target)]).filter((l): l is [number, number] => l[0] !== undefined && l[1] !== undefined);
  for (let step = 0; step < 220; step++) {
    const cool = 1 - step / 220;
    const force = pos.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) {
        const dx = pos[i].x - pos[j].x;
        const dy = pos[i].y - pos[j].y;
        const d2 = Math.max(25, dx * dx + dy * dy);
        const f = 140 / d2;
        force[i].x += dx * f; force[i].y += dy * f;
        force[j].x -= dx * f; force[j].y -= dy * f;
      }
    for (const [a, b] of links) {
      const dx = pos[b].x - pos[a].x;
      const dy = pos[b].y - pos[a].y;
      force[a].x += dx * 0.04; force[a].y += dy * 0.04;
      force[b].x -= dx * 0.04; force[b].y -= dy * 0.04;
    }
    pos.forEach((p, i) => {
      // Stronger pull across the wide axis keeps isolated accounts from piling up on the edges
      force[i].x += (w / 2 - p.x) * 0.035;
      force[i].y += (h / 2 - p.y) * 0.07;
      p.x = Math.min(w - 14, Math.max(14, p.x + Math.max(-12, Math.min(12, force[i].x)) * cool));
      p.y = Math.min(h - 14, Math.max(14, p.y + Math.max(-12, Math.min(12, force[i].y)) * cool));
    });
  }
  return { pos, links: net.edges.map((e) => ({ a: index.get(e.source)!, b: index.get(e.target)!, copy: e.kinds.includes("same-text") })).filter((l) => l.a !== undefined && l.b !== undefined) };
}

function NetworkGraph({ caseId }: { caseId: string }) {
  const [net, setNet] = useState<Network | null>(null);
  useEffect(() => {
    let stop = false;
    fetch(fileUrl(caseId, "/social/network.json"))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => !stop && setNet(d));
    return () => {
      stop = true;
    };
  }, [caseId]);
  const W = 420;
  const H = 230;
  const drawn = useMemo(() => (net && net.nodes.length ? layout(net, W, H) : null), [net]);
  if (!net || !drawn) return null;
  const clusters = new Set(net.nodes.map((nd) => nd.cluster)).size;
  return (
    <figure className="netgraph">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Conversation network: ${net.nodes.length} accounts in ${clusters} communities, ${net.edges.length} links`}>
        {drawn.links.map((l, i) => (
          <line
            key={i}
            x1={drawn.pos[l.a].x}
            y1={drawn.pos[l.a].y}
            x2={drawn.pos[l.b].x}
            y2={drawn.pos[l.b].y}
            className={l.copy ? "net-edge net-copy" : "net-edge"}
          />
        ))}
        {net.nodes.map((nd, i) => (
          <circle
            key={nd.id}
            cx={drawn.pos[i].x}
            cy={drawn.pos[i].y}
            r={3 + Math.min(6, Math.sqrt(nd.posts) * 1.6)}
            fill={CLUSTER_COLORS[nd.cluster % CLUSTER_COLORS.length] ?? "#8e8e93"}
            className={nd.local ? "net-node net-local" : "net-node"}
          >
            <title>
              {nd.handle} · {nd.posts} post{nd.posts === 1 ? "" : "s"} · {nd.followers} followers{nd.local ? " · mentions local places" : ""}
            </title>
          </circle>
        ))}
      </svg>
      <figcaption>
        <span>{net.nodes.length} accounts · {clusters} communities · {net.edges.length} links</span>
        <span className="net-key">
          <i className="net-key-line" /> reply, quote, repost, follow
          <i className="net-key-line net-key-copy" /> same text
          <i className="net-key-ring" /> mentions local places
        </span>
      </figcaption>
    </figure>
  );
}

// --- Tabs -----------------------------------------------------------------------------

const TAB_KEY = "steward-tab-order";

/** Tab order the viewer chose, kept per browser. New tabs join at the end. */
function useTabOrder(ids: string[]) {
  const [order, setOrder] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(TAB_KEY) ?? "[]") as string[];
      return [...saved.filter((id) => ids.includes(id)), ...ids.filter((id) => !saved.includes(id))];
    } catch {
      return ids;
    }
  });
  const save = (next: string[]) => {
    setOrder(next);
    try {
      localStorage.setItem(TAB_KEY, JSON.stringify(next));
    } catch {}
  };
  return [order, save] as const;
}

type Tab = { id: string; label: string; live?: boolean };
type Grab = { id: string; from: number; x0: number; lefts: number[]; widths: number[]; moved: boolean };

/** Browser-style tabs: size to their labels, scroll when they overflow, drag to reorder. */
function TabBar({ tabs, value, onSelect, onReorder }: {
  tabs: Tab[];
  value: string;
  onSelect(id: string): void;
  onReorder(ids: string[]): void;
}) {
  const bar = useRef<HTMLDivElement>(null);
  const grab = useRef<Grab | null>(null);
  const dragged = useRef(false);
  const [drag, setDrag] = useState<(Grab & { dx: number; to: number }) | null>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  const measure = () => {
    const el = bar.current;
    if (!el) return;
    const left = el.scrollLeft > 1;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setEdges((e) => (e.left === left && e.right === right ? e : { left, right }));
  };

  useEffect(() => {
    const el = bar.current;
    if (!el) return;
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    // A vertical wheel scrolls the strip sideways, like a browser's tab strip
    const wheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
      e.preventDefault();
      el.scrollLeft += e.deltaY;
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => {
      ro.disconnect();
      el.removeEventListener("wheel", wheel);
    };
  }, []);

  // Keep the selected tab in view
  useEffect(() => {
    bar.current?.querySelector<HTMLElement>(`[data-tab="${CSS.escape(value)}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [value]);

  const x = (e: React.PointerEvent) => e.clientX - bar.current!.getBoundingClientRect().left + bar.current!.scrollLeft;

  const down = (e: React.PointerEvent<HTMLButtonElement>, i: number) => {
    if (e.button !== 0 || !bar.current) return;
    dragged.current = false;
    const buttons = [...bar.current.querySelectorAll<HTMLElement>("[data-tab]")];
    grab.current = {
      id: tabs[i].id,
      from: i,
      x0: x(e),
      lefts: buttons.map((b) => b.offsetLeft),
      widths: buttons.map((b) => b.offsetWidth),
      moved: false,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const move = (e: React.PointerEvent) => {
    const g = grab.current;
    const el = bar.current;
    if (!g || !el) return;
    // Nudge the strip when the pointer reaches an edge
    const r = el.getBoundingClientRect();
    if (e.clientX < r.left + 24) el.scrollLeft -= 8;
    else if (e.clientX > r.right - 24) el.scrollLeft += 8;
    let dx = x(e) - g.x0;
    if (!g.moved && Math.abs(dx) < 4) return;
    g.moved = true;
    const { lefts, widths, from } = g;
    const n = lefts.length;
    dx = Math.max(lefts[0] - lefts[from], Math.min(dx, lefts[n - 1] + widths[n - 1] - widths[from] - lefts[from]));
    const center = lefts[from] + widths[from] / 2 + dx;
    let to = from;
    for (let j = 0; j < n; j++) {
      const mid = lefts[j] + widths[j] / 2;
      if (j > from && center > mid) to = j;
      if (j < from && center < mid) to = Math.min(to, j);
    }
    setDrag({ ...g, dx, to });
  };

  const up = () => {
    const g = grab.current;
    grab.current = null;
    if (!g?.moved) return;
    dragged.current = true;
    if (drag && drag.to !== g.from) {
      const ids = tabs.map((t) => t.id);
      ids.splice(drag.to, 0, ...ids.splice(g.from, 1));
      onReorder(ids);
    }
    setDrag(null);
  };

  const shift = (j: number) => {
    if (!drag) return 0;
    const { from, to, lefts, widths } = drag;
    if (j === from) return drag.dx;
    const w = widths[from] + (lefts[1] - lefts[0] - widths[0] || 0);
    if (from < to && j > from && j <= to) return -w;
    if (to < from && j >= to && j < from) return w;
    return 0;
  };

  const key = (e: React.KeyboardEvent, i: number) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const j = Math.max(0, Math.min(tabs.length - 1, i + step));
    if (j === i) return;
    if (e.altKey) {
      // Option + arrow moves the tab itself
      const ids = tabs.map((t) => t.id);
      ids.splice(j, 0, ...ids.splice(i, 1));
      onReorder(ids);
    } else onSelect(tabs[j].id);
    requestAnimationFrame(() =>
      bar.current?.querySelector<HTMLElement>(`[data-tab="${CSS.escape(e.altKey ? tabs[i].id : tabs[j].id)}"]`)?.focus(),
    );
  };

  return (
    <div className="tabs-wrap" data-fade-left={edges.left || undefined} data-fade-right={edges.right || undefined}>
      <div
        ref={bar}
        className="tabs"
        role="tablist"
        aria-label="Agent"
        data-dragging={drag ? "" : undefined}
        onScroll={measure}
      >
        {tabs.map((t, i) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            data-tab={t.id}
            aria-selected={value === t.id}
            tabIndex={value === t.id ? 0 : -1}
            title="Drag to reorder"
            data-live={t.live || undefined}
            data-grabbed={drag?.from === i || undefined}
            style={drag ? { transform: `translateX(${shift(i)}px)` } : undefined}
            onPointerDown={(e) => down(e, i)}
            onPointerMove={move}
            onPointerUp={up}
            onPointerCancel={() => {
              grab.current = null;
              setDrag(null);
            }}
            onClick={() => {
              if (dragged.current) dragged.current = false;
              else onSelect(t.id);
            }}
            onKeyDown={(e) => key(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// --- Panel ----------------------------------------------------------------------------

export default function AgentPanel({ onExpand }: { onExpand?(expanded: boolean): void }) {
  const { cases, current, selected, setSelected, detail, offline } = useSteward();
  const [collapsed, setCollapsed] = useState(true);
  const [view, setView] = useState<string>("lead");
  const [tabOrder, setTabOrder] = useTabOrder(["lead", ...SPECIALISTS]);
  const [file, setFile] = useState<{ key: string; image: boolean } | null>(null);
  const [fileText, setFileText] = useState("");
  const [seen, setSeen] = useState<string | null>(null);
  const now = useNow(detail?.busy ? 1000 : 30_000);
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // A new investigation opens the panel and follows it
  const newest = cases[0]?.threadId ?? null;
  if (newest && newest !== seen) {
    setSeen(newest);
    if (seen !== null) {
      setCollapsed(false);
      setSelected(null);
      setView("lead");
      setFile(null);
    }
  }

  useEffect(() => onExpand?.(!collapsed), [collapsed, onExpand]);

  useEffect(() => {
    if (!file || file.image || !detail) return;
    let stop = false;
    fetch(fileUrl(detail.caseId, file.key))
      .then((r) => r.text())
      .then((t) => !stop && setFileText(t));
    return () => {
      stop = true;
    };
  }, [file, detail]);

  const lane = detail?.lanes.find((l) => l.name === view);
  const steps = view === "lead" ? (detail?.steps ?? []) : (lane?.steps ?? []);
  const running = detail?.lanes.filter((l) => l.busy) ?? [];

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && pinned.current && detail?.busy) el.scrollTop = el.scrollHeight;
  }, [steps.length, detail?.busy]);

  const status = offline
    ? { text: "Agent Server unreachable", tone: "error" }
    : !detail
      ? { text: "Idle", tone: "idle" }
      : detail.busy
        ? { text: `Investigating · ${elapsed(detail.createdAt, now)}`, tone: "live" }
        : detail.verdict
          ? { text: VERDICT[detail.verdict.verdict]?.label ?? detail.verdict.verdict, tone: detail.verdict.verdict }
          : { text: "Stopped", tone: "idle" };

  const evidence = detail?.files.filter((f) => f.key.startsWith("/evidence/")) ?? [];
  const images = detail?.files.filter((f) => f.image) ?? [];

  return (
    <section className="agent-panel" data-collapsed={collapsed || undefined} aria-label="Lake Steward agent">
      <header className="agent-head">
        <button type="button" className="agent-title" onClick={() => setCollapsed((v) => !v)} aria-expanded={!collapsed}>
          <span className="agent-status-dot" data-tone={status.tone} aria-hidden="true" />
          <span>Lake Steward</span>
          <span className="agent-status">
            {status.text}
            {collapsed && detail && ` · Buoy ${detail.buoy} ${detail.metric}`}
          </span>
        </button>
        <button
          type="button"
          className="agent-collapse"
          aria-label={collapsed ? "Expand agent panel" : "Collapse agent panel"}
          onClick={() => setCollapsed((v) => !v)}
        >
          <svg viewBox="0 0 12 12" aria-hidden="true">
            <path d={collapsed ? "M3 7.5 6 4.5l3 3" : "M3 4.5 6 7.5l3-3"} />
          </svg>
        </button>
      </header>

      {!collapsed && (
        <>
          {cases.length > 0 && (
            <div className="agent-runs">
              <select
                value={selected ?? ""}
                onChange={(e) => {
                  setSelected(e.target.value || null);
                  setView("lead");
                  setFile(null);
                }}
                aria-label="Investigation"
              >
                <option value="">Latest case</option>
                {cases.map((c) => (
                  <option key={c.threadId} value={c.threadId}>
                    {clock(c.createdAt)} · Buoy {c.buoy} {c.metric}
                    {c.busy ? " · running" : ""}
                  </option>
                ))}
              </select>
              <span className="agent-run-meta">deepagents · async subagents</span>
            </div>
          )}

          <div
            ref={scroller}
            className="agent-scroll"
            onScroll={(e) => {
              const el = e.currentTarget;
              pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
            }}
          >
            {!detail ? (
              <p className="agent-empty">
                {offline
                  ? "Can’t reach the Lake Steward’s Agent Server. Check STEWARD_URL in frontend/.env.local."
                  : current
                    ? "Loading the case…"
                    : "No investigations yet. Turn on Debug, open a buoy and send an extreme reading."}
              </p>
            ) : (
              <>
                <div className="case-anomaly">
                  <span className="sec-kicker">
                    Anomaly · {clock(detail.createdAt)} · {detail.source}
                  </span>
                  <p>
                    <strong>
                      Buoy {detail.buoy} {detail.metric} {detail.value}
                      {detail.unit && ` ${detail.unit}`}
                    </strong>
                    {" · "}
                    {detail.reason}
                  </p>
                </div>

                {detail.verdict && <VerdictCard v={detail.verdict} />}

                <Plan todos={detail.todos} />

                <section className="sec" aria-label="Specialists">
                  <div className="sec-head">
                    <span>Specialists</span>
                    <span className="sec-meta">async · own threads · in parallel</span>
                  </div>
                  <div className="lane-grid">
                    {SPECIALISTS.map((n) => (
                      <LaneTile
                        key={n}
                        name={n}
                        lane={detail.lanes.find((l) => l.name === n)}
                        active={view === n}
                        onSelect={() => setView((v) => (v === n ? "lead" : n))}
                      />
                    ))}
                  </div>
                </section>

                <section className="sec">
                  <TabBar
                    tabs={tabOrder.map((n) => ({
                      id: n,
                      label: n === "lead" ? "Lead" : AGENT_INFO[n].label,
                      live: n !== "lead" && !!detail.lanes.find((l) => l.name === n)?.busy,
                    }))}
                    value={view}
                    onSelect={setView}
                    onReorder={setTabOrder}
                  />
                  {view === "community" && detail.files.some((f) => f.key === "/social/network.json") && (
                    <NetworkGraph caseId={detail.caseId} />
                  )}
                  {view !== "lead" && lane?.task && (
                    <div className="tl-assignment">
                      <span className="sec-kicker">Task from the lead</span>
                      <p>{lane.task}</p>
                    </div>
                  )}
                  <ol className="tl-list" role="tabpanel">
                    {steps.map((s, i) => (
                      <StepView key={i} step={s} caseId={detail.caseId} live={view === "lead" ? detail.busy : !!lane?.busy} />
                    ))}
                    {view === "lead" && detail.busy && (
                      <li className="tl tl-pending">
                        <span className="agent-shimmer">
                          {running.length
                            ? `Waiting on ${running.map((l) => AGENT_INFO[l.name]?.label ?? l.name).join(", ")}`
                            : "Thinking…"}
                        </span>
                      </li>
                    )}
                    {view !== "lead" && lane?.busy && (
                      <li className="tl tl-pending">
                        <span className="agent-shimmer">Working…</span>
                      </li>
                    )}
                    {view !== "lead" && !lane && <li className="tl tl-empty">Not launched for this case.</li>}
                  </ol>
                </section>

                {(evidence.length > 0 || images.length > 0) && (
                  <section className="sec" aria-label="Case files">
                    <div className="sec-head">
                      <span>Case files</span>
                      <span className="sec-meta">/case/ · shared by every agent</span>
                    </div>
                    <div className="evidence-files">
                      {images.map((f) => (
                        <button key={f.key} type="button" className="evidence-thumb" onClick={() => setFile(f)} aria-label="Open the satellite image">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={fileUrl(detail.caseId, f.key)} alt="" />
                        </button>
                      ))}
                      {evidence.map((f) => (
                        <button key={f.key} type="button" className="evidence-file" onClick={() => setFile(f)}>
                          {f.key.replace("/evidence/", "")}
                        </button>
                      ))}
                    </div>
                    {detail.memoryUpdated && <p className="evidence-note">Saved a lesson to /memories/AGENTS.md</p>}
                  </section>
                )}
              </>
            )}
          </div>

          {file && detail && (
            <div className="file-view" role="dialog" aria-label={`/case${file.key}`}>
              <div className="file-view-head">
                <code>/case{file.key}</code>
                <button type="button" className="agent-collapse" aria-label="Close file" onClick={() => setFile(null)}>
                  <svg viewBox="0 0 12 12" aria-hidden="true">
                    <path d="M3.5 3.5l5 5M8.5 3.5l-5 5" />
                  </svg>
                </button>
              </div>
              <div className="file-view-body">
                {file.image ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={fileUrl(detail.caseId, file.key)} alt="Sentinel-2 true-color image of Lake Carnegie" />
                ) : (
                  <Markdownish text={fileText} />
                )}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
