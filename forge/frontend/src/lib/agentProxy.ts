import { NextRequest } from "next/server";
import { agentApiUrl } from "@/lib/agentUrl";

const API = agentApiUrl();

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "te",
  "trailer",
  "upgrade",
  "proxy-authenticate",
  "proxy-authorization",
]);

/** Forward a browser /api/* request to the Lowkally agent (cookies included). */
export async function proxyToAgent(
  req: NextRequest,
  agentPath: string,
): Promise<Response> {
  const qs = req.nextUrl.search;
  const target = `${API}${agentPath}${qs}`;

  const headers = new Headers();
  const cookie = req.headers.get("cookie");
  if (cookie) headers.set("Cookie", cookie);
  const accept = req.headers.get("accept");
  if (accept) headers.set("Accept", accept);
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body:
        req.method !== "GET" && req.method !== "HEAD"
          ? await req.arrayBuffer()
          : undefined,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return Response.json(
      { detail: "Lowkally agent unreachable — try again in a moment." },
      { status: 503 },
    );
  }

  const out = new Headers();
  upstream.headers.forEach((value, key) => {
    if (HOP_BY_HOP.has(key.toLowerCase())) return;
    out.set(key, value);
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: out,
  });
}
