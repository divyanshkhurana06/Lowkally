"""Agent tools — clone, read, write, run, env approvals."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import store
from .executor import clone_repo, run_in_workspace
from .detection import discover_commands, detect_env_keys, detect_stack, is_web_project, parse_env_example
from .executor import clone_repo, run_in_workspace
from .gitlab_client import parse_gitlab_url
from .workspace import resolve_file, run_dir

_active_run: str | None = None


def set_active_run(run_id: str) -> None:
    global _active_run
    _active_run = run_id


def get_active_run() -> str | None:
    return _active_run


def _require_run() -> str:
    if not _active_run:
        raise RuntimeError("No active run context")
    return _active_run


def inspect_repo_url(repo_url: str) -> dict[str, Any]:
    """Parse repo URL and return host hints (GitLab project path if applicable)."""
    gl = parse_gitlab_url(repo_url)
    return {
        "url": repo_url,
        "is_gitlab": gl is not None,
        "gitlab": gl,
        "hint": (
            "Use GitLab MCP list_projects / get_repository_tree before clone if token is configured."
            if gl
            else "Public clone via git; use list_files after clone_repository."
        ),
    }


def clone_repository(repo_url: str, branch: str = "") -> dict[str, Any]:
    """
    Clone the target git repository into the isolated workspace for this run.
    Call once at the start of a forge session.
    """
    run_id = _require_run()
    dest = run_dir(run_id)
    result = clone_repo(repo_url, dest, branch or None)
    store.log_event(run_id, "clone", result)
    if result.get("success"):
        store.update_run(run_id, workspace_path=str(dest), status="cloned")
    else:
        store.update_run(run_id, status="failed", error=result.get("stderr", "clone failed"))
    return result


def list_files(path: str = ".") -> dict[str, Any]:
    """List files and directories relative to the cloned repository root."""
    run_id = _require_run()
    base = run_dir(run_id)
    target = resolve_file(run_id, path) if path != "." else base
    if not target.exists():
        return {"error": f"Path not found: {path}"}
    if target.is_file():
        return {"path": path, "type": "file", "size": target.stat().st_size}
    entries = []
    for child in sorted(target.iterdir()):
        if child.name == ".git":
            continue
        entries.append(
            {"name": child.name, "type": "dir" if child.is_dir() else "file"}
        )
    return {"path": path, "entries": entries}


def read_file(path: str) -> dict[str, Any]:
    """Read a text file from the cloned repository."""
    run_id = _require_run()
    target = resolve_file(run_id, path)
    if not target.exists():
        return {"error": f"File not found: {path}"}
    if not target.is_file():
        return {"error": f"Not a file: {path}"}
    if target.stat().st_size > 500_000:
        return {"error": "File too large (>500KB)"}
    content = target.read_text(encoding="utf-8", errors="replace")
    return {"path": path, "content": content, "lines": len(content.splitlines())}


def write_file(path: str, content: str) -> dict[str, Any]:
    """Write or overwrite a text file in the cloned repository."""
    run_id = _require_run()
    target = resolve_file(run_id, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    store.log_event(run_id, "write", {"path": path, "bytes": len(content)})
    return {"path": path, "written": True, "bytes": len(content)}


def run_command(command: str) -> dict[str, Any]:
    """
    Execute a shell command in the repository root. Use for install/start/test.
    Returns exit code, stdout, stderr. Increment heal iteration on failure.
    """
    run_id = _require_run()
    if store.iteration_limit_reached(run_id):
        return {"error": f"Iteration limit ({store.MAX_ITERATIONS}) reached", "success": False}

    cwd = run_dir(run_id)
    env_file = cwd / ".env"
    cmd = command
    if env_file.exists():
        cmd = f"set -a && source .env && set +a && {command}"
    result = run_in_workspace(cwd, cmd)
    store.log_event(run_id, "command", result)
    if not result.get("success"):
        store.increment_iteration(run_id)
        store.update_run(run_id, status="healing")
    return result


def detect_start_command() -> dict[str, Any]:
    """Detect stack and suggest install/build/run commands (RepoFix-style heuristics)."""
    run_id = _require_run()
    root = run_dir(run_id)
    if not root.exists():
        return {"error": "Clone the repository first"}

    stack = detect_stack(root)
    cmds = discover_commands(root, stack)
    env_keys = detect_env_keys(root)
    env_defaults = parse_env_example(root)

    return {
        "runtime": stack.runtime,
        "framework": stack.framework,
        "install": cmds.install,
        "build": cmds.build,
        "run": cmds.run,
        "source": cmds.source,
        "web": is_web_project(root, stack, cmds),
        "env_keys": env_keys,
        "env_defaults": env_defaults,
        "hint": "Run install command first, then run command. Use npx pnpm if pnpm lockfile present.",
    }


def request_env_write(keys: str) -> dict[str, Any]:
    """
    Request operator approval before writing .env. Pass JSON array of required key names.
    Example keys: '["PORT","DATABASE_URL","API_KEY"]'
    """
    run_id = _require_run()
    try:
        key_list = json.loads(keys)
        if not isinstance(key_list, list):
            raise ValueError("keys must be a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": str(exc)}

    approval = store.create_approval(run_id, [str(k) for k in key_list])
    store.update_run(run_id, status="awaiting_env")
    return approval


def write_env_file(approval_id: str) -> dict[str, Any]:
    """
    Write .env after operator approved via the UI. Reads approved values from the store.
    """
    run_id = _require_run()
    approval = store.get_approval(approval_id)
    if not approval:
        return {"error": "Approval not found"}
    if approval["status"] != "approved":
        return {"error": "Approval still pending — operator must approve in UI"}
    if approval["run_id"] != run_id:
        return {"error": "Approval belongs to a different run"}

    values = approval.get("values") or {}
    lines = [f"{k}={v}" for k, v in values.items()]
    target = resolve_file(run_id, ".env")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store.log_event(run_id, "env_written", {"keys": list(values.keys())})
    store.update_run(run_id, status="healing")
    return {"written": True, "path": ".env", "keys": list(values.keys())}


def mark_run_success(local_url: str, summary: str) -> dict[str, Any]:
    """Call when the app runs cleanly. Records the URL and completes the run."""
    run_id = _require_run()
    store.update_run(
        run_id,
        status="running",
        success_url=local_url,
        finished_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    )
    store.log_event(run_id, "success", {"url": local_url, "summary": summary})
    return {"run_id": run_id, "status": "running", "url": local_url}


def get_run_status() -> dict[str, Any]:
    """Current run metadata, iteration count, and pending approvals."""
    run_id = _require_run()
    run = store.get_run(run_id)
    pending = store.list_pending_approvals(run_id)
    return {
        "run": run,
        "iterations": run.get("iteration") if run else 0,
        "max_iterations": store.MAX_ITERATIONS,
        "pending_approvals": pending,
    }
