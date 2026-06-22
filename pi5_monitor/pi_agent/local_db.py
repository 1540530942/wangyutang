"""
本地 SQLite 缓冲数据库。
当网络不通时事件先存本地，网络恢复后批量上传。
"""
import json
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path("/var/lib/pi5-monitor/events.db")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.execute(
        """CREATE TABLE IF NOT EXISTS events (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            payload  TEXT    NOT NULL,
            uploaded INTEGER NOT NULL DEFAULT 0,
            created  REAL    NOT NULL DEFAULT (unixepoch('now'))
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS heartbeats (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            payload  TEXT    NOT NULL,
            uploaded INTEGER NOT NULL DEFAULT 0,
            created  REAL    NOT NULL DEFAULT (unixepoch('now'))
        )"""
    )
    c.commit()
    return c


class LocalDB:
    def __init__(self):
        self._lock = threading.Lock()
        self._conn = _conn()

    def save_event(self, event: dict) -> int:
        payload = json.dumps(event, ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (payload) VALUES (?)", (payload,)
            )
            self._conn.commit()
            return cur.lastrowid

    def save_heartbeat(self, heartbeat: dict) -> int:
        payload = json.dumps(heartbeat, ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO heartbeats (payload) VALUES (?)", (payload,)
            )
            self._conn.commit()
            return cur.lastrowid

    def get_pending_events(self, limit: int = 100) -> list[tuple[int, dict]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, payload FROM events WHERE uploaded=0 ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    def mark_uploaded(self, ids: list[int]):
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            self._conn.execute(
                f"UPDATE events SET uploaded=1 WHERE id IN ({placeholders})", ids
            )
            self._conn.commit()

    def count_pending(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE uploaded=0"
            ).fetchone()[0]
