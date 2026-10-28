"""Cookie sessions, password hashing and role checks."""

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta, timezone
from functools import lru_cache
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import get_db
from .models import LoginSession, Membership, User, now

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
COOKIE = "orqestra_session"
SESSION_SECONDS = 12 * 60 * 60
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60
_LOGIN_ATTEMPT_SCRIPT = """
local attempts = redis.call('INCR', KEYS[1])
if attempts == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {attempts, redis.call('TTL', KEYS[1])}
"""


@lru_cache(maxsize=1)
def _login_limiter(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)


def _claim_login_attempt(email: str) -> tuple[Redis, str] | None:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        # Local tests and the single-process development server may run without Redis.
        return None

    key = f"orqestra:auth:login:{hashlib.sha256(email.encode()).hexdigest()}"
    try:
        client = _login_limiter(redis_url)
        attempts, ttl = cast(
            Any,
            client.eval(_LOGIN_ATTEMPT_SCRIPT, 1, key, str(LOGIN_ATTEMPT_WINDOW_SECONDS)),
        )
    except (RedisError, ValueError):
        raise HTTPException(503, "Authentication service temporarily unavailable") from None
    if int(attempts) > LOGIN_ATTEMPT_LIMIT:
        raise HTTPException(
            429,
            "Too many login attempts. Try again later.",
            headers={"Retry-After": str(max(int(ttl), 1))},
        )
    return client, key


def _clear_login_attempts(limiter: tuple[Redis, str] | None) -> None:
    if limiter is None:
        return
    client, key = limiter
    try:
        client.delete(key)
    except RedisError:
        raise HTTPException(503, "Authentication service temporarily unavailable") from None


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=3, maxmem=64 * 1024 * 1024
    ).hex()
    return f"scrypt${salt}${digest}"


# Equal password work for existing and unknown users.
DUMMY_HASH = hash_password("unusable-dummy-password")


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, _ = stored.split("$")
        return scheme == "scrypt" and hmac.compare_digest(hash_password(password, salt), stored)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_origin(request: Request) -> None:
    if request.headers.get("origin") != os.getenv("APP_ORIGIN", "http://localhost:5173"):
        raise HTTPException(403, "Untrusted request origin")


@dataclass
class Identity:
    user: User
    session: LoginSession


def current_identity(request: Request, db: Session = Depends(get_db)) -> Identity:
    token = request.cookies.get(COOKIE, "")
    session = db.get(LoginSession, token_hash(token)) if token else None
    if session is None:
        raise HTTPException(401, "Sign in required")
    expiry = session.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= now():
        raise HTTPException(401, "Sign in required")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Sign in required")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        check_origin(request)
        if not hmac.compare_digest(
            request.headers.get("x-csrf-token", "").encode(), session.csrf_token.encode()
        ):
            raise HTTPException(403, "Invalid CSRF token")
    return Identity(user, session)


def require_role(
    db: Session,
    workspace_id: str,
    identity: Identity,
    roles: tuple[str, ...] = ("owner", "member", "viewer"),
) -> Membership:
    membership = db.get(Membership, (workspace_id, identity.user.id), populate_existing=True)
    if membership is None:
        raise HTTPException(404, "Workspace not found")
    if membership.role not in roles:
        raise HTTPException(403, "Your workspace role does not allow this action")
    return membership


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value


def session_response(db: Session, user: User, request: Request, response: Response) -> dict:
    old = request.cookies.get(COOKIE)
    if old:
        previous = db.get(LoginSession, token_hash(old))
        if previous is not None:
            db.delete(previous)
    token = secrets.token_urlsafe(32)
    session = LoginSession(
        token_hash=token_hash(token),
        user_id=user.id,
        csrf_token=secrets.token_hex(32),
        expires_at=now() + timedelta(seconds=SESSION_SECONDS),
    )
    db.add(session)
    db.commit()
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        path="/api/v1",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"user": {"id": user.id, "email": user.email}, "csrf_token": session.csrf_token}


@router.post("/register", status_code=201, dependencies=[Depends(check_origin)])
def register(
    body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)
):
    if os.getenv("ALLOW_REGISTRATION", "true").lower() != "true":
        raise HTTPException(403, "Registration is disabled")
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Account cannot be registered") from None
    return session_response(db, user, request, response)


@router.post("/login", dependencies=[Depends(check_origin)])
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    limiter = _claim_login_attempt(body.email)
    user = db.scalar(select(User).where(User.email == body.email))
    valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not valid or not user.is_active:
        raise HTTPException(401, "Invalid email or password")
    _clear_login_attempts(limiter)
    return session_response(db, user, request, response)


@router.get("/me")
def me(response: Response, identity: Identity = Depends(current_identity)):
    response.headers["Cache-Control"] = "no-store"
    return {
        "user": {"id": identity.user.id, "email": identity.user.email},
        "csrf_token": identity.session.csrf_token,
    }


@router.post("/logout")
def logout(
    response: Response,
    identity: Identity = Depends(current_identity),
    db: Session = Depends(get_db),
):
    db.delete(identity.session)
    db.commit()
    response.delete_cookie(COOKIE, path="/api/v1")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "signed_out"}
