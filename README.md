# Lowkally

Autonomous repository bootstrap engine — paste a git URL, get a running app.

Clone · detect stack · install · run · heal

## Run locally

```bash
bash scripts/start.sh
```

Open **http://localhost:3000**, paste a repo URL, click **Start run**.

## Environment

Copy `.env.example` → `.env`:

| Variable | Required | Purpose |
|----------|----------|---------|
| `GOOGLE_API_KEY` | Optional | Gemini ADK (hackathon agent layer) |
| `GITLAB_PERSONAL_ACCESS_TOKEN` | Optional | GitLab project picker + MCP |
| `FORGE_WORKSPACE` | No | Clone directory (default `./forge/workspace`) |
| `FORGE_DATA_DIR` | No | SQLite run history (default `./forge/data`) |

## Test

```bash
python scripts/test_lowkally.py
```

## License

MIT
