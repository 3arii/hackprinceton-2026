import { CASE_LIMIT, casesOpened, openCase, stewardConfigured, type Anomaly, type Reading } from "@/lib/steward";

// Anyone with the dashboard link can look; only someone with the passcode can inject readings
const passcodeOk = (request: Request) =>
  !process.env.DEBUG_PASSCODE || request.headers.get("x-debug-passcode") === process.env.DEBUG_PASSCODE;

// Checks a passcode without publishing anything
export async function GET(request: Request) {
  return Response.json({ ok: passcodeOk(request) }, { status: passcodeOk(request) ? 200 : 401 });
}

// Publishes a hand-edited buoy reading through AWS IoT Core (the token stays on the server),
// then hands any anomaly straight to the Lake Steward so the investigation starts at once.
export async function POST(request: Request) {
  if (!passcodeOk(request)) return Response.json({ error: "passcode" }, { status: 401 });
  const res = await fetch(`${process.env.BUOY_API_URL}/debug/reading`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-debug-token": process.env.BUOY_DEBUG_TOKEN ?? "" },
    body: await request.text(),
    cache: "no-store",
  });
  if (!res.ok) return new Response(res.body, { status: res.status, headers: { "Content-Type": "application/json" } });
  const { reading, anomalies } = (await res.json()) as { reading: Reading; anomalies: Anomaly[] };

  const cases = [];
  let limited = false;
  if (stewardConfigured() && anomalies.length) {
    const opened = await casesOpened();
    // One investigation per anomalous metric, within the overall budget
    for (const a of anomalies.slice(0, Math.max(0, Math.min(2, CASE_LIMIT - opened)))) cases.push(await openCase(reading, a));
    limited = cases.length < Math.min(2, anomalies.length);
  }
  return Response.json({ reading, anomalies, cases, limited });
}
