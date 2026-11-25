"""End-to-end test against a REAL PostgreSQL (skipped unless PG_TEST_PORT is set).

Runs the real app, the real SQL and the limited lumico_app role, exactly like production.
  PG_TEST_PORT=54329 pytest tests/test_integration_pg.py
"""
import os
import pathlib

import bcrypt
import psycopg
import pytest

PORT = os.environ.get("PG_TEST_PORT")
pytestmark = pytest.mark.skipif(not PORT, reason="set PG_TEST_PORT to run against a real Postgres")

HERE = pathlib.Path(__file__).parent
TODAY = "2026-10-22"


@pytest.fixture(scope="module")
def client():
    admin = psycopg.connect(host="127.0.0.1", port=int(PORT), user="pgtest", dbname="postgres", autocommit=True)
    admin.execute("DROP DATABASE IF EXISTS lumico_test")
    admin.execute("DROP ROLE IF EXISTS lumico_app")
    admin.execute("CREATE DATABASE lumico_test ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'")  # UTF-8 like RDS
    su = psycopg.connect(host="127.0.0.1", port=int(PORT), user="pgtest", dbname="lumico_test", autocommit=True)
    su.execute((HERE / "legacy_schema.sql").read_text())
    su.execute((HERE.parent / "migrations" / "001_inspections.sql").read_text())
    su.execute((HERE.parent / "migrations" / "002_panels.sql").read_text())
    h = bcrypt.hashpw(b"pw", bcrypt.gensalt(4)).decode()
    for u, f, l, role in [("ann", "Ann", "Admin", "admin"), ("bob", "Bob", "Builder", "staff"), ("cy", "Cy", "Young", "staff")]:
        su.execute("INSERT INTO users (username, password_hash, first_name, last_name, role) VALUES (%s,%s,%s,%s,%s)",
                   (u, h, f, l, role))

    os.environ.update(DB_HOST="127.0.0.1", DB_PORT=PORT, DB_NAME="lumico_test", DB_USER="lumico_app",
                      DB_PASSWORD="testpw", DB_SSLMODE="disable")
    from app import config, db
    config.db_credentials.cache_clear()
    db._pool = None
    from fastapi.testclient import TestClient
    from app.main import app
    yield TestClient(app, raise_server_exceptions=False)
    if db._pool:
        db._pool.close()


def login(client, user):
    r = client.post("/auth/login", json={"username": user, "password": "pw", "site": "Test"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


def body(**kw):
    base = {"site": "test", "block": 3, "tracker": 7, "work_date": TODAY}
    return {**base, **kw}


def panel(n, defects=("Cracked glass",), photos=None, notes=""):
    return {"panel": n, "defects": list(defects), "notes": notes,
            "photo_keys": photos if photos is not None else [f"maint/b3/p{n}.jpg"]}


def test_full_flow(client):
    bob, cy, ann = login(client, "bob"), login(client, "cy"), login(client, "ann")

    # --- validation
    assert client.post("/inspections", json=body()).status_code == 401
    for bad in (body(block=26), body(block=0), body(tracker=181),
                body(panels="x"), body(panels=[panel(0)]), body(panels=[panel(91)]), body(panels=[panel("3")]),
                body(panels=[panel(5), panel(5)]),                       # same panel twice
                body(panels=[panel(5, photos=[])]),                      # photo required for a broken panel
                body(panels=[panel(5, defects=["Made up"])]),
                body(panels=[panel(5, photos=["../../etc/passwd"])]),
                body(broken_panels=2)):                                   # old app version
        assert client.post("/inspections", json=bad, headers=bob).status_code == 400, bad

    # --- bob: tracker 7 clean, then re-checked with two broken panels (latest must win); tracker 8 one panel
    r = client.post("/inspections", json=body(), headers=bob)
    assert r.status_code == 200 and r.json()["panels_saved"] == 0
    r = client.post("/inspections", json=body(panels=[
        panel(13, defects=["Damaged frame"]),
        panel(12, defects=["Cracked glass", "Hotspot or burn mark"], notes="east end", photos=["maint/b3/p12a.jpg", "maint/b3/p12b.jpg"]),
    ]), headers=bob)
    assert r.status_code == 200 and r.json()["panels_saved"] == 2, r.text
    assert client.post("/inspections", json=body(tracker=8, panels=[panel(5)]), headers=bob).status_code == 200
    # a second submission for the same tracker adds another broken panel (nothing is replaced)
    assert client.post("/inspections", json=body(tracker=8, panels=[panel(6)]), headers=bob).status_code == 200
    st8 = client.get("/inspections/block-status", params={"site": "test", "block": 3, "work_date": TODAY}, headers=bob).json()
    t8 = {t["tracker"]: t for t in st8["trackers"]}[8]
    assert [p["panel"] for p in t8["panels"]] == [5, 6] and t8["broken_panels"] == 2
    # --- cy: block 4 tracker 1 clean, block 3 tracker 9 clean (with an optional photo)
    assert client.post("/inspections", json=body(block=4, tracker=1), headers=cy).status_code == 200
    assert client.post("/inspections", json=body(tracker=9, photo_keys=["maint/b3/t9.jpg"], notes="all good"), headers=cy).status_code == 200
    # username comes from the token, not the body
    client.post("/inspections", json={**body(tracker=10), "username": "ann"}, headers=cy)

    # --- staff views
    st = client.get("/inspections/block-status", params={"site": "test", "block": 3, "work_date": TODAY}, headers=bob).json()
    by = {t["tracker"]: t for t in st["trackers"]}
    assert sorted(by) == [7, 8, 9, 10] and by[9]["username"] == "cy" and by[10]["username"] == "cy"
    assert by[7]["broken_panels"] == 2 and [p["panel"] for p in by[7]["panels"]] == [12, 13]
    assert by[7]["panels"][0]["photo_keys"] == ["maint/b3/p12a.jpg", "maint/b3/p12b.jpg"]
    assert by[9]["panels"] == [] and by[9]["photo_keys"] == ["maint/b3/t9.jpg"] and by[9]["notes"] == "all good"
    mine = client.get("/inspections/mine", params={"site": "test", "work_date": TODAY}, headers=bob).json()
    assert (mine["inspections"], mine["trackers_checked"], mine["issues"], mine["broken_panels"]) == (4, 2, 2, 4)
    opts = client.get("/inspections/options", headers=bob).json()
    assert opts["blocks"] == 25 and opts["panels_per_tracker"] == 360

    # --- admin gate
    assert client.get("/reports/daily", params={"date": TODAY}).status_code == 401
    assert client.get("/reports/daily", params={"date": TODAY}, headers=bob).status_code == 403
    assert client.get("/reports/daily.csv", params={"date": TODAY}, headers=bob).status_code == 403

    # --- the report
    rep = client.get("/reports/daily", params={"date": TODAY}, headers=ann)
    assert rep.status_code == 200, rep.text
    rep = rep.json()
    t = rep["totals"]
    # saves = 6 (bob 3, cy 3). Distinct trackers = b3t7, b3t8, b3t9, b3t10, b4t1 = 5
    assert (t["inspections"], t["trackers_checked"], t["blocks_touched"]) == (7, 5, 2)
    assert (t["trackers_with_issues"], t["broken_panels"], t["trackers_total"]) == (2, 4, 4500)
    assert len(rep["blocks"]) == 25
    b3 = rep["blocks"][2]
    assert (b3["block"], b3["trackers_checked"], b3["trackers_total"], b3["issues"], b3["broken_panels"]) == (3, 4, 300, 2, 4)
    assert rep["blocks"][0]["trackers_checked"] == 0 and rep["blocks"][3]["trackers_checked"] == 1
    staff = {s["username"]: s for s in rep["staff"]}
    assert staff["bob"]["inspections"] == 4 and staff["bob"]["trackers_checked"] == 2 and staff["bob"]["issues"] == 2
    assert staff["cy"]["trackers_checked"] == 3 and staff["bob"]["first_name"] == "Bob"
    assert [(i["block"], i["tracker"], i["panel"], i["broken_panels"]) for i in rep["issues"]] == [(3, 7, 12, 1), (3, 7, 13, 1), (3, 8, 5, 1), (3, 8, 6, 1)]
    assert rep["issues"][0]["photo_keys"] == ["maint/b3/p12a.jpg", "maint/b3/p12b.jpg"]
    assert "Cracked glass" in rep["issues"][0]["defects"] and rep["issues"][0]["notes"] == "east end"
    assert rep["sites"] == ["test"] and rep["photo_base"].startswith("https://")
    assert [(x["block"], x["tracker"], x["broken_panels"]) for x in rep["trackers"]] == [(3, 7, 2), (3, 8, 2), (3, 9, 0), (3, 10, 0), (4, 1, 0)]
    assert {d["defect"]: d["panels"] for d in rep["defect_counts"]} == {"Cracked glass": 3, "Damaged frame": 1, "Hotspot or burn mark": 1}

    # site filter + empty day
    assert client.get("/reports/daily", params={"date": TODAY, "site": "nowhere"}, headers=ann).json()["totals"]["trackers_checked"] == 0
    empty = client.get("/reports/daily", params={"date": "2020-01-01"}, headers=ann).json()
    assert empty["totals"]["trackers_checked"] == 0 and len(empty["blocks"]) == 25 and empty["issues"] == []
    assert empty["trackers"] == [] and empty["defect_counts"] == []

    # --- staff progress + csv
    sp = client.get("/reports/staff-progress", params={"date_to": TODAY}, headers=ann).json()
    assert {(r["username"], r["trackers_checked"], r["broken_panels"]) for r in sp["rows"]} == {("bob", 2, 4), ("cy", 3, 0)}
    assert client.get("/reports/staff-progress", params={"date_from": "2026-01-01", "date_to": TODAY}, headers=ann).status_code == 400
    csv_r = client.get("/reports/daily.csv", params={"date": TODAY}, headers=ann)
    assert csv_r.status_code == 200 and "attachment" in csv_r.headers["content-disposition"]
    lines = csv_r.text.lstrip("\ufeff").strip().splitlines()
    assert lines[0].startswith("date,site,block,tracker,panel") and len(lines) == 9   # header + 8 saved rows
    assert "Cracked glass; Hotspot or burn mark" in csv_r.text and "Bob Builder" in csv_r.text

    # --- logout kills the token
    sid = client.post("/auth/login", json={"username": "bob", "password": "pw"}).json()
    h2 = {"Authorization": f"Bearer {sid['session_token']}"}
    assert client.get("/inspections/options", headers=h2).status_code == 200
    assert client.post("/auth/logout", json={"session_id": sid["session_id"]}).status_code == 200
    assert client.get("/inspections/options", headers=h2).status_code == 401
    assert client.get("/inspections/options", headers={"Authorization": "Bearer nonsense"}).status_code == 401
