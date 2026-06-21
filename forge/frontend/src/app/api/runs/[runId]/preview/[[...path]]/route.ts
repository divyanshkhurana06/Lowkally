import { NextRequest } from "next/server";
import { agentApiUrl } from "@/lib/agentUrl";

const API = agentApiUrl();

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

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

async function proxyPreview(
  req: NextRequest,
  runId: string,
  pathSegs: string[] | undefined,
): Promise<Response> {
  const sub = pathSegs?.length ? `/${pathSegs.map(encodeURIComponent).join("/")}` : "";
  const qs = req.nextUrl.search;
  const target = `${API}/api/runs/${encodeURIComponent(runId)}/preview${sub}${qs}`;

  const headers = new Headers();
  const cookie = req.headers.get("cookie");
  if (cookie) headers.set("Cookie", cookie);
  const accept = req.headers.get("accept");
  if (accept) headers.set("Accept", accept);
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  const range = req.headers.get("range");
  if (range) headers.set("Range", range);

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
    return new Response("Preview agent unreachable", { status: 503 });
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

type RouteCtx = { params: Promise<{ runId: string; path?: string[] }> };

export async function GET(req: NextRequest, ctx: RouteCtx) {
  const { runId, path } = await ctx.params;
  return proxyPreview(req, runId, path);
}

export async function HEAD(req: NextRequest, ctx: RouteCtx) {
  const { runId, path } = await ctx.params;
  return proxyPreview(req, runId, path);
}

export async function POST(req: NextRequest, ctx: RouteCtx) {
  const { runId, path } = await ctx.params;
  return proxyPreview(req, runId, path);
}

export async function PUT(req: NextRequest, ctx: RouteCtx) {
  const { runId, path } = await ctx.params;
  return proxyPreview(req, runId, path);
}

export async function PATCH(req: NextRequest, ctx: RouteCtx) {
  const { runId, path } = await ctx.params;
  return proxyPreview(req, runId, path);
}

export async function DELETE(req: NextRequest, ctx: RouteCtx) {
  const { runId, path } = await ctx.params;
  return proxyPreview(req, runId, path);
}

export async function OPTIONS(req: NextRequest, ctx: RouteCtx) {
  const { runId, path } = await ctx.params;
  return proxyPreview(req, runId, path);
}
