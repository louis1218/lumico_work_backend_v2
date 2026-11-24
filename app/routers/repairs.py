"""Panel repairs (fixed or replaced). Staff only (any signed-in user).

One save = one tracker, one or more panels. Each panel has an action (fix or replace),
at least one BEFORE photo and at least one AFTER photo.
"""
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import config, db
from ..auth import require_user
from .inspections import _batch_id, _client_time, _int, _keys, _text, parse_day

router = APIRouter(prefix="/repairs", tags=["repairs"])

ACTIONS = ("fix", "replace")


@router.post("")
def create_repair(payload: dict = Body(...), user: dict = Depends(require_user)):
    site = (payload.get("site") or "").strip().lower()
    if not site:
        raise HTTPException(status_code=400, detail="Missing site")
    block = _int(payload.get("block"), "block", 1, config.BLOCKS)
    tracker = _int(payload.get("tracker"), "tracker", 1, config.TRACKERS_PER_BLOCK)
    work_date = parse_day(payload.get("work_date"))

    panels = payload.get("panels")
    if not isinstance(panels, list) or not panels or len(panels) > config.PANELS_PER_TRACKER:
        raise HTTPException(status_code=400, detail="Add at least one panel")

    rows: list[tuple] = []
    seen: set[int] = set()
    for p in panels:
        if not isinstance(p, dict):
            raise HTTPException(status_code=400, detail="Invalid panels")
        n = _int(p.get("panel"), "panel", 1, config.PANELS_PER_TRACKER)
        if n in seen:
            raise HTTPException(status_code=400, detail=f"Panel {n} is listed twice")
        seen.add(n)
        action = p.get("action")
        if action not in ACTIONS:
            raise HTTPException(status_code=400, detail=f"Choose fixed or replaced for panel {n}")
        before = _keys(p.get("before_keys"), 10)
        after = _keys(p.get("after_keys"), 10)
        if not before:
            raise HTTPException(status_code=400, detail=f"Add a BEFORE photo for panel {n}")
        if not after:
            raise HTTPException(status_code=400, detail=f"Add an AFTER photo for panel {n}")
        rows.append((n, action, _text(p.get("notes")), before, after))
    rows.sort(key=lambda r: r[0])

    batch = _batch_id(payload.get("batch_id")) or str(uuid.uuid4())
    repaired_at = _client_time(payload.get("repaired_at"))

    # Already saved? (the phone retries after a lost reply). Answer as if it just saved.
    done = db.fetch_one(
        "SELECT COUNT(*) AS n, MAX(repaired_at) AS repaired_at FROM repairs WHERE batch_id = %s::uuid",
        (batch,),
    )
    if done and done["n"]:
        return {"message": "Repair saved", "batch_id": batch, "panels_saved": done["n"], "repaired_at": done["repaired_at"]}

    values = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, COALESCE(%s::timestamptz, now()))"] * len(rows))
    params: list = []
    for panel, action, notes, before, after in rows:
        params += [site, block, tracker, panel, action, user["username"], work_date, notes, before, after, batch, repaired_at]
    saved = db.fetch_all(
        f"""
        INSERT INTO repairs
            (site, block, tracker, panel, action, username, work_date, notes, before_keys, after_keys, batch_id, repaired_at)
        VALUES {values}
        RETURNING id, repaired_at
        """,
        params,
    )
    return {"message": "Repair saved", "batch_id": batch, "panels_saved": len(rows), "repaired_at": saved[0]["repaired_at"]}


@router.get("/mine")
def my_day(site: str, work_date: str | None = None, user: dict = Depends(require_user)):
    """The signed-in person's repair totals for the day."""
    day, site = parse_day(work_date), site.strip().lower()
    totals = db.fetch_one(
        """
        SELECT COUNT(*) AS panels,
               COUNT(*) FILTER (WHERE action = 'fix') AS fixed,
               COUNT(*) FILTER (WHERE action = 'replace') AS replaced,
               COUNT(DISTINCT (block, tracker)) AS trackers
        FROM repairs WHERE username = %s AND site = %s AND work_date = %s
        """,
        (user["username"], site, day),
    )
    return {"date": day.isoformat(), **totals}


@router.get("/block")
def block_repairs(site: str, block: int, user: dict = Depends(require_user)):
    """Every repair recorded in one block (newest first), so the form can show what is already done."""
    if not 1 <= block <= config.BLOCKS:
        raise HTTPException(status_code=400, detail="Unknown block")
    rows = db.fetch_all(
        """
        SELECT r.tracker, r.panel, r.action, r.notes, r.before_keys, r.after_keys,
               r.username, r.repaired_at, u.first_name, u.last_name
        FROM repairs r
        LEFT JOIN users u ON u.username = r.username
        WHERE r.site = %s AND r.block = %s
        ORDER BY r.tracker, r.panel, r.repaired_at DESC
        """,
        (site.strip().lower(), block),
    )
    return {
        "block": block,
        "repairs": [
            {
                "tracker": r["tracker"],
                "panel": r["panel"],
                "action": r["action"],
                "notes": r["notes"],
                "before_keys": list(r["before_keys"] or []),
                "after_keys": list(r["after_keys"] or []),
                "username": r["username"],
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "repaired_at": r["repaired_at"],
            }
            for r in rows
        ],
    }
