"""OAuth login (Google, GitHub, GitLab) and session JWT cookies."""

from __future__ import annotations

import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, Request, Response

from .users_store import get_user, upsert_user

JWT_SECRET = os.getenv("JWT_SECRET", "lowkally-dev-secret-change-in-production")
JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", str(60 * 60 * 24 * 14)))
def _normalize_app_url(raw: str) -> str:
    u = raw.strip().rstrip("/")
    if u and not u.startswith(("http://", "https://")):
        return f"https://{u}"
    return u or "http://localhost:3000"


APP_URL = _normalize_app_url(os.getenv("APP_URL", "http://localhost:3000"))
COOKIE_NAME = "lowkally_session"

GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITLAB_CLIENT_ID = os.getenv("GITLAB_CLIENT_ID", "")
GITLAB_CLIENT_SECRET = os.getenv("GITLAB_CLIENT_SECRET", "")
GITLAB_OAUTH_URL = os.getenv("GITLAB_OAUTH_URL", "https://gitlab.com")


def oauth_configured() -> dict[str, bool]:
    return {
        "google": bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET),
        "github": bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET),
        "gitlab": bool(GITLAB_CLIENT_ID and GITLAB_CLIENT_SECRET),
    }


def any_oauth_configured() -> bool:
    o = oauth_configured()
    return o["google"] or o["github"] or o["gitlab"]


def _callback_url(provider: str) -> str:
    return f"{APP_URL}/api/auth/{provider}/callback"


def create_session_token(user_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + JWT_TTL_SECONDS},
        JWT_SECRET,
        algorithm="HS256",
    )


def decode_session_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return str(payload.get("sub") or "")
    except jwt.PyJWTError:
        return None


def set_session_cookie(response: Response, user_id: str) -> None:
    token = create_session_token(user_id)
    secure = APP_URL.startswith("https")
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=JWT_TTL_SECONDS,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def user_from_request(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    user_id = decode_session_token(token)
    if not user_id:
        return None
    return get_user(user_id)


def require_user(request: Request) -> dict[str, Any]:
    user = user_from_request(request)
    if not user:
        raise HTTPException(401, "Login required")
    return user


def google_authorize_url() -> str:
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": _callback_url("google"),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "state": state,
        "prompt": "select_account",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def github_authorize_url() -> str:
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": _callback_url("github"),
        "scope": "read:user repo",
        "state": state,
    }
    return f"https://github.com/login/oauth/authorize?{urlencode(params)}"


def gitlab_authorize_url() -> str:
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": GITLAB_CLIENT_ID,
        "redirect_uri": _callback_url("gitlab"),
        "response_type": "code",
        "scope": "read_user read_api",
        "state": state,
    }
    return f"{GITLAB_OAUTH_URL}/oauth/authorize?{urlencode(params)}"


async def google_login(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": _callback_url("google"),
            },
        )
        if token_res.status_code != 200:
            raise HTTPException(400, f"Google token exchange failed: {token_res.text[:200]}")
        access = token_res.json().get("access_token")
        if not access:
            raise HTTPException(400, "Google token exchange failed")
        user_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access}"},
        )
        user_res.raise_for_status()
        g = user_res.json()
    return upsert_user(
        provider="google",
        provider_id=str(g.get("id")),
        username=g.get("email", g.get("name", "google-user")).split("@")[0],
        avatar_url=g.get("picture"),
    )


async def github_login(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": _callback_url("github"),
            },
        )
        if token_res.status_code != 200:
            raise HTTPException(400, f"GitHub token exchange failed: {token_res.text[:200]}")
        access = token_res.json().get("access_token")
        if not access:
            raise HTTPException(400, "GitHub token exchange failed")
        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"},
        )
        user_res.raise_for_status()
        gh = user_res.json()
    return upsert_user(
        provider="github",
        provider_id=str(gh.get("id")),
        username=gh.get("login") or "github-user",
        avatar_url=gh.get("avatar_url"),
        oauth_token=access,
    )


async def gitlab_login(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        token_res = await client.post(
            f"{GITLAB_OAUTH_URL}/oauth/token",
            data={
                "client_id": GITLAB_CLIENT_ID,
                "client_secret": GITLAB_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": _callback_url("gitlab"),
            },
        )
        if token_res.status_code != 200:
            raise HTTPException(400, f"GitLab token exchange failed: {token_res.text[:200]}")
        access = token_res.json().get("access_token")
        if not access:
            raise HTTPException(400, "GitLab token exchange failed")
        user_res = await client.get(
            f"{GITLAB_OAUTH_URL}/api/v4/user",
            headers={"Authorization": f"Bearer {access}"},
        )
        user_res.raise_for_status()
        gl = user_res.json()
    return upsert_user(
        provider="gitlab",
        provider_id=str(gl.get("id")),
        username=gl.get("username") or "gitlab-user",
        avatar_url=gl.get("avatar_url"),
        oauth_token=access,
    )
