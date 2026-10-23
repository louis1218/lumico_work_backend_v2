"""Admin-only progress reports."""
import csv
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .. import config, db
from ..auth import require_admin
from .inspections import parse_day, today_local

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_admin)])

# Latest check of each tracker on the day, so re-checking a tracker never double counts.
_LATEST = """
    SELECT DISTINCT ON (site, block, tracker) *
    FROM inspections
    WHERE work_date = %(day)s AND (%(site)s::text IS NULL OR site = %(site)s)
    ORDER BY site, block, tracker, inspected_at DESC
"""


def _site(site: str | None) -> str | None:
    return site.strip().lower() if site and site.strip() and site != "all" else None


@router.get("/daily")
def daily_report(date: str | None = None, site: str | None = None):
    day, site = parse_day(date), _site(site)
    p = {"day": day, "site": site}

    sites = [r["site"] for r in db.fetch_all("SELECT DISTINCT site FROM inspections ORDER BY site")]

    totals = db.fetch_one(
        f"""
        WITH latest AS ({_LATEST})
        SELECT COUNT(*) AS trackers_checked,
               COUNT(DISTINCT (site, block)) AS blocks_touched,
               COUNT(*) FILTER (WHERE broken_panels > 0) AS trackers_with_issues,
               COALESCE(SUM(broken_panels), 0) AS broken_panels
        FROM latest
        """,
        p,
    )
    n_inspections = db.fetch_one(
        "SELECT COUNT(*) AS n FROM inspections WHERE work_date = %(day)s AND (%(site)s::text IS NULL OR site = %(site)s)", p
    )["n"]
    n_sites = 1 if site else max(len({r["site"] for r in db.fetch_all(
        "SELECT DISTINCT site FROM inspections WHERE work_date = %s", (day,))}), 1)

    per_block = {
        r["block"]: r
        for r in db.fetch_all(
            f"""
            WITH latest AS ({_LATEST})
            SELECT block, COUNT(*) AS trackers_checked,
                   COUNT(*) FILTER (WHERE broken_panels > 0) AS issues,
                   COALESCE(SUM(broken_panels), 0) AS broken_panels
            FROM latest GROUP BY block
            """,
            p,
        )
    }
    blocks = [
        {
            "block": b,
            "trackers_checked": per_block.get(b, {}).get("trackers_checked", 0),
            "trackers_total": config.TRACKERS_PER_BLOCK * n_sites,
            "issues": per_block.get(b, {}).get("issues", 0),
            "broken_panels": per_block.get(b, {}).get("broken_panels", 0),
        }
        for b in range(1, config.BLOCKS + 1)
    ]

    staff = db.fetch_all(
        """
        SELECT i.username, u.first_name, u.last_name,
               COUNT(*) AS inspections,
               COUNT(DISTINCT (i.site, i.block, i.tracker)) AS trackers_checked,
               COUNT(*) FILTER (WHERE i.broken_panels > 0) AS issues,
               COALESCE(SUM(i.broken_panels), 0) AS broken_panels,
               MIN(i.inspected_at) AS first_at, MAX(i.inspected_at) AS last_at
        FROM inspections i LEFT JOIN users u ON u.username = i.username
        WHERE i.work_date = %(day)s AND (%(site)s::text IS NULL OR i.site = %(site)s)
        GROUP BY i.username, u.first_name, u.last_name
        ORDER BY trackers_checked DESC, i.username
        """,
        p,
    )

    issues = db.fetch_all(
        f"""
        WITH latest AS ({_LATEST})
        SELECT l.id, l.site, l.block, l.tracker, l.broken_panels, l.defects, l.notes, l.photo_keys,
               l.username, u.first_name, u.last_name, l.inspected_at
        FROM latest l LEFT JOIN users u ON u.username = l.username
        WHERE l.broken_panels > 0
        ORDER BY l.block, l.tracker
        """,
        p,
    )

    return {
        "date": day.isoformat(),
        "site": site,
        "sites": sites,
        "photo_base": config.S3_PUBLIC_BASE,
        "totals": {
            **totals,
            "inspections": n_inspections,
            "trackers_total": config.BLOCKS * config.TRACKERS_PER_BLOCK * n_sites,
        },
        "blocks": blocks,
        "staff": staff,
        "issues": issues,
    }


@router.get("/staff-progress")
def staff_progress(date_from: str | None = None, date_to: str | None = None, site: str | None = None):
    end = parse_day(date_to)
    start = parse_day(date_from) if date_from else end - timedelta(days=13)
    if start > end or (end - start).days > 62:
        raise HTTPException(status_code=400, detail="Choose a range of up to 63 days")
    rows = db.fetch_all(
        """
        SELECT i.work_date, i.username, u.first_name, u.last_name,
               COUNT(*) AS inspections,
               COUNT(DISTINCT (i.site, i.block, i.tracker)) AS trackers_checked,
               COALESCE(SUM(i.broken_panels), 0) AS broken_panels
        FROM inspections i LEFT JOIN users u ON u.username = i.username
        WHERE i.work_date BETWEEN %(start)s AND %(end)s AND (%(site)s::text IS NULL OR i.site = %(site)s)
        GROUP BY i.work_date, i.username, u.first_name, u.last_name
        ORDER BY i.work_date DESC, trackers_checked DESC
        """,
        {"start": start, "end": end, "site": _site(site)},
    )
    return {"from": start.isoformat(), "to": end.isoformat(), "rows": rows}


@router.get("/daily.csv")
def daily_csv(date: str | None = None, site: str | None = None):
    day, site = parse_day(date), _site(site)
    rows = db.fetch_all(
        """
        SELECT i.work_date, i.site, i.block, i.tracker, i.broken_panels, i.defects, i.notes,
               i.username, u.first_name, u.last_name, i.inspected_at, i.photo_keys
        FROM inspections i LEFT JOIN users u ON u.username = i.username
        WHERE i.work_date = %(day)s AND (%(site)s::text IS NULL OR i.site = %(site)s)
        ORDER BY i.site, i.block, i.tracker, i.inspected_at
        """,
        {"day": day, "site": site},
    )
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", "site", "block", "tracker", "broken_panels", "defects", "notes",
                "staff", "inspected_at", "photo_urls"])
    for r in rows:
        name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["username"]
        w.writerow([
            r["work_date"].isoformat(), r["site"], r["block"], r["tracker"], r["broken_panels"],
            "; ".join(r["defects"] or []), r["notes"], name, r["inspected_at"].isoformat(),
            " ".join(f"{config.S3_PUBLIC_BASE}/{k}" for k in (r["photo_keys"] or [])),
        ])
    return Response(
        content="﻿" + out.getvalue(),  # BOM so Excel reads UTF-8 correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="inspections-{day.isoformat()}.csv"'},
    )
