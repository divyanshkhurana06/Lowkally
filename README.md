# FORGE

Autonomous repository bootstrap engine for the Google Cloud Rapid Agent Hackathon.

Paste any git URL. FORGE clones it, installs dependencies, runs the start command, reads real stderr, patches files, and retries until the project runs or hits the iteration limit.

## Stack

- **Gemini + Google ADK** — planning and tool orchestration
- **GitLab MCP** (when `GITLAB_PERSONAL_ACCESS_TOKEN` is set) or **Filesystem MCP** — partner integration
- **FastAPI + SSE** — streaming execution trace
- **Next.js** — operator console
- **SQLite** — run history and env approval gate
- **Docker / Cloud Run** — production deployment

## Run locally

```bash
bash scripts/start.sh
```

Open **http://localhost:3000**, paste a public repo URL (e.g. a small Node or Python project), click **Forge repository**.

## Environment

Copy `.env.example` → `.env`:

| Variable | Required | Purpose |
|----------|----------|---------|
| `GOOGLE_API_KEY` | Yes | Gemini via ADK |
| `GITLAB_PERSONAL_ACCESS_TOKEN` | For GitLab MCP | GitLab track partner integration |
| `GITLAB_API_URL` | No | Default `https://gitlab.com/api/v4` |

Public GitHub repos work via `git clone` without GitLab token (Filesystem MCP is used as fallback).

## Deploy

```bash
docker compose up --build
```

Cloud Run (agent):

```bash
export GOOGLE_CLOUD_PROJECT=your-project
bash forge/deploy/cloud-run-agent.sh
```

Deploy frontend to Vercel with `NEXT_PUBLIC_API_URL` pointing at your Cloud Run URL.

## How it works

1. Clone repository into isolated workspace
2. Inspect manifests (`package.json`, `pyproject.toml`, `.env.example`)
3. Request operator approval before writing `.env`
4. Run install + start commands
5. On failure — read stack trace, edit files, retry (max 10 iterations)
6. Record success URL and persist run log

No demo apps. No hardcoded fixes. All actions come from agent tool calls on real repo output.

## License

MIT
