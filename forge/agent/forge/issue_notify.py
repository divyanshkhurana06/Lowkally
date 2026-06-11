"""Deliver issue reports to the maintainer (GitHub Issues API)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


def _issues_repo() -> tuple[str, str] | None:
    repo = os.getenv("GITHUB_ISSUES_REPO", "").strip()
    if repo and "/" in repo:
        owner, name = repo.split("/", 1)
        return owner, name.replace(".git", "")

    url = os.getenv("GITHUB_ISSUES_URL", "").strip()
    match = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", url)
    if match:
        return match.group(1), match.group(2).replace(".git", "")
    return None


def _issues_token() -> str | None:
    for key in ("GITHUB_ISSUES_TOKEN", "GITHUB_REPORT_TOKEN", "GITHUB_TOKEN"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return None


def create_github_issue(
    *,
    subject: str,
    body: str,
    contact: str | None = None,
    repo_url: str | None = None,
    user: dict[str, Any] | None = None,
    report_id: str | None = None,
) -> dict[str, Any]:
    """Open a GitHub issue when token + repo are configured. Returns {ok, url?, error?}."""
    token = _issues_token()
    parsed = _issues_repo()
    if not token:
        return {"ok": False, "error": "GITHUB_ISSUES_TOKEN not configured"}
    if not parsed:
        return {"ok": False, "error": "GITHUB_ISSUES_URL or GITHUB_ISSUES_REPO not configured"}

    owner, repo = parsed
    lines = [body.rstrip(), "", "---", "_Submitted via Lowkally report form_"]
    if report_id:
        lines.append(f"Report ID: `{report_id}`")
    if user:
        lines.append(f"User: `@{user.get('username', 'unknown')}` ({user.get('provider', 'unknown')})")
    if contact:
        lines.append(f"Contact: {contact}")
    if repo_url:
        lines.append(f"Repository: {repo_url}")

    payload = json.dumps({"title": subject[:240], "body": "\n".join(lines)}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/issues",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Lowkally-Report-Bot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "url": data.get("html_url"), "number": data.get("number")}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "error": f"GitHub API {exc.code}: {detail}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
