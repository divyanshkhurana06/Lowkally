"""Error classification and rule-based fixes — RepoFix-inspired."""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .detection import (
    StackInfo,
    _from_rails,
    _is_rails_app,
    compose_run_command,
    detect_package_manager,
    find_compose_file,
    rails_local_env_updates,
    rails_server_command,
    resolve_pm_cmd,
)
from .runtime_checks import docker_daemon_available

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
    run_command: str | None = None
    stack_override: StackInfo | None = None
    fatal_message: str | None = None
    next_step: str = "rerun"


def _port_free(port: int) -> bool:
    """Check all interfaces — Next.js often binds on :: and dual-stack hosts differ."""
    for family, addr in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((addr, port))
        except OSError:
            return False
    return True


_ALLOCATED_PORTS: set[int] = set()


def reserve_port(port: int) -> None:
    _ALLOCATED_PORTS.add(int(port))


def find_free_port(start: int | None = None, exclude: set[int] | None = None) -> int:
    import os

    skip = set(exclude or ()) | _ALLOCATED_PORTS
    default = int(os.getenv("LOWKALLY_PORT_START", "3010"))
    base = default if start is None else max(start, default)
    for port in range(base, base + 200):
        if port in skip:
            continue
        if _port_free(port):
            _ALLOCATED_PORTS.add(port)
            return port
    fallback = base + 200
    _ALLOCATED_PORTS.add(fallback)
    return fallback


def extract_app_url(output: str) -> str | None:
    local = re.search(r"Local:\s*(https?://[^\s]+)", output, re.I)
    if local:
        return local.group(1).rstrip("/")
    serving = re.search(r"http://127\.0\.0\.1:(\d{2,5})", output)
    if serving:
        return f"http://127.0.0.1:{serving.group(1)}"
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

    if "invalid environment variables" in blob.lower():
        add(
            ClassifiedError(
                error_type="missing_env_var",
                description="Invalid or missing environment variables (t3-env / Next.js)",
                extracted={"t3_env": True},
            )
        )

    if re.search(r"overmind:\s*command not found", blob, re.I):
        add(
            ClassifiedError(
                error_type="missing_tool",
                description="Overmind is not installed",
                extracted={"tool": "overmind"},
            )
        )

    for tool in ("bundle", "ruby", "python3", "python", "go", "java", "pnpm", "yarn", "npm"):
        if re.search(rf"{re.escape(tool)}:\s*command not found", blob, re.I):
            add(
                ClassifiedError(
                    error_type="missing_tool",
                    description=f"{tool} is not installed",
                    extracted={"tool": tool},
                )
            )

    if "unsupported engine" in blob.lower():
        add(
            ClassifiedError(
                error_type="node_engine",
                description="Node.js version does not match package requirements",
                extracted={},
            )
        )

    if re.search(r"(PG::|postgres|psql).*(connection refused|could not connect)", blob, re.I):
        add(
            ClassifiedError(
                error_type="database_unavailable",
                description="PostgreSQL is not reachable",
                extracted={"db": "postgres"},
            )
        )

    if re.search(r"redis.*(connection refused|ECONNREFUSED)|ECONNREFUSED.*6379", blob, re.I):
        add(
            ClassifiedError(
                error_type="database_unavailable",
                description="Redis is not reachable",
                extracted={"db": "redis"},
            )
        )

    if "request to github" in blob.lower() or "github token" in blob.lower():
        add(
            ClassifiedError(
                error_type="github_api",
                description="GitHub API rejected bootstrap credentials",
                extracted={},
            )
        )

    if not out and ("error" in blob.lower() or "failed" in blob.lower()):
        add(ClassifiedError(error_type="unknown", description=blob.strip()[-300:]))
    return out


def _rails_fallback(root: Path, description: str) -> FixAction | None:
    rails = _from_rails(root)
    if not rails:
        return None
    return FixAction(
        description=description,
        commands=[c for c in (rails.install,) if c],
        run_command=rails.run,
        stack_override=StackInfo(runtime="ruby", framework="rails", language="ruby"),
        env_updates=rails_local_env_updates(),
    )


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
        busy = error.extracted.get("port")
        start = int(busy) + 1 if busy else None
        port = find_free_port(start=start, exclude={int(busy)} if busy else None)
        return FixAction(
            description=f"Port {busy or '?'} in use — switching to {port}",
            env_updates={"PORT": str(port), "VITE_PORT": str(port), "NEXT_PUBLIC_PORT": str(port)},
            next_step="rerun",
        )

    if error.error_type == "missing_env_var":
        var = error.extracted.get("var_name")
        if var:
            return FixAction(description=f"Need env var {var}", next_step="need_env")

    if error.error_type == "github_api":
        return FixAction(
            description="Disable live GitHub fetch and retry",
            env_updates={"USE_GITHUB_DATA": "false"},
            next_step="rerun",
        )

    if error.error_type == "node_engine":
        pm = resolve_pm_cmd(root)
        return FixAction(
            description="Retry install ignoring Node engine strictness",
            commands=[f"{pm} install --engine-strict=false"],
            next_step="rerun",
        )

    if error.error_type == "database_unavailable":
        db = error.extracted.get("db")
        if db == "postgres" and find_compose_file(root) and docker_daemon_available():
            build, run = compose_run_command(root)
            return FixAction(
                description="Start Postgres via Docker Compose",
                commands=[build, run],
                run_command=run,
                stack_override=StackInfo(runtime="docker", framework="rails-docker", language="ruby"),
            )
        if db == "redis" and find_compose_file(root) and docker_daemon_available():
            cli = compose_run_command(root)[0].split()[0:2]
            return FixAction(
                description="Start Redis via Docker Compose",
                commands=[" ".join([*cli, "up", "-d", "redis"])],
                next_step="rerun",
            )
        return FixAction(
            fatal_message=f"{db or 'Database'} is not running. Start it locally or use Docker Desktop and retry.",
        )

    if error.error_type == "missing_tool":
        tool = error.extracted.get("tool")
        if tool == "overmind":
            compose = find_compose_file(root)
            if compose and docker_daemon_available():
                build, run = compose_run_command(root)
                return FixAction(
                    description="Overmind not installed — using Docker Compose for this Rails app",
                    commands=[build] if build else [],
                    run_command=run,
                    stack_override=StackInfo(runtime="docker", framework="rails-docker", language="ruby"),
                )
            fix = _rails_fallback(root, "Overmind not installed — starting Rails directly")
            if fix:
                return fix
            return FixAction(
                description="Install overmind (gem install overmind) or use Docker Compose",
                commands=["gem install overmind"],
                next_step="rerun",
            )
        if tool == "bundle":
            return FixAction(
                description="Install Ruby gems before starting Rails",
                commands=["bundle install"],
                next_step="rerun",
            )
        if tool in ("ruby", "bundle"):
            return FixAction(
                fatal_message="Ruby is not installed. Install Ruby (rbenv/asdf) or start Docker Desktop for containerized apps.",
            )
        if tool == "python3" or tool == "python":
            return FixAction(
                fatal_message="Python is not installed or not on PATH.",
            )
        if tool in ("pnpm", "yarn", "npm"):
            pm = "npx --yes pnpm" if tool == "pnpm" else f"npx --yes {tool}"
            return FixAction(
                description=f"Retry with {pm}",
                commands=[f"{pm} install"],
                next_step="rerun",
            )

    return None
