/** Same-origin in browser (Next rewrites + stream route handlers). Direct URL for SSR. */
const BASE =
  typeof window !== "undefined"
    ? ""
    : process.env.API_URL || "http://127.0.0.1:8080";

export type Setup = {
  gemini_configured: boolean;
  adk_agent: string;
  gitlab_mcp: boolean;
  gitlab_api_ok: boolean;
  gitlab_user?: string;
  gitlab_error?: string;
  gitlab_scope_hint?: string;
  filesystem_mcp: boolean;
  pipeline?: boolean;
  model: string;
  max_iterations: number;
  ready: boolean;
  hackathon: {
    beyond_chat: boolean;
    multi_step_agent: boolean;
    partner_mcp_gitlab: boolean;
    human_env_gate: boolean;
    deployable: boolean;
    hybrid_mode?: boolean;
  };
};

export type GitLabProject = {
  id: number;
  name: string;
  path_with_namespace: string;
  web_url: string;
  http_url_to_repo: string;
  default_branch: string;
};

export type Run = {
  id: string;
  repo_url: string;
  branch?: string;
  status: string;
  success_url?: string;
  error?: string;
  iteration?: number;
  created_at: string;
  finished_at?: string;
};

export type Approval = {
  id: string;
  run_id: string;
  keys: string[];
  status: string;
};

export type RunEvent = {
  ts: string;
  kind: string;
  payload: Record<string, unknown>;
};

export type AgentPart =
  | { type: "text"; text: string }
  | { type: "call"; name: string; args: Record<string, unknown>; source?: string }
  | { type: "response"; name: string; body: unknown; source?: string };

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, init);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

export const getSetup = () => j<Setup>("/api/setup");
export const getGitLabProjects = () => j<{ projects: GitLabProject[] }>("/api/gitlab/projects");
export const getRuns = () => j<{ runs: Run[] }>("/api/runs");
export const getRunDetail = (id: string) =>
  j<{ run: Run; events: RunEvent[] }>(`/api/runs/${id}`);
export const getApprovals = (runId?: string) =>
  j<{ approvals: Approval[] }>(`/api/approvals${runId ? `?run_id=${runId}` : ""}`);

export const approveEnv = (id: string, values: Record<string, string>) =>
  j(`/api/approvals/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values }),
  });

function normalizeSseBuffer(buf: string): string {
  return buf.replace(/\r\n/g, "\n");
}

async function parseSse(res: Response, onEvent: (type: string, data: unknown) => void) {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Stream failed (${res.status}): ${text.slice(0, 200)}`);
  }
  if (!res.body) throw new Error("Stream failed: empty response body");

  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf = normalizeSseBuffer(buf + dec.decode(value, { stream: true }));

    let sep = buf.indexOf("\n\n");
    while (sep !== -1) {
      const chunk = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      if (chunk.trim()) {
        let ev = "message";
        let data = "";
        for (const line of chunk.split("\n")) {
          if (line.startsWith("event:")) ev = line.slice(6).trim();
          if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (data) {
          try {
            onEvent(ev, JSON.parse(data));
          } catch {
            onEvent(ev, data);
          }
        }
      }
      sep = buf.indexOf("\n\n");
    }
  }
}

export async function forgeStream(
  repoUrl: string,
  branch: string,
  onEvent: (type: string, data: unknown) => void,
) {
  const res = await fetch(`${BASE}/api/forge/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_url: repoUrl, branch: branch || null }),
  });
  await parseSse(res, onEvent);
}

export async function continueRun(
  runId: string,
  sessionId: string,
  onEvent: (type: string, data: unknown) => void,
  message?: string,
) {
  const res = await fetch(`${BASE}/api/runs/${runId}/continue/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  await parseSse(res, onEvent);
}
