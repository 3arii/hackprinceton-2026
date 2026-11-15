import { getCaseFile } from "@/lib/steward";

// A file from a case's shared filesystem: evidence notes as text, satellite images as PNG
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const caseId = params.get("case") ?? "";
  const key = params.get("key") ?? "";
  if (!/^[\w-]+$/.test(caseId) || !/^\/[\w./-]+$/.test(key) || key.includes("..")) {
    return Response.json({ error: "bad request" }, { status: 400 });
  }
  try {
    const file = await getCaseFile(caseId, key);
    if (file.encoding === "base64") {
      return new Response(Buffer.from(file.content, "base64"), {
        headers: { "Content-Type": "image/png", "Cache-Control": "private, max-age=300" },
      });
    }
    return new Response(file.content, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 });
  }
}
