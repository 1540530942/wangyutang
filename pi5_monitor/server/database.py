"""
服务器侧数据库（SQLite）。
存储：heartbeats、events、shutdown_snapshots、offline_incidents。
"""
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/var/lib/pi5-monitor-server/monitor.db")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS heartbeats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT    NOT NULL,
            received_at REAL    NOT NULL DEFAULT (unixepoch('now')),
            payload     TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hb_device ON heartbeats(device_id, received_at);

        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT    NOT NULL,
            event_type  TEXT    NOT NULL,
            received_at REAL    NOT NULL DEFAULT (unixepoch('now')),
            payload     TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ev_device ON events(device_id, received_at);

        CREATE TABLE IF NOT EXISTS shutdown_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT    NOT NULL,
            received_at REAL    NOT NULL DEFAULT (unixepoch('now')),
            payload     TEXT    NOT NULL,
            analyzed    INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS offline_incidents (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id        TEXT    NOT NULL,
            detected_at      REAL    NOT NULL DEFAULT (unixepoch('now')),
            last_heartbeat   REAL,
            duration_s       REAL,
            came_back_at     REAL,
            cause_summary    TEXT,
            resolved         INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    c.commit()
    return c


class DB:
    def __init__(self):
        self._lock = threading.Lock()
        self._conn = _conn()

    # ── Heartbeats ──────────────────────────────────────────────────
    def save_heartbeat(self, device_id: str, payload: dict) -> int:
        raw = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO heartbeats (device_id, payload) VALUES (?, ?)",
                (device_id, raw),
            )
            self._conn.commit()
            return cur.lastrowid

    def last_heartbeat_time(self, device_id: str) -> float | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT received_at FROM heartbeats WHERE device_id=? ORDER BY received_at DESC LIMIT 1",
                (device_id,),
            ).fetchone()
        return row["received_at"] if row else None

    # ── Events ──────────────────────────────────────────────────────
    def save_events(self, device_id: str, events: list[dict]):
        with self._lock:
            self._conn.executemany(
                "INSERT INTO events (device_id, event_type, payload) VALUES (?, ?, ?)",
                [
                    (device_id, e.get("type", "unknown"), json.dumps(e, ensure_ascii=False))
                    for e in events
                ],
            )
            self._conn.commit()

    def get_events(
        self,
        device_id: str,
        since_ts: float | None = None,
        until_ts: float | None = None,
        event_types: list[str] | None = None,
        limit: int = 500,
    ) -> list[dict]:
        query = "SELECT * FROM events WHERE device_id=?"
        params: list = [device_id]
        if since_ts:
            query += " AND received_at >= ?"
            params.append(since_ts)
        if until_ts:
            query += " AND received_at <= ?"
            params.append(until_ts)
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            query += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        query += " ORDER BY received_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {"id": r["id"], "event_type": r["event_type"],
             "received_at": r["received_at"], **json.loads(r["payload"])}
            for r in rows
        ]

    # ── Shutdown Snapshots ───────────────────────────────────────────
    def save_shutdown_snapshot(self, device_id: str, payload: dict) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO shutdown_snapshots (device_id, payload) VALUES (?, ?)",
                (device_id, json.dumps(payload, ensure_ascii=False)),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_unanalyzed_snapshots(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM shutdown_snapshots WHERE analyzed=0 ORDER BY received_at"
            ).fetchall()
        return [
            {"id": r["id"], "device_id": r["device_id"],
             "received_at": r["received_at"], **json.loads(r["payload"])}
            for r in rows
        ]

    def mark_snapshot_analyzed(self, snapshot_id: int):
        with self._lock:
            self._conn.execute(
                "UPDATE shutdown_snapshots SET analyzed=1 WHERE id=?", (snapshot_id,)
            )
            self._conn.commit()

    # ── Offline Incidents ────────────────────────────────────────────
    def open_incident(self, device_id: str, last_heartbeat_ts: float | None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO offline_incidents (device_id, last_heartbeat) VALUES (?, ?)",
                (device_id, last_heartbeat_ts),
            )
            self._conn.commit()
            return cur.lastrowid

    def close_incident(self, incident_id: int, cause_summary: str):
        import time
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT detected_at FROM offline_incidents WHERE id=?", (incident_id,)
            ).fetchone()
            duration = now - row["detected_at"] if row else None
            self._conn.execute(
                """UPDATE offline_incidents
                   SET resolved=1, came_back_at=?, duration_s=?, cause_summary=?
                   WHERE id=?""",
                (now, duration, cause_summary, incident_id),
            )
            self._conn.commit()

    def get_incidents(self, device_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM offline_incidents WHERE device_id=?
                   ORDER BY detected_at DESC LIMIT ?""",
                (device_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
