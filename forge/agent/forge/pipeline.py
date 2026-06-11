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
    detect_env_keys,
    detect_stack,
    discover_commands,
    is_web_project,
    normalize_repo_url,
    parse_env_example,
)
from .executor import clone_repo, run_in_workspace
from .healing import apply_rule, classify_errors, extract_app_url, find_free_port
from .workspace import run_dir

SERVER_PROBE_SECONDS = int(os.getenv("FORGE_SERVER_PROBE", "90"))
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
    return {"author": "forge", "partial": False, "parts": parts}


def _run_env(run_id: str) -> dict[str, str]:
    return _RUN_ENV.setdefault(run_id, {})


def _merge_env(run_id: str, updates: dict[str, str]) -> None:
    _run_env(run_id).update(updates)


def _cmd_with_env(run_id: str, command: str, cwd: Path) -> str:
    chunks = []
    for k, v in _run_env(run_id).items():
        chunks.append(f'export {k}="{v}"')
    if (cwd / ".env").exists():
        chunks.append("set -a && source .env && set +a")
    chunks.append(command)
    return " && ".join(chunks)


async def _run_step(run_id: str, cwd: Path, command: str, timeout: int | None = None) -> dict[str, Any]:
    wrapped = _cmd_with_env(run_id, command, cwd)
    result = await asyncio.to_thread(run_in_workspace, cwd, wrapped, timeout)
    store.log_event(run_id, "command", result)
    return result


async def _verify_url(url: str) -> bool:
    def check() -> bool:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status < 500
        except urllib.error.HTTPError as exc:
            return exc.code < 500
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
    deadline = time.monotonic() + SERVER_PROBE_SECONDS
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
            elif url and await _verify_url(url):
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


def _write_env_file(cwd: Path, values: dict[str, str]) -> None:
    target = cwd / ".env"
    lines = [f"{k}={v}" for k, v in values.items()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _write_env_from_approval(run_id: str, cwd: Path) -> dict[str, Any]:
    approval = store.get_latest_approved(run_id)
    if not approval:
        return {"error": "No approved env request"}
    values = approval.get("values") or {}
    _write_env_file(cwd, values)
    store.log_event(run_id, "env_written", {"keys": list(values.keys())})
    return {"written": True, "keys": list(values.keys())}


async def _auto_env_from_example(run_id: str, cwd: Path) -> dict[str, Any] | None:
    defaults = parse_env_example(cwd)
    if not defaults or (cwd / ".env").exists():
        return None
    _write_env_file(cwd, defaults)
    store.log_event(run_id, "env_auto", {"keys": list(defaults.keys()), "source": ".env.example"})
    return {"written": True, "keys": list(defaults.keys()), "auto": True}


def _mark_success(run_id: str, url: str, summary: str) -> None:
    store.update_run(
        run_id,
        status="running",
        success_url=url,
        error="",
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    store.log_event(run_id, "success", {"url": url, "summary": summary})


async def _heal_loop(
    run_id: str,
    cwd: Path,
    stack: StackInfo,
    run_cmd: str,
    *,
    web: bool,
) -> AsyncIterator[dict[str, Any]]:
    while not store.iteration_limit_reached(run_id):
        yield _agent_event(call=("run_command", {"command": run_cmd}))

        if web:
            result = await _probe_server(run_id, cwd, run_cmd)
        else:
            result = await _run_step(run_id, cwd, run_cmd, timeout=60)

        yield _agent_event(response=("run_command", result))

        url = result.get("app_url")
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

        if fix.next_step == "need_env":
            auto = await _auto_env_from_example(run_id, cwd)
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

        yield _agent_event(text=fix.description)
        if fix.env_updates:
            _merge_env(run_id, fix.env_updates)
            yield _agent_event(response=("apply_env", fix.env_updates))

        for cmd in fix.commands:
            yield _agent_event(call=("run_command", {"command": cmd}))
            step = await _run_step(run_id, cwd, cmd)
            yield _agent_event(response=("run_command", step))
            if not step.get("success"):
                break

    store.update_run(run_id, status="failed", error="Iteration limit reached")
    yield _agent_event(text="Healing iteration limit reached.")


async def stream_forge(
    run_id: str,
    repo_url: str,
    branch: str | None = None,
    start_command: str | None = None,
    *,
    resume: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    url = normalize_repo_url(repo_url)
    cwd = run_dir(run_id)

    if not resume:
        yield _agent_event(text=f"FORGE pipeline — {url}")
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
        auto = await _auto_env_from_example(run_id, cwd)
        if auto:
            yield _agent_event(text=f"Auto-filled .env from example: {', '.join(auto['keys'])}")
            yield _agent_event(response=("write_env_file", auto))
        elif detect_env_keys(cwd) and not (cwd / ".env").exists():
            defaults = parse_env_example(cwd)
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
        yield _agent_event(call=("run_command", {"command": cmds.install}))
        install = await _run_step(run_id, cwd, cmds.install, timeout=300)
        yield _agent_event(response=("run_command", install))
        if not install.get("success"):
            store.update_run(run_id, status="failed", error=(install.get("stderr") or "Install failed")[:500])
            yield _agent_event(text="Install failed.")
            return

    if cmds.build and web and not (cmds.run and " dev" in f" {cmds.run}"):
        yield _agent_event(call=("run_command", {"command": cmds.build}))
        build = await _run_step(run_id, cwd, cmds.build, timeout=300)
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

    async for ev in _heal_loop(run_id, cwd, stack, run_cmd, web=web):
        yield ev

    _RUN_ENV.pop(run_id, None)
