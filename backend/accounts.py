import hashlib
from ipaddress import ip_address
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from .db import DATA, HOSTED, POSTGRES, cloud_engine, migrate, schema_for, workspace

api = APIRouter(prefix="/api/account")
ORIGIN = (os.getenv("BUDDY_PUBLIC_ORIGIN") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
COOKIE = "__Host-buddy_session"
SESSION_SECONDS = 14 * 86400
MAX_ACCOUNTS = int(os.getenv("BUDDY_MAX_ACCOUNTS", "50"))
OPEN_SIGNUP = os.getenv("BUDDY_OPEN_SIGNUP", "1") == "1"
MAX_WORKSPACE_BYTES = int(os.getenv("BUDDY_WORKSPACE_MIB", "128")) * 1024 * 1024
MAX_REQUEST_BYTES = 21 * 1024 * 1024
USER_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,31}\Z")
PUBLIC = {"/api/account", "/api/account/signup", "/api/account/login", "/api/account/recover"}
PUBLIC.update(f"/api/account/oauth/{provider}/{action}"
              for provider in ("google", "github") for action in ("start", "link", "callback"))
_attempts = defaultdict(deque)
_attempt_lock = threading.Lock()
_accounts_engine = create_engine(URL.create("sqlite", database=str(DATA / "accounts.db")),
                                 connect_args={"timeout": 10}, hide_parameters=True)


@contextmanager
def authority():
    database = cloud_engine() if POSTGRES else _accounts_engine
    with database.begin() as connection:
        yield connection


def initialize():
    origin = urlsplit(ORIGIN)
    if origin.scheme != "https" or not origin.hostname or ORIGIN != f"https://{origin.netloc}" or origin.username:
        raise RuntimeError("Hosted mode requires an HTTPS BUDDY_PUBLIC_ORIGIN")
    if MAX_ACCOUNTS < 1 or MAX_WORKSPACE_BYTES < MAX_REQUEST_BYTES:
        raise RuntimeError("Hosted account and storage limits must be positive")
    if os.getenv("RENDER") == "true" and not POSTGRES:
        raise RuntimeError("Set BUDDY_DATABASE_URL to persistent PostgreSQL before deploying to Render")
    if POSTGRES and len(os.getenv("BUDDY_PROVIDER_SECRET", "")) < 32:
        raise RuntimeError("Set a stable BUDDY_PROVIDER_SECRET before starting PostgreSQL mode")
    with authority() as db:
        if not POSTGRES:
            db.exec_driver_sql("PRAGMA journal_mode=WAL")
        binary = "BYTEA" if POSTGRES else "BLOB"
        db.exec_driver_sql(f"""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                password {binary} NOT NULL, salt {binary} NOT NULL, recovery {binary} NOT NULL,
                created BIGINT NOT NULL
            )
        """)
        db.exec_driver_sql(f"""
            CREATE TABLE IF NOT EXISTS sessions (
                token {binary} PRIMARY KEY, account TEXT NOT NULL, expires BIGINT NOT NULL
            )
        """)
        db.exec_driver_sql("CREATE INDEX IF NOT EXISTS sessions_account ON sessions(account)")
        db.exec_driver_sql("CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires)")
        db.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS oauth_identities (
                provider TEXT NOT NULL, subject TEXT NOT NULL,
                account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                PRIMARY KEY (provider, subject), UNIQUE (account, provider)
            )
        """)


def workspace_ids():
    with authority() as db:
        return list(db.exec_driver_sql("SELECT id FROM accounts ORDER BY created, id").scalars())


def current_account(token):
    if not token or len(token) != 43:
        return None
    with authority() as db:
        row = db.execute(text("""
            SELECT accounts.id, accounts.username FROM accounts JOIN sessions ON sessions.account = accounts.id
            WHERE sessions.token = :token AND sessions.expires > :now
        """), {"token": digest(token), "now": int(time.time())}).mappings().first()
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


def request_host(request):
    host = request.client.host if request.client else "unknown"
    if os.getenv("RENDER") == "true":
        forwarded = request.headers.get("cf-connecting-ip", "")
        try:
            host = str(ip_address(forwarded))
        except ValueError:
            pass
    return host


def authenticate_limit(request, username, *, signup=False):
    host = request_host(request)
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


def issue(username, identity, *, recovery_code=None, response=None):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with authority() as db:
        db.execute(text("DELETE FROM sessions WHERE expires <= :now"), {"now": now})
        db.execute(text("INSERT INTO sessions VALUES (:token, :identity, :expires)"),
                   {"token": digest(token), "identity": identity, "expires": now + SESSION_SECONDS})
    payload = {"authenticated": True, "username": username}
    if recovery_code:
        payload["recovery_code"] = recovery_code
    result = JSONResponse(payload) if response is None else response
    result.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite="lax", path="/")
    return result


@api.get("")
def account(request: Request):
    from .oauth import available, linked

    if not HOSTED:
        return {"hosted": False, "authenticated": True}
    row = current_account(request.cookies.get(COOKIE))
    return {"hosted": True, "authenticated": bool(row), "username": row["username"] if row else None,
            "open_signup": OPEN_SIGNUP, "sign_in_providers": available(),
            "linked_providers": linked(row["id"]) if row else []}


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
        if db.execute(text("SELECT 1 FROM accounts WHERE username = :username"), {"username": username}).first():
            raise HTTPException(409, "That username is already in use")
        if db.exec_driver_sql("SELECT COUNT(*) FROM accounts").scalar_one() >= MAX_ACCOUNTS:
            raise HTTPException(503, "This instance has reached its account limit")
    identity, salt, recovery = uuid4().hex, secrets.token_bytes(16), secrets.token_urlsafe(32)
    hashed = check_password(body.password, salt)
    with authority() as db:
        db.exec_driver_sql("SELECT pg_advisory_xact_lock(820034912)" if POSTGRES else "BEGIN IMMEDIATE")
        if db.exec_driver_sql("SELECT COUNT(*) FROM accounts").scalar_one() >= MAX_ACCOUNTS:
            raise HTTPException(503, "This instance has reached its account limit")
        try:
            db.execute(text("INSERT INTO accounts VALUES (:id, :username, :password, :salt, :recovery, :now)"),
                       {"id": identity, "username": username, "password": hashed, "salt": salt,
                        "recovery": digest(recovery), "now": int(time.time())})
        except IntegrityError as error:
            raise HTTPException(409, "That username is already in use") from error
        with workspace(identity):
            migrate(db if POSTGRES else None)
    return issue(username, identity, recovery_code=recovery)


@api.post("/login")
def login(body: Credentials, request: Request):
    check_hosted()
    username = normalized(body.username)
    authenticate_limit(request, username)
    with authority() as db:
        row = db.execute(text("SELECT id, password, salt FROM accounts WHERE username = :username"),
                         {"username": username}).mappings().first()
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
        row = db.execute(text("SELECT id, recovery FROM accounts WHERE username = :username"),
                         {"username": username}).mappings().first()
    proof = digest(body.recovery_code)
    if not row or not secrets.compare_digest(row["recovery"], proof):
        raise HTTPException(401, "Username or recovery code is incorrect")
    salt, code = secrets.token_bytes(16), secrets.token_urlsafe(32)
    hashed = check_password(body.password, salt)
    with authority() as db:
        updated = db.execute(text("UPDATE accounts SET password=:password, salt=:salt, recovery=:replacement "
                                  "WHERE id=:id AND recovery=:proof"),
                             {"password": hashed, "salt": salt, "replacement": digest(code), "id": row["id"], "proof": proof})
        if not updated.rowcount:
            raise HTTPException(409, "This recovery code has already been used")
        db.execute(text("DELETE FROM sessions WHERE account=:id"), {"id": row["id"]})
    return issue(username, row["id"], recovery_code=code)


@api.post("/logout")
def logout(request: Request):
    check_hosted()
    token = request.cookies.get(COOKIE)
    if token:
        with authority() as db:
            db.execute(text("DELETE FROM sessions WHERE token = :token"), {"token": digest(token)})
    result = JSONResponse({"authenticated": False})
    result.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    return result


def disk_used(identity):
    if POSTGRES:
        with authority() as db:
            return db.execute(text("""
                SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0) FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema AND c.relkind = 'r'
            """), {"schema": schema_for(identity)}).scalar_one()
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
