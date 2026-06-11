"""Lowkally API server."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from google.adk.runners import InMemoryRunner
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from forge import root_agent as forge_agent
from forge.auth import (
    APP_URL,
    any_oauth_configured,
    clear_session_cookie,
    github_authorize_url,
    github_login,
    gitlab_authorize_url,
    gitlab_login,
    google_authorize_url,
    google_login,
    oauth_configured,
    require_user,
    set_session_cookie,
    user_from_request,
)
from forge.gitlab_client import list_projects, verify_token
from forge.issue_notify import create_github_issue
from forge.detection import normalize_repo_url
from forge.hybrid import stream_hybrid_run
from forge.store import get_run, list_events, list_pending_approvals, resolve_approval, update_run
from forge.user_repos import list_repos_for_user
from forge.users_store import (
    create_issue_report,
    create_run_for_user,
    delete_saved_site,
    list_runs_for_user,
    list_saved_sites,
    get_user,
    public_user,
    save_site,
    toggle_favorite,
    upsert_user,
)

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv()

runner: InMemoryRunner | None = None
_run_lock: set[str] = set()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global runner
    runner = InMemoryRunner(agent=forge_agent.root_agent, app_name="forge")
    yield
    runner = None


app = FastAPI(title="Lowkally", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ForgeRequest(BaseModel):
    repo_url: str = Field(..., min_length=8)
    branch: str | None = None
    start_command: str | None = None
    session_id: str | None = None
    user_id: str = "operator"


class ApproveRequest(BaseModel):
    values: dict[str, str]


class ContinueRequest(BaseModel):
    message: str = "Environment approved. Write .env and continue until the app runs."
    session_id: str
    user_id: str = "operator"


class SaveSiteRequest(BaseModel):
    repo_url: str
    run_id: str | None = None
    title: str | None = None
    success_url: str | None = None
    summary: str | None = None
    labels: list[str] = Field(default_factory=list)
    favorite: bool = False


class IssueReportRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=200)
    body: str = Field(..., min_length=5, max_length=4000)
    contact: str | None = Field(default=None, max_length=200)
    repo_url: str | None = Field(default=None, max_length=500)


def _auth_required(request: Request) -> dict[str, Any]:
    oauth = oauth_configured()
    user = user_from_request(request)
    if user:
        return user
    if any_oauth_configured():
        raise HTTPException(401, "Login required")
    return upsert_user(
        provider="dev",
        provider_id="local",
        username="developer",
        avatar_url=None,
    )


def _check_run_access(request: Request, run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    user = _auth_required(request)
    if run.get("user_id") and run["user_id"] != user["id"]:
        raise HTTPException(403, "Not your run")
    return run


def _setup() -> dict[str, Any]:
    gitlab_token = bool(os.getenv("GITLAB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITLAB_TOKEN"))
    gitlab_check = verify_token() if gitlab_token else {"ok": False}
    gemini = bool(os.getenv("GOOGLE_API_KEY"))
    return {
        "gemini_configured": gemini,
        "adk_agent": "lowkally",
        "gitlab_mcp": gitlab_token,
        "gitlab_api_ok": gitlab_check.get("ok", False),
        "gitlab_user": gitlab_check.get("username"),
        "gitlab_error": gitlab_check.get("error") if not gitlab_check.get("ok") else None,
        "gitlab_scope_hint": (
            "Fine-grained token needs User: Read + Project/Repository/Code: Read on your projects."
            if gitlab_token and not gitlab_check.get("ok")
            else None
        ),
        "filesystem_mcp": True,
        "pipeline": True,
        "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "max_iterations": int(os.getenv("FORGE_MAX_ITERATIONS", "10")),
        "ready": True,
        "app_url": APP_URL,
        "oauth": oauth_configured(),
        "issues_url": os.getenv("GITHUB_ISSUES_URL"),
        "hackathon": {
            "beyond_chat": gemini,
            "multi_step_agent": gemini,
            "partner_mcp_gitlab": gitlab_token and gitlab_check.get("ok", False),
            "human_env_gate": True,
            "deployable": True,
            "hybrid_mode": True,
        },
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "lowkally", **_setup()}


@app.get("/api/setup")
def setup() -> dict[str, Any]:
    return _setup()


@app.get("/api/gitlab/projects")
def gitlab_projects(limit: int = 20) -> dict[str, Any]:
    result = list_projects(limit=limit)
    return result


@app.get("/api/gitlab/verify")
def gitlab_verify() -> dict[str, Any]:
    return verify_token()


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    user = user_from_request(request)
    return {"user": public_user(user), "oauth": oauth_configured()}


@app.get("/api/user/repos")
async def user_repos(request: Request, limit: int = 30) -> dict[str, Any]:
    user = _auth_required(request)
    if user.get("provider") not in ("github", "gitlab"):
        return {"repos": [], "error": "Sign in with GitHub or GitLab to list repositories"}
    full = get_user(user["id"]) or user
    return await list_repos_for_user(full, limit)


@app.get("/api/auth/google/login")
def auth_google_login() -> RedirectResponse:
    if not oauth_configured()["google"]:
        raise HTTPException(501, "Google OAuth not configured")
    return RedirectResponse(google_authorize_url())


@app.get("/api/auth/google/callback")
async def auth_google_callback(code: str) -> RedirectResponse:
    user = await google_login(code)
    redirect = RedirectResponse(f"{APP_URL}/?login=google")
    set_session_cookie(redirect, user["id"])
    return redirect


@app.get("/api/auth/github/login")
def auth_github_login() -> RedirectResponse:
    if not oauth_configured()["github"]:
        raise HTTPException(501, "GitHub OAuth not configured")
    return RedirectResponse(github_authorize_url())


@app.get("/api/auth/github/callback")
async def auth_github_callback(code: str) -> RedirectResponse:
    user = await github_login(code)
    redirect = RedirectResponse(f"{APP_URL}/?login=github")
    set_session_cookie(redirect, user["id"])
    return redirect


@app.get("/api/auth/gitlab/login")
def auth_gitlab_login() -> RedirectResponse:
    if not oauth_configured()["gitlab"]:
        raise HTTPException(501, "GitLab OAuth not configured")
    return RedirectResponse(gitlab_authorize_url())


@app.get("/api/auth/gitlab/callback")
async def auth_gitlab_callback(code: str) -> RedirectResponse:
    user = await gitlab_login(code)
    redirect = RedirectResponse(f"{APP_URL}/?login=gitlab")
    set_session_cookie(redirect, user["id"])
    return redirect


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict[str, Any]:
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/saved")
def saved_sites(request: Request, favorites: bool = False) -> dict[str, Any]:
    user = _auth_required(request)
    return {"sites": list_saved_sites(user["id"], favorites_only=favorites)}


@app.post("/api/saved")
def create_saved(request: Request, body: SaveSiteRequest) -> dict[str, Any]:
    user = _auth_required(request)
    site = save_site(
        user["id"],
        repo_url=body.repo_url,
        run_id=body.run_id,
        title=body.title,
        success_url=body.success_url,
        summary=body.summary,
        labels=body.labels,
        favorite=body.favorite,
    )
    return {"site": site}


@app.post("/api/saved/{site_id}/favorite")
def favorite_site(request: Request, site_id: str) -> dict[str, Any]:
    user = _auth_required(request)
    result = toggle_favorite(user["id"], site_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return {"site": result}


@app.delete("/api/saved/{site_id}")
def remove_saved(request: Request, site_id: str) -> dict[str, Any]:
    user = _auth_required(request)
    result = delete_saved_site(user["id"], site_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.post("/api/report-issue")
def report_issue(request: Request, body: IssueReportRequest) -> dict[str, Any]:
    user = user_from_request(request)
    report = create_issue_report(
        subject=body.subject,
        body=body.body,
        contact=body.contact,
        repo_url=body.repo_url,
        user_id=user["id"] if user else None,
    )
    github = create_github_issue(
        subject=body.subject,
        body=body.body,
        contact=body.contact,
        repo_url=body.repo_url,
        user=user,
        report_id=report.get("id"),
    )
    issues_url = github.get("url") or os.getenv("GITHUB_ISSUES_URL")
    return {
        "report": report,
        "issues_url": issues_url,
        "github": github,
        "delivered": bool(github.get("ok")),
    }


@app.get("/api/runs")
def runs(request: Request, limit: int = 30) -> dict[str, Any]:
    user = user_from_request(request)
    if not user:
        return {"runs": []}
    items = list_runs_for_user(user["id"], limit)
    return {"runs": items}


@app.get("/api/runs/{run_id}")
def run_detail(request: Request, run_id: str) -> dict[str, Any]:
    run = _check_run_access(request, run_id)
    return {"run": run, "events": list_events(run_id)}


@app.get("/api/approvals")
def approvals(run_id: str | None = None) -> dict[str, Any]:
    pending = list_pending_approvals(run_id)
    return {"approvals": pending}


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str, body: ApproveRequest) -> dict[str, Any]:
    result = resolve_approval(approval_id, body.values)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/forge/stream")
async def forge_stream(request: Request, req: ForgeRequest) -> EventSourceResponse:
    user = _auth_required(request)
    repo_url = normalize_repo_url(req.repo_url)
    run = create_run_for_user(repo_url, req.branch, req.start_command, user["id"])
    run_id = run["run_id"]
    session_id = req.session_id or str(uuid.uuid4())

    if run_id in _run_lock:
        raise HTTPException(409, "Run already active")

    async def generate() -> AsyncGenerator[dict[str, str], None]:
        _run_lock.add(run_id)
        try:
            assert runner is not None
            yield {"event": "run", "data": json.dumps({"run_id": run_id, "session_id": session_id})}
            async for event in stream_hybrid_run(
                runner,
                run_id=run_id,
                repo_url=repo_url,
                branch=req.branch,
                start_command=req.start_command,
                session_id=session_id,
                user_id=user["id"],
                resume=False,
            ):
                if event.get("type") == "repo_insight":
                    yield {"event": "insight", "data": json.dumps(event)}
                else:
                    yield {"event": "agent", "data": json.dumps(event)}

            final = get_run(run_id)
            pending = list_pending_approvals(run_id)
            yield {"event": "state", "data": json.dumps({"run": final, "approvals": pending})}
            yield {"event": "done", "data": json.dumps({"run_id": run_id})}
        except Exception as exc:
            update_run(run_id, status="failed", error=str(exc))
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}
            yield {"event": "done", "data": "{}"}
        finally:
            _run_lock.discard(run_id)

    return EventSourceResponse(generate(), ping=5)


@app.post("/api/runs/{run_id}/continue/stream")
async def continue_stream(request: Request, run_id: str, req: ContinueRequest) -> EventSourceResponse:
    run = _check_run_access(request, run_id)
    user = _auth_required(request)

    async def generate() -> AsyncGenerator[dict[str, str], None]:
        try:
            assert runner is not None
            yield {"event": "run", "data": json.dumps({"run_id": run_id, "session_id": req.session_id})}
            async for event in stream_hybrid_run(
                runner,
                run_id=run_id,
                repo_url=run["repo_url"],
                branch=run.get("branch"),
                start_command=run.get("start_command"),
                session_id=req.session_id,
                user_id=user["id"],
                resume=True,
            ):
                if event.get("type") == "repo_insight":
                    yield {"event": "insight", "data": json.dumps(event)}
                else:
                    yield {"event": "agent", "data": json.dumps(event)}

            final = get_run(run_id)
            pending = list_pending_approvals(run_id)
            yield {"event": "state", "data": json.dumps({"run": final, "approvals": pending})}
            yield {"event": "done", "data": json.dumps({"run_id": run_id})}
        except Exception as exc:
            update_run(run_id, status="failed", error=str(exc))
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}
            yield {"event": "done", "data": "{}"}

    return EventSourceResponse(generate(), ping=5)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
