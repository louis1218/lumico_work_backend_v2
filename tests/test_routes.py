"""Route tests against a fake db layer (no real database needed)."""
import bcrypt
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app

client = TestClient(app, raise_server_exceptions=False)
HASH = bcrypt.hashpw(b"pw123", bcrypt.gensalt(4)).decode()
calls = []


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    calls.clear()
    state = {"fetch_one": None, "fetch_all": [], "execute": 1}

    def f_one(sql, params=()):
        calls.append((sql, tuple(params)))
        v = state["fetch_one"]
        return v(sql, params) if callable(v) else v

    monkeypatch.setattr(db, "fetch_one", f_one)
    monkeypatch.setattr(db, "run", f_one)
    monkeypatch.setattr(db, "fetch_all", lambda s, p=(): (calls.append((s, tuple(p))), state["fetch_all"])[1])
    monkeypatch.setattr(db, "execute", lambda s, p=(): (calls.append((s, tuple(p))), state["execute"])[1])
    return state


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_login_ok(fake_db):
    fake_db["fetch_one"] = lambda s, p: (
        {"password_hash": HASH, "first_name": "A", "last_name": "B", "role": "staff"}
        if "FROM users" in s else {"id": 7, "login_time": "2026-10-20T00:00:00Z"})
    r = client.post("/auth/login", json={"username": "a", "password": "pw123", "site": "X"})
    assert r.status_code == 200 and r.json()["session_id"] == 7 and r.json()["role"] == "staff"


def test_login_bad_password_and_unknown_user_same_message(fake_db):
    fake_db["fetch_one"] = {"password_hash": HASH, "first_name": "", "last_name": "", "role": ""}
    a = client.post("/auth/login", json={"username": "a", "password": "wrong"})
    fake_db["fetch_one"] = None
    b = client.post("/auth/login", json={"username": "zz", "password": "x"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_validation_and_error_shape():
    r = client.post("/auth/login", json={})
    assert r.status_code == 400 and "error" in r.json()
    assert client.post("/auth/login", content="notjson").status_code == 400


def test_logout_404(fake_db):
    fake_db["execute"] = 0
    assert client.post("/auth/logout", json={"session_id": 1}).status_code == 404


def test_users(fake_db):
    fake_db["fetch_all"] = [{"username": "a"}]
    assert client.get("/users").json() == [{"username": "a"}]


def test_timesheet_sign_in_twice_rejected(fake_db):
    fake_db["fetch_one"] = {"id": 1, "sign_in": "x", "sign_out": None}
    r = client.post("/timesheet", json={"username": "a", "site": "s", "type": "sign_in",
                                        "timestamp": "t", "work_date": "2026-10-20"})
    assert r.status_code == 400 and r.json()["error"] == "User already signed in today"


def test_timesheet_sign_out(fake_db):
    fake_db["fetch_one"] = lambda s, p: ({"id": 1, "sign_in": "x", "sign_out": None}
                                         if s.lstrip().startswith("SELECT") else {"id": 1, "sign_out": "t"})
    r = client.post("/timesheet", json={"username": "a", "site": "s", "type": "sign_out",
                                        "timestamp": "t", "work_date": "2026-10-20"})
    assert r.status_code == 200 and r.json()["record"]["sign_out"] == "t"


def test_timesheet_query_requires_date():
    assert client.post("/timesheet/query", json={"site": "s"}).status_code == 400


def test_work_logs_filters(fake_db):
    fake_db["fetch_one"] = {"n": 5}
    r = client.get("/work-logs", params={"taskType": "realignment", "site": "S", "date": "2026-10-20"})
    assert r.status_code == 200 and r.json() == {"logs": [], "totalTeamTasks": 5}
    sql, params = calls[0]
    assert "realignment_task_session" in sql and params == ("2026-10-20", "2026-10-20", "S")
    assert client.get("/work-logs", params={"taskType": "drop table"}).status_code == 400


def test_upload_session_merges_tasks(fake_db):
    fake_db["fetch_one"] = lambda s, p: ({"tasks": [{"task_name": "t1", "checked": False}]}
                                         if s.lstrip().startswith("SELECT") else {"id": 3})
    r = client.post("/sessions/upload", json={"trackerId": "T", "site": "S", "block": "B", "username": "u",
                                             "tasks": [{"task_name": "t1", "checked": True}, {"task_name": "t2"}]})
    assert r.status_code == 200 and r.json()["id"] == 3
    import json
    saved = json.loads(calls[-1][1][5])
    assert saved[0]["checked"] is True and saved[1]["task_name"] == "t2" and saved[1]["remark"] == "planned"


def test_realignment_save_and_get(fake_db):
    fake_db["fetch_one"] = {"id": 9}
    r = client.post("/sessions/realignment", json={
        "trackerId": "T", "block": "B", "site": "S", "username": "u", "last_updated": "2026-10-20T01:02:03.000Z",
        "imageFiles": [{"type": "before", "key": "k1"}, {"type": "after", "key": "k2"}]})
    assert r.status_code == 200 and r.json()["id"] == 9
    assert client.post("/sessions/realignment/get", json={"trackerId": "T"}).status_code == 400


def test_issue_and_barcode_and_solar(fake_db):
    fake_db["fetch_one"] = {"id": 1, "created_at": "now"}
    assert client.post("/issues", json={"site": 1, "task": 1, "block": 1, "tracker_id": 1,
                                        "report": "r", "username": "u"}).status_code == 200
    assert client.post("/barcode/scan", json={"serial": "s", "timestamp": "t", "site": "x", "username": "u"}).status_code == 200
    assert client.post("/work-logs/solar-farm", json={"trackerId": "T", "site": "S", "block": "B",
                                                     "timestamp": "t", "username": "u", "tasks": []}).status_code == 200


def test_auth_enforced(monkeypatch, fake_db):
    monkeypatch.setattr(config, "AUTH_ENFORCED", True)
    assert client.get("/work-logs").status_code == 401                                   # no token
    assert client.get("/work-logs", headers={"Authorization": "Basic abc"}).status_code == 401
    fake_db["fetch_one"] = None
    assert client.get("/work-logs", headers={"Authorization": "Bearer nope"}).status_code == 401  # unknown token
    assert client.get("/users").status_code == 200                                       # login screen list stays public
    assert client.get("/health").status_code == 200


def test_new_endpoints_always_require_login(monkeypatch, fake_db):
    monkeypatch.setattr(config, "AUTH_ENFORCED", False)   # even with the legacy switch off
    assert client.post("/inspections", json={}).status_code == 401
    assert client.get("/reports/daily").status_code == 401
    fake_db["fetch_one"] = {"id": 1, "username": "bob", "role": "staff", "first_name": "B", "last_name": "B"}
    h = {"Authorization": "Bearer t"}
    assert client.get("/reports/daily", headers=h).status_code == 403     # staff cannot read reports
    assert client.get("/reports/daily.csv", headers=h).status_code == 403


def test_unhandled_error_hides_details(fake_db):
    def boom(s, p=()): raise RuntimeError("secret detail")
    fake_db["fetch_all"] = None
    import app.db as d
    d.fetch_all = boom
    r = client.get("/users")
    assert r.status_code == 500 and r.json() == {"error": "Internal server error"}


def test_presign(monkeypatch):
    from app.routers import uploads

    class FakeS3:
        def generate_presigned_url(self, op, Params, ExpiresIn):
            assert op == "put_object" and Params["ContentType"] == "image/jpeg" and ExpiresIn == 300
            return "https://bucket.example/" + Params["Key"] + "?sig=1"

    monkeypatch.setattr(uploads, "_s3", lambda: FakeS3())
    r = client.get("/uploads/presign", params={"project": "lumico", "mode": "before",
                                               "filename": "../../etc/pass wd", "contentType": "image/jpeg"})
    assert r.status_code == 200
    key = r.json()["key"]
    assert key.startswith("lumico/before/") and key.endswith(".jpg") and ".." not in key and " " not in key
    assert r.json()["uploadURL"].startswith("https://bucket.example/lumico/before/")


def test_presign_rejects_non_images():
    r = client.get("/uploads/presign", params={"project": "p", "mode": "m", "filename": "f",
                                               "contentType": "text/html"})
    assert r.status_code == 400


def test_upload_photo_via_api(monkeypatch):
    import base64
    from app.routers import uploads
    put = {}

    class FakeS3:
        def put_object(self, **kw):
            put.update(kw)

    monkeypatch.setattr(uploads, "_s3", lambda: FakeS3())
    body = {"project": "inspection_lilyvale", "mode": "block3", "filename": "repair_t12_p7_before_ab",
            "contentType": "image/jpeg", "data": base64.b64encode(b"jpegbytes").decode()}
    r = client.post("/uploads/photo", json=body)
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert key.startswith("inspection_lilyvale/block3/repair_t12_p7_before_ab_") and key.endswith(".jpg")
    assert put["Key"] == key and put["Body"] == b"jpegbytes" and put["ContentType"] == "image/jpeg"


def test_upload_photo_rejects_bad_data():
    body = {"project": "p", "mode": "m", "filename": "f", "contentType": "image/jpeg", "data": "not base64!!"}
    assert client.post("/uploads/photo", json=body).status_code == 400
    body["contentType"] = "text/plain"
    assert client.post("/uploads/photo", json=body).status_code == 400


def test_repairs_report_range_and_photo_counts(fake_db):
    from app.auth import require_admin
    app.dependency_overrides[require_admin] = lambda: {"username": "boss", "role": "admin"}
    try:
        fake_db["fetch_one"] = {"panels": 2, "fixed": 1, "replaced": 1, "before_photos": 3, "after_photos": 2, "with_both": 2}
        fake_db["fetch_all"] = []
        r = client.get("/reports/repairs", params={"date": "2026-11-25", "date_from": "2026-11-20", "site": "manildra"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["date_from"] == "2026-11-20" and body["date"] == "2026-11-25" and body["site"] == "manildra"
        assert body["totals"]["before_photos"] == 3 and "blocks" in body and "sites" in body
        sql = " ".join(s for s, _ in calls)
        assert "BETWEEN" in sql and "cardinality" in sql and "UNION SELECT site FROM repairs" in sql
        assert client.get("/reports/repairs", params={"date": "2026-11-20", "date_from": "2026-11-25"}).status_code == 400
        assert client.get("/reports/repairs.csv", params={"date": "2026-11-25", "date_from": "2026-11-20"}).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_repairs_today_counts_all_staff(fake_db):
    from app.auth import require_user
    app.dependency_overrides[require_user] = lambda: {"username": "louisli", "role": "admin"}
    try:
        fake_db["fetch_one"] = {"panels": 29, "fixed": 1, "replaced": 28, "trackers": 18}
        r = client.get("/repairs/today", params={"site": "Manildra", "work_date": "2026-11-25"})
        assert r.status_code == 200, r.text
        assert r.json()["fixed"] == 1 and r.json()["replaced"] == 28
        sql, params = calls[-1]
        assert "username" not in sql and params[0] == "manildra"
    finally:
        app.dependency_overrides.clear()



def test_repairs_report_includes_recorded_serials(monkeypatch, fake_db):
    from app.auth import require_admin
    app.dependency_overrides[require_admin] = lambda: {"username": "boss", "role": "admin"}
    rows = [{"id": 1, "site": "manildra", "block": 3, "tracker": 12, "panel": 7, "action": "replace", "notes": "",
             "before_keys": ["b.jpg"], "after_keys": ["a.jpg"], "username": "u", "first_name": "A", "last_name": "B",
             "repaired_at": "2026-11-25T03:00:00+00:00", "work_date": "2026-11-25"}]

    def fetch_all(sql, params=()):
        if "FROM photo_serials" in sql:
            return [{"photo_key": "b.jpg", "serial": "170927110104", "status": "read"}]
        return rows if "FROM repairs r LEFT JOIN users" in sql and "r.id" in sql else []
    monkeypatch.setattr(db, "fetch_all", fetch_all)
    fake_db["fetch_one"] = {"panels": 1}
    try:
        r = client.get("/reports/repairs", params={"date": "2026-11-25", "site": "manildra"})
        assert r.status_code == 200, r.text
        assert r.json()["serials"] == {"b.jpg": {"status": "read", "serial": "170927110104"}}
        assert r.json()["panels_to_date"] == 1
    finally:
        app.dependency_overrides.clear()
