"""BUGFIX-07 建档初代：先造假绿夹具，再落失败测见红，最后修到全绿。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import app  # noqa: E402
from db import connect, init_db  # noqa: E402
import db as dbmod  # noqa: E402

SLOT = {
    "title": "新建档",
    "start_at": "2026-12-01T09:00:00",
    "end_at": "2026-12-01T11:00:00",
    "capacity": 5,
}


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    if db.exists():
        db.unlink()
    init_db()
    yield


def _login(c: TestClient, user: str = "admin") -> dict:
    r = c.post("/api/login", json={"username": user, "password": "123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def blank_generation_slot():
    """假绿夹具：复现“建档成功但代数为空”的现场（B-07 缺陷态）。

    直接落一条 generation=0 的档期，模拟旧建档写口的产物；
    在旧实现下整库仍可跑绿，缺陷因此被“假绿”掩盖。
    """
    with connect() as conn:
        conn.execute(
            """INSERT INTO slots(code,title,start_at,end_at,capacity,booked,status,generation)
               VALUES (?,?,?,?,?,0,'开放',0)""",
            ("S-BUG-07", "缺陷现场档", "2026-12-02T09:00:00", "2026-12-02T11:00:00", 5),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM slots WHERE code='S-BUG-07'").fetchone()
    return dict(row)


def test_false_green_blank_generation_fixture(blank_generation_slot):
    """假绿夹具本身成立：建档成功，但代数为空。"""
    assert int(blank_generation_slot["generation"] or 0) == 0


def test_create_slot_must_write_initial_generation():
    """红测一：建档无初代必须失败——建档须同时落下初代。"""
    with TestClient(app) as c:
        h = _login(c)
        r = c.post("/api/slots", headers=h, json={"code": "S-NEW-07", **SLOT})
        assert r.status_code == 200, r.text
        s = c.get("/api/slots/S-NEW-07", headers=h).json()
        assert s["generation"] == 1, "建档必须同时落下初代，不得为 0/空"
        # 后续占位必须挂得上代
        r2 = c.post(
            "/api/holds",
            headers=h,
            json={"slot_code": "S-NEW-07", "party_key": "P-GEN07", "seats": 1},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["generation"] == 1, "占位须带可挂代数"
        r3 = c.post(
            "/api/bookings/confirm", headers=h, json={"hold_id": r2.json()["hold_id"]}
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()["snapshot_gen"] == 1


def test_same_code_concurrent_create_later_only_contends():
    """红测二：两人同时建同名档——须占档锁，后到只记争用。"""
    with TestClient(app) as c:
        admin = _login(c, "admin")
        clerk = _login(c, "clerk")
        key = "slot:S-RACE-07"
        # 甲在建档事务中已持有档锁（模拟两人同时）
        r = c.post("/api/locks/acquire", headers=admin, json={"lock_key": key})
        assert r.status_code == 200, r.text
        r2 = c.post("/api/slots", headers=clerk, json={"code": "S-RACE-07", **SLOT})
        assert r2.status_code == 409, r2.text
        # 后到不写业务
        assert c.get("/api/slots/S-RACE-07", headers=admin).status_code == 404
        # 争用必须落盘（不是只抛错）
        cont = c.get("/api/contention", headers=admin).json()["items"]
        assert any(
            x["lock_key"] == key and x["actor"] == "clerk" and x["action"] == "争用"
            for x in cont
        ), "后到只记争用，且争用记录须落盘"


def _slot_count() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) c FROM slots").fetchone()["c"]


def test_create_failure_rolls_back_both_row_and_generation():
    """甲路径失败：档期行与代数同滚，两边零增。"""
    with TestClient(app) as c:
        h = _login(c)
        before = _slot_count()
        r = c.post(
            "/api/slots",
            headers=h,
            json={"code": "S-BAD-07", "title": "倒挂档", "end_at": "2026-12-03T09:00:00",
                  "start_at": "2026-12-03T11:00:00", "capacity": 5},
        )
        assert r.status_code == 400, r.text
        assert _slot_count() == before, "失败后档期行零增"
        assert c.get("/api/slots/S-BAD-07", headers=h).status_code == 404


def test_deviation_requests_rejected_and_recorded():
    """偏离建档初代卡：拒写并记偏。"""
    with TestClient(app) as c:
        h = _login(c)
        for body in (
            {"code": "S-DEV-1", **SLOT, "generation": 0},
            {"code": "S-DEV-2", **SLOT, "generation": 2},
            {"code": "S-DEV-3", **SLOT, "mode": "shadow"},
        ):
            r = c.post("/api/slots", headers=h, json=body)
            assert r.status_code == 409, r.text
            assert c.get("/api/slots/" + body["code"], headers=h).status_code == 404
        audits = c.get("/api/audit", headers=h, params={"limit": 100}).json()["items"]
        rejected = [a for a in audits if a["action"] == "deviation_reject"]
        assert {a["target"] for a in rejected} >= {"S-DEV-1", "S-DEV-2", "S-DEV-3"}


def test_blank_generation_slot_blocks_hold_and_confirm(blank_generation_slot):
    """缺代档期不得可挂代：占位与确认都拒。"""
    with TestClient(app) as c:
        h = _login(c)
        r = c.post(
            "/api/holds",
            headers=h,
            json={"slot_code": "S-BUG-07", "party_key": "P-BUG", "seats": 1},
        )
        assert r.status_code == 409, r.text
        # 旧库存量占位（绕开占位写口直接落库）也不得确认入册
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO holds(slot_id,party_key,seats,status,expire_at,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (blank_generation_slot["id"], "P-BUG-LEGACY", 1, "占位中",
                 "2026-12-31T23:59:59", "2026-11-22T00:00:00"),
            )
            conn.commit()
            hid = cur.lastrowid
        r2 = c.post("/api/bookings/confirm", headers=h, json={"hold_id": hid})
        assert r2.status_code == 409, r2.text
        # 候补晋升同样挂不上代
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO waitlist(slot_id,party_key,seats,reason,status,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (blank_generation_slot["id"], "P-BUG-WAIT", 1, "容量不足", "候补中",
                 "2026-11-22T00:00:00"),
            )
            conn.commit()
            wid = cur.lastrowid
        r3 = c.post("/api/waitlist/promote", headers=h, json={"wait_id": wid})
        assert r3.status_code == 409, r3.text


def test_five_surfaces_share_generation_state():
    """档期页/代数列/占位/对账/审计五处对同一档期的代数同态。"""
    with TestClient(app) as c:
        h = _login(c)
        c.post("/api/slots", headers=h, json={"code": "S-SYNC-07", **SLOT})
        r = c.post(
            "/api/holds",
            headers=h,
            json={"slot_code": "S-SYNC-07", "party_key": "P-SYNC", "seats": 1},
        )
        assert r.json()["generation"] == 1
        hid = r.json()["hold_id"]
        c.post("/api/bookings/confirm", headers=h, json={"hold_id": hid})

        slot = c.get("/api/slots/S-SYNC-07", headers=h).json()
        hold = [x for x in c.get("/api/holds", headers=h).json()["items"]
                if x["party_key"] == "P-SYNC"][0]
        rec = c.get("/api/reconcile/S-SYNC-07", headers=h).json()
        audits = c.get("/api/audit", headers=h, params={"limit": 100}).json()["items"]
        create_audit = [a for a in audits
                        if a["action"] == "slot_create" and a["target"] == "S-SYNC-07"][0]
        assert slot["generation"] == 1
        assert hold["generation"] == 1
        assert rec["generation"] == 1 and rec["gen_ok"] is True and rec["ok"] is True
        assert '"generation": 1' in create_audit["payload"]

