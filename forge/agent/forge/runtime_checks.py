"""Host runtime availability checks (Docker, Ruby, etc.)."""

from __future__ import annotations

import shutil
import subprocess


def docker_cli_available() -> bool:
    return shutil.which("docker") is not None


def docker_daemon_available() -> bool:
    if not docker_cli_available():
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def tool_on_path(name: str) -> bool:
    return shutil.which(name) is not None
