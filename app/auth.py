"""Session check, shared by every protected route.

The old Lambdas never validated session_id. This does, but stays switched off
until AUTH_ENFORCED=true so the like-for-like port can be tested first. When on,
the frontend must send the id from login as the X-Session-Id header.
"""
from typing import Optional

from fastapi import Header, HTTPException

from . import config, db


def require_session(x_session_id: Optional[str] = Header(default=None)) -> Optional[dict]:
    if not config.AUTH_ENFORCED:
        return None
    if not x_session_id or not x_session_id.isdigit():
        raise HTTPException(status_code=401, detail="Not authenticated")
    row = db.fetch_one(
        """
        SELECT id, username FROM user_sessions
        WHERE id = %s AND logout_time IS NULL
          AND login_time > NOW() - make_interval(hours => %s)
        """,
        (int(x_session_id), config.SESSION_HOURS),
    )
    if not row:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return row
