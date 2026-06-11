"""Users, saved sites, favorites, and issue reports."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .store import DB_PATH, _conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            username TEXT NOT NULL,
            avatar_url TEXT,
            oauth_token TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(provider, provider_id)
        );
        CREATE TABLE IF NOT EXISTS saved_sites (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            run_id TEXT,
            repo_url TEXT NOT NULL,
            title TEXT,
            success_url TEXT,
            summary TEXT,
            labels_json TEXT,
            is_favorite INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS issue_reports (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            contact TEXT,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            repo_url TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN user_id TEXT")
    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if user_cols and "oauth_token" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN oauth_token TEXT")


def _db() -> sqlite3.Connection:
    conn = _conn()
    _migrate(conn)
    return conn


def upsert_user(
    *,
    provider: str,
    provider_id: str,
    username: str,
    avatar_url: str | None,
    oauth_token: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with _db() as c:
        row = c.execute(
            "SELECT * FROM users WHERE provider = ? AND provider_id = ?",
            (provider, provider_id),
        ).fetchone()
        if row:
            if oauth_token:
                c.execute(
                    "UPDATE users SET username = ?, avatar_url = ?, oauth_token = ? WHERE id = ?",
                    (username, avatar_url, oauth_token, row["id"]),
                )
            else:
                c.execute(
                    "UPDATE users SET username = ?, avatar_url = ? WHERE id = ?",
                    (username, avatar_url, row["id"]),
                )
            return dict(c.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone())
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        c.execute(
            """
            INSERT INTO users (id, provider, provider_id, username, avatar_url, oauth_token, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, provider, provider_id, username, avatar_url, oauth_token, now),
        )
        return dict(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": user["id"],
        "provider": user["provider"],
        "username": user["username"],
        "avatar_url": user.get("avatar_url"),
    }


def get_user(user_id: str) -> dict[str, Any] | None:
    with _db() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def create_run_for_user(
    repo_url: str,
    branch: str | None,
    start_command: str | None,
    user_id: str | None,
) -> dict[str, Any]:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    with _db() as c:
        c.execute(
            """
            INSERT INTO runs (id, repo_url, branch, status, start_command, created_at, user_id)
            VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (run_id, repo_url, branch, start_command, now, user_id),
        )
    return {"run_id": run_id, "created_at": now}


def list_runs_for_user(user_id: str | None, limit: int = 30) -> list[dict[str, Any]]:
    with _db() as c:
        if user_id:
            rows = c.execute(
                """
                SELECT id, repo_url, branch, status, success_url, created_at, finished_at
                FROM runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        else:
            rows = []
    return [dict(r) for r in rows]


def user_owns_run(run_id: str, user_id: str | None) -> bool:
    if not user_id:
        return False
    with _db() as c:
        row = c.execute("SELECT user_id FROM runs WHERE id = ?", (run_id,)).fetchone()
    return bool(row and row["user_id"] == user_id)


def save_site(
    user_id: str,
    *,
    repo_url: str,
    run_id: str | None = None,
    title: str | None = None,
    success_url: str | None = None,
    summary: str | None = None,
    labels: list[str] | None = None,
    favorite: bool = False,
) -> dict[str, Any]:
    site_id = f"site_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    with _db() as c:
        existing = c.execute(
            "SELECT id FROM saved_sites WHERE user_id = ? AND repo_url = ?",
            (user_id, repo_url),
        ).fetchone()
        if existing:
            site_id = existing["id"]
            c.execute(
                """
                UPDATE saved_sites
                SET run_id = COALESCE(?, run_id),
                    title = COALESCE(?, title),
                    success_url = COALESCE(?, success_url),
                    summary = COALESCE(?, summary),
                    labels_json = COALESCE(?, labels_json),
                    is_favorite = CASE WHEN ? = 1 THEN 1 ELSE is_favorite END
                WHERE id = ?
                """,
                (
                    run_id,
                    title,
                    success_url,
                    summary,
                    json.dumps(labels) if labels else None,
                    1 if favorite else 0,
                    site_id,
                ),
            )
        else:
            c.execute(
                """
                INSERT INTO saved_sites
                (id, user_id, run_id, repo_url, title, success_url, summary, labels_json, is_favorite, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    site_id,
                    user_id,
                    run_id,
                    repo_url,
                    title or repo_url.split("/")[-1].removesuffix(".git"),
                    success_url,
                    summary,
                    json.dumps(labels or []),
                    1 if favorite else 0,
                    now,
                ),
            )
        row = c.execute("SELECT * FROM saved_sites WHERE id = ?", (site_id,)).fetchone()
    return _site_dict(row)


def _site_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["labels"] = json.loads(d.pop("labels_json") or "[]")
    d["is_favorite"] = bool(d.get("is_favorite"))
    return d


def list_saved_sites(user_id: str, favorites_only: bool = False) -> list[dict[str, Any]]:
    with _db() as c:
        if favorites_only:
            rows = c.execute(
                "SELECT * FROM saved_sites WHERE user_id = ? AND is_favorite = 1 ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM saved_sites WHERE user_id = ? ORDER BY is_favorite DESC, created_at DESC",
                (user_id,),
            ).fetchall()
    return [_site_dict(r) for r in rows]


def toggle_favorite(user_id: str, site_id: str) -> dict[str, Any]:
    with _db() as c:
        row = c.execute(
            "SELECT * FROM saved_sites WHERE id = ? AND user_id = ?",
            (site_id, user_id),
        ).fetchone()
        if not row:
            return {"error": "Not found"}
        new_val = 0 if row["is_favorite"] else 1
        c.execute("UPDATE saved_sites SET is_favorite = ? WHERE id = ?", (new_val, site_id))
        updated = c.execute("SELECT * FROM saved_sites WHERE id = ?", (site_id,)).fetchone()
    return _site_dict(updated)


def delete_saved_site(user_id: str, site_id: str) -> dict[str, Any]:
    with _db() as c:
        cur = c.execute("DELETE FROM saved_sites WHERE id = ? AND user_id = ?", (site_id, user_id))
        if cur.rowcount == 0:
            return {"error": "Not found"}
    return {"deleted": True}


def create_issue_report(
    *,
    subject: str,
    body: str,
    contact: str | None = None,
    repo_url: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    issue_id = f"issue_{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    with _db() as c:
        c.execute(
            """
            INSERT INTO issue_reports (id, user_id, contact, subject, body, repo_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (issue_id, user_id, contact, subject, body, repo_url, now),
        )
    return {"id": issue_id, "created_at": now}
