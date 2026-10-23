"""Replaces: SiteLogin, siteLogout, reset-password Lambdas. These are public (no session needed)."""
import secrets

import bcrypt
from fastapi import APIRouter, Body, HTTPException

from .. import db
from ..common import require_fields

router = APIRouter(prefix="/auth", tags=["auth"])

# Same message for unknown user and wrong password, so usernames can't be probed.
_BAD_LOGIN = "Invalid username or password"


@router.post("/login")
def login(payload: dict = Body(...)):
    require_fields(payload, ["username", "password"], "Missing username or password")
    username, password = payload["username"], payload["password"]

    user = db.fetch_one(
        "SELECT password_hash, first_name, last_name, role FROM users WHERE username = %s",
        (username,),
    )
    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail=_BAD_LOGIN)

    token = secrets.token_urlsafe(32)
    session = db.run(
        "INSERT INTO user_sessions (username, site, token) VALUES (%s, %s, %s) RETURNING id, login_time",
        (username, payload.get("site") or "unknown", token),
    )
    return {
        "message": "Login successful",
        "username": username,
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "role": user["role"],
        "session_id": session["id"],
        "session_token": token,
        "login_time": session["login_time"],
    }


@router.post("/logout")
def logout(payload: dict = Body(...)):
    session_id = payload.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")
    rows = db.execute(
        "UPDATE user_sessions SET logout_time = NOW() WHERE id = %s AND logout_time IS NULL",
        (session_id,),
    )
    if rows == 0:
        raise HTTPException(status_code=404, detail="Active session not found")
    return {"message": "Logout successful"}


@router.post("/reset-password")
def reset_password(payload: dict = Body(...)):
    require_fields(payload, ["username", "oldPassword", "newPassword"])
    username, old, new = payload["username"], payload["oldPassword"], payload["newPassword"]

    user = db.fetch_one("SELECT password_hash FROM users WHERE username = %s", (username,))
    if not user or not bcrypt.checkpw(old.encode(), user["password_hash"].encode()):
        # The old Lambda said "User not found" vs "Old password is incorrect"; one message now.
        raise HTTPException(status_code=401, detail="Username or old password is incorrect")

    new_hash = bcrypt.hashpw(new.encode(), bcrypt.gensalt(rounds=10)).decode()
    db.execute(
        "UPDATE users SET password_hash = %s, updated_at = NOW() WHERE username = %s",
        (new_hash, username),
    )
    return {"message": "Password updated successfully"}
