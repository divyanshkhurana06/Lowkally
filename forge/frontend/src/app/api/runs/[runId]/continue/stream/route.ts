import { NextRequest } from "next/server";
import { agentApiUrl } from "@/lib/agentUrl";

const API = agentApiUrl();

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  const body = await req.text();
  let upstream: Response;
  try {
    const cookie = req.headers.get("cookie");
    upstream = await fetch(`${API}/api/runs/${runId}/continue/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(cookie ? { Cookie: cookie } : {}),
      },
      body,
      cache: "no-store",
    });
  } catch {
    return new Response(JSON.stringify({ error: "Agent offline" }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }

  if (!upstream.ok || !upstream.body) {
    const text = await upstream.text().catch(() => "");
    return new Response(text || "Stream failed", { status: upstream.status });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
