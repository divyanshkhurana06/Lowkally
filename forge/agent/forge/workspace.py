"""Sandboxed workspace paths for cloned repositories."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

WORKSPACE_ROOT = Path(os.getenv("FORGE_WORKSPACE", Path(__file__).resolve().parents[2] / "workspace"))
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def run_dir(run_id: str) -> Path:
    if not _RUN_ID.match(run_id):
        raise ValueError("Invalid run id")
    path = (WORKSPACE_ROOT / run_id).resolve()
    root = WORKSPACE_ROOT.resolve()
    if root not in path.parents and path != root:
        raise ValueError("Path escapes workspace")
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_file(run_id: str, relative: str) -> Path:
    rel = relative.strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError("Invalid relative path")
    base = run_dir(run_id)
    target = (base / rel).resolve()
    if base not in target.parents and target != base:
        raise ValueError("Path escapes run directory")
    return target
