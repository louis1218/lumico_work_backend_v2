"""Field inspections: one record per tracker check. Staff only (any signed-in user)."""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import config, db
from ..auth import require_user

router = APIRouter(prefix="/inspections", tags=["inspections"])

_KEY = re.compile(r"^[A-Za-z0-9_./-]+$")


def today_local() -> date:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def parse_day(value: str | None) -> date:
    if not value:
        return today_local()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date must look like 2026-10-22")


def _int(payload: dict, field: str, low: int, high: int) -> int:
    v = payload.get(field)
    if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be a whole number from {low} to {high}",
        )
    return v


@router.get("/options")
def options(user: dict = Depends(require_user)):
    return {
        "blocks": config.BLOCKS,
        "trackers_per_block": config.TRACKERS_PER_BLOCK,
        "defect_types": config.DEFECT_TYPES,
    }


@router.post("")
def create_inspection(payload: dict = Body(...), user: dict = Depends(require_user)):
    site = (payload.get("site") or "").strip().lower()
    if not site:
        raise HTTPException(status_code=400, detail="Missing site")
    block = _int(payload, "block", 1, config.BLOCKS)
    tracker = _int(payload, "tracker", 1, config.TRACKERS_PER_BLOCK)
    broken = _int(payload, "broken_panels", 0, 999)

    defects = payload.get("defects") or []
    if not isinstance(defects, list) or any(
        d not in config.DEFECT_TYPES for d in defects
    ):
        raise HTTPException(status_code=400, detail="Unknown defect type")
    if broken == 0:
        defects = []  # nothing broken, nothing to categorise

    keys = payload.get("photo_keys") or []
    if (
        not isinstance(keys, list)
        or len(keys) > 20
        or any(not isinstance(k, str) or ".." in k or not _KEY.match(k) for k in keys)
    ):
        raise HTTPException(status_code=400, detail="Invalid photos")
    if broken > 0 and not keys:
        raise HTTPException(
            status_code=400, detail="Add at least one photo of the damage"
        )

    notes = str(payload.get("notes") or "").strip()[:2000]
    work_date = parse_day(payload.get("work_date"))

    row = db.run(
        """
        INSERT INTO inspections
            (site, block, tracker, username, work_date, broken_panels, defects, notes, photo_keys)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, inspected_at
        """,
        (
            site,
            block,
            tracker,
            user["username"],
            work_date,
            broken,
            defects,
            notes,
            keys,
        ),
    )
    return {
        "message": "Inspection saved",
        "id": row["id"],
        "inspected_at": row["inspected_at"],
    }


@router.get("/block-status")
def block_status(
    site: str,
    block: int,
    work_date: str | None = None,
    user: dict = Depends(require_user),
):
    """Latest inspection of each tracker in one block for one day. Powers the block board."""
    if not 1 <= block <= config.BLOCKS:
        raise HTTPException(status_code=400, detail="Unknown block")
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (i.tracker)
               i.tracker, i.broken_panels, i.defects, i.notes, i.photo_keys,
               i.username, i.inspected_at, u.first_name, u.last_name
        FROM inspections i
        LEFT JOIN users u ON u.username = i.username
        WHERE i.site = %s AND i.block = %s AND i.work_date = %s
        ORDER BY i.tracker, i.inspected_at DESC
        """,
        (site.strip().lower(), block, parse_day(work_date)),
    )
    return {"block": block, "trackers": rows}


@router.get("/mine")
def my_day(site: str, work_date: str | None = None, user: dict = Depends(require_user)):
    """The signed-in person's own totals and recent saves for the day."""
    day, site = parse_day(work_date), site.strip().lower()
    totals = db.fetch_one(
        """
        SELECT COUNT(*) AS inspections,
               COUNT(DISTINCT (block, tracker)) AS trackers_checked,
               COUNT(*) FILTER (WHERE broken_panels > 0) AS issues,
               COALESCE(SUM(broken_panels), 0) AS broken_panels
        FROM inspections WHERE username = %s AND site = %s AND work_date = %s
        """,
        (user["username"], site, day),
    )
    recent = db.fetch_all(
        """
        SELECT id, block, tracker, broken_panels, inspected_at FROM inspections
        WHERE username = %s AND site = %s AND work_date = %s
        ORDER BY inspected_at DESC LIMIT 15
        """,
        (user["username"], site, day),
    )
    return {"date": day.isoformat(), **totals, "recent": recent}
