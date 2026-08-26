from __future__ import annotations

import asyncio
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "speaker_queue.db"
TOKEN_FILE = BASE_DIR / "speaker_token"

LONG_POLL_SECONDS = 30.0
LONG_POLL_TICK = 0.2
TIMEOUT_SECONDS = 600.0  # 10 minutes

TTS_URL = "https://www.wangyutang.cn/common/api/tts/speech"
TTS_MODEL = "qwen3-tts-12hz-1.7b-customvoice"
TTS_VOICE = "vivian"
TTS_INSTRUCTIONS = "用清新自然、甜美温柔的语气说，声音明亮亲切，语调轻快柔和"

app = FastAPI(title="ESP32 Speaker", version="1.0.0")


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _load_or_create_token() -> str:
    env_token = os.getenv("SPEAKER_TOKEN", "").strip()
    if env_token:
        return env_token
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    token = secrets.token_hex(32)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    print(f"[speaker] Generated token written to {TOKEN_FILE}")
    print(f"[speaker] Set SPEAKER_TOKEN={token} or read the file above.")
    return token


_TOKEN: str = ""


def _token() -> str:
    global _TOKEN
    if not _TOKEN:
        _TOKEN = _load_or_create_token()
    return _TOKEN


def require_token(x_speaker_token: Annotated[str | None, Header()] = None) -> None:
    expected = _token()
    if not expected:
        return
    if not x_speaker_token or not secrets.compare_digest(x_speaker_token, expected):
        raise HTTPException(status_code=401, detail="invalid speaker token")


# ---------------------------------------------------------------------------
# SQLite queue (WAL mode, FIFO)
# ---------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(db_path: Path | None = None) -> None:
    path = db_path or DB_PATH
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS speaker_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                text        TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pending',
                created_at  REAL    NOT NULL,
                claimed_at  REAL,
                detail      TEXT    DEFAULT ''
            )
        """)
        conn.commit()


_init_db()


def _enqueue(text: str) -> dict[str, Any]:
    now = time.time()
    with _db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.execute(
            "INSERT INTO speaker_queue (text, status, created_at) VALUES (?, 'pending', ?)",
            (text, now),
        )
        queue_id = cur.lastrowid
        position = conn.execute(
            "SELECT COUNT(*) FROM speaker_queue WHERE status='pending' AND id <= ?",
            (queue_id,),
        ).fetchone()[0]
        conn.commit()
    return {"queue_id": queue_id, "position": position}


def _next_task(db_path: Path | None = None) -> dict[str, Any] | None:
    """Claim the oldest pending task (or reclaim a timed-out task)."""
    path = db_path or DB_PATH
    now = time.time()
    timeout_threshold = now - TIMEOUT_SECONDS
    with sqlite3.connect(str(path), check_same_thread=False) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # reclaim timed-out tasks first, then normal pending
        row = conn.execute("""
            SELECT id, text FROM speaker_queue
            WHERE status = 'pending'
               OR (status = 'claimed' AND claimed_at < ?)
            ORDER BY id ASC
            LIMIT 1
        """, (timeout_threshold,)).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE speaker_queue SET status='claimed', claimed_at=? WHERE id=?",
            (now, row["id"]),
        )
        conn.commit()
    return {"queue_id": row["id"], "text": row["text"]}


def _mark_result(queue_id: int, status: str, detail: str, db_path: Path | None = None) -> bool:
    path = db_path or DB_PATH
    with sqlite3.connect(str(path), check_same_thread=False) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.execute(
            "UPDATE speaker_queue SET status=?, detail=? WHERE id=? AND status='claimed'",
            (status, detail, queue_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SayRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


class ResultRequest(BaseModel):
    queue_id: int
    status: str = Field(..., pattern="^(done|failed)$")
    detail: str = Field("", max_length=500)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.post("/api/speaker/say")
def say(
    payload: SayRequest,
    x_speaker_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    require_token(x_speaker_token)
    text = payload.text[:100]
    result = _enqueue(text)
    return result


@app.get("/api/speaker/next")
async def next_item(
    x_speaker_token: Annotated[str | None, Header()] = None,
) -> Response:
    require_token(x_speaker_token)
    deadline = time.monotonic() + LONG_POLL_SECONDS
    while True:
        task = _next_task()
        if task is not None:
            return JSONResponse({
                "queue_id": task["queue_id"],
                "text": task["text"],
                "tts": {
                    "url": TTS_URL,
                    "payload": {
                        "model": TTS_MODEL,
                        "input": task["text"],
                        "voice": TTS_VOICE,
                        "language": "chinese",
                        "instructions": TTS_INSTRUCTIONS,
                        "response_format": "wav",
                    },
                },
            })
        if time.monotonic() >= deadline:
            return Response(status_code=204)
        await asyncio.sleep(LONG_POLL_TICK)


@app.post("/api/speaker/result")
def result(
    payload: ResultRequest,
    x_speaker_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    require_token(x_speaker_token)
    ok = _mark_result(payload.queue_id, payload.status, payload.detail)
    if not ok:
        raise HTTPException(status_code=404, detail="task not found or not in claimed state")
    return {"ok": True, "queue_id": payload.queue_id, "status": payload.status}


@app.get("/api/health")
def health() -> dict[str, Any]:
    with _db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        pending = conn.execute("SELECT COUNT(*) FROM speaker_queue WHERE status='pending'").fetchone()[0]
        claimed = conn.execute("SELECT COUNT(*) FROM speaker_queue WHERE status='claimed'").fetchone()[0]
    return {"status": "ok", "pending": pending, "claimed": claimed}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.getenv("SPEAKER_HOST", "127.0.0.1")
    port = int(os.getenv("SPEAKER_PORT", "8937"))
    uvicorn.run("server:app", host=host, port=port, reload=False)
