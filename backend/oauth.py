import base64
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.id_token import verify_oauth2_token
from sqlalchemy import text

from . import accounts
from .db import POSTGRES, migrate, workspace

api = APIRouter(prefix="/api/account/oauth")
COOKIE = "__Host-buddy_oauth"
TTL = 600
PROVIDERS = {
    "google": ("https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token", "openid profile"),
    "github": ("https://github.com/login/oauth/authorize", "https://github.com/login/oauth/access_token", "read:user"),
}


def credentials(provider):
    prefix = f"BUDDY_{provider.upper()}_CLIENT_"
    return os.getenv(prefix + "ID", "").strip(), os.getenv(prefix + "SECRET", "").strip()


def state_secret():
    return os.getenv("BUDDY_OAUTH_STATE_SECRET") or os.getenv("BUDDY_PROVIDER_SECRET", "")


def available():
    if len(state_secret()) < 32:
        return []
    return [provider for provider in PROVIDERS if all(credentials(provider))]


def linked(identity):
    enabled = available()
    if not enabled:
        return []
    with accounts.authority() as db:
        rows = db.execute(text("SELECT provider FROM oauth_identities WHERE account = :id"), {"id": identity})
        return [provider for provider in rows.scalars() if provider in enabled]


def codec():
    key = hmac.digest(state_secret().encode(), b"study-buddy-oauth-flow-v1", "sha256")
    return Fernet(base64.urlsafe_b64encode(key))


def callback_url(provider):
    return f"{accounts.ORIGIN}/api/account/oauth/{provider}/callback"


def seal_cookie(response, flow):
    response.set_cookie(COOKIE, codec().encrypt(json.dumps(flow).encode()).decode(), max_age=TTL,
                        secure=True, httponly=True, samesite="lax", path="/")
    return response


def begin(provider, request, *, link=False):
    accounts.check_hosted()
    if provider not in available():
        raise HTTPException(404, "This sign-in method is unavailable")
    if not accounts.rate_allowed(("oauth", accounts.request_host(request)), limit=12, window=900):
        raise HTTPException(429, "Too many sign-in requests. Try again later.")
    session = request.cookies.get(accounts.COOKIE)
    if link and not accounts.current_account(session):
        raise HTTPException(401, "Sign in to connect another method")
    flow = {"provider": provider, "state": secrets.token_urlsafe(32), "verifier": secrets.token_urlsafe(48),
            "nonce": secrets.token_urlsafe(32), "session": accounts.digest(session).hex() if link else None}
    challenge = base64.urlsafe_b64encode(accounts.digest(flow["verifier"])).decode().rstrip("=")
    auth_url, _, scope = PROVIDERS[provider]
    client_id, _ = credentials(provider)
    params = {"client_id": client_id, "redirect_uri": callback_url(provider), "response_type": "code",
              "scope": scope, "state": flow["state"], "code_challenge": challenge, "code_challenge_method": "S256"}
    if provider == "google":
        params.update(nonce=flow["nonce"], prompt="select_account")
    target = auth_url + "?" + urlencode(params)
    return seal_cookie(JSONResponse({"url": target}) if link else RedirectResponse(target, 303), flow)


@api.get("/{provider}/start")
def start(provider: str, request: Request):
    return begin(provider, request)


@api.post("/{provider}/link")
def connect(provider: str, request: Request):
    return begin(provider, request, link=True)


class BoundedGoogleRequest(GoogleRequest):
    def __call__(self, *args, **kwargs):
        kwargs["timeout"] = 8
        return super().__call__(*args, **kwargs)


def provider_identity(provider, code, flow):
    _, token_url, _ = PROVIDERS[provider]
    client_id, secret = credentials(provider)
    with httpx.Client(timeout=8) as client:
        response = client.post(token_url, data={"client_id": client_id, "client_secret": secret,
                                               "grant_type": "authorization_code", "redirect_uri": callback_url(provider),
                                               "code": code, "code_verifier": flow["verifier"]},
                               headers={"Accept": "application/json"})
        response.raise_for_status()
        token = response.json()
        if provider == "google":
            if not isinstance(token.get("id_token"), str):
                raise ValueError("Missing ID token")
            claims = verify_oauth2_token(token["id_token"], BoundedGoogleRequest(), client_id)
            if claims.get("nonce") != flow["nonce"]:
                raise ValueError("Invalid nonce")
            subject, name = claims.get("sub"), claims.get("given_name")
        else:
            if not isinstance(token.get("access_token"), str):
                raise ValueError("Missing access token")
            response = client.get("https://api.github.com/user", headers={
                "Accept": "application/vnd.github+json", "Authorization": "Bearer " + token["access_token"],
            })
            response.raise_for_status()
            user = response.json()
            subject = str(user["id"]) if type(user.get("id")) is int else None
            name = user.get("login")
    if not isinstance(subject, str) or not 0 < len(subject) <= 255:
        raise ValueError("Invalid subject")
    return subject, name if isinstance(name, str) else provider


def resolve(provider, subject, name, existing, request):
    values = {"provider": provider, "subject": subject}
    with accounts.authority() as db:
        db.exec_driver_sql("SELECT pg_advisory_xact_lock(820034912)" if POSTGRES else "BEGIN IMMEDIATE")
        owner = db.execute(text("""
            SELECT accounts.id, accounts.username FROM oauth_identities
            JOIN accounts ON accounts.id = oauth_identities.account
            WHERE provider = :provider AND subject = :subject
        """), values).mappings().first()
        if existing:
            other = db.execute(text("SELECT 1 FROM oauth_identities WHERE account = :id AND provider = :provider"),
                               {"id": existing["id"], "provider": provider}).first()
            if owner and owner["id"] == existing["id"]:
                return existing, None
            if owner or other:
                return None, "linked"
            db.execute(text("INSERT INTO oauth_identities VALUES (:provider, :subject, :id)"),
                       {**values, "id": existing["id"]})
            return existing, None
        if owner:
            return dict(owner), None
        if not accounts.OPEN_SIGNUP:
            return None, "closed"
        if not accounts.rate_allowed(("signup", accounts.request_host(request)), limit=5, window=3600):
            return None, "busy"
        if db.exec_driver_sql("SELECT COUNT(*) FROM accounts").scalar_one() >= accounts.MAX_ACCOUNTS:
            return None, "full"
        identity = uuid4().hex
        prefix = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:16].rstrip("-") or provider
        username = f"{prefix}-{identity[:15]}"
        db.execute(text("INSERT INTO accounts VALUES (:id, :username, :password, :salt, :recovery, :now)"),
                   {"id": identity, "username": username, "password": secrets.token_bytes(32),
                    "salt": secrets.token_bytes(16), "recovery": secrets.token_bytes(32), "now": int(time.time())})
        db.execute(text("INSERT INTO oauth_identities VALUES (:provider, :subject, :id)"),
                   {**values, "id": identity})
        with workspace(identity):
            migrate(db if POSTGRES else None)
    return {"id": identity, "username": username}, None


def finish(error=None, *, link=False):
    query = "?" + urlencode({"signin_error": error}) if error else ("?signin_connected=1" if link else "")
    response = RedirectResponse("/" + query + ("#settings" if link else ""), 303)
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    return response


@api.get("/{provider}/callback")
def callback(provider: str, request: Request, state: str = "", code: str = "", error: str = ""):
    accounts.check_hosted()
    if provider not in available():
        return finish("unavailable")
    try:
        flow = json.loads(codec().decrypt(request.cookies.get(COOKIE, "").encode(), ttl=TTL))
    except (InvalidToken, ValueError):
        return finish("expired")
    if flow["provider"] != provider or not secrets.compare_digest(state.encode(), flow["state"].encode()):
        return finish("expired")
    linking = flow["session"] is not None
    if error or not code:
        return finish("cancelled", link=linking)
    session = request.cookies.get(accounts.COOKIE, "")
    existing = None
    if linking:
        if secrets.compare_digest(accounts.digest(session).hex(), flow["session"]):
            existing = accounts.current_account(session)
        if not existing:
            return finish("expired", link=True)
    try:
        subject, name = provider_identity(provider, code, flow)
    except (httpx.HTTPError, GoogleAuthError, ValueError, KeyError, TypeError, AttributeError):
        return finish("provider", link=linking)
    account, issue = resolve(provider, subject, name, existing, request)
    response = finish(issue, link=linking)
    if account and not linking:
        return accounts.issue(account["username"], account["id"], response=response)
    return response
