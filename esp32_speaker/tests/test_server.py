"""
ESP32 Speaker Server 测试

超时回归队首用例（test_timeout_reclaim）：
通过 monkeypatch 将 TIMEOUT_SECONDS 改为 1 秒，sleep 1.5 秒后
调用 _next_task，验证超时任务可被再次领取。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server as srv


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate each test with its own SQLite DB and token."""
    db_file = tmp_path / "speaker_queue.db"
    srv._init_db(db_file)

    token = "test-token-abc123"
    monkeypatch.setattr(srv, "DB_PATH", db_file)
    monkeypatch.setattr(srv, "_TOKEN", token)
    return db_file, token


@pytest.fixture()
def client(db):
    return TestClient(srv.app), db[0], db[1]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def auth(token: str) -> dict:
    return {"x-speaker-token": token}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_say_enqueue_next_result_204(client):
    c, db_file, token = client

    # say → enqueue
    r = c.post("/api/speaker/say", json={"text": "你好"}, headers=auth(token))
    assert r.status_code == 200
    data = r.json()
    assert "queue_id" in data
    queue_id = data["queue_id"]
    assert data["position"] == 1

    # next → get task with tts payload
    r = c.get("/api/speaker/next", headers=auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["queue_id"] == queue_id
    assert body["text"] == "你好"
    assert body["tts"]["url"] == srv.TTS_URL
    assert body["tts"]["payload"]["input"] == "你好"

    # result done
    r = c.post("/api/speaker/result",
               json={"queue_id": queue_id, "status": "done", "detail": ""},
               headers=auth(token))
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # next → 204 (nothing left)
    r = c.get("/api/speaker/next", headers=auth(token))
    assert r.status_code == 204


def test_no_token_401(client):
    c, _, _ = client
    assert c.post("/api/speaker/say", json={"text": "hi"}).status_code == 401
    assert c.get("/api/speaker/next").status_code == 401
    assert c.post("/api/speaker/result",
                  json={"queue_id": 1, "status": "done", "detail": ""}).status_code == 401


def test_wrong_token_401(client):
    c, _, _ = client
    assert c.post("/api/speaker/say",
                  json={"text": "hi"},
                  headers=auth("wrong-token")).status_code == 401


def test_text_truncated_to_100(client):
    c, db_file, token = client
    long_text = "あ" * 200
    r = c.post("/api/speaker/say", json={"text": long_text}, headers=auth(token))
    assert r.status_code == 200
    queue_id = r.json()["queue_id"]

    r = c.get("/api/speaker/next", headers=auth(token))
    assert r.status_code == 200
    assert len(r.json()["text"]) == 100


def test_fifo_order(client):
    c, db_file, token = client

    # Enqueue two tasks
    r1 = c.post("/api/speaker/say", json={"text": "第一条"}, headers=auth(token))
    r2 = c.post("/api/speaker/say", json={"text": "第二条"}, headers=auth(token))
    id1 = r1.json()["queue_id"]
    id2 = r2.json()["queue_id"]

    # Next should return the first one
    n1 = c.get("/api/speaker/next", headers=auth(token))
    assert n1.status_code == 200
    assert n1.json()["queue_id"] == id1
    assert n1.json()["text"] == "第一条"

    # Mark done so it's gone
    c.post("/api/speaker/result",
           json={"queue_id": id1, "status": "done", "detail": ""},
           headers=auth(token))

    # Next should return the second one
    n2 = c.get("/api/speaker/next", headers=auth(token))
    assert n2.status_code == 200
    assert n2.json()["queue_id"] == id2
    assert n2.json()["text"] == "第二条"


def test_timeout_reclaim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """超时任务（10分钟→1秒）可被再次领取。"""
    db_file = tmp_path / "speaker_queue.db"
    srv._init_db(db_file)

    # Enqueue a task
    import sqlite3
    now = time.time()
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.execute(
            "INSERT INTO speaker_queue (text, status, created_at, claimed_at) VALUES (?, 'claimed', ?, ?)",
            ("超时任务", now, now),
        )
        queue_id = cur.lastrowid
        conn.commit()

    # With normal timeout (600s) the task should NOT be reclaimed yet
    task = srv._next_task(db_path=db_file)
    assert task is None, "Task should not be reclaimed before timeout"

    # Simulate timeout by monkeypatching and sleeping
    monkeypatch.setattr(srv, "TIMEOUT_SECONDS", 1.0)
    time.sleep(1.5)

    task = srv._next_task(db_path=db_file)
    assert task is not None, "Timed-out task should be reclaimable"
    assert task["queue_id"] == queue_id
    assert task["text"] == "超时任务"
