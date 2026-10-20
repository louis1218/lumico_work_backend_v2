"""Replaces: solar_farm-tracker-scan.

Public for now: the frontend never calls this, so some other client (scanner?) does, and
a session header would break it. Find out who calls it before adding auth.
"""
from fastapi import APIRouter, Body

from .. import db
from ..common import require_fields

router = APIRouter(tags=["barcode"])


@router.post("/barcode/scan")
def save_barcode_scan(payload: dict = Body(...)):
    require_fields(payload, ["serial", "timestamp", "site", "username"])
    row = db.run(
        """
        INSERT INTO barcode_log
            (serial, tracker_id, note, block, site, timestamp, username, scan_image_file, photo_links)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            payload["serial"], payload.get("trackerId"), payload.get("note"), payload.get("block"),
            payload["site"], payload["timestamp"], payload["username"],
            payload.get("scanImageFile"), payload.get("photo_links") or [],
        ),
    )
    return {"message": "Scan log saved", "id": row["id"]}
