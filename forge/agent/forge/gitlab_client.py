"""GitLab REST API helpers (token verification + project listing)."""

from __future__ import annotations

import os
from typing import Any

import httpx

API_URL = os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4").rstrip("/")
TOKEN = os.getenv("GITLAB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITLAB_TOKEN", "")


def configured() -> bool:
    return bool(TOKEN)


def _headers() -> dict[str, str]:
    return {"PRIVATE-TOKEN": TOKEN}


def verify_token() -> dict[str, Any]:
    if not configured():
        return {"ok": False, "error": "GITLAB_PERSONAL_ACCESS_TOKEN not set"}
    try:
        r = httpx.get(f"{API_URL}/user", headers=_headers(), timeout=15.0)
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code, "error": r.text[:200]}
        user = r.json()
        return {
            "ok": True,
            "username": user.get("username"),
            "name": user.get("name"),
            "api_url": API_URL,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_projects(limit: int = 20) -> dict[str, Any]:
    if not configured():
        return {"projects": [], "error": "GitLab token not configured"}
    try:
        r = httpx.get(
            f"{API_URL}/projects",
            headers=_headers(),
            params={
                "membership": "true",
                "simple": "true",
                "order_by": "last_activity_at",
                "per_page": limit,
            },
            timeout=20.0,
        )
        if r.status_code != 200:
            return {"projects": [], "error": r.text[:200]}
        projects = []
        for p in r.json():
            projects.append(
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "path_with_namespace": p.get("path_with_namespace"),
                    "web_url": p.get("web_url"),
                    "http_url_to_repo": p.get("http_url_to_repo"),
                    "ssh_url_to_repo": p.get("ssh_url_to_repo"),
                    "default_branch": p.get("default_branch", "main"),
                }
            )
        return {"count": len(projects), "projects": projects}
    except Exception as exc:
        return {"projects": [], "error": str(exc)}


def parse_gitlab_url(repo_url: str) -> dict[str, Any] | None:
    """Extract project path from gitlab.com clone URLs."""
    url = repo_url.strip().rstrip("/")
    for prefix in (
        "https://gitlab.com/",
        "http://gitlab.com/",
        "git@gitlab.com:",
    ):
        if url.startswith(prefix):
            path = url[len(prefix) :].removesuffix(".git")
            return {"host": "gitlab.com", "path_with_namespace": path}
    return None
