"""Error classification and rule-based fixes — RepoFix-inspired."""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .detection import StackInfo, detect_package_manager

_JS_MODULE_RE = re.compile(r"Cannot find module ['\"](.+?)['\"]")
_JS_PKG_RE = re.compile(r"Cannot find package ['\"](.+?)['\"]", re.I)
_PY_MODULE_RE = re.compile(r"No module named ['\"]?([^\s'\"]+)")
_PORT_RE = re.compile(r"(?:EADDRINUSE|address already in use).*?(?:port |:)(\d{2,5})", re.I)
_ENV_RE = re.compile(
    r"(?:process\.env\.([A-Z_][A-Z0-9_]*)|Missing (?:env|environment).*['\"]([A-Z_][A-Z0-9_]*)['\"])",
    re.I,
)
_LOCALHOST_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1):(\d{2,5})")


@dataclass
class ClassifiedError:
    error_type: str
    description: str
    extracted: dict[str, Any] = field(default_factory=dict)


@dataclass
class FixAction:
    description: str
    commands: list[str] = field(default_factory=list)
    env_updates: dict[str, str] = field(default_factory=dict)
    next_step: str = "rerun"


def find_free_port(start: int = 3000) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start + 50


def extract_app_url(output: str) -> str | None:
    local = re.search(r"Local:\s*(https?://[^\s]+)", output, re.I)
    if local:
        return local.group(1).rstrip("/")
    matches = _LOCALHOST_RE.findall(output)
    if matches:
        return f"http://localhost:{matches[-1]}"
    port_matches = re.findall(r"localhost:(\d{2,5})", output, re.I)
    if port_matches:
        return f"http://localhost:{port_matches[-1]}"
    return None


def classify_errors(stderr: str, stdout: str, runtime: str) -> list[ClassifiedError]:
    blob = f"{stderr}\n{stdout}"
    out: list[ClassifiedError] = []
    seen: set[str] = set()

    def add(err: ClassifiedError) -> None:
        key = (err.error_type, str(err.extracted))
        if key not in seen:
            seen.add(key)
            out.append(err)

    for pattern, etype in (
        (_JS_MODULE_RE, "missing_dependency"),
        (_JS_PKG_RE, "missing_dependency"),
        (_PY_MODULE_RE, "missing_dependency"),
    ):
        for match in pattern.finditer(blob):
            pkg = match.group(1).split("/")[0].split("@")[0]
            add(
                ClassifiedError(
                    error_type=etype,
                    description=f"Missing package: {pkg}",
                    extracted={"package": pkg, "runtime": runtime},
                )
            )

    port_match = _PORT_RE.search(blob)
    if port_match:
        add(
            ClassifiedError(
                error_type="port_conflict",
                description=f"Port {port_match.group(1)} in use",
                extracted={"port": int(port_match.group(1))},
            )
        )

    for match in _ENV_RE.finditer(blob):
        var = match.group(1) or match.group(2)
        if var:
            add(
                ClassifiedError(
                    error_type="missing_env_var",
                    description=f"Missing env: {var}",
                    extracted={"var_name": var},
                )
            )

    if not out and ("error" in blob.lower() or "failed" in blob.lower()):
        add(ClassifiedError(error_type="unknown", description=blob.strip()[-300:]))
    return out


def apply_rule(error: ClassifiedError, stack: StackInfo, root: Path) -> FixAction | None:
    if error.error_type == "missing_dependency":
        pkg = error.extracted.get("package")
        if not pkg:
            return None
        runtime = stack.runtime
        if runtime == "node":
            pm = detect_package_manager(root)
            scoped = pkg if pkg.startswith("@") else pkg
            if scoped.startswith("."):
                return FixAction(description="Local import — retry after install", commands=[f"{pm} install"], next_step="reinstall")
            return FixAction(
                description=f"Install missing Node package {scoped}",
                commands=[f"{pm} install {scoped}"],
                next_step="rerun",
            )
        if runtime == "python":
            mapping = {
                "cv2": "opencv-python",
                "PIL": "Pillow",
                "sklearn": "scikit-learn",
                "dotenv": "python-dotenv",
                "yaml": "pyyaml",
            }
            name = mapping.get(pkg, pkg)
            if (root / "uv.lock").exists():
                cmd = f"uv add {name}"
            elif (root / "requirements.txt").exists():
                cmd = f"pip install {name}"
            else:
                cmd = f"pip install {name}"
            return FixAction(description=f"Install missing Python package {name}", commands=[cmd], next_step="rerun")

    if error.error_type == "port_conflict":
        port = find_free_port(int(error.extracted.get("port", 3000)) + 1)
        return FixAction(
            description=f"Switch to port {port}",
            env_updates={"PORT": str(port), "VITE_PORT": str(port), "NEXT_PUBLIC_PORT": str(port)},
            next_step="rerun",
        )

    if error.error_type == "missing_env_var":
        var = error.extracted.get("var_name")
        if var:
            return FixAction(description=f"Need env var {var}", next_step="need_env")

    return None
