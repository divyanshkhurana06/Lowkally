"""Stack and command discovery — RepoFix-inspired heuristics."""

from __future__ import annotations

import json
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from .runtime_checks import docker_daemon_available

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


_MONOREPO_FRONTEND = ("frontend", "client", "web", "app", "ui", "miniapp")
_MONOREPO_BACKEND = ("backend", "api", "server")
_SKIP_DIRS = {"node_modules", ".git", "contracts", "dist", "build", ".next"}
_ENV_TEMPLATE_NAMES = (".env.example", ".env.sample", "env.example", ".env.template", ".env.local.example")


def _has_nextjs(pkg_dir: Path) -> bool:
    return _framework_from_package_json(pkg_dir) == "nextjs"


def _collect_env_search_dirs(root: Path) -> list[Path]:
    """Directories that may hold .env templates or t3-env schemas."""
    dirs: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen or not path.is_dir():
            return
        seen.add(resolved)
        dirs.append(path)

    add(root)
    sub = _find_node_app_dir(root)
    if sub:
        add(sub)
    for backend in _MONOREPO_BACKEND:
        backend_dir = root / backend
        if backend_dir.is_dir():
            add(backend_dir)
    try:
        for pkg in root.rglob("package.json"):
            if any(part in _SKIP_DIRS for part in pkg.parts):
                continue
            add(pkg.parent)
    except OSError:
        pass
    return dirs


def resolve_env_dir(root: Path) -> Path:
    """Best directory to write .env for the primary runnable app."""
    for base in _collect_env_search_dirs(root):
        if (base / "src/lib/env.ts").is_file():
            return base
    for base in _collect_env_search_dirs(root):
        if _has_nextjs(base):
            return base
    sub = _find_node_app_dir(root)
    return sub or root


def env_file_path(root: Path) -> Path:
    """Absolute path to the .env file Lowkally should manage."""
    return resolve_env_dir(root) / ".env"


def env_relative_path(root: Path) -> str:
    """Workspace-relative path to .env (for agent tools)."""
    env_path = env_file_path(root)
    if env_path.parent == root.resolve():
        return ".env"
    return f"{env_path.parent.relative_to(root.resolve()).as_posix()}/.env"


def _parse_t3_required_keys(env_ts: Path) -> list[str]:
    """Required keys from @t3-oss/env-nextjs createEnv (no .optional() / .default())."""
    if not env_ts.is_file():
        return []
    try:
        text = env_ts.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if "createEnv" not in text:
        return []
    required: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if ".optional()" in stripped or ".default(" in stripped:
            continue
        match = re.match(r"^(\w+):\s*z\.string\(\)\.(?:url|min|uuid)\(", stripped)
        if match:
            required.append(match.group(1))
    return required


def env_placeholder(key: str) -> str:
    upper = key.upper()
    if upper.endswith("_URL") or "URL" in upper:
        return "https://placeholder.example.com"
    if any(x in upper for x in ("SECRET", "TOKEN", "KEY", "PASSWORD", "PRIVATE")):
        return "placeholder-dev-value"
    return "placeholder"


def _strip_env_value(raw: str) -> str:
    raw = raw.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    return raw


def _needs_safe_placeholder(value: str) -> bool:
    """True when a template value is unsafe for shell sourcing or clearly a placeholder."""
    if not value:
        return True
    if any(c in value for c in " \t$`!#&|;<>()"):
        return True
    upper = value.upper()
    if upper.startswith("YOUR ") or upper.startswith("YOU ") or " HERE" in upper:
        return True
    return False


def format_env_line(key: str, value: str) -> str:
    """Single KEY=value line safe for dotenv readers and bash source."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=value pairs from an on-disk .env file (no shell execution)."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for match in _ENV_LINE_RE.finditer(text):
        key = match.group(1)
        if key in ("PATH", "HOME", "USER"):
            continue
        raw = _strip_env_value(match.group(2))
        if raw and not raw.startswith("#"):
            values[key] = raw
    return values


def env_export_statements(values: dict[str, str]) -> list[str]:
    """Shell export lines for injecting env without sourcing .env."""
    out: list[str] = []
    for key, value in values.items():
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'export {key}="{escaped}"')
    return out


def _is_placeholder_env_value(value: str) -> bool:
    if not value:
        return True
    if _needs_safe_placeholder(value):
        return True
    lower = value.lower()
    return lower in ("placeholder", "placeholder-dev-value", "your-github-token", "changeme")


def enrich_env_from_user(user: dict | None, values: dict[str, str]) -> dict[str, str]:
    """Fill GitHub-related keys from a logged-in GitHub OAuth session."""
    if not user or user.get("provider") != "github":
        return values
    token = user.get("oauth_token")
    username = user.get("username")
    if not token:
        return values
    out = dict(values)
    for key in out:
        upper = key.upper()
        if "GITHUB" in upper and any(x in upper for x in ("TOKEN", "PAT", "KEY")):
            out[key] = token
    if username:
        for key in out:
            if key.upper() in ("GITHUB_USERNAME", "GITHUB_USER", "GH_USERNAME"):
                out[key] = username
    if "USE_GITHUB_DATA" in out:
        out["USE_GITHUB_DATA"] = "true"
    return out


def _env_value_looks_corrupt(value: str) -> bool:
    """True when a parsed .env value looks like multiple KEY= pairs mashed together."""
    if re.search(r"[A-Z][A-Z0-9_]*\s*=", value):
        return True
    return False


def finalize_bootstrap_env(
    root: Path,
    values: dict[str, str],
    *,
    user: dict | None = None,
) -> dict[str, str]:
    """Dev-safe env: real OAuth creds when available, otherwise disable live API fetches."""
    cleaned = {
        k: env_placeholder(k) if _env_value_looks_corrupt(v) else v
        for k, v in values.items()
    }
    out = enrich_env_from_user(user, dict(cleaned))
    has_github_token = any(
        not _is_placeholder_env_value(v)
        for k, v in out.items()
        if "GITHUB" in k.upper() and any(x in k.upper() for x in ("TOKEN", "PAT", "KEY"))
    )
    if "USE_GITHUB_DATA" in out and not has_github_token:
        out["USE_GITHUB_DATA"] = "false"
    if "MEDIUM_USERNAME" in out and _is_placeholder_env_value(out["MEDIUM_USERNAME"]):
        del out["MEDIUM_USERNAME"]
    if out.get("USE_GITHUB_DATA", "").lower() != "true":
        for key in list(out):
            if "GITHUB" in key.upper() and "USERNAME" in key.upper() and _is_placeholder_env_value(out[key]):
                del out[key]
    if "SECRET_KEY_BASE" in out and _is_placeholder_env_value(out["SECRET_KEY_BASE"]):
        out["SECRET_KEY_BASE"] = secrets.token_hex(64)
    if find_compose_file(root):
        out.setdefault("REDIS_PASSWORD", "")
    return out


def build_env_defaults(root: Path, *, user: dict | None = None) -> dict[str, str]:
    """Merge .env templates with required t3-env keys and dev placeholders."""
    values = parse_env_example(root)
    for key, value in list(values.items()):
        if _needs_safe_placeholder(value):
            values[key] = env_placeholder(key)
    env_dir = resolve_env_dir(root)
    for key in _parse_t3_required_keys(env_dir / "src/lib/env.ts"):
        if key not in values or not values[key] or values[key].startswith("your-"):
            values[key] = env_placeholder(key)
    return finalize_bootstrap_env(root, values, user=user)


def is_dev_run_command(run: str | None) -> bool:
    """True when the discovered run command starts a dev server (build step not required)."""
    if not run:
        return False
    lower = f" {run.lower()} "
    if " dev" in lower or " run dev" in lower:
        return True
    return any(x in lower for x in (" run start", " npm start", " yarn start", " pnpm start", " bun start"))


_STATIC_INDEX = ("index.html", "index.htm", "Index.html")
_STATIC_SUBDIRS = ("public", "dist", "docs", "site", "www", "build")


def _find_static_site_dir(root: Path) -> Path | None:
    """Locate a static HTML site (index.html + optional css/js)."""
    best: tuple[int, Path] | None = None

    def consider(directory: Path, depth: int) -> None:
        nonlocal best
        if not directory.is_dir():
            return
        if not any((directory / name).is_file() for name in _STATIC_INDEX):
            return
        score = 12 - depth * 2
        if (directory / "css").is_dir() or (directory / "js").is_dir():
            score += 6
        if (directory / "scss").is_dir() or (directory / "assets").is_dir():
            score += 2
        if directory.resolve() == root.resolve():
            score += 3
        if best is None or score > best[0]:
            best = (score, directory)

    consider(root, 0)
    for name in _STATIC_SUBDIRS:
        consider(root / name, 1)
    if best is None:
        try:
            for child in sorted(root.iterdir()):
                if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP_DIRS:
                    continue
                consider(child, 1)
        except OSError:
            pass
    return best[1] if best else None


def _from_static_html(root: Path) -> CommandSet | None:
    site = _find_static_site_dir(root)
    if not site:
        return None
    prefix = ""
    if site.resolve() != root.resolve():
        prefix = f"cd {site.relative_to(root).as_posix()} && "
    run = f"{prefix}python3 -m http.server $PORT --bind 127.0.0.1"
    return CommandSet(install=None, run=run, source="static-html")


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


_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
_COMPOSE_WEB_SERVICES = ("rails", "web", "app", "frontend", "api", "server")


def find_compose_file(root: Path) -> Path | None:
    for name in _COMPOSE_FILES:
        path = root / name
        if path.is_file():
            return path
    return None


def compose_cli() -> str:
    return "docker compose" if shutil.which("docker") else "docker-compose"


def _is_rails_app(root: Path) -> bool:
    gemfile = root / "Gemfile"
    if not gemfile.is_file():
        return False
    try:
        text = gemfile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"""gem\s+['"]rails['"]""", text, re.I))


def _compose_needs_docker(root: Path) -> bool:
    path = find_compose_file(root)
    if not path:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return any(x in text for x in ("postgres", "redis", "mysql", "mongodb", "mailhog", "sidekiq"))


def guess_compose_web_service(root: Path) -> str | None:
    path = find_compose_file(root)
    if not path:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for name in _COMPOSE_WEB_SERVICES:
        if re.search(rf"^\s*{name}\s*:", text, re.M):
            return name
    return None


def compose_run_command(root: Path) -> tuple[str, str]:
    cli = compose_cli()
    service = guess_compose_web_service(root)
    build = f"{cli} build"
    run = f"{cli} up -d {service}" if service else f"{cli} up -d"
    return build, run


def is_docker_command(command: str | None) -> bool:
    if not command:
        return False
    lower = command.lower()
    return lower.startswith("docker ") or " docker " in f" {lower} "


def compose_http_url(root: Path) -> str | None:
    """Best-effort public URL for a docker-compose web service."""
    path = find_compose_file(root)
    if not path:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    service = guess_compose_web_service(root) or "web"
    block = re.search(rf"^\s*{re.escape(service)}\s*:(.*?)(?=^\S|\Z)", text, re.M | re.S)
    search = block.group(1) if block else text
    match = re.search(r"[-\s]+[\"']?(\d{2,5}):\d{2,5}[\"']?", search)
    if match:
        return f"http://127.0.0.1:{match.group(1)}"
    if re.search(r":3000", search):
        return "http://127.0.0.1:3000"
    return None


def rails_local_env_updates() -> dict[str, str]:
    return {
        "POSTGRES_HOST": "127.0.0.1",
        "REDIS_URL": "redis://127.0.0.1:6379",
        "FRONTEND_URL": "http://127.0.0.1:3010",
        "BASE_URL": "http://127.0.0.1:3010",
    }


def adjust_stack_for_host(root: Path, stack: StackInfo) -> tuple[StackInfo, str | None]:
    """Downgrade rails-docker only when the Docker daemon is unavailable."""
    if stack.framework != "rails-docker":
        return stack, None
    if docker_daemon_available():
        return stack, None
    return (
        StackInfo(runtime="ruby", framework="rails", language="ruby"),
        "Docker is not running — trying native Rails instead (needs Ruby, Postgres, and Redis).",
    )


def rails_server_command(root: Path) -> str | None:
    mk = root / "Makefile"
    if mk.is_file():
        try:
            text = mk.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if re.search(r"^server\s*:", text, re.M):
            return "make server"
    if _is_rails_app(root):
        return "bundle exec rails server -b 127.0.0.1 -p $PORT"
    return None


def _from_rails(root: Path) -> CommandSet | None:
    if not _is_rails_app(root):
        return None
    parts = ["bundle install"]
    if (root / "package.json").is_file():
        parts.append(f"{resolve_pm_cmd(root)} install")
    install = " && ".join(parts)
    run = rails_server_command(root)
    return CommandSet(install=install, run=run, source="Gemfile")


def detect_stack(root: Path) -> StackInfo:
    if _is_rails_app(root) and find_compose_file(root) and _compose_needs_docker(root):
        return StackInfo(runtime="docker", framework="rails-docker", language="ruby")
    if _is_rails_app(root):
        return StackInfo(runtime="ruby", framework="rails", language="ruby")
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
    if _find_static_site_dir(root):
        return StackInfo(runtime="static", framework="static-html", language="html")
    return StackInfo()


def detect_package_manager(root: Path) -> str:
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            pm_field = str(json.loads(pkg.read_text(encoding="utf-8")).get("packageManager", ""))
            if pm_field.startswith("pnpm"):
                return "pnpm"
            if pm_field.startswith("yarn"):
                return "yarn"
            if pm_field.startswith("bun"):
                return "bun"
            if pm_field.startswith("npm"):
                return "npm"
        except (OSError, json.JSONDecodeError):
            pass
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    return "npm"


def resolve_pm_cmd(root: Path) -> str:
    """Return a shell-invokable package manager (uses npx when not on PATH)."""
    pm = detect_package_manager(root)
    if pm == "pnpm":
        if shutil.which("pnpm"):
            return "pnpm"
        return "npx --yes pnpm"
    if pm == "yarn":
        if shutil.which("yarn"):
            return "yarn"
        return "npx --yes yarn"
    if pm == "bun":
        if shutil.which("bun"):
            return "bun"
        return "npx --yes bun"
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
    elif stack.runtime == "ruby":
        cmds = _from_rails(root) or _stack_defaults(stack)
    elif stack.runtime == "python":
        cmds = _from_python(root) or _stack_defaults(stack)
    elif stack.runtime == "go":
        cmds = CommandSet(install="go mod download", run="go run .", source="go.mod")
    elif stack.runtime == "cargo":
        cmds = CommandSet(install="cargo fetch", build="cargo build", run="cargo run", source="Cargo.toml")
    elif stack.runtime == "docker" and stack.framework == "rails-docker":
        compose = find_compose_file(root)
        if compose and docker_daemon_available():
            build, run = compose_run_command(root)
            cmds = CommandSet(install=build, run=run, source=compose.name)
        else:
            cmds = _from_rails(root) or CommandSet(source="Gemfile")
    elif stack.runtime == "static":
        cmds = _from_static_html(root) or CommandSet(source="unknown")
    else:
        cmds = _stack_defaults(stack)

    if stack.runtime in ("docker", "ruby"):
        merged = cmds
    elif stack.runtime == "node":
        merged = _merge(cmds, mk)
    else:
        merged = _merge(mk, cmds)
    result = merged or CommandSet(source="unknown")
    if result.run:
        result = CommandSet(
            install=result.install,
            build=result.build,
            run=_tailor_run_command(result.run, stack, root),
            source=result.source,
        )
    return result


def detect_env_keys(root: Path) -> list[str]:
    return list(build_env_defaults(root).keys())


def parse_env_example(root: Path) -> dict[str, str]:
    """Parse KEY=value defaults from .env.example / .env.template files."""
    values: dict[str, str] = {}
    for base in _collect_env_search_dirs(root):
        for name in _ENV_TEMPLATE_NAMES:
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
                raw = _strip_env_value(match.group(2))
                if raw and not raw.startswith("#"):
                    values[key] = raw
    return values


def is_web_project(root: Path, stack: StackInfo, cmds: CommandSet) -> bool:
    if stack.runtime == "static" or stack.framework == "static-html":
        return True
    run = cmds.run or ""
    if stack.framework in ("nextjs", "react", "vite", "express", "rails", "rails-docker"):
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


def _default_install(root: Path, stack: StackInfo, pm: str | None = None) -> str | None:
    if stack.runtime == "node":
        cmd = resolve_pm_cmd(root) if pm is None else pm
        return f"{cmd} install"
    if stack.runtime == "ruby":
        rails = _from_rails(root)
        return rails.install if rails else "bundle install"
    if stack.runtime == "python":
        if (root / "uv.lock").exists():
            return "uv sync"
        if (root / "requirements.txt").exists():
            return "pip install -r requirements.txt"
        return "pip install -e ."
    return None


def _makefile_run_target(text: str) -> str | None:
    """Pick a Makefile run target; skip overmind-based `run` when `server` exists."""
    run_block = re.search(r"^run\s*:(.*?)(?=^\S|\Z)", text, re.M | re.S)
    if run_block and "overmind" in run_block.group(1).lower():
        if re.search(r"^server\s*:", text, re.M):
            return "make server"
    for target in ("dev", "run", "start", "serve"):
        if re.search(rf"^{target}\s*:", text, re.M):
            return f"make {target}"
    if re.search(r"^server\s*:", text, re.M):
        return "make server"
    return None


def _from_makefile(root: Path) -> CommandSet | None:
    mk = root / "Makefile"
    if not mk.is_file():
        return None
    text = mk.read_text(encoding="utf-8", errors="replace")
    install = "make install" if re.search(r"^install\s*:", text, re.M) else None
    build = "make build" if re.search(r"^build\s*:", text, re.M) else None
    run = _makefile_run_target(text)
    if not any((install, build, run)):
        run = "make" if re.search(r"^all\s*:", text, re.M) else None
    if not any((install, build, run)):
        return None
    return CommandSet(install=install, build=build, run=run, source="Makefile")


def _tailor_run_command(run: str | None, stack: StackInfo, root: Path) -> str | None:
    """Add explicit host/port for dev servers (avoids clashing with Lowkally UI on :3000)."""
    if not run:
        return run
    fw = stack.framework or _framework_from_package_json(root)
    if fw == "nextjs" and " run dev" in f" {run}":
        if "-p " not in run and "-p$" not in run.replace(" ", ""):
            return f"{run} -- -p $PORT -H 127.0.0.1"
    if fw == "vite" and " run dev" in f" {run}":
        return run
    return run


def _from_package_json(root: Path) -> CommandSet | None:
    pkg_path = root / "package.json"
    if not pkg_path.is_file():
        return None
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pm = resolve_pm_cmd(root)
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
