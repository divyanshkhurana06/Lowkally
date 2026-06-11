# Deploying Lowkally

Lowkally runs as two services: **agent** (FastAPI, port 8080) and **UI** (Next.js, port 3000). The UI proxies `/api/*` to the agent so OAuth cookies stay on one domain.

## Quick deploy (Docker Compose — VPS, Railway, etc.)

1. Copy `.env.example` → `.env` and fill secrets (see below).
2. Set `APP_URL` to your public URL (e.g. `https://lowkally.yourdomain.com`).
3. Set `CORS_ORIGINS` to the same URL.
4. Run:

```bash
docker compose up -d --build
```

Open `APP_URL` in the browser. Persistent data lives in Docker volumes `forge-workspace` and `forge-data`.

## Google Cloud Run (hackathon)

### Agent

```bash
export GOOGLE_CLOUD_PROJECT=your-project
export APP_URL=https://your-ui-domain.com
export JWT_SECRET=$(openssl rand -base64 32)
bash forge/deploy/cloud-run-agent.sh
```

Mount a Cloud Storage FUSE volume or use Cloud SQL for production persistence (default container disk is ephemeral).

### UI

Deploy `forge/frontend` to Vercel, Cloud Run, or Firebase Hosting:

```bash
cd forge/frontend
# Vercel
vercel --prod
# Set env: API_URL=https://your-agent-xxx.run.app
```

Set `APP_URL` on the agent to your Vercel URL so OAuth redirects work.

## OAuth (required for multi-user production)

Create OAuth apps:

| Provider | Callback URL |
|----------|----------------|
| GitHub | `{APP_URL}/api/auth/github/callback` |
| GitLab | `{APP_URL}/api/auth/gitlab/callback` |

Add to `.env`:

```
APP_URL=https://your-domain.com
JWT_SECRET=long-random-string
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITLAB_CLIENT_ID=
GITLAB_CLIENT_SECRET=
GITHUB_ISSUES_URL=https://github.com/you/lowkally/issues
```

Without OAuth, local dev uses a shared `developer` account (not for production).

## Environment reference

| Variable | Required | Purpose |
|----------|----------|---------|
| `APP_URL` | Prod | Public UI URL for OAuth redirects |
| `JWT_SECRET` | Prod | Session cookie signing |
| `GOOGLE_API_KEY` | Yes | Gemini ADK |
| `GITLAB_PERSONAL_ACCESS_TOKEN` | MCP | GitLab MCP + project picker |
| `GITHUB_CLIENT_ID/SECRET` | OAuth | GitHub login |
| `GITLAB_CLIENT_ID/SECRET` | OAuth | GitLab login |
| `CORS_ORIGINS` | Prod | Comma-separated allowed origins |

## Multi-user isolation

- Every run is tied to `user_id` from login.
- Run history, saved sites, and favorites are per-user.
- Users cannot access another user's runs (`403`).

## Saved sites & compare

- **Save / Favorite** on the Run page after a successful bootstrap.
- **Library** — one-click open (live URL or re-run repo).
- **Compare** — split-screen two saved sites (iframes need publicly reachable URLs).

## Issue reports (Report issue button)

Reports are saved in SQLite (`forge/data/forge.db`, `issue_reports` table) and, when configured, **opened as GitHub Issues** so they reach you.

Add to `.env`:

```
GITHUB_ISSUES_URL=https://github.com/divyanshkhurana06/Lowkally/issues
GITHUB_ISSUES_TOKEN=ghp_...   # classic PAT with repo scope, or fine-grained Issues: Read and write
```

Without `GITHUB_ISSUES_TOKEN`, reports are stored locally only (users see a friendly message, not raw API errors).
