"""
auth.py
-------
JWT-based auth (OAuth2 password flow), RBAC dependency, and a lightweight
per-API-key token-bucket rate limiter.

Production note: swap the in-memory rate limiter's dict for Redis
(INCR + EXPIRE / a Lua token-bucket script) so limits are enforced
correctly across multiple API replicas.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext

SECRET_KEY = os.environ.get("JWT_SECRET", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

ROLE_HIERARCHY = {"viewer": 0, "fleet_manager": 1, "admin": 2}


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(*, sub: str, fleet_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": sub, "fleet_id": fleet_id, "role": role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                             detail="Invalid or expired token")


def get_current_claims(token: str = Depends(oauth2_scheme)) -> dict:
    return decode_token(token)


def require_role(min_role: str):
    """Dependency factory enforcing RBAC + tenant isolation.
    Every DB query downstream must also filter by claims['fleet_id'] —
    this only gates the endpoint, it does not filter rows."""
    def dependency(claims: dict = Depends(get_current_claims)) -> dict:
        if ROLE_HIERARCHY.get(claims.get("role"), -1) < ROLE_HIERARCHY[min_role]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                 detail=f"requires role >= {min_role}")
        return claims
    return dependency


class TokenBucketLimiter:
    """Simple in-process token bucket, keyed by client identity.
    Good enough for the MVP / single-replica demo; see module docstring
    for the Redis-backed production version."""

    def __init__(self, rate_per_sec: float = 20, burst: int = 40):
        self.rate = rate_per_sec
        self.burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1, now)
        return True


rate_limiter = TokenBucketLimiter()


def rate_limit_dependency(request: Request):
    client_key = request.headers.get("authorization", request.client.host)
    if not rate_limiter.check(client_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                             detail="Rate limit exceeded")
