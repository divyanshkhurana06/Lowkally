"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  approveEnv,
  continueRun,
  forgeStream,
  getApprovals,
  getAuthMe,
  getGitLabProjects,
  getRunDetail,
  getUserRepos,
  getRuns,
  getSetup,
  normalizeInsightLabels,
  normalizeRepoUrl,
  saveSite,
  type AgentPart,
  type Approval,
  type AuthUser,
  type GitLabProject,
  type Run,
  type RunEvent,
  type RepoInsight,
  type Setup,
} from "@/lib/api";
import { queueReport } from "@/lib/reportContext";

type Trace =
  | { t: "text"; v: string; author?: string }
  | { t: "call"; name: string; args: Record<string, unknown>; author?: string; source?: string }
  | { t: "response"; name: string; body: unknown; author?: string; source?: string };

const STATUS_COLOR: Record<string, string> = {
  running: "var(--ok)",
  completed: "var(--ok)",
  active: "var(--link)",
  healing: "#b45309",
  awaiting_env: "#b45309",
  failed: "var(--err)",
  cloned: "var(--muted)",
};

function eventsToTrace(events: RunEvent[]): Trace[] {
  const out: Trace[] = [];
  for (const ev of events) {
    const p = ev.payload;
    if (ev.kind === "clone") {
      out.push({ t: "response", name: "clone_repository", body: p });
    } else if (ev.kind === "env_auto" || ev.kind === "env_written") {
      out.push({ t: "text", v: `Wrote .env keys: ${(p.keys as string[])?.join(", ") || ""}` });
    } else if (ev.kind === "command" || ev.kind === "run_probe") {
      if (p.command) out.push({ t: "call", name: "run_command", args: { command: p.command } });
      out.push({ t: "response", name: "run_command", body: p });
    } else if (ev.kind === "success") {
      out.push({ t: "text", v: `Success: ${p.url || p.summary || "done"}` });
    }
  }
  return out;
}

function ForgeApp() {
  const searchParams = useSearchParams();
  const [setup, setSetup] = useState<Setup | null>(null);
  const [projects, setProjects] = useState<GitLabProject[]>([]);
  const [userRepos, setUserRepos] = useState<GitLabProject[]>([]);
  const [userReposError, setUserReposError] = useState("");
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [repoUrl, setRepoUrl] = useState(
    () => searchParams.get("repo") || "github.com/divyanshkhurana06/portfolio",
  );
  const [branch, setBranch] = useState("");
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState("");
  const [trace, setTrace] = useState<Trace[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [finalRun, setFinalRun] = useState<Run | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<Run[]>([]);
  const [offline, setOffline] = useState(false);
  const [streamError, setStreamError] = useState("");
  const [repoInsight, setRepoInsight] = useState<RepoInsight | null>(null);
  const traceRef = useRef<HTMLDivElement>(null);

  const scrollTrace = () => {
    traceRef.current?.scrollTo({ top: traceRef.current.scrollHeight, behavior: "smooth" });
  };

  const refresh = useCallback(async () => {
    try {
      const [s, r, a] = await Promise.all([getSetup(), getRuns(), getApprovals()]);
      setSetup(s);
      setHistory(r.runs);
      setApprovals(a.approvals);
      setOffline(false);
      try {
        const auth = await getAuthMe();
        setAuthUser(auth.user);
        if (auth.user?.provider === "github" || auth.user?.provider === "gitlab") {
          const ur = await getUserRepos();
          setUserRepos(ur.repos || []);
          setUserReposError(ur.error || "");
        } else {
          setUserRepos([]);
          setUserReposError("");
        }
      } catch {
        setAuthUser(null);
        setUserRepos([]);
      }
      if (s.gitlab_api_ok) {
        try {
          const gp = await getGitLabProjects();
          setProjects(gp.projects || []);
        } catch {
          setProjects([]);
        }
      }
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, running ? 4000 : 10000);
    return () => clearInterval(t);
  }, [refresh, running]);

  useEffect(() => {
    if (!running || !runId) return;
    const poll = setInterval(async () => {
      try {
        const detail = await getRunDetail(runId);
        setFinalRun(detail.run);
        const fromDb = eventsToTrace(detail.events);
        if (fromDb.length > trace.length) {
          setTrace(fromDb);
          scrollTrace();
        }
        if (detail.run.status === "awaiting_env") {
          setPhase("Waiting for .env approval");
          const pending = await getApprovals(runId);
          setApprovals(pending.approvals);
        } else if (detail.run.status === "healing") {
          setPhase("Healing from errors…");
        } else if (detail.run.status === "active") {
          setPhase("Installing / running…");
        } else if (detail.run.status === "running") {
          setPhase("App is live");
        } else         if (detail.run.status === "failed") {
          setPhase("Failed");
          setRunning(false);
        }
        const insightEv = detail.events.find((e) => e.kind === "repo_insight");
        if (insightEv?.payload) {
          const p = insightEv.payload as RepoInsight;
          if (p.summary) {
            setRepoInsight({
              ...p,
              labels: normalizeInsightLabels(p.labels),
            });
          }
        }
      } catch {
        /* ignore poll errors */
      }
    }, 2000);
    return () => clearInterval(poll);
  }, [running, runId, trace.length]);

  useEffect(() => {
    scrollTrace();
  }, [trace.length]);

  const pushTrace = (items: Trace[]) => {
    if (!items.length) return;
    setTrace((t) => [...t, ...items]);
  };

  const handleStream = (type: string, data: unknown) => {
    if (type === "run" && data && typeof data === "object") {
      const d = data as { run_id: string; session_id: string };
      setRunId(d.run_id);
      setSessionId(d.session_id);
      setPhase("Run started");
    }
    if (type === "insight" && data && typeof data === "object") {
      const d = data as RepoInsight & { type?: string };
      if (d.summary) {
        setRepoInsight({
          summary: d.summary,
          labels: normalizeInsightLabels(d.labels),
          source: d.source,
        });
      }
    }
    if (type === "agent" && data && typeof data === "object") {
      const d = data as { parts?: AgentPart[]; author?: string };
      const author = d.author;
      for (const p of d.parts || []) {
        if (p.type === "text") {
          setPhase(p.text.slice(0, 80));
          pushTrace([{ t: "text", v: p.text, author }]);
        }
        if (p.type === "call")
          pushTrace([
            {
              t: "call",
              name: p.name,
              args: p.args,
              author,
              source: (p as { source?: string }).source,
            },
          ]);
        if (p.type === "response")
          pushTrace([
            {
              t: "response",
              name: p.name,
              body: p.body,
              author,
              source: (p as { source?: string }).source,
            },
          ]);
      }
    }
    if (type === "state" && data && typeof data === "object") {
      const st = data as { run: Run; approvals: Approval[] };
      setFinalRun(st.run);
      setApprovals(st.approvals);
      if (st.approvals[0]) {
        setEnvValues(Object.fromEntries(st.approvals[0].keys.map((k) => [k, ""])));
      }
    }
    if (type === "error" && data && typeof data === "object") {
      const msg = (data as { message?: string }).message || "Stream error";
      setStreamError(msg);
      pushTrace([{ t: "text", v: `ERROR: ${msg}` }]);
      setRunning(false);
    }
    if (type === "done") {
      setRunning(false);
      setPhase("Done");
      refresh();
    }
  };

  const onForge = async () => {
    if (!repoUrl.trim() || running) return;
    setRunning(true);
    setStreamError("");
    setRepoInsight(null);
    setTrace([{ t: "text", v: "Connecting to agent…" }]);
    setFinalRun(null);
    setRunId(null);
    setSessionId(null);
    setPhase("Starting…");
    try {
      await forgeStream(normalizeRepoUrl(repoUrl.trim()), branch.trim(), handleStream);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setStreamError(msg);
      pushTrace([{ t: "text", v: `ERROR: ${msg}` }]);
      setRunning(false);
      setPhase("Connection failed");
    }
  };

  const onApprove = async (id: string) => {
    await approveEnv(id, envValues);
    setApprovals([]);
    if (runId && sessionId) {
      setRunning(true);
      setPhase("Resuming after approval…");
      try {
        await continueRun(runId, sessionId, handleStream);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setStreamError(msg);
        pushTrace([{ t: "text", v: `ERROR: ${msg}` }]);
        setRunning(false);
      }
    }
    await refresh();
  };

  const pickProject = (p: GitLabProject) => {
    setRepoUrl(p.http_url_to_repo);
    setBranch(p.default_branch || "main");
  };

  const liveUrl =
    finalRun?.success_url && finalRun.success_url.startsWith("http") ? finalRun.success_url : null;

  const onSave = async (favorite = false) => {
    if (!repoUrl.trim()) return;
    await saveSite({
      repo_url: repoUrl.trim(),
      run_id: runId || undefined,
      success_url: liveUrl || undefined,
      summary: repoInsight?.summary,
      labels: repoInsight?.labels,
      favorite,
    });
  };

  return (
    <div className="min-h-screen flex flex-col">
      {offline && (
        <div className="banner-err">Agent offline — run: bash scripts/start.sh</div>
      )}
      {streamError && !running && (
        <div className="banner-err">{streamError}</div>
      )}

      <header className="site-header">
        <div>
          <p className="run-subtitle">Clone · detect stack · install · run · heal</p>
        </div>
        <StatusBar setup={setup} running={running} phase={phase} runStatus={finalRun?.status} />
      </header>

      <main className="site-main">
        <aside className="sidebar">
          <Panel title="Repository">
            <input
              placeholder="github.com/you/project or full https URL"
              value={repoUrl}
              disabled={running}
              onChange={(e) => setRepoUrl(e.target.value)}
            />
            <input
              className="mt-2"
              placeholder="branch (optional)"
              value={branch}
              disabled={running}
              onChange={(e) => setBranch(e.target.value)}
            />
            <button
              className="btn btn-solid w-full mt-3"
              disabled={running || !setup?.ready || !repoUrl.trim()}
              onClick={onForge}
            >
              {running ? phase || "Running…" : "Start run"}
            </button>
          </Panel>

          {repoInsight && (
            <Panel title="Repo insight">
              <p className="insight-summary">{repoInsight.summary}</p>
              <InsightLabels labels={repoInsight.labels} />
              {repoInsight.source && (
                <p className="hint mt-2">
                  via {repoInsight.source === "gemini" ? "Gemini" : "stack heuristics"}
                </p>
              )}
            </Panel>
          )}

          {liveUrl && (
            <Panel title="Live app">
              <a href={liveUrl} className="success-link" target="_blank" rel="noreferrer">
                {liveUrl}
              </a>
              <p className="hint mt-2">Open in a new tab — dev server started by Lowkally.</p>
              <div className="library-actions mt-2">
                <button type="button" className="btn btn-solid" onClick={() => onSave(false)}>
                  Save to library
                </button>
                <button type="button" className="btn btn-ghost" onClick={() => onSave(true)}>
                  ★ Favorite
                </button>
              </div>
            </Panel>
          )}

          {finalRun?.status === "failed" && finalRun.error && (
            <Panel title="Failure">
              <pre className="err-pre">{finalRun.error.slice(0, 400)}</pre>
              <button
                type="button"
                className="btn btn-ghost mt-2"
                onClick={() =>
                  queueReport({
                    subject: `Run failed: ${repoUrl.replace(/^https?:\/\//, "").slice(0, 80)}`,
                    body: `Repository: ${repoUrl}\nRun ID: ${finalRun.id}\n\nError:\n${finalRun.error}`,
                    repo_url: repoUrl,
                  })
                }
              >
                Report this error
              </button>
            </Panel>
          )}

          {userRepos.length > 0 && (
            <Panel title={`Your ${authUser?.provider === "github" ? "GitHub" : "GitLab"} repos`}>
              <p className="hint">One click to bootstrap locally.</p>
              <ul className="project-list">
                {userRepos.slice(0, 12).map((p) => (
                  <li key={`${p.id}-${p.path_with_namespace}`}>
                    <button type="button" disabled={running} onClick={() => pickProject(p)}>
                      <span className="proj-name">{p.path_with_namespace}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {authUser && userReposError && !userRepos.length && (
            <Panel title="Your repositories">
              <p className="hint">{userReposError}</p>
            </Panel>
          )}

          {!authUser && projects.length > 0 && (
            <Panel title="GitLab projects">
              <ul className="project-list">
                {projects.slice(0, 8).map((p) => (
                  <li key={p.id}>
                    <button type="button" disabled={running} onClick={() => pickProject(p)}>
                      <span className="proj-name">{p.path_with_namespace}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {approvals.length > 0 && (
            <Panel title=".env approval">
              <p className="hint">Pipeline paused — confirm values to write.</p>
              {approvals.map((a) => (
                <div key={a.id} className="approval-block">
                  {a.keys.map((k) => (
                    <label key={k} className="env-field">
                      <span>{k}</span>
                      <input
                        value={envValues[k] || ""}
                        onChange={(e) => setEnvValues((v) => ({ ...v, [k]: e.target.value }))}
                      />
                    </label>
                  ))}
                  <button className="btn btn-solid mt-2" onClick={() => onApprove(a.id)}>
                    Allow & continue
                  </button>
                </div>
              ))}
            </Panel>
          )}

          <Panel title="History">
            <ul className="history-list">
              {history.slice(0, 10).map((r) => (
                <li key={r.id}>
                  <span style={{ color: STATUS_COLOR[r.status] || "var(--muted)" }}>{r.status}</span>
                  <span className="hist-url">{r.repo_url.replace(/^https?:\/\//, "").slice(0, 36)}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </aside>

        <section className="trace-panel">
          <div className="trace-header">
            <div>
              <h2>Execution trace</h2>
              {phase && running && <span className="phase-live">{phase}</span>}
            </div>
            {runId && <code>{runId}</code>}
          </div>
          <div className="trace-body" ref={traceRef}>
            {trace.length === 0 && !running && (
              <p className="hint">Paste a repo URL and start — live steps stream here.</p>
            )}
            {trace.map((item, i) => (
              <TraceLine key={i} item={item} />
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

function StatusBar({
  setup,
  running,
  phase,
  runStatus,
}: {
  setup: Setup | null;
  running: boolean;
  phase: string;
  runStatus?: string;
}) {
  const h = setup?.hackathon;
  return (
    <div className="status-bar">
      <Tag ok={h?.multi_step_agent} text="Gemini ADK" />
      <Tag ok={h?.partner_mcp_gitlab} text="GitLab MCP" />
      <Tag ok={setup?.pipeline} text="Pipeline fallback" />
      <Tag ok={setup?.gitlab_api_ok} text={setup?.gitlab_user ? `@${setup.gitlab_user}` : "GitLab API"} />
      {running && <span className="pulse">{phase || "running"}</span>}
      {!running && runStatus && <span className="run-tag">{runStatus}</span>}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

function Tag({ ok, text }: { ok?: boolean; text: string }) {
  return <span className={`tag ${ok ? "tag-ok" : "tag-off"}`}>{text}</span>;
}

function InsightLabels({ labels }: { labels: string[] }) {
  const chips = normalizeInsightLabels(labels);
  if (!chips.length) return null;
  return (
    <div className="insight-labels" role="list" aria-label="Repository tags">
      {chips.map((label, i) => (
        <span key={`${label}-${i}`} className={`insight-label insight-label-${i % 3}`} role="listitem">
          {label}
        </span>
      ))}
    </div>
  );
}

export default function ForgePage() {
  return (
    <Suspense fallback={<div className="page-wrap"><p className="hint">Loading…</p></div>}>
      <ForgeApp />
    </Suspense>
  );
}

function TraceLine({ item }: { item: Trace }) {
  const badge =
    item.author || (item.t !== "text" ? item.source : undefined);
  const label = badge ? `[${badge}] ` : "";
  if (item.t === "text") return <div className="trace-text">{label}{item.v}</div>;
  if (item.t === "call")
    return (
      <div className="trace-call">
        <strong>
          {label}
          {item.name}
        </strong>
        <pre>{JSON.stringify(item.args, null, 2)}</pre>
      </div>
    );
  const body = item.body as Record<string, unknown> | null;
  const summary =
    body && typeof body === "object" && "app_url" in body && body.app_url
      ? String(body.app_url)
      : body && "success" in body
        ? body.success
          ? "ok"
          : "failed"
        : null;
  return (
    <div className="trace-ok">
      <strong>
        {label}
        {item.name}
        {summary ? ` → ${summary}` : ""}
      </strong>
      <pre>{JSON.stringify(item.body, null, 2)}</pre>
    </div>
  );
}
