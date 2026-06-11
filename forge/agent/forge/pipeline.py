"""Deterministic forge pipeline — RepoFix-style clone → detect → install → run → heal."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import store
from .detection import (
    StackInfo,
    adjust_stack_for_host,
    build_env_defaults,
    compose_http_url,
    detect_env_keys,
    detect_stack,
    discover_commands,
    env_export_statements,
    env_file_path,
    format_env_line,
    is_dev_run_command,
    is_docker_command,
    is_web_project,
    normalize_repo_url,
    parse_env_example,
    parse_env_file,
    resolve_env_dir,
)
from .users_store import get_user
from .executor import clone_repo, run_in_workspace
from .healing import apply_rule, classify_errors, extract_app_url, find_free_port
from .workspace import run_dir

SERVER_PROBE_SECONDS = int(os.getenv("FORGE_SERVER_PROBE", "180"))
_RUN_ENV: dict[str, dict[str, str]] = {}
_RUN_PROCS: dict[str, subprocess.Popen[str]] = {}


def _agent_event(
    text: str | None = None,
    call: tuple[str, dict] | None = None,
    response: tuple[str, Any] | None = None,
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    if call:
        parts.append({"type": "call", "name": call[0], "args": call[1]})
    if response:
        parts.append({"type": "response", "name": response[0], "body": response[1]})
    return {"author": "lowkally", "partial": False, "parts": parts}


def _run_env(run_id: str) -> dict[str, str]:
    return _RUN_ENV.setdefault(run_id, {})


def _merge_env(run_id: str, updates: dict[str, str]) -> None:
    _run_env(run_id).update(updates)


def _cmd_with_env(run_id: str, command: str, cwd: Path) -> str:
    chunks: list[str] = []
    docker_cmd = is_docker_command(command)
    if not docker_cmd:
        env_path = env_file_path(cwd)
        if not env_path.is_file() and (cwd / ".env").is_file():
            env_path = cwd / ".env"
        if env_path.is_file():
            chunks.extend(env_export_statements(parse_env_file(env_path)))
    for k, v in _run_env(run_id).items():
        if docker_cmd and k not in ("PORT", "HOSTNAME"):
            continue
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        chunks.append(f'export {k}="{escaped}"')
    chunks.append(command)
    return " && ".join(chunks)


def _step_timeout(command: str | None) -> int:
    if command and "docker compose build" in command:
        return int(os.getenv("FORGE_DOCKER_BUILD_TIMEOUT", "1200"))
    if command and is_docker_command(command):
        return int(os.getenv("FORGE_DOCKER_TIMEOUT", "600"))
    return int(os.getenv("FORGE_CMD_TIMEOUT", "120"))


async def _run_step(run_id: str, cwd: Path, command: str, timeout: int | None = None) -> dict[str, Any]:
    wrapped = _cmd_with_env(run_id, command, cwd)
    result = await asyncio.to_thread(
        run_in_workspace,
        cwd,
        wrapped,
        timeout if timeout is not None else _step_timeout(command),
    )
    store.log_event(run_id, "command", result)
    return result


async def _verify_url(url: str) -> bool:
    def check() -> bool:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status >= 500:
                    return False
                body = resp.read(8000).decode("utf-8", errors="replace")
                if "Invalid environment variables" in body:
                    return False
                return True
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                return False
            try:
                body = exc.read(8000).decode("utf-8", errors="replace")
                if "Invalid environment variables" in body:
                    return False
            except Exception:
                pass
            return True
        except Exception:
            return False

    return await asyncio.to_thread(check)


async def _probe_server(run_id: str, cwd: Path, command: str) -> dict[str, Any]:
    wrapped = _cmd_with_env(run_id, command, cwd)
    env = os.environ.copy()
    env.update(_run_env(run_id))

    proc = subprocess.Popen(
        wrapped,
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    _RUN_PROCS[run_id] = proc
    output_lines: list[str] = []
    url: str | None = None
    port = _run_env(run_id).get("PORT")
    fallback_url = f"http://127.0.0.1:{port}" if port else None
    probe_seconds = SERVER_PROBE_SECONDS
    if is_dev_run_command(command):
        probe_seconds = max(probe_seconds, int(os.getenv("FORGE_DEV_PROBE", "300")))
    deadline = time.monotonic() + probe_seconds
    try:
        while time.monotonic() < deadline:
            if proc.stdout is None:
                break
            line = await asyncio.to_thread(proc.stdout.readline)
            if line:
                output_lines.append(line)
                url = extract_app_url("".join(output_lines)) or url
                if url and await _verify_url(url):
                    break
            elif proc.poll() is not None:
                break
            else:
                if not url and fallback_url and await _verify_url(fallback_url):
                    url = fallback_url
                    break
                if url and await _verify_url(url):
                    break
            await asyncio.sleep(0.2)
    except Exception:
        pass

    output = "".join(output_lines)[-8000:]
    exit_code = proc.poll()
    if url and await _verify_url(url):
        result = {
            "command": command,
            "exit_code": exit_code if exit_code is not None else 0,
            "stdout": output,
            "stderr": "",
            "success": True,
            "app_url": url,
        }
    else:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        _RUN_PROCS.pop(run_id, None)
        result = {
            "command": command,
            "exit_code": exit_code if exit_code is not None else 1,
            "stdout": output,
            "stderr": output,
            "success": False,
            "app_url": None,
        }
    store.log_event(run_id, "run_probe", result)
    return result


def _write_env_file(cwd: Path, values: dict[str, str]) -> Path:
    target = env_file_path(cwd)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [format_env_line(k, v) for k, v in values.items()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


async def _write_env_from_approval(run_id: str, cwd: Path) -> dict[str, Any]:
    approval = store.get_latest_approved(run_id)
    if not approval:
        return {"error": "No approved env request"}
    values = approval.get("values") or {}
    target = _write_env_file(cwd, values)
    store.log_event(run_id, "env_written", {"keys": list(values.keys()), "path": str(target.relative_to(cwd))})
    return {"written": True, "keys": list(values.keys()), "path": str(target.relative_to(cwd))}


def _bootstrap_user(user_id: str | None) -> dict | None:
    if not user_id or user_id == "operator":
        return None
    return get_user(user_id)


async def _auto_env_from_example(run_id: str, cwd: Path, *, user_id: str | None = None) -> dict[str, Any] | None:
    target = env_file_path(cwd)
    if target.is_file():
        return None
    user = _bootstrap_user(user_id)
    defaults = build_env_defaults(cwd, user=user)
    if not defaults:
        return None
    written = _write_env_file(cwd, defaults)
    rel = written.relative_to(cwd)
    store.log_event(run_id, "env_auto", {"keys": list(defaults.keys()), "path": str(rel)})
    return {"written": True, "keys": list(defaults.keys()), "auto": True, "path": str(rel)}


def _mark_success(run_id: str, url: str, summary: str) -> None:
    store.update_run(
        run_id,
        status="running",
        success_url=url,
        error="",
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    store.log_event(run_id, "success", {"url": url, "summary": summary})


async def _apply_fix(
    run_id: str,
    cwd: Path,
    fix: Any,
) -> AsyncIterator[dict[str, Any]]:
    yield _agent_event(text=fix.description)
    if fix.env_updates:
        _merge_env(run_id, fix.env_updates)
        yield _agent_event(response=("apply_env", fix.env_updates))
    for cmd in fix.commands:
        yield _agent_event(call=("run_command", {"command": cmd}))
        step = await _run_step(run_id, cwd, cmd)
        yield _agent_event(response=("run_command", step))
        if not step.get("success"):
            return


async def _heal_loop(
    run_id: str,
    cwd: Path,
    stack: StackInfo,
    run_cmd: str,
    *,
    web: bool,
    user_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    while not store.iteration_limit_reached(run_id):
        yield _agent_event(call=("run_command", {"command": run_cmd}))

        if web:
            result = await _probe_server(run_id, cwd, run_cmd)
        else:
            result = await _run_step(run_id, cwd, run_cmd, timeout=60)

        yield _agent_event(response=("run_command", result))

        url = result.get("app_url")
        if not url and result.get("success") and is_docker_command(run_cmd):
            url = compose_http_url(cwd)
        if url and await _verify_url(url):
            _mark_success(run_id, url, "Web app responding")
            yield _agent_event(text=f"App running at {url}")
            return

        if not web and result.get("success"):
            _mark_success(run_id, "cli://verified", f"Command succeeded: {run_cmd}")
            yield _agent_event(text=f"Install verified — `{run_cmd}` succeeded.")
            return

        errors = classify_errors(result.get("stderr", ""), result.get("stdout", ""), stack.runtime)
        if not errors:
            msg = (result.get("stderr") or result.get("stdout") or "Run failed").strip()[-500:]
            store.update_run(run_id, status="failed", error=msg or "Run failed")
            yield _agent_event(text=f"Run failed: {msg[:200]}")
            return

        err = errors[0]
        fix = apply_rule(err, stack, cwd)
        store.increment_iteration(run_id)
        store.update_run(run_id, status="healing")

        if not fix:
            store.update_run(run_id, status="failed", error=err.description)
            yield _agent_event(text=f"No rule fix for: {err.description}")
            return

        if fix.fatal_message:
            store.update_run(run_id, status="failed", error=fix.fatal_message)
            yield _agent_event(text=fix.fatal_message)
            return

        if fix.stack_override:
            stack = fix.stack_override

        if fix.next_step == "need_env":
            auto = await _auto_env_from_example(run_id, cwd, user_id=user_id)
            if auto:
                yield _agent_event(text="Auto-filled .env from .env.example")
                yield _agent_event(response=("write_env_file", auto))
                continue
            keys = detect_env_keys(cwd)
            var = err.extracted.get("var_name")
            if var and var not in keys:
                keys.append(str(var))
            if not keys:
                keys = [str(var or "SECRET")]
            approval = store.create_approval(run_id, keys)
            store.update_run(run_id, status="awaiting_env")
            yield _agent_event(text=f"Need environment variables: {', '.join(keys)}")
            yield _agent_event(response=("request_env_write", approval))
            return

        async for ev in _apply_fix(run_id, cwd, fix):
            yield ev

        if fix.run_command:
            run_cmd = fix.run_command
            continue

    store.update_run(run_id, status="failed", error="Iteration limit reached")
    yield _agent_event(text="Healing iteration limit reached.")


async def stream_forge(
    run_id: str,
    repo_url: str,
    branch: str | None = None,
    start_command: str | None = None,
    *,
    resume: bool = False,
    user_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    url = normalize_repo_url(repo_url)
    cwd = run_dir(run_id)

    if not resume:
        yield _agent_event(text=f"Lowkally pipeline — {url}")
        yield _agent_event(call=("clone_repository", {"repo_url": url, "branch": branch or ""}))
        clone = await asyncio.to_thread(clone_repo, url, cwd, branch or None)
        store.log_event(run_id, "clone", clone)
        yield _agent_event(response=("clone_repository", clone))
        if not clone.get("success"):
            store.update_run(run_id, status="failed", error=clone.get("stderr", "clone failed"))
            return
        store.update_run(run_id, workspace_path=str(cwd), status="cloned")
    elif not cwd.exists():
        store.update_run(run_id, status="failed", error="Workspace missing — restart forge run")
        yield _agent_event(text="Workspace missing.")
        return

    yield _agent_event(text="Detecting stack and commands…")
    stack = detect_stack(cwd)
    stack, host_note = adjust_stack_for_host(cwd, stack)
    if host_note:
        yield _agent_event(text=host_note)
    cmds = discover_commands(cwd, stack, start_command)
    web = is_web_project(cwd, stack, cmds)
    yield _agent_event(
        response=(
            "detect_stack",
            {
                "runtime": stack.runtime,
                "framework": stack.framework,
                "install": cmds.install,
                "build": cmds.build,
                "run": cmds.run,
                "source": cmds.source,
                "web": web,
            },
        )
    )

    if resume:
        write = await _write_env_from_approval(run_id, cwd)
        yield _agent_event(response=("write_env_file", write))
        if write.get("error"):
            store.update_run(run_id, status="failed", error=write["error"])
            return

    if not resume:
        auto = await _auto_env_from_example(run_id, cwd, user_id=user_id)
        if auto:
            yield _agent_event(text=f"Auto-filled .env from example: {', '.join(auto['keys'])}")
            yield _agent_event(response=("write_env_file", auto))
        elif detect_env_keys(cwd) and not env_file_path(cwd).is_file():
            defaults = build_env_defaults(cwd, user=_bootstrap_user(user_id))
            if defaults:
                approval = store.create_approval(run_id, list(defaults.keys()))
                store.update_run(run_id, status="awaiting_env")
                yield _agent_event(
                    text=f".env.example requires: {', '.join(defaults.keys())} — waiting for approval"
                )
                yield _agent_event(response=("request_env_write", approval))
                return

    store.update_run(run_id, status="active")
    port = find_free_port()
    _merge_env(run_id, {"PORT": str(port), "HOSTNAME": "127.0.0.1"})

    if not cmds.run and not cmds.install:
        store.update_run(
            run_id,
            status="completed",
            success_url="",
            error="",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        yield _agent_event(text="Repository cloned — no install/run commands found (static or docs-only repo).")
        return

    if cmds.install:
        install_cmd = cmds.install
        install_ok = False
        yield _agent_event(text=f"Installing dependencies ({install_cmd})…")
        while not store.iteration_limit_reached(run_id):
            yield _agent_event(call=("run_command", {"command": install_cmd}))
            install = await _run_step(run_id, cwd, install_cmd)
            yield _agent_event(response=("run_command", install))
            if install.get("success"):
                install_ok = True
                break
            err = (install.get("stderr") or install.get("stdout") or "Install failed").strip()
            if "pnpm: command not found" in err and "npx" not in (install_cmd or ""):
                retry = (install_cmd or "").replace("pnpm", "npx --yes pnpm")
                if retry != install_cmd:
                    yield _agent_event(text="pnpm not on PATH — retrying with npx pnpm")
                    install_cmd = retry
                    continue
            errors = classify_errors(install.get("stderr", ""), install.get("stdout", ""), stack.runtime)
            if not errors:
                break
            fix = apply_rule(errors[0], stack, cwd)
            store.increment_iteration(run_id)
            store.update_run(run_id, status="healing")
            if not fix:
                break
            if fix.fatal_message:
                store.update_run(run_id, status="failed", error=fix.fatal_message)
                yield _agent_event(text=fix.fatal_message)
                return
            if fix.stack_override:
                stack = fix.stack_override
                cmds = discover_commands(cwd, stack, start_command)
            async for ev in _apply_fix(run_id, cwd, fix):
                yield ev
            if fix.commands:
                install_ok = True
                break
            if cmds.install and cmds.install != install_cmd:
                install_cmd = cmds.install
                continue
            break
        if not install_ok:
            err = (install.get("stderr") or install.get("stdout") or "Install failed").strip()
            store.update_run(run_id, status="failed", error=err[:500])
            yield _agent_event(text="Install failed.")
            return

    if (cwd / "prisma" / "schema.prisma").is_file():
        yield _agent_event(text="Prisma detected — applying schema to local database…")
        yield _agent_event(call=("run_command", {"command": "npx prisma db push --skip-generate"}))
        prisma = await _run_step(run_id, cwd, "npx prisma db push --skip-generate", timeout=120)
        yield _agent_event(response=("run_command", prisma))

    if cmds.build and web and not is_dev_run_command(cmds.run):
        build_timeout = int(os.getenv("FORGE_BUILD_TIMEOUT", "600"))
        yield _agent_event(text=f"Building app ({cmds.build}) — may take 1–3 min…")
        yield _agent_event(call=("run_command", {"command": cmds.build}))
        build = await _run_step(run_id, cwd, cmds.build, timeout=build_timeout)
        yield _agent_event(response=("run_command", build))
        if not build.get("success"):
            store.update_run(run_id, status="failed", error=(build.get("stderr") or "Build failed")[:500])
            yield _agent_event(text="Build failed.")
            return

    run_cmd = cmds.run
    if not run_cmd:
        if cmds.install:
            _mark_success(run_id, "install://ok", "Dependencies installed")
            yield _agent_event(text="Dependencies installed successfully.")
            return
        store.update_run(run_id, status="failed", error="No run command discovered")
        yield _agent_event(text="Could not discover a run command.")
        return

    yield _agent_event(text=f"Starting app ({run_cmd})…")
    async for ev in _heal_loop(run_id, cwd, stack, run_cmd, web=web, user_id=user_id):
        yield ev

    _RUN_ENV.pop(run_id, None)
