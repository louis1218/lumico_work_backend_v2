"""Shared edit sessions for the two task types.

Replaces: getWorkLogSharedEditSession, WorkLogSharedEditSession (upload),
          getSharedSessionRealignmentLog, SharedSessionRealignmentTask (realignment).
The "get" calls stay POST-with-body, exactly as the frontend sends them today.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import require_session
from ..common import require_fields

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(require_session)])


def _lookup_keys(payload: dict):
    tracker_id, site, block = payload.get("trackerId"), payload.get("site"), payload.get("block")
    if not tracker_id or not site or not block:
        raise HTTPException(status_code=400, detail="Missing trackerId, site or block in body")
    return tracker_id, site, block


# ---------- upload (multi_task_session) ----------

@router.post("/upload/get")
def get_upload_session(payload: dict = Body(...)):
    return db.fetch_one(
        "SELECT * FROM multi_task_session WHERE tracker_id = %s AND site = %s AND block = %s LIMIT 1",
        _lookup_keys(payload),
    )


@router.post("/upload")
def save_upload_session(payload: dict = Body(...)):
    require_fields(payload, ["trackerId", "site", "block", "username"], "Missing required fields for multi_task_session")
    if not isinstance(payload.get("tasks"), list):
        raise HTTPException(status_code=400, detail="Missing required fields for multi_task_session")

    tracker_id, site, block, username = payload["trackerId"], payload["site"], payload["block"], payload["username"]

    row = db.fetch_one(
        "SELECT tasks FROM multi_task_session WHERE tracker_id = %s AND site = %s AND block = %s",
        (tracker_id, site, block),
    )
    existing = row["tasks"] if row and isinstance(row["tasks"], list) else []

    now = datetime.now(timezone.utc).isoformat()
    for incoming in payload["tasks"]:
        name = incoming.get("task_name")
        idx = next((i for i, t in enumerate(existing) if t.get("task_name") == name), -1)
        merged = {
            **(existing[idx] if idx != -1 else {}),
            **incoming,
            "task_name": incoming.get("task_name") or name,
            "remark": incoming.get("remark") or "planned",
            "created_by": username,
            "updated_at": now,
        }
        if idx != -1:
            existing[idx] = merged
        else:
            existing.append(merged)

    result = db.run(
        """
        INSERT INTO multi_task_session
            (tracker_id, site, block, username, notes, tasks, submitted, last_updated, completion_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)
        ON CONFLICT (tracker_id, site, block)
        DO UPDATE SET
            username = EXCLUDED.username,
            notes = EXCLUDED.notes,
            tasks = EXCLUDED.tasks,
            submitted = EXCLUDED.submitted,
            last_updated = NOW(),
            completion_date = COALESCE(EXCLUDED.completion_date, multi_task_session.completion_date)
        RETURNING id
        """,
        (
            tracker_id, site, block, username,
            payload.get("notes") or "",
            json.dumps(existing),
            payload.get("submitted", False),
            payload.get("completion_date"),
        ),
    )
    return {"message": "Session saved to multi_task_session", "id": result["id"]}


# ---------- realignment (realignment_task_session) ----------

@router.post("/realignment/get")
def get_realignment_session(payload: dict = Body(...)):
    return db.fetch_one(
        """
        SELECT tracker_id, block, site, last_updated, notes, username,
               image_files, before_barcodes, after_barcodes, submitted
        FROM realignment_task_session
        WHERE tracker_id = %s AND site = %s AND block = %s
        ORDER BY created_at DESC LIMIT 1
        """,
        _lookup_keys(payload),
    )


@router.post("/realignment")
def save_realignment_session(payload: dict = Body(...)):
    require_fields(payload, ["trackerId", "block", "site", "username", "last_updated"])
    try:
        last_updated = datetime.fromisoformat(str(payload["last_updated"]).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid last_updated")

    files = payload.get("imageFiles") or []
    image_files = {
        "before_image": [f["key"] for f in files if f.get("type") == "before"],
        "after_image": [f["key"] for f in files if f.get("type") == "after"],
    }

    result = db.run(
        """
        INSERT INTO realignment_task_session (
            tracker_id, block, site, last_updated, notes, username,
            image_files, before_barcodes, after_barcodes, submitted, is_adhoc, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (tracker_id, block, site)
        DO UPDATE SET
            last_updated = EXCLUDED.last_updated,
            notes = EXCLUDED.notes,
            username = EXCLUDED.username,
            image_files = EXCLUDED.image_files,
            before_barcodes = EXCLUDED.before_barcodes,
            after_barcodes = EXCLUDED.after_barcodes,
            submitted = EXCLUDED.submitted,
            is_adhoc = EXCLUDED.is_adhoc,
            created_at = NOW()
        RETURNING id
        """,
        (
            payload["trackerId"], payload["block"], payload["site"], last_updated,
            payload.get("notes") or "", payload["username"],
            json.dumps(image_files),
            json.dumps(payload.get("beforeBarcode") or []),
            json.dumps(payload.get("afterBarcode") or []),
            payload.get("submitted") is True,
            payload.get("isAdhoc") is True,
        ),
    )
    return {"message": "Realignment task logged successfully", "id": result["id"]}
