"""FORGE agent — Google ADK + GitLab MCP + workspace tools."""

from __future__ import annotations

import os

from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters

from .tools import (
    clone_repository,
    detect_start_command,
    get_run_status,
    inspect_repo_url,
    list_files,
    mark_run_success,
    read_file,
    request_env_write,
    run_command,
    write_env_file,
    write_file,
)

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

INSTRUCTION = """You are FORGE — an autonomous repository bootstrap engine (Google ADK + GitLab MCP).

MISSION: Clone a real git repository and make it run locally. Every action must use tools.

MULTI-STEP WORKFLOW:

PHASE 1 — DISCOVER
- inspect_repo_url on the repository URL
- If GitLab URL: use GitLab MCP list_projects, get_repository_tree, get_file_contents
  to read README, package.json, .env.example BEFORE cloning
- clone_repository once

PHASE 2 — PROVISION
- list_files + read_file on manifests (package.json, pyproject.toml, README)
- detect_start_command for install/start hints
- If .env required: request_env_write with JSON array of keys, STOP for operator approval
- After approval: write_env_file(approval_id)

PHASE 3 — EXECUTE & HEAL (loop until success or iteration limit)
- run_command for install then start (commands load .env automatically if present)
- On non-zero exit: read stderr, read_file affected paths, write_file minimal fix, retry
- get_run_status to check iteration count

PHASE 4 — HANDOFF
- mark_run_success with localhost URL parsed from stdout (e.g. http://localhost:PORT)

RULES:
- Never invent errors or file contents
- Smallest possible edits
- GitLab MCP for GitLab repos; local tools after clone for execution
- Human must approve before .env is written
"""

_tools = [
    FunctionTool(inspect_repo_url),
    FunctionTool(clone_repository),
    FunctionTool(list_files),
    FunctionTool(read_file),
    FunctionTool(write_file),
    FunctionTool(run_command),
    FunctionTool(detect_start_command),
    FunctionTool(request_env_write),
    FunctionTool(write_env_file),
    FunctionTool(mark_run_success),
    FunctionTool(get_run_status),
]


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
            "search_repositories",
            "list_projects",
            "get_project",
        ],
    )


def build_tools() -> list:
    # Local sandboxed read/write/list tools cover the workspace; filesystem MCP
    # exposes the same names and Gemini rejects duplicate function declarations.
    tools: list = list(_tools)
    gitlab = _gitlab_mcp()
    if gitlab:
        tools.append(gitlab)
    return tools


root_agent = Agent(
    name="forge",
    model=MODEL,
    description="Clone a repository, fix runtime errors, and bring it up locally.",
    instruction=INSTRUCTION,
    tools=build_tools(),
)
