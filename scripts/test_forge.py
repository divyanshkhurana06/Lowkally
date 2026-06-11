#!/usr/bin/env python3
"""End-to-end FORGE test — exits 0 only when portfolio run reaches running + HTTP OK."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8080"
REPO = "https://github.com/divyanshkhurana06/portfolio"


def http_json(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = resp.read().decode()
        if raw.startswith("{"):
            return json.loads(raw)
        return {}


def parse_sse_stream(resp):
    run_id = None
    session_id = None
    final_status = None
    success_url = None
    for raw in resp:
        line = raw.decode(errors="replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            obj = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("run_id"):
            run_id = obj["run_id"]
            session_id = obj.get("session_id")
        if isinstance(obj, dict) and obj.get("run"):
            final_status = obj["run"].get("status")
            success_url = obj["run"].get("success_url")
        if isinstance(obj, dict) and obj.get("parts"):
            for p in obj["parts"]:
                if p.get("type") == "text":
                    print(" ", p["text"][:120])
                if p.get("type") == "response" and p.get("name") == "run_command":
                    b = p.get("body") or {}
                    if b.get("app_url"):
                        print("  URL:", b["app_url"])
    return run_id, session_id, final_status, success_url


def forge_run():
    body = json.dumps({"repo_url": REPO, "branch": None}).encode()
    req = urllib.request.Request(
        f"{API}/api/forge/stream",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print(f"Forge run: {REPO}")
    with urllib.request.urlopen(req, timeout=600) as resp:
        return parse_sse_stream(resp)


def continue_run(run_id: str, session_id: str) -> tuple[str | None, str | None]:
    body = json.dumps({"session_id": session_id, "user_id": "operator"}).encode()
    req = urllib.request.Request(
        f"{API}/api/runs/{run_id}/continue/stream",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print(f"Continue run: {run_id}")
    with urllib.request.urlopen(req, timeout=600) as resp:
        _, _, status, url = parse_sse_stream(resp)
        return status, url


def verify_http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except Exception:
        return False


def main() -> int:
    try:
        health = http_json("GET", "/health")
    except Exception as exc:
        print(f"Agent not running on {API}: {exc}", file=sys.stderr)
        return 1
    if health.get("status") != "ok":
        print("Agent unhealthy", file=sys.stderr)
        return 1

    run_id, session_id, status, url = forge_run()

    if status == "awaiting_env" and run_id and session_id:
        approvals = http_json("GET", "/api/approvals")
        pending = [a for a in approvals.get("approvals", []) if a.get("run_id") == run_id]
        if pending:
            aid = pending[0]["id"]
            values = {k: "file:./data/port.db" if k == "DATABASE_URL" else "forge-test-secret" for k in pending[0]["keys"]}
            http_json("POST", f"/api/approvals/{aid}/approve", {"values": values})
            status, url = continue_run(run_id, session_id)

    if not run_id:
        print("No run_id returned", file=sys.stderr)
        return 1

    detail = http_json("GET", f"/api/runs/{run_id}")
    run = detail.get("run", {})
    status = run.get("status") or status
    url = run.get("success_url") or url

    print(f"Result: status={status} url={url}")

    if status != "running" or not url or not url.startswith("http"):
        print(f"FAILED: {run.get('error', '')[:300]}", file=sys.stderr)
        return 1

    for _ in range(5):
        if verify_http(url):
            print(f"PASS — {url} responds")
            return 0
        time.sleep(2)

    print(f"FAILED — {url} not responding", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
