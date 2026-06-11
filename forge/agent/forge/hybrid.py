"""Hybrid run streaming — Gemini ADK + GitLab MCP + pipeline fallback."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

from google.adk.runners import InMemoryRunner
from google.genai import types

from .gitlab_client import parse_gitlab_url
from .mcp_discover import stream_gitlab_discover
from .pipeline import stream_forge
from .repo_insight import generate_repo_insight_async
from .store import log_event, update_run
from .tools import set_active_run


def _is_quota_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "resource_exhausted" in msg or "quota" in msg


def _event_dict(event: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "author": "gemini_adk",
        "partial": getattr(event, "partial", False),
    }
    if event.content and event.content.parts:
        parts = []
        for p in event.content.parts:
            if p.text:
                parts.append({"type": "text", "text": p.text})
            if p.function_call:
                parts.append(
                    {
                        "type": "call",
                        "name": p.function_call.name,
                        "args": dict(p.function_call.args or {}),
                        "source": "gemini_adk" if p.function_call.name not in {
                            "get_project",
                            "get_repository_tree",
                            "get_file_contents",
                            "list_projects",
                            "search_repositories",
                        } else "gitlab_mcp",
                    }
                )
            if p.function_response:
                name = p.function_response.name
                parts.append(
                    {
                        "type": "response",
                        "name": name,
                        "body": p.function_response.response,
                        "source": "gitlab_mcp"
                        if name
                        in {
                            "get_project",
                            "get_repository_tree",
                            "get_file_contents",
                            "list_projects",
                            "search_repositories",
                        }
                        else "gemini_adk",
                    }
                )
        out["parts"] = parts
    return out


def _build_prompt(
    run_id: str,
    repo_url: str,
    branch: str | None,
    start_command: str | None,
) -> str:
    lines = [
        f"Lowkally run_id: {run_id}",
        f"Repository: {repo_url}",
        "You are the Google ADK bootstrap agent (Gemini). Use tools for every step — do not chat only.",
    ]
    if branch:
        lines.append(f"Branch: {branch}")
    if start_command:
        lines.append(f"Preferred start command: {start_command}")
    if parse_gitlab_url(repo_url):
        lines.append(
            "This is a GitLab repo. GitLab MCP discovery may have already run — "
            "use get_file_contents / get_repository_tree if you need more context, then clone."
        )
    lines.extend(
        [
            "Workflow: inspect_repo_url → clone_repository → detect_start_command → "
            "run_command (install) → run_command (start) → heal on errors → mark_run_success.",
            "Call detect_start_command after clone — it returns install/run with pnpm/npm heuristics.",
            "If .env.example exists, call request_env_write and STOP until operator approves.",
        ]
    )
    return "\n".join(lines)


async def _ensure_session(runner: InMemoryRunner, user_id: str, session_id: str) -> None:
    session = await runner.session_service.get_session(
        app_name="forge", user_id=user_id, session_id=session_id
    )
    if session is None:
        await runner.session_service.create_session(
            app_name="forge", user_id=user_id, session_id=session_id
        )


async def _emit_insight_if_ready(
    task: asyncio.Task | None,
    *,
    run_id: str,
    sent: bool,
) -> tuple[dict[str, Any] | None, bool]:
    if sent or task is None or not task.done():
        return None, sent
    try:
        insight = task.result()
        if not insight:
            return None, sent
        log_event(run_id, "repo_insight", insight)
        return {"type": "repo_insight", **insight}, True
    except Exception:
        return None, sent


async def stream_hybrid_run(
    runner: InMemoryRunner,
    *,
    run_id: str,
    repo_url: str,
    branch: str | None,
    start_command: str | None,
    session_id: str,
    user_id: str = "operator",
    resume: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Gemini ADK + GitLab MCP primary; deterministic pipeline on quota failure."""
    set_active_run(run_id)
    update_run(run_id, status="active")

    insight_task: asyncio.Task | None = None
    insight_sent = False

    if not resume:
        insight_task = asyncio.create_task(
            generate_repo_insight_async(repo_url, branch)
        )
        yield {
            "author": "lowkally",
            "partial": False,
            "parts": [{"type": "text", "text": "Gemini — reading README for repo insight…"}],
        }

    if not resume and parse_gitlab_url(repo_url) and os.getenv("GOOGLE_API_KEY"):
        yield {
            "author": "lowkally",
            "partial": False,
            "parts": [{"type": "text", "text": "Phase 1 — GitLab MCP discovery (partner integration)…"}],
        }
        try:
            async for ev in stream_gitlab_discover(repo_url, f"{session_id}_gitlab", user_id):
                yield ev
                insight_ev, insight_sent = await _emit_insight_if_ready(
                    insight_task, run_id=run_id, sent=insight_sent
                )
                if insight_ev:
                    yield insight_ev
        except Exception as exc:
            yield {
                "author": "lowkally",
                "partial": False,
                "parts": [{"type": "text", "text": f"GitLab MCP discover warning: {exc}"}],
            }

    if insight_task and not insight_sent:
        try:
            insight = await asyncio.wait_for(asyncio.shield(insight_task), timeout=12.0)
            if insight:
                log_event(run_id, "repo_insight", insight)
                yield {"type": "repo_insight", **insight}
                insight_sent = True
        except (asyncio.TimeoutError, Exception):
            insight_ev, insight_sent = await _emit_insight_if_ready(
                insight_task, run_id=run_id, sent=insight_sent
            )
            if insight_ev:
                yield insight_ev

    use_agent = bool(os.getenv("GOOGLE_API_KEY")) and os.getenv("LOWKALLY_PIPELINE_ONLY") != "1"

    if use_agent:
        yield {
            "author": "lowkally",
            "partial": False,
            "parts": [{"type": "text", "text": "Phase 2 — Gemini ADK agent bootstrap (multi-step tools)…"}],
        }
        try:
            await _ensure_session(runner, user_id, session_id)
            prompt = _build_prompt(run_id, repo_url, branch, start_command)
            if resume:
                prompt = (
                    f"Environment approved for run {run_id}. "
                    "Call write_env_file with the approval id, then continue install/run until mark_run_success."
                )
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
            ):
                ev = _event_dict(event)
                if ev.get("parts"):
                    yield ev
            return
        except Exception as exc:
            if not _is_quota_error(exc):
                raise
            yield {
                "author": "lowkally",
                "partial": False,
                "parts": [
                    {
                        "type": "text",
                        "text": f"Gemini quota hit — falling back to deterministic pipeline: {str(exc)[:120]}",
                    }
                ],
            }

    yield {
        "author": "lowkally",
        "partial": False,
        "parts": [{"type": "text", "text": "Running deterministic bootstrap pipeline…"}],
    }
    async for event in stream_forge(
        run_id,
        repo_url,
        branch,
        start_command,
        resume=resume,
        user_id=user_id,
    ):
        yield event
