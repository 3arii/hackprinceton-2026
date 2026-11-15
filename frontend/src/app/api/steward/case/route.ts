import { getCase } from "@/lib/steward";

export async function GET(request: Request) {
  const id = new URL(request.url).searchParams.get("thread") ?? "";
  if (!/^[\w-]+$/.test(id)) return Response.json({ error: "bad request" }, { status: 400 });
  try {
    return Response.json(await getCase(id));
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 });
  }
}
