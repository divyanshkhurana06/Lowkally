"""Proxy preview URLs so cloud users don't open their own localhost."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from forge.auth import APP_URL
from forge.next_preview import get_preview_basepath

_LOCALHOST_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1)(?::(\d+))?", re.I)

# Populated by pipeline during runs
_RUN_ENV: dict[str, dict[str, str]] = {}
_PREVIEW_PORTS: dict[str, int] = {}


def bind_run_env(store: dict[str, dict[str, str]]) -> None:
    """Share pipeline run env map for preview upstream resolution."""
    global _RUN_ENV
    _RUN_ENV = store


def _port_from_url(url: str) -> int | None:
    m = _LOCALHOST_RE.search(url or "")
    if m and m.group(1):
        return int(m.group(1))
    return None


def register_preview_port(run_id: str, url: str) -> None:
    """Remember which port the app actually bound (survives after run stream ends)."""
    port = _port_from_url(url)
    if port:
        _PREVIEW_PORTS[run_id] = port
        _RUN_ENV.setdefault(run_id, {})["PREVIEW_PORT"] = str(port)


def get_preview_upstream(run_id: str) -> str | None:
    port = _PREVIEW_PORTS.get(run_id)
    if not port:
        env = _RUN_ENV.get(run_id, {})
        raw = env.get("PREVIEW_PORT") or env.get("PORT")
        if raw and str(raw).isdigit():
            port = int(raw)
    if not port:
        from forge import store as run_store

        for ev in reversed(run_store.list_events(run_id, limit=40)):
            if ev.get("kind") != "success":
                continue
            payload = ev.get("payload") or {}
            for key in ("internal_url", "url"):
                found = _port_from_url(str(payload.get(key) or ""))
                if found:
                    port = found
                    _PREVIEW_PORTS[run_id] = port
                    break
            if port:
                break
    if port:
        return f"http://127.0.0.1:{port}"
    return None


def is_localhost_url(url: str) -> bool:
    return bool(_LOCALHOST_RE.match(url or ""))


def public_preview_url(run_id: str) -> str:
    base = APP_URL.rstrip("/")
    return f"{base}/api/runs/{run_id}/preview/"


def resolve_success_url(run_id: str, internal_url: str) -> str:
    """Store the real app URL; the UI picks proxy vs direct localhost."""
    return internal_url


def rewrite_location_header(value: str, run_id: str) -> str:
    """Rewrite redirects from app localhost to the preview proxy path."""
    prefix = f"/api/runs/{run_id}/preview"
    parsed = urlparse(value)
    if parsed.scheme and is_localhost_url(value):
        path = parsed.path or "/"
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{prefix}{path}{query}"
    if value.startswith("/"):
        if value == prefix or value.startswith(f"{prefix}/"):
            return value
        return f"{prefix}{value}"
    return value


HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

# Strip headers that block iframe embeds or break proxied asset loading.
STRIP_RESPONSE_HEADERS = frozenset(
    {
        "content-security-policy",
        "content-security-policy-report-only",
        "x-frame-options",
        "cross-origin-opener-policy",
        "cross-origin-embedder-policy",
        "cross-origin-resource-policy",
        "permissions-policy",
    }
)


def proxy_response_headers(headers: Any, run_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in HOP_BY_HOP or lower in STRIP_RESPONSE_HEADERS:
            continue
        if lower == "location":
            out[key] = rewrite_location_header(value, run_id)
        else:
            out[key] = value
    return out


def build_upstream_url(run_id: str, path: str, query: str) -> str | None:
    base = get_preview_upstream(run_id)
    if not base:
        return None
    bp = get_preview_basepath(run_id)
    if not bp and APP_URL.startswith("https://"):
        from forge.next_preview import is_nextjs_project
        from forge.workspace import run_dir

        root = run_dir(run_id)
        if root.is_dir() and is_nextjs_project(root):
            bp = preview_path_prefix(run_id)
    if bp:
        upstream_path = f"{bp}/{path.lstrip('/')}" if path else f"{bp}/"
        target = f"{base.rstrip('/')}{upstream_path}"
    else:
        target = urljoin(f"{base.rstrip('/')}/", path or "")
    if query:
        target = f"{target}?{query}"
    return target


def preview_path_prefix(run_id: str) -> str:
    return f"/api/runs/{run_id}/preview"


_REWRITE_TYPES = frozenset(
    {
        "text/html",
        "text/css",
        "application/javascript",
        "text/javascript",
        "application/json",
        "application/x-javascript",
    }
)

# Root paths served by bootstrapped apps (not Lowkally's /api/runs/... proxy paths)
_APP_ROOTS = (
    "/_next/",
    "/assets/",
    "/static/",
    "/images/",
    "/fonts/",
    "/favicon",
    "/icon",
)

# Bootstrapped app API routes — must not rewrite Lowkally's own /api/runs/ prefix.
_APP_API_ROOT = re.compile(r'(["\'\(])\/api\/(?!runs\/)')


def _rewrite_asset_paths(text: str, prefix: str) -> str:
    """Rewrite root-absolute asset URLs (HTML, JS bundles, CSS)."""
    pairs = (
        ('"/_next/', f'"{prefix}/_next/'),
        ("'/_next/", f"'{prefix}/_next/"),
        ('\\"/_next/', f'\\"{prefix}/_next/'),
        ('(/_next/', f'({prefix}/_next/'),
        ('url(/_next/', f'url({prefix}/_next/'),
        ('url("/_next/', f'url("{prefix}/_next/'),
        ("url('/_next/", f"url('{prefix}/_next/"),
        ('"/static/', f'"{prefix}/static/'),
        ("'/static/", f"'{prefix}/static/"),
        ('"/assets/', f'"{prefix}/assets/'),
        ("'/assets/", f"'{prefix}/assets/"),
        ('"/images/', f'"{prefix}/images/'),
        ("'/images/", f"'{prefix}/images/"),
        ('"/fonts/', f'"{prefix}/fonts/'),
        ("'/fonts/", f"'{prefix}/fonts/"),
    )
    for old, new in pairs:
        if old in text:
            text = text.replace(old, new)

    for root in _APP_ROOTS:
        if root in ("/_next/", "/static/", "/assets/", "/images/", "/fonts/"):
            continue
        for quote in ('"', "'", "("):
            src = f"{quote}{root}"
            dst = f"{quote}{prefix}{root}"
            if src in text:
                text = text.replace(src, dst)

    text = _APP_API_ROOT.sub(rf"\1{prefix}/api/", text)
    return text


def rewrite_preview_body(body: bytes, content_type: str, run_id: str) -> bytes:
    """Rewrite root-absolute asset URLs so they load through the preview proxy."""
    if get_preview_basepath(run_id):
        return body
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct not in _REWRITE_TYPES:
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body

    prefix = preview_path_prefix(run_id)
    text = _rewrite_asset_paths(text, prefix)

    if ct == "text/html" and "<head" in text.lower() and "<base " not in text.lower():
        text = re.sub(
            r"(<head[^>]*>)",
            rf'\1<base href="{prefix}/">',
            text,
            count=1,
            flags=re.IGNORECASE,
        )

    return text.encode("utf-8")
