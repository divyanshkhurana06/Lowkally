"""Beginner-friendly post-run guide: what Lowkally showed vs run locally for full app."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .detection import CommandSet, StackInfo, detect_package_manager, resolve_pm_cmd


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _scan_workspace(root: Path) -> dict[str, Any]:
    hints: dict[str, Any] = {
        "has_backend": (root / "backend").is_dir(),
        "has_docker_compose": any(
            (root / n).is_file() for n in ("docker-compose.yml", "docker-compose.yaml", "compose.yml")
        ),
        "has_prisma": (root / "prisma" / "schema.prisma").is_file(),
        "has_redis_env": False,
        "has_websocket": False,
        "has_blockchain": False,
        "monorepo": False,
    }
    for env_name in (".env.example", ".env.template", "backend/.env.example"):
        p = root / env_name
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            if "redis" in text:
                hints["has_redis_env"] = True
    pkg = _read_json(root / "package.json")
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    dep_blob = " ".join(deps.keys()).lower()
    if any(x in dep_blob for x in ("@mysten/sui", "@mysten/dapp-kit", "ethers", "web3")):
        hints["has_blockchain"] = True
    if "socket.io" in dep_blob:
        hints["has_websocket"] = True
    if (root / "backend" / "package.json").is_file() or (root / "server" / "package.json").is_file():
        hints["monorepo"] = True
    # quick source scan for socket imports without dep listed
    try:
        for ts in list(root.glob("src/**/*.ts"))[:40]:
            if "socket.io" in ts.read_text(encoding="utf-8", errors="replace"):
                hints["has_websocket"] = True
                break
    except OSError:
        pass
    return hints


def build_run_guide(
    *,
    repo_url: str,
    cwd: Path,
    stack: StackInfo,
    cmds: CommandSet | None,
    status: str,
    success_url: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Return structured beginner guide after a bootstrap run."""
    pm = resolve_pm_cmd(cwd) if cwd.is_dir() else "npm"
    hints = _scan_workspace(cwd) if cwd.is_dir() else {}
    source = (cmds.source if cmds else "") or ""
    partial = "vite-dev" in source or "dev-fallback" in source
    preview_only = "vite-build" in source and not partial

    showing: list[str] = []
    missing: list[str] = []
    steps: list[dict[str, Any]] = []

    if status == "running" and success_url:
        if partial:
            showing.append(f"Frontend dev preview at {success_url} (hot reload, no production build).")
        elif preview_only:
            showing.append(f"Built static frontend served at {success_url}.")
        else:
            showing.append(f"App responding at {success_url} inside the Lowkally workspace.")
        showing.append("Dependencies were installed in an isolated copy of your repo on the agent server.")
    elif status == "completed":
        showing.append("Repository cloned and inspected. No long-running web server was required.")
    elif status == "failed":
        showing.append("Clone and/or install may have completed, but the app did not fully start.")
        if error:
            showing.append(f"Last error: {error[:200]}")
    else:
        showing.append("Run finished. Check the execution trace for details.")

    if hints.get("has_backend") or hints.get("monorepo"):
        missing.append("Backend API server (Lowkally only bootstrapped the main package by default).")
    if hints.get("has_redis_env"):
        missing.append("Redis (referenced in env templates — not started on Cloud Run).")
    if hints.get("has_websocket"):
        missing.append("WebSocket / real-time server (live games, chat, etc. need a running backend).")
    if hints.get("has_blockchain"):
        missing.append("Blockchain wallet connection (needs browser extension + network access).")
    if hints.get("has_docker_compose"):
        missing.append("Docker Compose services (database, Redis, etc.) — not started in cloud bootstrap.")
    if hints.get("has_prisma") and not success_url:
        missing.append("Database migrations may need `npx prisma db push` or `prisma migrate` locally.")

    if partial or missing:
        missing.append(
            "Cloud Run preview is agent-localhost only — it is not a public URL you can share; "
            "open the link while the run is active or clone locally for full control."
        )

    clone_url = repo_url if repo_url.startswith("http") else f"https://{repo_url.lstrip('/')}"
    branch_note = ""

    steps.append(
        {
            "title": "1. Clone the repo on your computer",
            "commands": [f"git clone {clone_url}", f"cd {clone_url.rstrip('/').split('/')[-1].replace('.git', '')}"],
            "note": "You need Git installed. This gives you the full project on your machine.",
        }
    )

    install_cmd = (cmds.install if cmds else None) or f"{pm} install"
    steps.append(
        {
            "title": "2. Install dependencies",
            "commands": [install_cmd],
            "note": "Run this in the project folder (and again inside `backend/` if the repo has a separate backend).",
        }
    )

    if hints.get("has_backend"):
        steps.append(
            {
                "title": "3. Start the backend (separate terminal)",
                "commands": [
                    "cd backend",
                    f"{pm} install",
                    f"{pm} run dev",
                ],
                "note": "Check backend/README or package.json scripts. Some apps use `npm start` instead of `dev`.",
            }
        )

    if hints.get("has_redis_env"):
        steps.append(
            {
                "title": "4. Start Redis (if the app uses it)",
                "commands": [
                    "docker run -d --name redis -p 6379:6379 redis:alpine",
                ],
                "note": "Or install Redis locally. Copy REDIS_* values from `.env.example` into `.env`.",
            }
        )

    if hints.get("has_docker_compose"):
        steps.append(
            {
                "title": "Run everything with Docker Compose",
                "commands": ["docker compose up --build"],
                "note": "Often the easiest way to get database + API + frontend together. Requires Docker Desktop.",
            }
        )

    run_local = (cmds.run if cmds else None) or f"{pm} run dev"
    if hints.get("has_prisma"):
        run_local = f"npx prisma db push && {run_local}"

    steps.append(
        {
            "title": f"{'5' if hints.get('has_backend') else '3'}. Start the frontend / main app",
            "commands": [run_local],
            "note": "Open http://localhost:3000 (or the port printed in the terminal). "
            "With backend + Redis running, features that failed in cloud preview should work locally.",
        }
    )

    if hints.get("has_blockchain"):
        steps.append(
            {
                "title": "Blockchain / wallet",
                "commands": [],
                "note": "Install a Sui or Ethereum wallet browser extension. Connect on the site after both frontend and API are running.",
            }
        )

    headline = "What Lowkally showed vs what to run locally"
    if status == "running" and partial:
        headline = "Partial preview only — finish setup on your machine"
    elif status == "failed":
        headline = "Run did not finish — try these steps locally"

    return {
        "headline": headline,
        "showing": showing,
        "missing": missing or ["Nothing major detected — if something still breaks, check README and .env.example."],
        "local_steps": steps,
        "repo_url": clone_url,
        "stack": {"runtime": stack.runtime, "framework": stack.framework},
        "partial_preview": partial,
    }
