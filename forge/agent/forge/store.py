"""Run history, approvals, and iteration limits."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(os.getenv("FORGE_DATA_DIR", Path(__file__).resolve().parents[2] / "data")) / "forge.db"
MAX_ITERATIONS = int(__import__("os").getenv("FORGE_MAX_ITERATIONS", "10"))


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            repo_url TEXT NOT NULL,
            branch TEXT,
            status TEXT NOT NULL,
            workspace_path TEXT,
            iteration INTEGER DEFAULT 0,
            start_command TEXT,
            success_url TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            keys_json TEXT NOT NULL,
            status TEXT NOT NULL,
            values_json TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        """
    )
    return conn


def create_run(repo_url: str, branch: str | None, start_command: str | None) -> dict[str, Any]:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO runs (id, repo_url, branch, status, start_command, created_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (run_id, repo_url, branch, start_command, now),
        )
    return {"run_id": run_id, "created_at": now}


def update_run(run_id: str, **fields: Any) -> None:
    allowed = {"status", "workspace_path", "iteration", "success_url", "error", "finished_at"}
    sets = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    vals.append(run_id)
    with _conn() as c:
        c.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = ?", vals)


def get_run(run_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_runs(limit: int = 30) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, repo_url, branch, status, success_url, created_at, finished_at FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def log_event(run_id: str, kind: str, payload: dict[str, Any]) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO events (run_id, ts, kind, payload) VALUES (?, ?, ?, ?)",
            (run_id, datetime.now(timezone.utc).isoformat(), kind, json.dumps(payload)),
        )


def list_events(run_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, kind, payload FROM events WHERE run_id = ? ORDER BY id DESC LIMIT ?",
            (run_id, limit),
        ).fetchall()
    out = []
    for r in reversed(rows):
        item = dict(r)
        item["payload"] = json.loads(item["payload"])
        out.append(item)
    return out


def increment_iteration(run_id: str) -> int:
    run = get_run(run_id)
    if not run:
        return 0
    n = int(run.get("iteration") or 0) + 1
    update_run(run_id, iteration=n)
    return n


def iteration_limit_reached(run_id: str) -> bool:
    run = get_run(run_id)
    return bool(run and int(run.get("iteration") or 0) >= MAX_ITERATIONS)


def create_approval(run_id: str, keys: list[str]) -> dict[str, Any]:
    aid = f"appr_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO approvals (id, run_id, keys_json, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (aid, run_id, json.dumps(keys), now),
        )
    return {"approval_id": aid, "run_id": run_id, "keys": keys, "status": "pending"}


def resolve_approval(approval_id: str, values: dict[str, str]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            "UPDATE approvals SET status = 'approved', values_json = ?, resolved_at = ? WHERE id = ? AND status = 'pending'",
            (json.dumps(values), now, approval_id),
        )
        if cur.rowcount == 0:
            return {"error": "Approval not found or already resolved"}
        row = c.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    return dict(row) if row else {"error": "not found"}


def get_approval(approval_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["keys"] = json.loads(d.pop("keys_json"))
    if d.get("values_json"):
        d["values"] = json.loads(d["values_json"])
    return d


def get_latest_approved(run_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND status = 'approved' ORDER BY resolved_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["keys"] = json.loads(d.pop("keys_json"))
    if d.get("values_json"):
        d["values"] = json.loads(d["values_json"])
    return d


def list_pending_approvals(run_id: str | None = None) -> list[dict[str, Any]]:
    with _conn() as c:
        if run_id:
            rows = c.execute(
                "SELECT * FROM approvals WHERE status = 'pending' AND run_id = ?",
                (run_id,),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM approvals WHERE status = 'pending'").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["keys"] = json.loads(d.pop("keys_json"))
        out.append(d)
    return out
