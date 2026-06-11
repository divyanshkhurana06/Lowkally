"""List repositories for logged-in GitHub / GitLab OAuth users."""

from __future__ import annotations

import os
from typing import Any

import httpx

GITLAB_API_URL = os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4").rstrip("/")
GITLAB_OAUTH_URL = os.getenv("GITLAB_OAUTH_URL", "https://gitlab.com")


def _normalize_gitlab(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in projects:
        out.append(
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "path_with_namespace": p.get("path_with_namespace"),
                "web_url": p.get("web_url"),
                "http_url_to_repo": p.get("http_url_to_repo"),
                "ssh_url_to_repo": p.get("ssh_url_to_repo"),
                "default_branch": p.get("default_branch", "main"),
                "provider": "gitlab",
            }
        )
    return out


def _normalize_github(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in repos:
        out.append(
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "path_with_namespace": r.get("full_name"),
                "web_url": r.get("html_url"),
                "http_url_to_repo": r.get("clone_url"),
                "ssh_url_to_repo": r.get("ssh_url"),
                "default_branch": r.get("default_branch", "main"),
                "provider": "github",
            }
        )
    return out


async def list_github_repos(token: str, limit: int = 30) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                "https://api.github.com/user/repos",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                params={
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": limit,
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
        if r.status_code != 200:
            return {"repos": [], "error": r.text[:200], "provider": "github"}
        return {
            "count": len(r.json()),
            "repos": _normalize_github(r.json()),
            "provider": "github",
        }
    except Exception as exc:
        return {"repos": [], "error": str(exc), "provider": "github"}


async def list_gitlab_repos(token: str, limit: int = 30) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{GITLAB_API_URL}/projects",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "membership": "true",
                    "simple": "true",
                    "order_by": "last_activity_at",
                    "per_page": limit,
                },
            )
        if r.status_code != 200:
            try:
                body = r.json()
                if body.get("error") == "insufficient_scope":
                    return {
                        "repos": [],
                        "error": (
                            "GitLab needs the read_api permission to list your projects. "
                            "Log out, then sign in again and approve access when GitLab asks."
                        ),
                        "provider": "gitlab",
                    }
            except Exception:
                pass
            return {"repos": [], "error": r.text[:200], "provider": "gitlab"}
        data = r.json()
        return {
            "count": len(data),
            "repos": _normalize_gitlab(data),
            "provider": "gitlab",
        }
    except Exception as exc:
        return {"repos": [], "error": str(exc), "provider": "gitlab"}


async def list_repos_for_user(user: dict[str, Any], limit: int = 30) -> dict[str, Any]:
    provider = user.get("provider")
    token = user.get("oauth_token")
    if not token:
        return {
            "repos": [],
            "error": "Re-login to refresh repository access",
            "provider": provider,
        }
    if provider == "github":
        return await list_github_repos(token, limit)
    if provider == "gitlab":
        return await list_gitlab_repos(token, limit)
    return {
        "repos": [],
        "error": "Repository picker available for GitHub and GitLab sign-in",
        "provider": provider,
    }
