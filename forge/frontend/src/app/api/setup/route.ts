import { NextRequest } from "next/server";
import { proxyToAgent } from "@/lib/agentProxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  return proxyToAgent(req, "/api/setup");
}
