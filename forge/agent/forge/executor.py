"""Subprocess execution inside a run workspace."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .detection import normalize_repo_url

DEFAULT_TIMEOUT = int(os.getenv("FORGE_CMD_TIMEOUT", "120"))


def run_in_workspace(
    cwd: Path,
    command: str,
    timeout: int | None = None,
) -> dict[str, Any]:
    timeout = timeout or DEFAULT_TIMEOUT
    env = os.environ.copy()
    env["FORGE_RUN"] = "1"
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return {
            "command": command,
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-8000:] if proc.stdout else "",
            "stderr": proc.stderr[-8000:] if proc.stderr else "",
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "success": False,
        }
    except Exception as exc:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(exc),
            "success": False,
        }


def clone_repo(repo_url: str, dest: Path, branch: str | None = None) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and any(dest.iterdir()):
        return {"success": True, "path": str(dest), "note": "workspace already populated"}

    url = normalize_repo_url(repo_url)
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([url, str(dest)])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return {
            "success": proc.returncode == 0,
            "path": str(dest),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stderr": "git clone timed out"}
    except Exception as exc:
        return {"success": False, "stderr": str(exc)}
