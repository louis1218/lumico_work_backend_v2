"""Replaces: getUserWorkLog, solar_farm_work_log, issueReport."""
import json

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import db
from ..auth import require_session
from ..common import require_fields

router = APIRouter(tags=["work-logs"], dependencies=[Depends(require_session)])

_TABLES = {"upload": "public.multi_task_session", "realignment": "public.realignment_task_session"}


@router.get("/work-logs")
def get_work_logs(
    taskType: str = "upload",
    date: str | None = None,
    site: str | None = None,
    block: str | None = None,
    tracker_id: str | None = None,
):
    table = _TABLES.get(taskType)
    if not table:
        raise HTTPException(status_code=400, detail="Invalid taskType (must be 'upload' or 'realignment')")

    conds, params = [], []
    if date:
        conds.append("created_at >= %s::date AND created_at < (%s::date + interval '1 day')")
        params += [date, date]
    if site:
        conds.append("site = %s")
        params.append(site)
    if block:
        conds.append("block = %s")
        params.append(block)
    if tracker_id:
        conds.append("tracker_id = %s")
        params.append(tracker_id)

    where = "".join(f" AND {c}" for c in conds)
    logs = db.fetch_all(
        f"SELECT * FROM {table} WHERE submitted = true{where} ORDER BY last_updated DESC", params
    )

    total_sql = f"SELECT COUNT(*) AS n FROM {table} WHERE submitted = true"
    total_params = []
    if site:
        total_sql += " AND site = %s"
        total_params.append(site)
    total = db.fetch_one(total_sql, total_params)["n"]

    return {"logs": logs, "totalTeamTasks": int(total)}


@router.post("/work-logs/solar-farm")
def save_solar_farm_log(payload: dict = Body(...)):
    require_fields(payload, ["trackerId", "site", "timestamp", "username", "block"],
                   "Missing required fields for multi_task_log")
    if not isinstance(payload.get("tasks"), list):
        raise HTTPException(status_code=400, detail="Missing required fields for multi_task_log")
    row = db.run(
        """
        INSERT INTO multi_task_log (tracker_id, site, block, timestamp, username, notes, tasks)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            payload["trackerId"], payload["site"], payload["block"], payload["timestamp"],
            payload["username"], payload.get("notes") or "", json.dumps(payload["tasks"]),
        ),
    )
    return {"message": "Metadata saved to multi_task_log", "id": row["id"]}


@router.post("/issues")
def submit_issue(payload: dict = Body(...)):
    require_fields(payload, ["site", "task", "block", "tracker_id", "report", "username"])
    row = db.run(
        """
        INSERT INTO task_issue_reports (site, task, block, tracker_id, report, username)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at
        """,
        tuple(payload[k] for k in ("site", "task", "block", "tracker_id", "report", "username")),
    )
    return {"message": "Report submitted successfully", "id": row["id"], "created_at": row["created_at"]}
