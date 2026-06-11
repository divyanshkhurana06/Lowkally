# Lowkally

Autonomous repository bootstrap engine — paste a git URL, get a running app.

Clone · detect stack · install · run · heal

## Run locally

```bash
bash scripts/start.sh
```

Open **http://localhost:3000**, paste a repo URL, click **Start run**.

## Deploy

See **[DEPLOY.md](DEPLOY.md)** for Docker Compose, Cloud Run, and OAuth setup.

## Features

- **Gemini ADK** + **GitLab MCP** multi-step agent (with pipeline fallback)
- **Repo insight** — AI summary + tags from README
- **Login** — GitHub / GitLab OAuth (per-user runs & library)
- **Library** — save & favorite sites, one-click open
- **Compare** — split-screen two saved sites
- **Report issue** — top-right feedback form

## Environment

Copy `.env.example` → `.env`. Key vars: `GOOGLE_API_KEY`, `GITLAB_PERSONAL_ACCESS_TOKEN`, `APP_URL`, `JWT_SECRET`, OAuth client IDs for production.

## Test

```bash
python scripts/test_lowkally.py
```

## License

MIT
