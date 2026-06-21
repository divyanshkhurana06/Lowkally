"""Inject Next.js basePath so cloud preview works under /api/runs/{id}/preview."""

from __future__ import annotations

import json
import re
from pathlib import Path

from forge.auth import APP_URL

_PREVIEW_BASEPATH: dict[str, str] = {}


def preview_basepath(run_id: str) -> str | None:
    if run_id in _PREVIEW_BASEPATH:
        return _PREVIEW_BASEPATH[run_id]
    from forge import store as run_store

    for ev in reversed(run_store.list_events(run_id, limit=60)):
        if ev.get("kind") != "preview_basepath":
            continue
        payload = ev.get("payload") or {}
        bp = payload.get("basepath")
        if isinstance(bp, str) and bp.startswith("/"):
            register_preview_basepath(run_id, bp)
            return bp
    return None


def get_preview_basepath(run_id: str) -> str | None:
    cached = preview_basepath(run_id)
    if cached:
        return cached
    return read_basepath_from_workspace(run_id)


def read_basepath_from_workspace(run_id: str) -> str | None:
    """Read basePath from the bootstrapped repo (survives agent restarts)."""
    from forge.workspace import run_dir

    cwd = run_dir(run_id)
    if not cwd.is_dir():
        return None
    for name in ("next.config.ts", "next.config.mjs", "next.config.js"):
        path = cwd / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"""basePath\s*:\s*['"]([^'"]+)['"]""", text)
        if m:
            bp = m.group(1).strip()
            if bp.startswith("/api/runs/") and "/preview" in bp:
                register_preview_basepath(run_id, bp)
                return bp
    return None


def register_preview_basepath(run_id: str, basepath: str) -> None:
    _PREVIEW_BASEPATH[run_id] = basepath


def is_nextjs_project(cwd: Path) -> bool:
    pkg = cwd / "package.json"
    if not pkg.is_file():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    return "next" in deps


def inject_nextjs_preview_basepath(cwd: Path, run_id: str) -> str | None:
    """Patch next.config with basePath for Cloud Run preview subpath."""
    if not APP_URL.startswith("https://"):
        return None
    if not is_nextjs_project(cwd):
        return None

    base = f"/api/runs/{run_id}/preview"
    for name in ("next.config.ts", "next.config.mjs", "next.config.js"):
        path = cwd / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if f'basePath: "{base}"' in text or f"basePath: '{base}'" in text:
            register_preview_basepath(run_id, base)
            return base

        if re.search(r"\bbasePath\s*:", text):
            text = re.sub(
                r"basePath\s*:\s*['\"][^'\"]*['\"]",
                f'basePath: "{base}"',
                text,
                count=1,
            )
        elif re.search(r"(?:const|let)\s+\w+\s*=\s*\{", text):
            text = re.sub(
                r"((?:const|let)\s+\w+\s*=\s*\{)",
                rf'\1\n  basePath: "{base}",',
                text,
                count=1,
            )
        elif re.search(r"export\s+default\s*\{", text):
            text = re.sub(
                r"export\s+default\s*\{",
                f'export default {{\n  basePath: "{base}",',
                text,
                count=1,
            )
        elif re.search(r"module\.exports\s*=\s*\{", text):
            text = re.sub(
                r"module\.exports\s*=\s*\{",
                f'module.exports = {{\n  basePath: "{base}",',
                text,
                count=1,
            )
        else:
            continue

        path.write_text(text, encoding="utf-8")
        register_preview_basepath(run_id, base)
        return base

    return None
