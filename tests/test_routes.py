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
    assert client.get("/work-logs").status_code == 401                       # no header
    assert client.get("/work-logs", headers={"X-Session-Id": "abc"}).status_code == 401
    fake_db["fetch_one"] = None
    assert client.get("/work-logs", headers={"X-Session-Id": "5"}).status_code == 401  # unknown session
    assert client.get("/users").status_code == 200                           # login screen list stays public
    assert client.get("/health").status_code == 200


def test_unhandled_error_hides_details(fake_db):
    def boom(s, p=()): raise RuntimeError("secret detail")
    fake_db["fetch_all"] = None
    import app.db as d
    d.fetch_all = boom
    r = client.get("/users")
    assert r.status_code == 500 and r.json() == {"error": "Internal server error"}
