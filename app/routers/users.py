"""Replaces: getUserList."""
from fastapi import APIRouter

from .. import db

router = APIRouter(tags=["users"])


# Public on purpose for now: the login screen needs this list before anyone has a session.
@router.get("/users")
def list_users():
    return db.fetch_all(
        "SELECT username, first_name, last_name FROM users WHERE is_active = true ORDER BY username ASC"
    )
