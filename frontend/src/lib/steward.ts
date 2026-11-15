// Server-side client for the Lake Steward deep agent on the LangGraph Agent Server.
// Route handlers use it; the browser only ever sees the compact shapes below.

const BASE = (process.env.STEWARD_URL ?? "").replace(/\/$/, "");
const HEADERS: Record<string, string> = {
  "Content-Type": "application/json",
  ...(process.env.STEWARD_KEY ? { "x-api-key": process.env.STEWARD_KEY } : {}),
};

export const stewardConfigured = () => BASE.length > 0;

async function call<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: init?.method ?? (init?.body ? "POST" : "GET"),
    headers: HEADERS,
    body: init?.body ? JSON.stringify(init.body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Agent Server ${path}: ${res.status}`);
  return res.json() as Promise<T>;
}

// --- Opening a case ---------------------------------------------------------------

export type Reading = { buoy: number; time: string; run?: string } & Record<string, unknown>;
export type Anomaly = { metric: string; value: number; unit?: string; reason: string };

// Hard ceiling on investigations, so model spend can't run away (about $0.25 each)
export const CASE_LIMIT = Number(process.env.CASE_LIMIT ?? 40);

export async function casesOpened() {
  return call<number>("/threads/count", { body: { metadata: { kind: "case" } } });
}

export async function openCase(reading: Reading, anomaly: Anomaly) {
  const stamp = reading.time.slice(0, 19).replace(/[-:]/g, "");
  const caseCtx = {
    case_id: `${stamp}-b${reading.buoy}-${anomaly.metric}`,
    buoy: reading.buoy,
    metric: anomaly.metric,
    value: anomaly.value,
    unit: anomaly.unit ?? "",
    reason: anomaly.reason,
    reading_time: reading.time,
    run: reading.run ?? null,
  };
  const thread = await call<{ thread_id: string }>("/threads", {
    body: { metadata: { kind: "case", ...caseCtx } },
  });
  await call(`/threads/${thread.thread_id}/runs`, {
    body: {
      assistant_id: "steward",
      input: {
        messages: [
          {
            role: "user",
            content: `New anomaly. Buoy ${caseCtx.buoy}, ${caseCtx.metric} = ${caseCtx.value} ${caseCtx.unit} at ${caseCtx.reading_time}. ${caseCtx.reason}. Investigate and decide.`,
          },
        ],
      },
      context: caseCtx,
    },
  });
  return { caseId: caseCtx.case_id, threadId: thread.thread_id };
}

// --- iMessage conversations with the lake manager -------------------------------------

export const CHAT_LIMIT = Number(process.env.CHAT_LIMIT ?? 80);

type ChatContext = { phone: string; space_id: string; agent_id: string; message_id: string };

/** Hand one incoming text to the liaison agent, continuing that person's conversation thread. */
export async function chat(text: string, ctx: ChatContext) {
  const found = await call<RawThread[]>("/threads/search", {
    body: { metadata: { kind: "chat", phone: ctx.phone }, limit: 1 },
  });
  const thread = found[0] ?? (await call<RawThread>("/threads", { body: { metadata: { kind: "chat", phone: ctx.phone } } }));
  // Count replies on this thread too, so a chatty demo can't run up the bill
  const runs = await call<unknown[]>(`/threads/${thread.thread_id}/runs?limit=${CHAT_LIMIT}`);
  if (runs.length >= CHAT_LIMIT) throw new Error("chat budget used up");
  await call(`/threads/${thread.thread_id}/runs`, {
    body: {
      assistant_id: "liaison",
      input: { messages: [{ role: "user", content: text }] },
      // A case for any live check the liaison launches: the text itself, at the time it arrived
      context: {
        ...ctx,
        case_id: `chat-${ctx.message_id}`,
        buoy: 0,
        metric: "check",
        value: 0,
        unit: "",
        reason: text,
        reading_time: new Date().toISOString(),
      },
      multitask_strategy: "enqueue",
    },
  });
}

// --- Reading a case -----------------------------------------------------------------

type Block = { type: string; text?: string; thinking?: string; name?: string; input?: unknown; id?: string };
type RawMessage = {
  type: "human" | "ai" | "tool" | "system";
  content: string | Block[];
  tool_calls?: { id: string; name: string; args: Record<string, unknown> }[];
  tool_call_id?: string;
  name?: string;
  status?: string;
};
type RawThread = {
  thread_id: string;
  created_at: string;
  updated_at: string;
  status: "idle" | "busy" | "interrupted" | "error";
  metadata: Record<string, unknown>;
};

export type Step =
  | { kind: "user"; text: string }
  | { kind: "notification"; agent: string; status: string; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "text"; text: string }
  | { kind: "tool"; id: string; name: string; args: Record<string, unknown>; result?: string; ok?: boolean };

export type Todo = { content: string; status: "pending" | "in_progress" | "completed" };
export type Lane = {
  name: string;
  threadId: string;
  status: string; // running | success | error | cancelled | waiting | ...
  busy: boolean;
  task?: string;
  steps: Step[];
};
export type Verdict = {
  verdict: "benign" | "monitor" | "warn";
  cause: string;
  confidence: number;
  summary: string;
  evidence: string[];
  alerted: boolean;
};
export type CaseSummary = {
  threadId: string;
  caseId: string;
  createdAt: string;
  updatedAt: string;
  busy: boolean;
  buoy: number;
  metric: string;
  value: number;
  unit: string;
  reason: string;
  source: string;
};
export type CaseDetail = CaseSummary & {
  steps: Step[];
  todos: Todo[];
  lanes: Lane[];
  files: { key: string; size: number; image: boolean }[];
  verdict: Verdict | null;
  memoryUpdated: boolean;
};

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}…` : s);
const textOf = (content: RawMessage["content"]) =>
  typeof content === "string" ? content : content.map((b) => b.text ?? "").join("");

function steps(messages: RawMessage[]): Step[] {
  const out: Step[] = [];
  const tools = new Map<string, Extract<Step, { kind: "tool" }>>();
  for (const m of messages) {
    if (m.type === "human") {
      const text = textOf(m.content);
      const notif = text.match(/<agent-name>([^<]*)<\/agent-name>\s*<status>([^<]*)<\/status>/);
      if (notif) {
        const result = text.match(/<result>\s*([\s\S]*?)\s*<\/result>/)?.[1] ?? "";
        out.push({ kind: "notification", agent: notif[1], status: notif[2], text: clip(result, 1400) });
      } else out.push({ kind: "user", text: clip(text, 600) });
    } else if (m.type === "ai") {
      const blocks: Block[] = typeof m.content === "string" ? [{ type: "text", text: m.content }] : m.content;
      for (const b of blocks) {
        if (b.type === "thinking" && b.thinking?.trim()) out.push({ kind: "thinking", text: clip(b.thinking.trim(), 1600) });
        else if (b.type === "text" && b.text?.trim()) out.push({ kind: "text", text: clip(b.text.trim(), 2400) });
      }
      for (const tc of m.tool_calls ?? []) {
        const step = { kind: "tool" as const, id: tc.id, name: tc.name, args: tc.args };
        tools.set(tc.id, step);
        out.push(step);
      }
    } else if (m.type === "tool" && m.tool_call_id) {
      const step = tools.get(m.tool_call_id);
      if (step) {
        step.result = clip(textOf(m.content), 3000);
        step.ok = m.status !== "error";
      }
    }
  }
  return out;
}

function summary(t: RawThread): CaseSummary {
  const md = t.metadata;
  return {
    threadId: t.thread_id,
    caseId: String(md.case_id ?? t.thread_id),
    createdAt: t.created_at,
    updatedAt: t.updated_at,
    busy: t.status === "busy",
    buoy: Number(md.buoy),
    metric: String(md.metric ?? ""),
    value: Number(md.value),
    unit: String(md.unit ?? ""),
    reason: String(md.reason ?? ""),
    source: String(md.run ?? "live").split("-")[0],
  };
}

export async function listCases(limit = 12): Promise<CaseSummary[]> {
  const threads = await call<RawThread[]>("/threads/search", {
    body: { metadata: { kind: "case" }, limit, sort_by: "created_at", sort_order: "desc" },
  });
  return threads.map(summary);
}

type State = { values: { messages?: RawMessage[]; todos?: Todo[]; async_tasks?: Record<string, { agent_name: string; status: string; thread_id: string }> } };

export async function getCase(threadId: string): Promise<CaseDetail> {
  const [thread, state, children] = await Promise.all([
    call<RawThread>(`/threads/${threadId}`),
    call<State>(`/threads/${threadId}/state`),
    call<RawThread[]>("/threads/search", { body: { metadata: { parent_thread_id: threadId }, limit: 20 } }),
  ]);
  const base = summary(thread);
  const messages = state.values.messages ?? [];
  const lead = steps(messages);

  // What the steward asked each specialist to do
  const tasks = new Map<string, string>();
  for (const s of lead) {
    if (s.kind === "tool" && s.name === "start_async_task") {
      const id = s.result?.match(/task_id:\s*([\w-]+)/)?.[1];
      if (id) tasks.set(id, String(s.args.description ?? ""));
    }
  }
  const tracked = state.values.async_tasks ?? {};
  const lanes = await Promise.all(
    children.map(async (c): Promise<Lane> => {
      const st = await call<State>(`/threads/${c.thread_id}/state`);
      return {
        name: String(c.metadata.subagent_name ?? "subagent"),
        threadId: c.thread_id,
        status: c.status === "busy" ? "running" : (tracked[c.thread_id]?.status ?? (c.status === "error" ? "error" : "success")),
        busy: c.status === "busy",
        task: tasks.get(c.thread_id),
        steps: steps(st.values.messages ?? []),
      };
    }),
  );
  const order = ["hydrology", "satellite", "permits", "community", "history"];
  lanes.sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));

  const verdictCall = lead.findLast((s) => s.kind === "tool" && s.name === "submit_verdict") as
    | Extract<Step, { kind: "tool" }>
    | undefined;
  const alerted = lead.some((s) => s.kind === "tool" && s.name === "send_alert" && s.ok !== false && !!s.result);
  const verdict = verdictCall
    ? ({ ...(verdictCall.args as Omit<Verdict, "alerted">), alerted } as Verdict)
    : null;
  const memoryUpdated = lead.some(
    (s) => s.kind === "tool" && (s.name === "edit_file" || s.name === "write_file") && String(s.args.file_path ?? "").startsWith("/memories/"),
  );

  let files: CaseDetail["files"] = [];
  try {
    const found = await call<{ items: { key: string; value: { content: string; encoding?: string } }[] }>(
      "/store/items/search",
      { body: { namespace_prefix: ["case", base.caseId], limit: 50 } },
    );
    files = found.items.map((i) => ({
      key: i.key,
      size: i.value.content.length,
      image: i.value.encoding === "base64",
    }));
  } catch {
    // The store is optional for rendering
  }

  return {
    ...base,
    busy: base.busy || lanes.some((l) => l.busy),
    steps: lead,
    todos: state.values.todos ?? [],
    lanes,
    files,
    verdict,
    memoryUpdated,
  };
}

export async function getCaseFile(caseId: string, key: string) {
  const params = new URLSearchParams({ namespace: `case.${caseId}`, key });
  const item = await call<{ value: { content: string; encoding?: string } }>(`/store/items?${params}`);
  return item.value;
}
