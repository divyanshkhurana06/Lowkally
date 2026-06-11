/** Same-origin in browser (Next rewrites + stream route handlers). Direct URL for SSR. */
const BASE =
  typeof window !== "undefined"
    ? ""
    : process.env.API_URL || "http://127.0.0.1:8080";

export type AuthUser = {
  id: string;
  provider: string;
  username: string;
  avatar_url?: string;
};

export type SavedSite = {
  id: string;
  repo_url: string;
  run_id?: string;
  title?: string;
  success_url?: string;
  summary?: string;
  labels: string[];
  is_favorite: boolean;
  created_at: string;
};

export type Setup = {
  gemini_configured: boolean;
  adk_agent: string;
  gitlab_mcp: boolean;
  gitlab_api_ok: boolean;
  gitlab_user?: string;
  gitlab_error?: string;
  filesystem_mcp: boolean;
  pipeline?: boolean;
  model: string;
  max_iterations: number;
  ready: boolean;
  app_url?: string;
  oauth?: { google: boolean; github: boolean; gitlab: boolean };
  issues_url?: string;
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

export type RepoInsight = {
  summary: string;
  labels: string[];
  source?: "gemini" | "heuristic";
};

const LABEL_TOKENS = [
  "javascript",
  "typescript",
  "nextjs",
  "tailwind",
  "portfolio",
  "website",
  "webpage",
  "static",
  "python",
  "react",
  "html",
  "css",
  "js",
  "node",
  "api",
  "oss",
  "app",
  "code",
  "go",
  "vue",
  "vite",
];

function splitMashedLabel(text: string): string[] {
  const s = text.toLowerCase();
  const found: string[] = [];
  let i = 0;
  while (i < s.length) {
    let matched = false;
    for (const token of [...LABEL_TOKENS].sort((a, b) => b.length - a.length)) {
      if (s.startsWith(token, i)) {
        found.push(token);
        i += token.length;
        matched = true;
        break;
      }
    }
    if (!matched) i += 1;
  }
  return found.length ? found : text ? [text] : [];
}

/** Coerce API payloads into exactly 3 separate label chips. */
/** Ensure git clone URLs include a scheme (https://github.com/...). */
export function normalizeRepoUrl(url: string): string {
  const u = url.trim();
  if (!u) return u;
  if (u.startsWith("git@")) return u;
  if (/^https?:\/\//i.test(u)) return u;
  return `https://${u.replace(/^\/+/, "")}`;
}

export function normalizeInsightLabels(raw: unknown): string[] {
  const parts: string[] = [];

  const add = (piece: string) => {
    const word = piece
      .trim()
      .toLowerCase()
      .replace(/_/g, "-")
      .replace(/[^a-z0-9-]/g, "");
    if (word.length >= 2 && !parts.includes(word)) parts.push(word);
  };

  if (typeof raw === "string") {
    raw.split(/[,;/|]+/).forEach((chunk) => chunk.split(/\s+/).forEach(add));
  } else if (Array.isArray(raw)) {
    for (const item of raw) {
      if (typeof item !== "string") continue;
      if (/[,;/|\s]/.test(item)) item.split(/[,;/|\s]+/).forEach(add);
      else add(item);
    }
  }

  const expanded: string[] = [];
  for (const label of parts) {
    if (label.length <= 12 && LABEL_TOKENS.includes(label)) {
      expanded.push(label);
      continue;
    }
    for (const token of splitMashedLabel(label)) {
      if (!expanded.includes(token)) expanded.push(token);
    }
  }

  return expanded.slice(0, 3);
}

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

const creds: RequestInit = { credentials: "include" };

export function parseApiError(status: number, raw: string): string {
  const trimmed = raw.trim();
  if (status === 401) return "Please log in and try again.";
  if (status === 404) return "Agent API not found — restart with: bash scripts/start.sh";
  if (status === 0 || trimmed === "Failed to fetch") {
    return "Cannot reach the agent — run: bash scripts/start.sh";
  }
  try {
    const data = JSON.parse(trimmed) as { detail?: unknown };
    const detail = data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((item) => {
          if (item && typeof item === "object" && "msg" in item) {
            const loc = Array.isArray((item as { loc?: unknown }).loc)
              ? (item as { loc: unknown[] }).loc.filter((x) => x !== "body").join(".")
              : "";
            const msg = String((item as { msg: unknown }).msg);
            if (loc === "body" && msg.includes("at least")) {
              return "Please add a bit more detail (at least 5 characters).";
            }
            if (loc === "subject") return "Subject must be at least 3 characters.";
            return loc ? `${loc}: ${msg}` : msg;
          }
          return null;
        })
        .filter(Boolean);
      if (msgs.length) return msgs.join(" ");
    }
  } catch {
    /* not JSON */
  }
  if (trimmed.startsWith("<!")) return `Server error (${status}). Is the agent running?`;
  return trimmed.slice(0, 180) || `Request failed (${status})`;
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { ...creds, ...init });
  if (!r.ok) {
    const raw = await r.text();
    throw new Error(parseApiError(r.status, raw));
  }
  return r.json();
}

export const getSetup = () => j<Setup>("/api/setup");
export const getAuthMe = () =>
  j<{ user: AuthUser | null; oauth: { google: boolean; github: boolean; gitlab: boolean } }>(
    "/api/auth/me",
  );
export const logout = () => j<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
export const getSavedSites = (favorites = false) =>
  j<{ sites: SavedSite[] }>(`/api/saved${favorites ? "?favorites=true" : ""}`);
export const saveSite = (body: {
  repo_url: string;
  run_id?: string;
  title?: string;
  success_url?: string;
  summary?: string;
  labels?: string[];
  favorite?: boolean;
}) =>
  j<{ site: SavedSite }>("/api/saved", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
export const toggleFavorite = (siteId: string) =>
  j<{ site: SavedSite }>(`/api/saved/${siteId}/favorite`, { method: "POST" });
export const deleteSaved = (siteId: string) =>
  j(`/api/saved/${siteId}`, { method: "DELETE" });
export type ReportIssueResult = {
  report: { id: string; created_at: string };
  issues_url?: string;
  delivered: boolean;
  github?: { ok: boolean; url?: string; error?: string };
};

export const reportIssue = (body: {
  subject: string;
  body: string;
  contact?: string;
  repo_url?: string;
}) =>
  j<ReportIssueResult>("/api/report-issue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
export const getGitLabProjects = () => j<{ projects: GitLabProject[] }>("/api/gitlab/projects");
export const getUserRepos = () =>
  j<{ repos: GitLabProject[]; error?: string; provider?: string }>("/api/user/repos");
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
    ...creds,
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
    ...creds,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  await parseSse(res, onEvent);
}
