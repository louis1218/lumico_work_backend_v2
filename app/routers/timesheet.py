"""Replaces: getTimeSheet (POST body lookup) and logTimesheet (sign in / sign out)."""
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import require_session
from ..common import require_fields

router = APIRouter(prefix="/timesheet", tags=["timesheet"], dependencies=[Depends(require_session)])


@router.post("/query")
def get_timesheet(payload: dict = Body(...)):
    site, username, work_date = payload.get("site"), payload.get("username"), payload.get("work_date")
    if not site:
        raise HTTPException(status_code=400, detail="Missing site")
    if not work_date:
        # The old Lambda crashed with a 500 here; a clear 400 instead.
        raise HTTPException(status_code=400, detail="Missing work_date")
    if username:
        return db.fetch_all(
            "SELECT * FROM timesheet WHERE username = %s AND site = %s AND DATE(sign_in) = %s::date",
            (username, site, work_date),
        )
    return db.fetch_all(
        "SELECT * FROM timesheet WHERE site = %s AND DATE(sign_in) = %s::date",
        (site, work_date),
    )


@router.post("")
def log_timesheet(payload: dict = Body(...)):
    require_fields(payload, ["username", "site", "type", "work_date"], "Missing or invalid parameters")
    username, site, kind = payload["username"], payload["site"], payload["type"]
    timestamp, work_date = payload.get("timestamp"), payload["work_date"]

    existing = db.fetch_one(
        """
        SELECT * FROM timesheet
        WHERE username = %s AND site = %s AND sign_in::date = %s::date
        ORDER BY sign_in DESC LIMIT 1
        """,
        (username, site, work_date),
    )

    if kind == "sign_in":
        if existing and existing.get("sign_in"):
            raise HTTPException(status_code=400, detail="User already signed in today")
        record = db.run(
            "INSERT INTO timesheet (username, site, sign_in) VALUES (%s, %s, %s) RETURNING *",
            (username, site, timestamp),
        )
    elif kind == "sign_out":
        if not existing:
            raise HTTPException(status_code=400, detail="User has not signed in today")
        if existing.get("sign_out"):
            raise HTTPException(status_code=400, detail="User already signed out today")
        record = db.run(
            "UPDATE timesheet SET sign_out = %s WHERE id = %s RETURNING *",
            (timestamp, existing["id"]),
        )
    else:
        raise HTTPException(status_code=400, detail="type must be sign_in or sign_out")

    return {"message": "Timesheet updated", "record": record}
