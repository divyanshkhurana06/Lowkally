"""GitLab MCP discovery phase — runs before bootstrap for hackathon demo."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.genai import types
from mcp import StdioServerParameters

from .gitlab_client import parse_gitlab_url

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def _gitlab_mcp() -> McpToolset | None:
    token = os.getenv("GITLAB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITLAB_TOKEN")
    if not token:
        return None
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="npx",
                args=["-y", "@zereight/mcp-gitlab@latest"],
                env={
                    "GITLAB_PERSONAL_ACCESS_TOKEN": token,
                    "GITLAB_API_URL": os.getenv("GITLAB_API_URL", "https://gitlab.com/api/v4"),
                },
            ),
            timeout=120,
        ),
        tool_filter=[
            "get_file_contents",
            "get_repository_tree",
            "list_projects",
            "get_project",
        ],
    )


def _event_dict(event: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "author": "gitlab_mcp",
        "partial": getattr(event, "partial", False),
    }
    parts: list[dict[str, Any]] = []
    if event.content and event.content.parts:
        for p in event.content.parts:
            if p.text:
                parts.append({"type": "text", "text": p.text})
            if p.function_call:
                parts.append(
                    {
                        "type": "call",
                        "name": p.function_call.name,
                        "args": dict(p.function_call.args or {}),
                        "source": "gitlab_mcp",
                    }
                )
            if p.function_response:
                parts.append(
                    {
                        "type": "response",
                        "name": p.function_response.name,
                        "body": p.function_response.response,
                        "source": "gitlab_mcp",
                    }
                )
    if parts:
        out["parts"] = parts
    return out


async def stream_gitlab_discover(
    repo_url: str,
    session_id: str,
    user_id: str = "operator",
) -> AsyncIterator[dict[str, Any]]:
    """Run a short ADK + GitLab MCP discover pass for GitLab repository URLs."""
    gl = parse_gitlab_url(repo_url)
    mcp = _gitlab_mcp()
    if not gl or not mcp or not os.getenv("GOOGLE_API_KEY"):
        return

    project_path = gl.get("path_with_namespace") or gl.get("project_path", "")
    agent = Agent(
        name="gitlab_discover",
        model=MODEL,
        description="Discover GitLab repository structure via MCP before bootstrap.",
        instruction="""You are the GitLab MCP discovery agent for Lowkally (Google ADK hackathon).

Use GitLab MCP tools ONLY — no guessing file contents.

Required steps for the given GitLab project:
1. get_project for the project path
2. get_repository_tree for the default branch root
3. get_file_contents for README.md or README
4. get_file_contents for package.json, pyproject.toml, or .env.example if present

Summarize stack hints (language, install/run commands) from what MCP returns.""",
        tools=[mcp],
    )
    runner = InMemoryRunner(agent=agent, app_name="lowkally_gitlab")
    await runner.session_service.create_session(
        app_name="lowkally_gitlab", user_id=user_id, session_id=session_id
    )
    prompt = (
        f"GitLab project path: {project_path}\n"
        f"Clone URL: {repo_url}\n"
        "Run the required GitLab MCP discovery steps now."
    )
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
    ):
        ev = _event_dict(event)
        if ev.get("parts"):
            yield ev
