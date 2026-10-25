"""Field inspections. Staff only (any signed-in user).

One tracker check = one batch of rows:
  - nothing broken  -> a single row with panel NULL
  - broken panels   -> one row per broken panel, each with its own defects, notes and photos
"""
import re
import uuid
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


def _int(value, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise HTTPException(status_code=400, detail=f"{field} must be a whole number from {low} to {high}")
    return value


def _keys(value, maxlen: int = 20) -> list[str]:
    keys = value or []
    if (not isinstance(keys, list) or len(keys) > maxlen
            or any(not isinstance(k, str) or ".." in k or not _KEY.match(k) for k in keys)):
        raise HTTPException(status_code=400, detail="Invalid photos")
    return keys


def _text(value, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


@router.get("/options")
def options(user: dict = Depends(require_user)):
    return {
        "blocks": config.BLOCKS,
        "trackers_per_block": config.TRACKERS_PER_BLOCK,
        "panels_per_tracker": config.PANELS_PER_TRACKER,
        "defect_types": config.DEFECT_TYPES,
    }


@router.post("")
def create_inspection(payload: dict = Body(...), user: dict = Depends(require_user)):
    site = (payload.get("site") or "").strip().lower()
    if not site:
        raise HTTPException(status_code=400, detail="Missing site")
    block = _int(payload.get("block"), "block", 1, config.BLOCKS)
    tracker = _int(payload.get("tracker"), "tracker", 1, config.TRACKERS_PER_BLOCK)
    work_date = parse_day(payload.get("work_date"))

    if "panels" not in payload and "broken_panels" in payload:
        raise HTTPException(status_code=400, detail="The app is out of date. Refresh the page and try again.")

    panels = payload.get("panels") or []
    if not isinstance(panels, list) or len(panels) > config.PANELS_PER_TRACKER:
        raise HTTPException(status_code=400, detail="Invalid panels")

    # rows: (panel, broken_panels, defects, notes, photo_keys)
    rows: list[tuple] = []
    if not panels:
        # Tracker checked, nothing broken. Tracker-level notes and photos are optional.
        rows.append((None, 0, [], _text(payload.get("notes")), _keys(payload.get("photo_keys"))))
    else:
        seen: set[int] = set()
        for p in panels:
            if not isinstance(p, dict):
                raise HTTPException(status_code=400, detail="Invalid panels")
            n = _int(p.get("panel"), "panel", 1, config.PANELS_PER_TRACKER)
            if n in seen:
                raise HTTPException(status_code=400, detail=f"Panel {n} is listed twice")
            seen.add(n)
            defects = p.get("defects") or []
            if not isinstance(defects, list) or any(d not in config.DEFECT_TYPES for d in defects):
                raise HTTPException(status_code=400, detail="Unknown defect type")
            keys = _keys(p.get("photo_keys"), 10)
            if not keys:
                raise HTTPException(status_code=400, detail=f"Add at least one photo for panel {n}")
            rows.append((n, 1, defects, _text(p.get("notes")), keys))
        rows.sort(key=lambda r: r[0])

    batch = str(uuid.uuid4())
    values = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid)"] * len(rows))
    params: list = []
    for panel, broken, defects, notes, keys in rows:
        params += [site, block, tracker, user["username"], work_date, panel, broken, defects, notes, keys, batch]
    saved = db.fetch_all(
        f"""
        INSERT INTO inspections
            (site, block, tracker, username, work_date, panel, broken_panels, defects, notes, photo_keys, batch_id)
        VALUES {values}
        RETURNING id, inspected_at
        """,
        params,
    )
    return {
        "message": "Inspection saved",
        "batch_id": batch,
        "panels_saved": sum(1 for r in rows if r[0] is not None),
        "inspected_at": saved[0]["inspected_at"],
    }


# What counts for each tracker in a block on a day: newest record per panel, and the
# "no issues" row only while no broken panel is recorded (see reports._LATEST)
_BLOCK_LATEST = """
    WITH eff AS (
        SELECT DISTINCT ON (tracker, COALESCE(panel, 0)) *
        FROM inspections
        WHERE site = %s AND block = %s AND work_date = %s
        ORDER BY tracker, COALESCE(panel, 0), inspected_at DESC, id DESC
    )
    SELECT i.tracker, i.panel, i.broken_panels, i.defects, i.notes, i.photo_keys,
           i.username, i.inspected_at, u.first_name, u.last_name
    FROM eff i
    LEFT JOIN users u ON u.username = i.username
    WHERE i.panel IS NOT NULL
       OR NOT EXISTS (SELECT 1 FROM eff e WHERE e.tracker = i.tracker AND e.panel IS NOT NULL)
    ORDER BY i.tracker, i.panel NULLS FIRST
"""


@router.get("/block-status")
def block_status(site: str, block: int, work_date: str | None = None, user: dict = Depends(require_user)):
    """Latest check of each tracker in one block for one day, with its broken panels."""
    if not 1 <= block <= config.BLOCKS:
        raise HTTPException(status_code=400, detail="Unknown block")
    rows = db.fetch_all(_BLOCK_LATEST, (site.strip().lower(), block, parse_day(work_date)))
    trackers: dict[int, dict] = {}
    for r in rows:
        t = trackers.setdefault(
            r["tracker"],
            {
                "tracker": r["tracker"],
                "broken_panels": 0,
                "username": r["username"],
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "inspected_at": r["inspected_at"],
                "notes": "",
                "photo_keys": [],
                "panels": [],
            },
        )
        t["broken_panels"] += r["broken_panels"]
        if r["inspected_at"] > t["inspected_at"]:
            t.update(username=r["username"], first_name=r["first_name"], last_name=r["last_name"], inspected_at=r["inspected_at"])
        if r["panel"] is None:
            t["notes"] = r["notes"]
            t["photo_keys"] = list(r["photo_keys"] or [])
        else:
            t["panels"].append(
                {
                    "panel": r["panel"],
                    "defects": list(r["defects"] or []),
                    "notes": r["notes"],
                    "photo_keys": list(r["photo_keys"] or []),
                }
            )
    return {"block": block, "trackers": list(trackers.values())}


@router.get("/mine")
def my_day(site: str, work_date: str | None = None, user: dict = Depends(require_user)):
    """The signed-in person's own totals and recent saves for the day."""
    day, site = parse_day(work_date), site.strip().lower()
    totals = db.fetch_one(
        """
        SELECT COUNT(DISTINCT batch_id) AS inspections,
               COUNT(DISTINCT (block, tracker)) AS trackers_checked,
               COUNT(DISTINCT (block, tracker)) FILTER (WHERE broken_panels > 0) AS issues,
               COALESCE(SUM(broken_panels), 0) AS broken_panels
        FROM inspections WHERE username = %s AND site = %s AND work_date = %s
        """,
        (user["username"], site, day),
    )
    recent = db.fetch_all(
        """
        SELECT batch_id, block, tracker, SUM(broken_panels) AS broken_panels, MAX(inspected_at) AS inspected_at
        FROM inspections
        WHERE username = %s AND site = %s AND work_date = %s
        GROUP BY batch_id, block, tracker
        ORDER BY MAX(inspected_at) DESC LIMIT 15
        """,
        (user["username"], site, day),
    )
    return {"date": day.isoformat(), **totals, "recent": recent}
