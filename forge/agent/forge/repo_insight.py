"""Repo insight — Gemini summary + labels from README / package.json."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .gitlab_client import API_URL as GITLAB_API_URL, TOKEN as GITLAB_TOKEN, parse_gitlab_url

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_README_NAMES = ("README.md", "README.MD", "readme.md", "README.rst", "README", "README.txt")
_PKG_PATHS = ("package.json", "apps/web/package.json", "frontend/package.json", "web/package.json")
_LABEL_TOKENS = (
    "javascript",
    "typescript",
    "nextjs",
    "tailwind",
    "portfolio",
    "website",
    "webpage",
    "static",
    "python",
    "react",
    "express",
    "supabase",
    "web3",
    "docker",
    "html",
    "css",
    "js",
    "node",
    "api",
    "oss",
    "app",
    "code",
    "go",
    "rust",
    "java",
    "vue",
    "vite",
)


def normalize_labels(raw: Any) -> list[str]:
    """Always return up to 3 separate single-word labels."""
    parts: list[str] = []

    def add_piece(piece: str) -> None:
        word = piece.strip().lower().replace("_", "-")
        word = re.sub(r"[^a-z0-9-]", "", word)
        if len(word) >= 2 and word not in parts:
            parts.append(word)

    if isinstance(raw, str):
        for piece in re.split(r"[,;/|]+", raw):
            for sub in re.split(r"\s+", piece.strip()):
                if sub:
                    add_piece(sub)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                if re.search(r"[,;/|\s]", item):
                    for piece in re.split(r"[,;/|\s]+", item):
                        if piece:
                            add_piece(piece)
                else:
                    add_piece(item)

    expanded: list[str] = []
    for label in parts:
        if len(label) <= 12 and label in _LABEL_TOKENS:
            expanded.append(label)
            continue
        mashed = _split_mashed_label(label)
        for token in mashed:
            if token not in expanded:
                expanded.append(token)

    return expanded[:3]


def _split_mashed_label(text: str) -> list[str]:
    """Split glued tokens like htmlcssjs → html, css, js."""
    s = text.lower()
    found: list[str] = []
    i = 0
    while i < len(s):
        matched = False
        for token in sorted(_LABEL_TOKENS, key=len, reverse=True):
            if s[i:].startswith(token):
                found.append(token)
                i += len(token)
                matched = True
                break
        if not matched:
            i += 1
    return found if found else ([text] if text else [])


def parse_github_url(repo_url: str) -> dict[str, str] | None:
    url = repo_url.strip().rstrip("/").removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if url.startswith(prefix):
            path = url[len(prefix) :]
            parts = path.split("/")
            if len(parts) >= 2:
                return {"host": "github.com", "owner": parts[0], "repo": parts[1]}
    if "github.com/" in url:
        chunk = url.split("github.com/", 1)[1]
        parts = chunk.split("/")
        if len(parts) >= 2:
            return {"host": "github.com", "owner": parts[0], "repo": parts[1].removesuffix(".git")}
    return None


def _branches(branch: str | None) -> list[str]:
    if branch:
        return [branch, "main", "master"]
    return ["main", "master", "develop"]


def _fetch_text(url: str, headers: dict[str, str] | None = None) -> str | None:
    try:
        r = httpx.get(url, headers=headers or {}, timeout=12.0, follow_redirects=True)
        if r.status_code == 200 and r.text.strip():
            return r.text[:6000]
    except Exception:
        pass
    return None


def _fetch_github_file(owner: str, repo: str, path: str, branch: str | None) -> str | None:
    for ref in _branches(branch):
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        text = _fetch_text(url)
        if text:
            return text
    return None


def _fetch_gitlab_file(project_path: str, path: str, branch: str | None) -> str | None:
    encoded = quote(project_path, safe="")
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN} if GITLAB_TOKEN else {}
    for ref in _branches(branch):
        api = f"{GITLAB_API_URL}/projects/{encoded}/repository/files/{quote(path, safe='')}/raw?ref={ref}"
        text = _fetch_text(api, headers)
        if text:
            return text
        raw = f"https://gitlab.com/{project_path}/-/raw/{ref}/{path}"
        text = _fetch_text(raw)
        if text:
            return text
    return None


def fetch_repo_context(repo_url: str, branch: str | None = None) -> dict[str, Any]:
    """Fetch README + package.json snippets from GitHub/GitLab without cloning."""
    ctx: dict[str, Any] = {"repo_url": repo_url, "name": repo_url.rstrip("/").split("/")[-1].removesuffix(".git")}
    readme: str | None = None
    package_json: str | None = None

    gl = parse_gitlab_url(repo_url)
    gh = parse_github_url(repo_url)

    if gl:
        path = gl["path_with_namespace"]
        ctx["name"] = path.split("/")[-1]
        for name in _README_NAMES:
            readme = _fetch_gitlab_file(path, name, branch)
            if readme:
                break
        for pkg in _PKG_PATHS:
            package_json = _fetch_gitlab_file(path, pkg, branch)
            if package_json:
                break

    elif gh:
        owner, repo = gh["owner"], gh["repo"]
        ctx["name"] = repo
        for name in _README_NAMES:
            readme = _fetch_github_file(owner, repo, name, branch)
            if readme:
                break
        for pkg in _PKG_PATHS:
            package_json = _fetch_github_file(owner, repo, pkg, branch)
            if package_json:
                break

    if readme:
        ctx["readme"] = readme[:4000]
    if package_json:
        ctx["package_json"] = package_json[:2000]
    return ctx


def fetch_local_context(workspace: Path) -> dict[str, Any]:
    """Enrich context from a cloned workspace."""
    ctx: dict[str, Any] = {}
    for name in _README_NAMES:
        for path in workspace.rglob(name):
            if any(p in path.parts for p in (".git", "node_modules")):
                continue
            try:
                ctx["readme"] = path.read_text(encoding="utf-8", errors="replace")[:4000]
                break
            except OSError:
                continue
        if "readme" in ctx:
            break
    for rel in _PKG_PATHS:
        path = workspace / rel
        if path.is_file():
            try:
                ctx["package_json"] = path.read_text(encoding="utf-8", errors="replace")[:2000]
                break
            except OSError:
                pass
    return ctx


def _heuristic_insight(ctx: dict[str, Any]) -> dict[str, Any]:
    labels: list[str] = []
    summary_parts: list[str] = []
    name = str(ctx.get("name") or "repository")

    pkg_data: dict[str, Any] = {}
    if ctx.get("package_json"):
        try:
            pkg_data = json.loads(ctx["package_json"])
        except json.JSONDecodeError:
            pkg_data = {}

    deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
    stack_map = [
        ("next", "nextjs"),
        ("react", "react"),
        ("vue", "vue"),
        ("express", "api"),
        ("fastapi", "python"),
        ("django", "python"),
        ("tailwindcss", "tailwind"),
        ("supabase", "supabase"),
        ("hardhat", "web3"),
        ("ethers", "web3"),
    ]
    for dep, label in stack_map:
        if dep in deps and label not in labels:
            labels.append(label)

    desc = (pkg_data.get("description") or "").strip()
    if desc:
        summary_parts.append(desc.rstrip(".") + ".")

    if ctx.get("readme"):
        readme = ctx["readme"]
        for line in readme.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("![") or line.startswith("["):
                continue
            if len(line) > 20:
                summary_parts.append(line.rstrip(".") + ".")
                break

    if not summary_parts:
        summary_parts.append(f"{name} — software project detected from repository metadata.")

    if not labels:
        readme_lower = (ctx.get("readme") or "").lower()
        if "webpage" in readme_lower or "index.html" in readme_lower:
            labels = ["html", "static", "website"]
        else:
            labels = ["code", "oss", "app"]

    return {
        "summary": " ".join(summary_parts[:2])[:320],
        "labels": normalize_labels(labels),
        "source": "heuristic",
    }


def _parse_gemini_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("summary"):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]*\"summary\"[^{}]*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _gemini_insight(ctx: dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    if not ctx.get("readme") and not ctx.get("package_json"):
        return None

    from google import genai

    prompt = f"""Analyze this git repository and respond with ONLY valid JSON (no markdown):
{{"summary": "2-3 concise sentences describing what this project is and does", "labels": ["word1", "word2", "word3"]}}

Rules:
- summary: plain English, max 3 sentences, no hype
- labels: exactly 3 single lowercase words (stack, domain, or type — e.g. nextjs, portfolio, api)
- base labels only on provided README/package.json

Repository: {ctx.get("repo_url", "")}
Name: {ctx.get("name", "")}

README excerpt:
{(ctx.get("readme") or "(not found)")[:3000]}

package.json excerpt:
{(ctx.get("package_json") or "(not found)")[:1500]}
"""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=MODEL, contents=prompt)
        text = (response.text or "").strip()
        data = _parse_gemini_json(text)
        if not data:
            return None
        labels = normalize_labels(data.get("labels") or [])
        if len(labels) < 3:
            labels = normalize_labels(labels + ["app", "code", "oss"])
        summary = str(data.get("summary", "")).strip()
        if not summary:
            return None
        return {
            "summary": summary[:400],
            "labels": labels[:3],
            "source": "gemini",
        }
    except Exception:
        return None


def generate_repo_insight(
    repo_url: str,
    branch: str | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Build repo summary + 3 one-word labels (Gemini with heuristic fallback)."""
    ctx = fetch_repo_context(repo_url, branch)
    if workspace and workspace.is_dir():
        local = fetch_local_context(workspace)
        ctx.update({k: v for k, v in local.items() if v})

    insight = _gemini_insight(ctx)
    if insight:
        return insight
    return _heuristic_insight(ctx)


async def generate_repo_insight_async(
    repo_url: str,
    branch: str | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(generate_repo_insight, repo_url, branch, workspace)
