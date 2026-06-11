# Google Cloud Rapid Agent Hackathon — Submission checklist

**Track:** GitLab  
**Project:** Lowkally — autonomous repo bootstrap agent

---

## Devpost form (copy/paste)

| Field | Value |
|-------|--------|
| **Hosted project URL** | https://lowkally-ui-ksy3havi2a-uc.a.run.app |
| **Public repository** | https://github.com/divyanshkhurana06/Lowkally |
| **Open-source license** | MIT — see [LICENSE](LICENSE) (set **About → License → MIT** on GitHub if not auto-detected) |
| **Partner track** | **GitLab** |
| **Demo video (~3 min)** | Upload to YouTube/Vimeo; paste link in Devpost (see script below) |

---

## How Lowkally meets the rules

### Challenge requirements

| Requirement | How we satisfy it |
|-------------|-------------------|
| **Move beyond chat** | Agent clones repos, runs shell commands, writes `.env`, heals errors, and marks success — not Q&A only. |
| **Multi-step mission** | Phases: GitLab MCP discover → Gemini ADK tools → deterministic pipeline fallback → install → build → start → heal loop. |
| **Partner MCP (GitLab)** | `@zereight/mcp-gitlab` via Google ADK `McpToolset`: `list_projects`, `get_repository_tree`, `get_file_contents`, etc. |
| **Gemini + Agent Builder** | [Google ADK](https://google.github.io/adk-docs/) (`google-adk`) orchestrates Gemini with tools; hybrid runner in `forge/agent/forge/hybrid.py`. |
| **Google Cloud** | Agent + UI on **Cloud Run** (project `gen-lang-client-0974235583`). |
| **Human oversight** | `.env` secrets require operator approval (`request_env_write` / approval UI) before write. |

Verify live: open `/api/setup` on the hosted URL — `hackathon` block lists flags judges can check.

---

## Demo video script (~3 minutes)

1. **Problem (20s)** — “Cloning a repo and getting it running locally is slow and brittle.”
2. **Login (15s)** — GitHub/GitLab OAuth on the hosted URL.
3. **GitLab MCP (45s)** — Run a **GitLab** repo URL; point to trace: *Phase 1 — GitLab MCP discovery* and MCP tool calls in the execution trace.
4. **Gemini ADK (45s)** — Show *Phase 2 — Gemini ADK* tool calls (`clone_repository`, `detect_start_command`, `run_command`) or quota fallback to pipeline.
5. **Outcome (30s)** — Successful run, preview link, save to Library, Compare view.
6. **Stack (15s)** — Gemini ADK + GitLab MCP + Cloud Run; MIT repo link on screen.

**Tip:** Demo a **GitLab** repo for the GitLab track. Use a small Node/React app for a fast run.

---

## Judging criteria (talking points)

| Criterion | Lowkally angle |
|-----------|----------------|
| **Technological implementation** | ADK agent, GitLab MCP, Cloud Run deploy, OAuth, SSE streaming, healing pipeline. |
| **Design** | Single-page run console, live trace, library/compare, report issue. |
| **Potential impact** | Onboarding, hackathons, CI preview, “paste URL → running app” for any dev. |
| **Quality of the idea** | RepoFix-style autonomy + partner MCP pre-discovery + multi-user cloud product. |

---

## Before you submit

- [ ] Repo is **public**: https://github.com/divyanshkhurana06/Lowkally
- [ ] GitHub **About** shows **License: MIT**
- [ ] `LICENSE` committed at repo root (this file)
- [ ] Hosted URL loads: https://lowkally-ui-ksy3havi2a-uc.a.run.app
- [ ] OAuth callbacks set for production URL (GitHub / GitLab / Google)
- [ ] Demo video uploaded (~3 min)
- [ ] Devpost: select **GitLab** track and submit before **12 Jun 2026, 2:30 AM IST**

---

## Architecture (for judges)

```
User → Cloud Run UI (Next.js)
         ↓ SSE /api/forge/stream
       Cloud Run Agent (FastAPI + Google ADK)
         ├─ Gemini (repo insight + ADK tools)
         ├─ GitLab MCP (discover before clone)
         └─ Pipeline fallback (clone → detect → install → build → run → heal)
```

Key files:

- `forge/agent/forge/root_agent.py` — ADK agent + GitLab MCP toolset
- `forge/agent/forge/mcp_discover.py` — GitLab MCP discovery phase
- `forge/agent/forge/hybrid.py` — ADK + pipeline hybrid streaming
- `forge/agent/forge/pipeline.py` — Deterministic bootstrap + healing
