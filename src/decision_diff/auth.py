"""Authentication: argon2id passwords, opaque server-side sessions, run-scoped share links, Fetch-Metadata CSRF.

A session cookie holds a random token; only its SHA-256 is stored, so a database leak does not leak sessions.
A share session is bound to one run and grants read-only access to that run's report.
"""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request

COOKIE = "__Host-dd_session"
SESSION_DAYS = 14
HASHER = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def token_hash(token):
    return hashlib.sha256(token.encode()).digest()


def new_token():
    return secrets.token_urlsafe(32)


def hash_password(password):
    return HASHER.hash(password)


def verify_password(hashed, password):
    try:
        return HASHER.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def csrf_ok(request: Request):
    """Fetch-Metadata check: browsers send Sec-Fetch-Site; cross-site unsafe requests are rejected."""
    if request.method in SAFE_METHODS:
        return True
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = request.headers.get("origin")
    return origin is None or origin.split("://", 1)[-1] == request.headers.get("host")


async def current(request: Request, pool):
    """``{"user_id", "login", "run_id"}`` of the request's session; raises 401 without a valid session."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "not signed in")
    async with pool.connection() as c:
        row = await (await c.execute("""
            SELECT s.user_id, s.run_id, u.login FROM sessions s LEFT JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > now()""", (token_hash(token),))).fetchone()
    if row is None:
        raise HTTPException(401, "session expired")
    return row
