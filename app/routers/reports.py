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

# Rows that count for the day. A tracker can be submitted many times (one broken panel at a time).
# Each panel counts once (its newest record wins); a "no issues" row only counts while the
# tracker has no broken panel recorded that day.
_LATEST = """
    SELECT i.*
    FROM (
        SELECT DISTINCT ON (site, block, tracker, COALESCE(panel, 0)) *
        FROM inspections
        WHERE work_date = %(day)s AND (%(site)s::text IS NULL OR site = %(site)s)
        ORDER BY site, block, tracker, COALESCE(panel, 0), inspected_at DESC, id DESC
    ) i
    WHERE i.panel IS NOT NULL
       OR NOT EXISTS (
            SELECT 1 FROM inspections x
            WHERE x.site = i.site AND x.block = i.block AND x.tracker = i.tracker
              AND x.work_date = i.work_date AND x.panel IS NOT NULL)
"""


def _site(site: str | None) -> str | None:
    return site.strip().lower() if site and site.strip() and site != "all" else None


@router.get("/daily")
def daily_report(date: str | None = None, site: str | None = None):
    day, site = parse_day(date), _site(site)
    p = {"day": day, "site": site}

    sites = _all_sites()

    totals = db.fetch_one(
        f"""
        WITH latest AS ({_LATEST})
        SELECT COUNT(DISTINCT (site, block, tracker)) AS trackers_checked,
               COUNT(DISTINCT (site, block)) AS blocks_touched,
               COUNT(DISTINCT (site, block, tracker)) FILTER (WHERE broken_panels > 0) AS trackers_with_issues,
               COALESCE(SUM(broken_panels), 0) AS broken_panels
        FROM latest
        """,
        p,
    )
    n_inspections = db.fetch_one(
        "SELECT COUNT(DISTINCT batch_id) AS n FROM inspections WHERE work_date = %(day)s AND (%(site)s::text IS NULL OR site = %(site)s)", p
    )["n"]
    n_sites = 1 if site else max(len({r["site"] for r in db.fetch_all(
        "SELECT DISTINCT site FROM inspections WHERE work_date = %s", (day,))}), 1)

    per_block = {
        r["block"]: r
        for r in db.fetch_all(
            f"""
            WITH latest AS ({_LATEST})
            SELECT block, COUNT(DISTINCT (site, tracker)) AS trackers_checked,
                   COUNT(DISTINCT (site, tracker)) FILTER (WHERE broken_panels > 0) AS issues,
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
               COUNT(DISTINCT i.batch_id) AS inspections,
               COUNT(DISTINCT (i.site, i.block, i.tracker)) AS trackers_checked,
               COUNT(DISTINCT (i.site, i.block, i.tracker)) FILTER (WHERE i.broken_panels > 0) AS issues,
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
        SELECT l.id, l.site, l.block, l.tracker, l.panel, l.broken_panels, l.defects, l.notes, l.photo_keys,
               l.username, u.first_name, u.last_name, l.inspected_at
        FROM latest l LEFT JOIN users u ON u.username = l.username
        WHERE l.broken_panels > 0
        ORDER BY l.site, l.block, l.tracker, l.panel NULLS LAST
        """,
        p,
    )

    trackers = db.fetch_all(
        f"""
        WITH latest AS ({_LATEST})
        SELECT l.site, l.block, l.tracker, SUM(l.broken_panels) AS broken_panels,
               MAX(l.username) AS username, MAX(u.first_name) AS first_name, MAX(u.last_name) AS last_name,
               MAX(l.inspected_at) AS inspected_at
        FROM latest l LEFT JOIN users u ON u.username = l.username
        GROUP BY l.site, l.block, l.tracker
        ORDER BY l.site, l.block, l.tracker
        """,
        p,
    )

    defect_counts = db.fetch_all(
        f"""
        WITH latest AS ({_LATEST})
        SELECT d AS defect, COUNT(*) AS panels
        FROM latest, unnest(defects) AS d
        WHERE broken_panels > 0
        GROUP BY d ORDER BY panels DESC, d
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
        "trackers": trackers,
        "defect_counts": defect_counts,
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
               COUNT(DISTINCT i.batch_id) AS inspections,
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
        SELECT i.work_date, i.site, i.block, i.tracker, i.panel, i.broken_panels, i.defects, i.notes,
               i.username, u.first_name, u.last_name, i.inspected_at, i.photo_keys
        FROM inspections i LEFT JOIN users u ON u.username = i.username
        WHERE i.work_date = %(day)s AND (%(site)s::text IS NULL OR i.site = %(site)s)
        ORDER BY i.site, i.block, i.tracker, i.inspected_at, i.panel NULLS FIRST
        """,
        {"day": day, "site": site},
    )
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", "site", "block", "tracker", "panel", "broken_panels", "defects", "notes",
                "staff", "inspected_at", "photo_urls"])
    for r in rows:
        name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["username"]
        w.writerow([
            r["work_date"].isoformat(), r["site"], r["block"], r["tracker"], r["panel"] or "", r["broken_panels"],
            "; ".join(r["defects"] or []), r["notes"], name, r["inspected_at"].isoformat(),
            " ".join(f"{config.S3_PUBLIC_BASE}/{k}" for k in (r["photo_keys"] or [])),
        ])
    return Response(
        content="﻿" + out.getvalue(),  # BOM so Excel reads UTF-8 correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="inspections-{day.isoformat()}.csv"'},
    )


_REPAIR_SITE = "(%(site)s::text IS NULL OR r.site = %(site)s)"
_REPAIR_DAYS = "r.work_date BETWEEN %(start)s AND %(day)s"


def _all_sites() -> list[str]:
    """Every site with inspections or repairs, for the site picker."""
    return [r["site"] for r in db.fetch_all(
        "SELECT site FROM inspections UNION SELECT site FROM repairs ORDER BY site")]


def _repair_range(date: str | None, date_from: str | None):
    day = parse_day(date)
    start = parse_day(date_from) if date_from else day
    if start > day or (day - start).days > 366:
        raise HTTPException(status_code=400, detail="Choose a range of up to a year")
    return start, day


@router.get("/repairs")
def repairs_report(date: str | None = None, site: str | None = None, date_from: str | None = None):
    """Panels fixed or replaced, with before and after photos. One day, or date_from..date."""
    start, day = _repair_range(date, date_from)
    site = _site(site)
    p = {"start": start, "day": day, "site": site}
    where = f"{_REPAIR_DAYS} AND {_REPAIR_SITE}"

    totals = db.fetch_one(
        f"""
        SELECT COUNT(*) AS panels,
               COUNT(*) FILTER (WHERE r.action = 'fix') AS fixed,
               COUNT(*) FILTER (WHERE r.action = 'replace') AS replaced,
               COUNT(DISTINCT (r.site, r.block, r.tracker)) AS trackers,
               COUNT(DISTINCT (r.site, r.block)) AS blocks_touched,
               COUNT(DISTINCT r.work_date) AS days,
               COALESCE(SUM(cardinality(r.before_keys)), 0) AS before_photos,
               COALESCE(SUM(cardinality(r.after_keys)), 0) AS after_photos,
               COUNT(*) FILTER (WHERE cardinality(r.before_keys) > 0 AND cardinality(r.after_keys) > 0) AS with_both,
               MIN(r.repaired_at) AS first_at, MAX(r.repaired_at) AS last_at
        FROM repairs r WHERE {where}
        """,
        p,
    )
    blocks = db.fetch_all(
        f"""
        SELECT r.block,
               COUNT(*) AS panels,
               COUNT(*) FILTER (WHERE r.action = 'fix') AS fixed,
               COUNT(*) FILTER (WHERE r.action = 'replace') AS replaced,
               COUNT(DISTINCT (r.site, r.tracker)) AS trackers,
               COALESCE(SUM(cardinality(r.before_keys) + cardinality(r.after_keys)), 0) AS photos
        FROM repairs r WHERE {where}
        GROUP BY r.block ORDER BY r.block
        """,
        p,
    )
    staff = db.fetch_all(
        f"""
        SELECT r.username, u.first_name, u.last_name,
               COUNT(*) AS panels,
               COUNT(*) FILTER (WHERE r.action = 'fix') AS fixed,
               COUNT(*) FILTER (WHERE r.action = 'replace') AS replaced,
               MIN(r.repaired_at) AS first_at, MAX(r.repaired_at) AS last_at
        FROM repairs r LEFT JOIN users u ON u.username = r.username
        WHERE {where}
        GROUP BY r.username, u.first_name, u.last_name
        ORDER BY panels DESC, r.username
        """,
        p,
    )
    repairs = db.fetch_all(
        f"""
        SELECT r.id, r.site, r.block, r.tracker, r.panel, r.action, r.notes, r.before_keys, r.after_keys,
               r.username, u.first_name, u.last_name, r.repaired_at, r.work_date
        FROM repairs r LEFT JOIN users u ON u.username = r.username
        WHERE {where}
        ORDER BY r.site, r.block, r.tracker, r.panel, r.repaired_at
        """,
        p,
    )
    return {
        "serials": _serial_map(_photo_keys(repairs)),
        "date": day.isoformat(),
        "date_from": start.isoformat(),
        "site": site,
        "sites": _all_sites(),
        "photo_base": config.S3_PUBLIC_BASE,
        "totals": totals,
        "blocks": blocks,
        "staff": staff,
        "repairs": repairs,
    }


def _photo_keys(rows: list[dict]) -> list[str]:
    return [k for r in rows for k in [*(r["before_keys"] or []), *(r["after_keys"] or [])]]


def _serial_map(keys: list[str]) -> dict[str, dict]:
    """photo key -> {"status", "serial"} for photos whose panel serial has been recorded."""
    if not keys:
        return {}
    rows = db.fetch_all("SELECT photo_key, serial, status FROM photo_serials WHERE photo_key = ANY(%s)", (keys,))
    return {r["photo_key"]: {"status": r["status"], "serial": r["serial"]} for r in rows}


@router.get("/repairs.csv")
def repairs_csv(date: str | None = None, site: str | None = None, date_from: str | None = None):
    start, day = _repair_range(date, date_from)
    site = _site(site)
    rows = db.fetch_all(
        f"""
        SELECT r.work_date, r.site, r.block, r.tracker, r.panel, r.action, r.notes,
               r.username, u.first_name, u.last_name, r.repaired_at, r.before_keys, r.after_keys
        FROM repairs r LEFT JOIN users u ON u.username = r.username
        WHERE {_REPAIR_DAYS} AND {_REPAIR_SITE}
        ORDER BY r.site, r.block, r.tracker, r.panel
        """,
        {"start": start, "day": day, "site": site},
    )
    out = io.StringIO()
    w = csv.writer(out)
    found = _serial_map(_photo_keys(rows))

    def serial_of(keys):
        return " ".join(found[k]["serial"] for k in (keys or []) if found.get(k, {}).get("serial"))

    w.writerow(["date", "site", "block", "tracker", "panel", "action", "notes", "staff", "repaired_at",
                "old_panel_serial", "new_panel_serial", "before_photo_urls", "after_photo_urls"])
    for r in rows:
        name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r["username"]
        w.writerow([
            r["work_date"].isoformat(), r["site"], r["block"], r["tracker"], r["panel"],
            "Fixed" if r["action"] == "fix" else "Replaced", r["notes"], name, r["repaired_at"].isoformat(),
            serial_of(r["before_keys"]), serial_of(r["after_keys"]),
            " ".join(f"{config.S3_PUBLIC_BASE}/{k}" for k in (r["before_keys"] or [])),
            " ".join(f"{config.S3_PUBLIC_BASE}/{k}" for k in (r["after_keys"] or [])),
        ])
    return Response(
        content="\ufeff" + out.getvalue(),  # BOM so Excel reads UTF-8 correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="repairs-{start.isoformat() + "_to_" if start != day else ""}{day.isoformat()}.csv"'},
    )
