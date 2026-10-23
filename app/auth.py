"""Login sessions.

The login token (random, 43 chars) is sent as `Authorization: Bearer <token>`.
  require_user    - always enforced (new inspection endpoints)
  require_admin   - always enforced, role must be admin (reports)
  require_session - legacy routes; only enforced when AUTH_ENFORCED=true
"""
from typing import Optional

from fastapi import Depends, Header, HTTPException

from . import config, db


def _token(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def _load(token: Optional[str]) -> dict:
    if not token:
        raise HTTPException(status_code=401, detail="Not signed in")
    row = db.fetch_one(
        """
        SELECT s.id, s.username, u.role, u.first_name, u.last_name
        FROM user_sessions s
        JOIN users u ON u.username = s.username
        WHERE s.token = %s AND s.logout_time IS NULL
          AND s.login_time > NOW() - make_interval(hours => %s)
        """,
        (token, config.SESSION_HOURS),
    )
    if not row:
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
    return row


def require_user(authorization: Optional[str] = Header(default=None)) -> dict:
    return _load(_token(authorization))


def require_admin(user: dict = Depends(require_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    return user


def require_session(authorization: Optional[str] = Header(default=None)) -> Optional[dict]:
    if not config.AUTH_ENFORCED:
        return None
    return _load(_token(authorization))
