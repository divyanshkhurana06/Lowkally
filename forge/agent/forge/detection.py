"""Stack and command discovery — RepoFix-inspired heuristics."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_PREFERRED_RUN = ("dev", "start", "serve", "preview")
_ENV_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=", re.M)
_ENV_LINE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", re.M)


@dataclass
class StackInfo:
    runtime: str = "unknown"
    framework: str = "unknown"
    language: str = "unknown"

    def is_known(self) -> bool:
        return self.runtime != "unknown"


@dataclass
class CommandSet:
    install: str | None = None
    build: str | None = None
    run: str | None = None
    source: str = "defaults"

    def has_run(self) -> bool:
        return bool(self.run)


def normalize_repo_url(url: str) -> str:
    u = url.strip()
    if u.startswith("git@"):
        return u
    if not u.startswith(("http://", "https://", "git://")):
        u = f"https://{u.lstrip('/')}"
    if u.endswith(".git"):
        return u
    return u if u.endswith("/") else u


_MONOREPO_FRONTEND = ("frontend", "client", "web", "app", "ui")
_MONOREPO_BACKEND = ("backend", "api", "server")
_SKIP_DIRS = {"node_modules", ".git", "contracts", "dist", "build", ".next"}


def _framework_from_package_json(pkg_dir: Path) -> str:
    try:
        data = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        if "next" in deps:
            return "nextjs"
        if "react" in deps:
            return "react"
        if "express" in deps:
            return "express"
        if "vite" in deps:
            return "vite"
    except (OSError, json.JSONDecodeError):
        pass
    return "node"


def _find_node_app_dir(root: Path) -> Path | None:
    for name in _MONOREPO_FRONTEND:
        sub = root / name
        if (sub / "package.json").is_file():
            return sub
    best: Path | None = None
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP_DIRS:
            continue
        if not (child / "package.json").is_file():
            continue
        try:
            scripts = json.loads((child / "package.json").read_text()).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if any(k in scripts for k in _PREFERRED_RUN):
            return child
        if best is None:
            best = child
    return best


def _commands_in_subdir(sub: Path, root: Path) -> CommandSet | None:
    cmds = _from_package_json(sub)
    if not cmds or not cmds.run:
        return None
    rel = sub.relative_to(root).as_posix()
    prefix = f"cd {rel} && "
    return CommandSet(
        install=prefix + cmds.install if cmds.install else None,
        build=prefix + cmds.build if cmds.build else None,
        run=prefix + cmds.run,
        source=f"monorepo/{rel}",
    )


def detect_stack(root: Path) -> StackInfo:
    if (root / "docker-compose.yml").exists() or (root / "Dockerfile").exists():
        return StackInfo(runtime="docker", framework="docker", language="docker")
    if (root / "package.json").exists():
        return StackInfo(
            runtime="node",
            framework=_framework_from_package_json(root),
            language="javascript",
        )
    sub = _find_node_app_dir(root)
    if sub:
        return StackInfo(
            runtime="node",
            framework=_framework_from_package_json(sub),
            language="javascript",
        )
    if any((root / n).exists() for n in ("pyproject.toml", "requirements.txt", "setup.py")):
        return StackInfo(runtime="python", framework="python", language="python")
    if (root / "go.mod").exists():
        return StackInfo(runtime="go", framework="go", language="go")
    if (root / "Cargo.toml").exists():
        return StackInfo(runtime="cargo", framework="rust", language="rust")
    if (root / "Makefile").exists():
        return StackInfo(runtime="make", framework="make", language="unknown")
    return StackInfo()


def detect_package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    return "npm"


def discover_commands(root: Path, stack: StackInfo, override_run: str | None = None) -> CommandSet:
    if override_run:
        pm = detect_package_manager(root) if stack.runtime == "node" else "npm"
        install = _default_install(root, stack, pm)
        return CommandSet(install=install, run=override_run, source="override")

    mk = _from_makefile(root)
    if stack.runtime == "node":
        cmds = _from_package_json(root)
        if not cmds:
            sub = _find_node_app_dir(root)
            if sub:
                cmds = _commands_in_subdir(sub, root)
        if not cmds:
            cmds = _stack_defaults(stack)
    elif stack.runtime == "python":
        cmds = _from_python(root) or _stack_defaults(stack)
    elif stack.runtime == "go":
        cmds = CommandSet(install="go mod download", run="go run .", source="go.mod")
    elif stack.runtime == "cargo":
        cmds = CommandSet(install="cargo fetch", build="cargo build", run="cargo run", source="Cargo.toml")
    elif stack.runtime == "docker":
        if (root / "docker-compose.yml").exists():
            cmds = CommandSet(install="docker compose pull", run="docker compose up", source="docker-compose")
        else:
            cmds = CommandSet(install="docker build -t forge-app .", run="docker run --rm -p 8080:8080 forge-app", source="Dockerfile")
    else:
        cmds = _stack_defaults(stack)

    merged = _merge(mk, cmds)
    return merged or CommandSet(source="unknown")


def detect_env_keys(root: Path) -> list[str]:
    return list(parse_env_example(root).keys())


def parse_env_example(root: Path) -> dict[str, str]:
    """Parse KEY=value defaults from .env.example files."""
    values: dict[str, str] = {}
    search_dirs = [root]
    sub = _find_node_app_dir(root)
    if sub:
        search_dirs.append(sub)
    for name in _MONOREPO_BACKEND:
        backend = root / name
        if backend.is_dir():
            search_dirs.append(backend)
    for base in search_dirs:
        for name in (".env.example", ".env.sample", "env.example", ".env.template"):
            path = base / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in _ENV_LINE_RE.finditer(text):
                key = match.group(1)
                if key in ("PATH", "HOME", "USER"):
                    continue
                raw = match.group(2).strip()
                if raw and not raw.startswith("#"):
                    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                        raw = raw[1:-1]
                    values[key] = raw
    return values


def is_web_project(root: Path, stack: StackInfo, cmds: CommandSet) -> bool:
    run = cmds.run or ""
    if stack.framework in ("nextjs", "react", "vite", "express"):
        return True
    if any(x in run for x in ("dev", "start", "serve", "preview", "runserver")):
        return True
    if (root / "package.json").exists():
        try:
            scripts = json.loads((root / "package.json").read_text()).get("scripts", {})
            return any(k in scripts for k in _PREFERRED_RUN)
        except (OSError, json.JSONDecodeError):
            pass
    sub = _find_node_app_dir(root)
    if sub:
        try:
            scripts = json.loads((sub / "package.json").read_text()).get("scripts", {})
            return any(k in scripts for k in _PREFERRED_RUN)
        except (OSError, json.JSONDecodeError):
            pass
    return False


def _merge(primary: CommandSet | None, fallback: CommandSet | None) -> CommandSet | None:
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    return CommandSet(
        install=primary.install or fallback.install,
        build=primary.build or fallback.build,
        run=primary.run or fallback.run,
        source=primary.source if primary.run or primary.install else fallback.source,
    )


def _default_install(root: Path, stack: StackInfo, pm: str) -> str | None:
    if stack.runtime == "node":
        return f"{pm} install"
    if stack.runtime == "python":
        if (root / "uv.lock").exists():
            return "uv sync"
        if (root / "requirements.txt").exists():
            return "pip install -r requirements.txt"
        return "pip install -e ."
    return None


def _from_makefile(root: Path) -> CommandSet | None:
    mk = root / "Makefile"
    if not mk.is_file():
        return None
    text = mk.read_text(encoding="utf-8", errors="replace")
    install = "make install" if re.search(r"^install\s*:", text, re.M) else None
    build = "make build" if re.search(r"^build\s*:", text, re.M) else None
    run = None
    for target in ("dev", "run", "start", "serve"):
        if re.search(rf"^{target}\s*:", text, re.M):
            run = f"make {target}"
            break
    if not any((install, build, run)):
        run = "make" if re.search(r"^all\s*:", text, re.M) else None
    if not any((install, build, run)):
        return None
    return CommandSet(install=install, build=build, run=run, source="Makefile")


def _from_package_json(root: Path) -> CommandSet | None:
    pkg_path = root / "package.json"
    if not pkg_path.is_file():
        return None
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pm = detect_package_manager(root)
    scripts: dict = data.get("scripts") or {}
    install = f"{pm} install"
    build = None
    run = None
    for name in ("build", "compile"):
        if name in scripts:
            build = f"{pm} run {name}"
            break
    for name in _PREFERRED_RUN:
        if name in scripts:
            run = f"{pm} run {name}"
            break
    if not run and scripts:
        run = f"{pm} run {next(iter(scripts))}"
    return CommandSet(install=install, build=build, run=run, source="package.json")


def _from_python(root: Path) -> CommandSet | None:
    install = None
    run = None
    if (root / "uv.lock").exists():
        install = "uv sync"
    elif (root / "requirements.txt").exists():
        install = "pip install -r requirements.txt"
    elif (root / "pyproject.toml").exists():
        install = 'pip install -e ".[dev]"' if _has_dev_extra(root) else "pip install -e ."
        script = _python_console_script(root)
        if script:
            return CommandSet(install=install, run=f"{script} --help", source="pyproject.scripts")
    for candidate in ("main.py", "app.py", "manage.py"):
        if (root / candidate).exists():
            if candidate == "manage.py":
                run = "python manage.py runserver 0.0.0.0:8000"
            else:
                run = f"python {candidate}"
            break
    return CommandSet(install=install, run=run, source="python") if install or run else None


def _has_dev_extra(root: Path) -> bool:
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return "[project.optional-dependencies]" in text or 'dev = [' in text


def _python_console_script(root: Path) -> str | None:
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    in_scripts = False
    for line in text.splitlines():
        if line.strip().startswith("[project.scripts]"):
            in_scripts = True
            continue
        if in_scripts:
            if line.startswith("["):
                break
            m = re.match(r"^(\w[\w-]*)\s*=", line)
            if m:
                return m.group(1)
    return None


def _stack_defaults(stack: StackInfo) -> CommandSet:
    if stack.runtime == "node":
        pm = "npm"
        return CommandSet(install=f"{pm} install", run=f"{pm} run dev", source="defaults")
    if stack.runtime == "python":
        return CommandSet(install="pip install -r requirements.txt", run="python main.py", source="defaults")
    return CommandSet(source="unknown")
