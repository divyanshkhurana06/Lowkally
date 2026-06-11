# Lowkally

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Autonomous repository bootstrap agent — paste a git URL, get a running app.

**Live demo:** https://lowkally-ui-ksy3havi2a-uc.a.run.app · **Repo:** https://github.com/divyanshkhurana06/Lowkally · **License:** [MIT](LICENSE)

Clone · detect stack · install · run · heal

## Run locally

```bash
bash scripts/start.sh
```

Open **http://localhost:3000**, paste a repo URL, click **Start run**.

## Deploy

See **[DEPLOY.md](DEPLOY.md)** for Docker Compose, Cloud Run, and OAuth setup.

```bash
export GOOGLE_CLOUD_PROJECT=your-project
export APP_URL=https://your-ui-url
bash scripts/deploy-vercel-gcp.sh agent
export AGENT_URL=...
bash scripts/deploy-vercel-gcp.sh ui-cloudrun
```

## Features

- **Google ADK + Gemini** — multi-step tool agent (with deterministic pipeline fallback)
- **GitLab MCP** — partner integration: discover README/manifests before clone
- **Repo insight** — AI summary + tags from README
- **Login** — GitHub / GitLab / Google OAuth (per-user runs & library)
- **Library** — save & favorite sites, one-click open
- **Compare** — split-screen two saved sites
- **Report issue** — feedback → GitHub Issues

## Environment

Copy `.env.example` → `.env`. Key vars: `GOOGLE_API_KEY`, `GITLAB_PERSONAL_ACCESS_TOKEN`, `APP_URL`, `JWT_SECRET`, OAuth client IDs for production.

## License

MIT — Copyright (c) 2026 Divyansh Khurana. See [LICENSE](LICENSE).
