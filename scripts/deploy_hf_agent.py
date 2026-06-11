#!/usr/bin/env python3
"""Deploy Lowkally agent to Hugging Face Spaces (free, no card)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "forge" / "agent"
HF_USER = os.getenv("HF_USERNAME", "divyanshkhurana06")
SPACE_NAME = os.getenv("HF_SPACE_NAME", "lowkally-agent")
REPO_ID = f"{HF_USER}/{SPACE_NAME}"

SECRET_KEYS = [
    "GOOGLE_API_KEY",
    "JWT_SECRET",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "GITLAB_CLIENT_ID",
    "GITLAB_CLIENT_SECRET",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GITLAB_PERSONAL_ACCESS_TOKEN",
    "GITHUB_ISSUES_TOKEN",
]

VAR_KEYS = [
    "APP_URL",
    "CORS_ORIGINS",
    "GEMINI_MODEL",
    "GITLAB_API_URL",
    "GITHUB_ISSUES_URL",
    "FORGE_MAX_ITERATIONS",
    "FORGE_CMD_TIMEOUT",
]


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def stage_space(tmp: Path) -> None:
    shutil.copytree(AGENT_SRC, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    for name in ("README.md", "Dockerfile"):
        shutil.copy(ROOT / "forge" / "deploy" / "hf-space" / name, tmp / name)


def main() -> int:
    load_env()
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
    if not token:
        print("HF_TOKEN not set. Create a write token at https://huggingface.co/settings/tokens", file=sys.stderr)
        print("Then: export HF_TOKEN=hf_... && bash scripts/deploy-vercel-free.sh", file=sys.stderr)
        return 1

    try:
        from huggingface_hub import HfApi, upload_folder
    except ImportError:
        print("Install: pip install huggingface_hub", file=sys.stderr)
        return 1

    api = HfApi(token=token)
    who = api.whoami()
    username = who.get("name") or HF_USER
    repo_id = f"{username}/{SPACE_NAME}"

    try:
        api.repo_info(repo_id, repo_type="space")
        print(f"Space exists: {repo_id}")
    except Exception:
        print(f"Creating space: {repo_id}")
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            private=False,
        )

    app_url = os.getenv("APP_URL", "https://placeholder.vercel.app")
    cors = os.getenv("CORS_ORIGINS", app_url)
    os.environ["APP_URL"] = app_url
    os.environ["CORS_ORIGINS"] = cors

    for key in SECRET_KEYS:
        val = os.getenv(key, "").strip()
        if val:
            print(f"Setting secret: {key}")
            try:
                api.add_space_secret(repo_id=repo_id, key=key, value=val)
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    raise

    for key in VAR_KEYS:
        val = os.getenv(key, "").strip()
        if val:
            print(f"Setting variable: {key}")
            try:
                api.add_space_variable(repo_id=repo_id, key=key, value=val)
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    raise

    with tempfile.TemporaryDirectory() as td:
        stage_space(Path(td))
        print(f"Uploading agent to {repo_id}...")
        upload_folder(
            folder_path=td,
            repo_id=repo_id,
            repo_type="space",
            commit_message="Deploy Lowkally agent",
        )

    agent_url = f"https://{username}-{SPACE_NAME}.hf.space"
    print(f"\nAGENT_URL={agent_url}")
    out = ROOT / ".deploy-agent-url"
    out.write_text(agent_url + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
