import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .db import DATA, HOSTED, migrate, workspace

api = APIRouter(prefix="/api/account")
ORIGIN = os.getenv("BUDDY_PUBLIC_ORIGIN", "").rstrip("/")
COOKIE = "__Host-buddy_session"
SESSION_SECONDS = 14 * 86400
MAX_ACCOUNTS = int(os.getenv("BUDDY_MAX_ACCOUNTS", "50"))
OPEN_SIGNUP = os.getenv("BUDDY_OPEN_SIGNUP", "1") == "1"
MAX_WORKSPACE_BYTES = int(os.getenv("BUDDY_WORKSPACE_MIB", "128")) * 1024 * 1024
MAX_REQUEST_BYTES = 21 * 1024 * 1024
USER_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,31}\Z")
PUBLIC = {"/api/account", "/api/account/signup", "/api/account/login", "/api/account/recover"}
_attempts = defaultdict(deque)
_attempt_lock = threading.Lock()


@contextmanager
def authority():
    connection = sqlite3.connect(DATA / "accounts.db", timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    origin = urlsplit(ORIGIN)
    if origin.scheme != "https" or not origin.hostname or ORIGIN != f"https://{origin.netloc}" or origin.username:
        raise RuntimeError("Hosted mode requires an HTTPS BUDDY_PUBLIC_ORIGIN")
    if MAX_ACCOUNTS < 1 or MAX_WORKSPACE_BYTES < MAX_REQUEST_BYTES:
        raise RuntimeError("Hosted account and storage limits must be positive")
    with authority() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                password BLOB NOT NULL, salt BLOB NOT NULL, recovery BLOB NOT NULL,
                created INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token BLOB PRIMARY KEY, account TEXT NOT NULL, expires INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_account ON sessions(account);
            CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires);
        """)


def workspace_ids():
    with authority() as db:
        return [row[0] for row in db.execute("SELECT id FROM accounts ORDER BY created, id")]


def current_account(token):
    if not token or len(token) != 43:
        return None
    with authority() as db:
        row = db.execute("""
            SELECT accounts.id, accounts.username FROM accounts JOIN sessions ON sessions.account = accounts.id
            WHERE sessions.token = ? AND sessions.expires > ?
        """, (digest(token), int(time.time()))).fetchone()
        return dict(row) if row else None


def digest(token):
    return hashlib.sha256(token.encode()).digest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=160 * 1024 * 1024, dklen=32)


# Bound the memory used by concurrent password checks on a small hosting instance.
_password_slots = threading.BoundedSemaphore(1)


def check_password(password, salt):
    if not _password_slots.acquire(blocking=False):
        raise HTTPException(429, "Sign-in is busy. Try again in a moment.", headers={"Retry-After": "2"})
    try:
        return password_hash(password, salt)
    finally:
        _password_slots.release()


def rate_allowed(key, *, limit, window):
    now = time.monotonic()
    with _attempt_lock:
        if len(_attempts) >= 4096:
            expired = [name for name, values in _attempts.items() if not values or values[-1] <= now - 3600]
            for name in expired:
                del _attempts[name]
            if key not in _attempts and len(_attempts) >= 4096:
                return False
        visits = _attempts[key]
        while visits and visits[0] <= now - window:
            visits.popleft()
        if len(visits) >= limit:
            return False
        visits.append(now)
        return True


def authenticate_limit(request, username, *, signup=False):
    host = request.client.host if request.client else "unknown"
    allowed_ip = rate_allowed(("auth", host), limit=12, window=900)
    allowed_name = rate_allowed(("name", username), limit=8, window=900)
    allowed_signup = not signup or rate_allowed(("signup", host), limit=5, window=3600)
    if not (allowed_ip and allowed_name and allowed_signup):
        raise HTTPException(429, "Too many account requests. Try again later.", headers={"Retry-After": "900"})


def check_hosted():
    if not HOSTED:
        raise HTTPException(404, "Account management is available in hosted mode")


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class Recovery(Credentials):
    recovery_code: str = Field(min_length=43, max_length=64)


def normalized(username):
    value = username.strip().lower()
    if not USER_NAME.fullmatch(value):
        raise HTTPException(422, "Use 3–32 letters, digits, dots, hyphens or underscores for the username")
    return value


def issue(username, identity, *, recovery_code=None):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with authority() as db:
        db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (digest(token), identity, now + SESSION_SECONDS))
    payload = {"authenticated": True, "username": username}
    if recovery_code:
        payload["recovery_code"] = recovery_code
    result = JSONResponse(payload)
    result.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite="lax", path="/")
    return result


@api.get("")
def account(request: Request):
    if not HOSTED:
        return {"hosted": False, "authenticated": True}
    row = current_account(request.cookies.get(COOKIE))
    return {"hosted": True, "authenticated": bool(row), "username": row["username"] if row else None,
            "open_signup": OPEN_SIGNUP}


@api.post("/signup")
def signup(body: Credentials, request: Request):
    check_hosted()
    if not OPEN_SIGNUP:
        raise HTTPException(403, "New registrations are paused")
    username = normalized(body.username)
    authenticate_limit(request, username, signup=True)
    if len(body.password) < 12:
        raise HTTPException(422, "Use at least 12 characters for your password")
    with authority() as db:
        if db.execute("SELECT 1 FROM accounts WHERE username = ?", (username,)).fetchone():
            raise HTTPException(409, "That username is already in use")
        if db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= MAX_ACCOUNTS:
            raise HTTPException(503, "This instance has reached its account limit")
    identity, salt, recovery = uuid4().hex, secrets.token_bytes(16), secrets.token_urlsafe(32)
    hashed = check_password(body.password, salt)
    with authority() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= MAX_ACCOUNTS:
            raise HTTPException(503, "This instance has reached its account limit")
        try:
            db.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?)",
                       (identity, username, hashed, salt, digest(recovery), int(time.time())))
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, "That username is already in use") from error
        with workspace(identity):
            migrate()
    return issue(username, identity, recovery_code=recovery)


@api.post("/login")
def login(body: Credentials, request: Request):
    check_hosted()
    username = normalized(body.username)
    authenticate_limit(request, username)
    with authority() as db:
        row = db.execute("SELECT id, password, salt FROM accounts WHERE username = ?", (username,)).fetchone()
    attempt = check_password(body.password, row["salt"] if row else b"\0" * 16)
    if row is None or not secrets.compare_digest(row["password"], attempt):
        raise HTTPException(401, "Username or password is incorrect")
    return issue(username, row["id"])


@api.post("/recover")
def recover(body: Recovery, request: Request):
    check_hosted()
    username = normalized(body.username)
    authenticate_limit(request, username)
    if len(body.password) < 12:
        raise HTTPException(422, "Use at least 12 characters for your new password")
    with authority() as db:
        row = db.execute("SELECT id, recovery FROM accounts WHERE username = ?", (username,)).fetchone()
    proof = digest(body.recovery_code)
    if not row or not secrets.compare_digest(row["recovery"], proof):
        raise HTTPException(401, "Username or recovery code is incorrect")
    salt, code = secrets.token_bytes(16), secrets.token_urlsafe(32)
    hashed = check_password(body.password, salt)
    with authority() as db:
        updated = db.execute("UPDATE accounts SET password=?, salt=?, recovery=? WHERE id=? AND recovery=?",
                             (hashed, salt, digest(code), row["id"], proof))
        if not updated.rowcount:
            raise HTTPException(409, "This recovery code has already been used")
        db.execute("DELETE FROM sessions WHERE account=?", (row["id"],))
    return issue(username, row["id"], recovery_code=code)


@api.post("/logout")
def logout(request: Request):
    check_hosted()
    token = request.cookies.get(COOKIE)
    if token:
        with authority() as db:
            db.execute("DELETE FROM sessions WHERE token = ?", (digest(token),))
    result = JSONResponse({"authenticated": False})
    result.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    return result


def disk_used(identity):
    root = DATA / "workspaces" / identity
    return sum(path.stat().st_size for path in root.iterdir() if path.is_file()) if root.is_dir() else 0


class HostedAccess:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = request.url.path
        api_request = path.startswith("/api/")
        mutation = request.method not in ("GET", "HEAD", "OPTIONS")

        async def secured(message):
            if message["type"] == "http.response.start":
                headers = list(message["headers"])
                headers.extend([(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                                (b"referrer-policy", b"no-referrer"),
                                (b"strict-transport-security", b"max-age=31536000"),
                                (b"content-security-policy",
                                 b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                 b"img-src 'self' data:; font-src 'self'; object-src 'none'; frame-ancestors 'none'; "
                                 b"base-uri 'self'; form-action 'self'")])
                if api_request:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        async def reject(message, status):
            await JSONResponse({"detail": message}, status_code=status)(scope, receive, secured)

        if api_request and mutation and request.headers.get("origin") != ORIGIN:
            return await reject("Open Study Buddy in its own tab to change study data", 403)
        length = request.headers.get("content-length")
        if length and length.isdecimal() and int(length) > (4096 if path.startswith("/api/account/") else MAX_REQUEST_BYTES):
            return await reject("Request exceeds the allowed size", 413)
        if not api_request or path in PUBLIC or path == "/api/health" or path == "/api/account/logout":
            return await self.app(scope, receive, secured)
        active = await run_in_threadpool(current_account, request.cookies.get(COOKIE))
        if active is None:
            return await reject("Sign in to access your study workspace", 401)
        identity = active["id"]
        if not rate_allowed(("workspace", identity), limit=240, window=60):
            return await reject("Workspace request limit reached. Try again in a minute.", 429)
        if mutation and not path.endswith("/download"):
            if await run_in_threadpool(disk_used, identity) + (int(length) if length and length.isdecimal() else 0) > MAX_WORKSPACE_BYTES:
                if request.method != "DELETE":
                    return await reject("This workspace has reached its storage limit", 507)
        with workspace(identity):
            await self.app(scope, receive, secured)
