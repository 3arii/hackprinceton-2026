import { Webhook } from "standardwebhooks";
import { chat, stewardConfigured } from "@/lib/steward";

// Photon delivers iMessage events here (Standard Webhooks signatures). A text from the lake
// manager becomes a run of the Lake Steward's liaison agent, which replies in the same conversation.

const seen = new Set<string>(); // webhook-id, to drop Photon's redelivered duplicates

type Incoming = {
  type?: string;
  data?: {
    object?: {
      id: string;
      platform: string;
      status: string;
      space: { id: string };
      sentBy: { kind: string; handle: string };
      receivedBy?: { id: string; kind: string };
      content?: { type: string; text?: string };
    };
  };
};

export async function POST(request: Request) {
  const secret = process.env.PHOTON_WEBHOOK_SECRET;
  if (!secret || !stewardConfigured()) return new Response(null, { status: 503 });

  const raw = await request.text();
  const id = request.headers.get("webhook-id") ?? "";
  let event: Incoming;
  try {
    event = new Webhook(secret).verify(raw, {
      "webhook-id": id,
      "webhook-timestamp": request.headers.get("webhook-timestamp") ?? "",
      "webhook-signature": request.headers.get("webhook-signature") ?? "",
    }) as Incoming;
  } catch {
    return new Response("bad signature", { status: 400 });
  }
  if (seen.has(id)) return new Response(null, { status: 204 });
  seen.add(id);
  if (seen.size > 500) seen.delete(seen.values().next().value!);

  const m = event.data?.object;
  const text = m?.content?.type === "text" ? m.content.text?.trim() : "";
  // Only plain texts from the registered manager reach the agent
  if (!m || m.status !== "received" || m.sentBy.kind !== "human" || !text || !m.receivedBy) {
    return new Response(null, { status: 204 });
  }
  if (process.env.ALERT_PHONE && m.sentBy.handle !== process.env.ALERT_PHONE) return new Response(null, { status: 204 });

  try {
    await chat(text, { phone: m.sentBy.handle, space_id: m.space.id, agent_id: m.receivedBy.id, message_id: m.id });
  } catch {
    return new Response(null, { status: 503 }); // Photon retries
  }
  return new Response(null, { status: 204 });
}
