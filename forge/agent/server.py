"""Lowkally API server."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.adk.runners import InMemoryRunner
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from forge import root_agent as forge_agent
from forge.gitlab_client import list_projects, verify_token
from forge.hybrid import stream_hybrid_run
from forge.store import create_run, get_run, list_events, list_pending_approvals, list_runs, resolve_approval, update_run

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


@app.get("/api/runs")
def runs(limit: int = 30) -> dict[str, Any]:
    items = list_runs(limit)
    return {"runs": items}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
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
async def forge_stream(req: ForgeRequest) -> EventSourceResponse:
    run = create_run(req.repo_url, req.branch, req.start_command)
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
                repo_url=req.repo_url,
                branch=req.branch,
                start_command=req.start_command,
                session_id=session_id,
                user_id=req.user_id,
                resume=False,
            ):
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
async def continue_stream(run_id: str, req: ContinueRequest) -> EventSourceResponse:
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

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
                user_id=req.user_id,
                resume=True,
            ):
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
