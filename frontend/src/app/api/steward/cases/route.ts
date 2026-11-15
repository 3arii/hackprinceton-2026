import { listCases, stewardConfigured } from "@/lib/steward";

export async function GET() {
  if (!stewardConfigured()) return Response.json({ error: "STEWARD_URL is not set" }, { status: 503 });
  try {
    return Response.json(await listCases());
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 });
  }
}
