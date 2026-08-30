"""device_hub — 设备终端注册/心跳/控制服务.

实现《设备 ↔ 平台 API 契约 v1》(见 API_CONTRACT.md)。设备侧仓库 1540530942/esp32_wifi
按契约上报;本服务是平台侧唯一实现。设计上与设备完全解耦:平台只认契约,用 device_type
区分设备种类,加新设备不改本文件。

路由说明:Caddy `handle_path /devices/*` 会剥掉 `/devices` 前缀,故本服务内部路由是
`/api/...` 与 `/`;对外即 `https://www.wangyutang.cn/devices/...`。

v1 从简:不做鉴权,所有接口开放。
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import secrets
import struct
import threading
import time
import urllib.request
import urllib.error
import wave
from pathlib import Path
from typing import Any, Optional

import paho.mqtt.client as _mqtt_lib
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DEVICES_FILE = DATA_DIR / "devices.json"
AUDIO_DIR = DATA_DIR / "audio"

DATA_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

TTS_URL = os.environ.get("TTS_URL", "http://audio-interact:8097/api/tts")
AUDIO_PUBLIC_BASE = os.environ.get("AUDIO_PUBLIC_BASE", "http://110.40.154.41/devices/api/audio")
UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # 20MB

MQTT_HOST = os.environ.get("MQTT_HOST", "172.19.0.1")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

# PCM 流参数: 24000Hz / 16-bit / mono / 20ms 帧
PCM_SAMPLE_RATE = 24000
PCM_FRAME_MS = 20
PCM_FRAME_BYTES = PCM_SAMPLE_RATE * 2 * PCM_FRAME_MS // 1000  # 960 bytes
PCM_STORE: dict[str, bytes] = {}       # stream_id -> PCM bytes
PCM_STORE_TTL: dict[str, float] = {}   # stream_id -> created_at (5min TTL)

# 契约常量
HEARTBEAT_INTERVAL_S = 5           # 建议心跳周期,注册回执下发给设备
OFFLINE_AFTER_S = 15               # 3× 心跳无上报 -> offline(容忍偶发丢包)
MAX_LOGS = 200                     # 每设备环形日志上限
MAX_COMMAND_HISTORY = 50           # 每设备已完成指令保留上限
KNOWN_ACTIONS = {"reboot", "set_volume", "identify", "ota"}
OFFLINE_ALERT_AFTER_S = 60    # 超过此时长无心跳 → 记录告警（4× OFFLINE_AFTER_S，过滤偶发断联）
ALERTS_FILE = DATA_DIR / "alerts.jsonl"
MAX_ALERTS = 500

DATA_LOCK = threading.Lock()
_alerted_offline: set[str] = set()   # 已告警的设备 id，防止重复；服务重启后重置

app = FastAPI(title="wangyutang device_hub", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# 存储:单一 JSON 文件 { device_id: record }。对齐平台其余服务的 data/*.json 习惯。
# ---------------------------------------------------------------------------
def _load() -> dict[str, Any]:
    if not DEVICES_FILE.exists():
        return {}
    try:
        return json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    tmp = DEVICES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DEVICES_FILE)


def _new_device(device_id: str, device_type: str, name: str) -> dict[str, Any]:
    now = time.time()
    return {
        "device_id": device_id,
        "device_type": device_type or "unknown",
        "name": name or device_id,
        "first_seen": now,
        "last_seen": now,
        "state": {},
        "logs": [],       # [{level, message, ts}] 最新在末尾
        "commands": [],   # [{id, action, args, status, created_at, dispatched_at, done_at, message}]
    }


def _is_online(record: dict[str, Any]) -> bool:
    return (time.time() - float(record.get("last_seen", 0))) <= OFFLINE_AFTER_S


def _public_view(record: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """转成前端友好的结构。full=True 附带日志与完整指令队列。"""
    view = {
        "device_id": record["device_id"],
        "device_type": record.get("device_type", "unknown"),
        "name": record.get("name") or record["device_id"],
        "online": _is_online(record),
        "first_seen": record.get("first_seen", 0),
        "last_seen": record.get("last_seen", 0),
        "state": record.get("state", {}),
    }
    if full:
        view["logs"] = record.get("logs", [])[-MAX_LOGS:]
        view["commands"] = record.get("commands", [])
    else:
        # 列表页只需要 state 里的常用键摘要,原样带过去由前端挑选
        view["pending_commands"] = sum(
            1 for c in record.get("commands", []) if c.get("status") == "pending"
        )
    return view


def _err(status: int, code: str, message: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": code, "message": message or code},
    )


# ---------------------------------------------------------------------------
# 离线告警基础设施
# ---------------------------------------------------------------------------
def _write_alert(alert: dict[str, Any]) -> None:
    try:
        line = json.dumps(alert, ensure_ascii=False) + "\n"
        with open(ALERTS_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        if ALERTS_FILE.stat().st_size > 200_000:
            lines = ALERTS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
            ALERTS_FILE.write_text("".join(lines[-MAX_ALERTS:]), encoding="utf-8")
    except OSError:
        pass


def _check_offline_alerts() -> None:
    """后台线程：每 10s 扫一次，设备离线超 OFFLINE_ALERT_AFTER_S 时记录告警。"""
    while True:
        time.sleep(10)
        alerts_to_write: list[dict[str, Any]] = []
        with DATA_LOCK:
            data = _load()
            now = time.time()
            changed = False
            for device_id, rec in data.items():
                last_seen = float(rec.get("last_seen", 0))
                gone_for = now - last_seen
                online = gone_for <= OFFLINE_AFTER_S
                if online and device_id in _alerted_offline:
                    _alerted_offline.discard(device_id)
                elif not online and gone_for > OFFLINE_ALERT_AFTER_S and device_id not in _alerted_offline:
                    _alerted_offline.add(device_id)
                    alerts_to_write.append({
                        "ts": now,
                        "device_id": device_id,
                        "device_name": rec.get("name", device_id),
                        "event": "offline",
                        "gone_for_s": int(gone_for),
                    })
                    logs = rec.setdefault("logs", [])
                    logs.append({"level": "warn", "message": f"离线已 {int(gone_for)}s，告警已记录", "ts": now})
                    del logs[:-MAX_LOGS]
                    changed = True
            if changed:
                _save(data)
        for alert in alerts_to_write:
            _write_alert(alert)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class RegisterReq(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=80)
    device_type: str = Field("unknown", max_length=40)
    name: str = Field("", max_length=80)
    state: dict[str, Any] = Field(default_factory=dict)


class HeartbeatReq(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=80)
    state: dict[str, Any] = Field(default_factory=dict)


class AckReq(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=80)
    command_id: str = Field(..., min_length=1, max_length=40)
    status: str = Field("done", max_length=20)   # done | failed | unsupported
    message: str = Field("", max_length=500)


class LogReq(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=80)
    level: str = Field("info", max_length=10)     # debug | info | warn | error
    message: str = Field("", max_length=500)
    ts: float = 0.0


class CommandReq(BaseModel):
    action: str = Field(..., min_length=1, max_length=40)
    args: dict[str, Any] = Field(default_factory=dict)


class BatchCommandReq(BaseModel):
    device_ids: list[str] | None = Field(None, description="None = 所有已注册设备")
    action: str = Field(..., min_length=1, max_length=40)
    args: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 设备 -> 平台
# ---------------------------------------------------------------------------
@app.post("/api/register")
def register(req: RegisterReq) -> Any:
    with DATA_LOCK:
        data = _load()
        rec = data.get(req.device_id)
        if rec is None:
            rec = _new_device(req.device_id, req.device_type, req.name)
        else:
            # 幂等:更新登记信息
            if req.device_type and req.device_type != "unknown":
                rec["device_type"] = req.device_type
            if req.name:
                rec["name"] = req.name
        rec["last_seen"] = time.time()
        if req.state:
            rec.setdefault("state", {}).update(req.state)
        data[req.device_id] = rec
        _save(data)
    return {
        "ok": True,
        "device_id": req.device_id,
        "server_time": int(time.time()),
        "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
        "config": {},
    }


@app.post("/api/heartbeat")
def heartbeat(req: HeartbeatReq) -> Any:
    with DATA_LOCK:
        data = _load()
        rec = data.get(req.device_id)
        if rec is None:
            # 未注册也宽容接纳:自动建档(设备可能重启后直接心跳)
            rec = _new_device(req.device_id, "unknown", "")
        rec["last_seen"] = time.time()
        if req.state:
            rec.setdefault("state", {}).update(req.state)

        # 取出待下发指令(status==pending),标记为 dispatched 避免每次心跳重复下发
        dispatch: list[dict[str, Any]] = []
        for c in rec.get("commands", []):
            if c.get("status") == "pending":
                c["status"] = "dispatched"
                c["dispatched_at"] = time.time()
                dispatch.append({"command_id": c["id"], "id": c["id"], "action": c["action"], "args": c.get("args", {}), "payload": c.get("args", {})})

        data[req.device_id] = rec
        _save(data)
    return {"ok": True, "server_time": int(time.time()), "commands": dispatch}


@app.post("/api/ack")
def ack(req: AckReq) -> Any:
    with DATA_LOCK:
        data = _load()
        rec = data.get(req.device_id)
        if rec is None:
            return _err(404, "not_found", "unknown device_id")
        found = False
        for c in rec.get("commands", []):
            if c.get("id") == req.command_id:
                c["status"] = req.status if req.status in {"done", "failed", "unsupported"} else "done"
                c["message"] = req.message
                c["done_at"] = time.time()
                found = True
                break
        if not found:
            return _err(404, "not_found", "unknown command_id")
        # 修剪已完成指令历史
        done = [c for c in rec["commands"] if c.get("status") in {"done", "failed", "unsupported"}]
        active = [c for c in rec["commands"] if c.get("status") in {"pending", "dispatched"}]
        rec["commands"] = active + done[-MAX_COMMAND_HISTORY:]
        data[req.device_id] = rec
        _save(data)
    return {"ok": True}


@app.post("/api/log")
def log(req: LogReq) -> Any:
    with DATA_LOCK:
        data = _load()
        rec = data.get(req.device_id)
        if rec is None:
            rec = _new_device(req.device_id, "unknown", "")
        entry = {
            "level": req.level if req.level in {"debug", "info", "warn", "error"} else "info",
            "message": req.message,
            "ts": req.ts or time.time(),
        }
        logs = rec.setdefault("logs", [])
        logs.append(entry)
        del logs[:-MAX_LOGS]  # 环形:仅保留最近 MAX_LOGS 条
        rec["last_seen"] = time.time()
        data[req.device_id] = rec
        _save(data)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 前端 -> 平台
# ---------------------------------------------------------------------------
@app.get("/api/list")
def list_devices() -> Any:
    with DATA_LOCK:
        data = _load()
    devices = [_public_view(r) for r in data.values()]
    devices.sort(key=lambda d: (not d["online"], -d["last_seen"]))
    return {"ok": True, "devices": devices, "server_time": int(time.time())}


@app.get("/api/device/{device_id}")
def get_device(device_id: str) -> Any:
    with DATA_LOCK:
        data = _load()
        rec = data.get(device_id)
    if rec is None:
        return _err(404, "not_found", "unknown device_id")
    return {"ok": True, "device": _public_view(rec, full=True), "server_time": int(time.time())}


@app.post("/api/device/{device_id}/command")
def enqueue_command(device_id: str, req: CommandReq) -> Any:
    with DATA_LOCK:
        data = _load()
        rec = data.get(device_id)
        if rec is None:
            return _err(404, "not_found", "unknown device_id")
        command_id = "c-" + secrets.token_hex(3)
        rec.setdefault("commands", []).append({
            "id": command_id,
            "action": req.action,
            "args": req.args,
            "status": "pending",
            "created_at": time.time(),
            "dispatched_at": 0.0,
            "done_at": 0.0,
            "message": "",
        })
        data[device_id] = rec
        _save(data)
    known = req.action in KNOWN_ACTIONS
    return {"ok": True, "command_id": command_id, "known_action": known}


class SpeakReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    volume: Optional[int] = Field(None, ge=0, le=100)


@app.post("/api/device/{device_id}/speak")
def speak(device_id: str, req: SpeakReq) -> Any:
    """文本 → TTS WAV → 保存 → 下发 play_audio 给设备。"""
    with DATA_LOCK:
        data = _load()
        if device_id not in data:
            return _err(404, "not_found", "unknown device_id")

    try:
        body = json.dumps({"text": req.text}).encode()
        tts_req = urllib.request.Request(
            TTS_URL, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(tts_req, timeout=30) as resp:
            wav_bytes = resp.read()
    except urllib.error.URLError as e:
        return _err(502, "tts_error", f"TTS 服务不可达: {e}")

    filename = secrets.token_hex(8) + ".wav"
    (AUDIO_DIR / filename).write_bytes(wav_bytes)

    audio_url = f"{AUDIO_PUBLIC_BASE}/{filename}"
    with DATA_LOCK:
        data = _load()
        rec = data.get(device_id)
        if rec is None:
            return _err(404, "not_found", "unknown device_id")
        command_id = "c-" + secrets.token_hex(3)
        rec.setdefault("commands", []).append({
            "id": command_id,
            "action": "play_audio",
            "args": {"url": audio_url},
            "status": "pending",
            "created_at": time.time(),
            "dispatched_at": 0.0,
            "done_at": 0.0,
            "message": "",
        })
        data[device_id] = rec
        _save(data)
    return {"ok": True, "command_id": command_id, "audio_url": audio_url}


@app.post("/api/device/{device_id}/upload_audio")
async def upload_audio(device_id: str, file: UploadFile = File(...)) -> Any:
    """上传音频文件 → 保存 → 下发 play_audio 给设备。"""
    with DATA_LOCK:
        data = _load()
        if device_id not in data:
            return _err(404, "not_found", "unknown device_id")

    content = await file.read(UPLOAD_MAX_BYTES + 1)
    if len(content) > UPLOAD_MAX_BYTES:
        return _err(413, "too_large", f"文件超过 {UPLOAD_MAX_BYTES // 1024 // 1024}MB 限制")

    suffix = Path(file.filename or "audio.wav").suffix.lower() or ".wav"
    filename = secrets.token_hex(8) + suffix
    (AUDIO_DIR / filename).write_bytes(content)

    audio_url = f"{AUDIO_PUBLIC_BASE}/{filename}"
    with DATA_LOCK:
        data = _load()
        rec = data.get(device_id)
        if rec is None:
            return _err(404, "not_found", "unknown device_id")
        command_id = "c-" + secrets.token_hex(3)
        rec.setdefault("commands", []).append({
            "id": command_id,
            "action": "play_audio",
            "args": {"url": audio_url},
            "status": "pending",
            "created_at": time.time(),
            "dispatched_at": 0.0,
            "done_at": 0.0,
            "message": "",
        })
        data[device_id] = rec
        _save(data)
    return {"ok": True, "command_id": command_id, "audio_url": audio_url}


@app.get("/api/audio/{filename}")
def serve_audio(filename: str) -> FileResponse:
    """提供 TTS 生成或上传的音频文件（供 ESP32 下载）。"""
    path = AUDIO_DIR / filename
    if not path.exists() or path.parent != AUDIO_DIR:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/batch_command")
def batch_command(req: BatchCommandReq) -> Any:
    """向多台（或全部）设备同时下发同一指令。"""
    with DATA_LOCK:
        data = _load()
        targets = list(data.keys()) if req.device_ids is None else req.device_ids
        results: dict[str, Any] = {}
        now = time.time()
        for device_id in targets:
            rec = data.get(device_id)
            if rec is None:
                results[device_id] = {"ok": False, "error": "not_found"}
                continue
            command_id = "c-" + secrets.token_hex(3)
            rec.setdefault("commands", []).append({
                "id": command_id,
                "action": req.action,
                "args": req.args,
                "status": "pending",
                "created_at": now,
                "dispatched_at": 0.0,
                "done_at": 0.0,
                "message": "",
            })
            results[device_id] = {"ok": True, "command_id": command_id}
        _save(data)
    return {"ok": True, "known_action": req.action in KNOWN_ACTIONS,
            "total": len(targets), "results": results}


@app.get("/api/alerts")
def get_alerts(limit: int = 50) -> Any:
    """返回最近的离线告警，按时间倒序。前端每 5s 轮询。"""
    if not ALERTS_FILE.exists():
        return {"ok": True, "alerts": []}
    try:
        lines = ALERTS_FILE.read_text(encoding="utf-8").splitlines()
        alerts: list[dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                alerts.append(json.loads(line))
            except json.JSONDecodeError:
                pass
            if len(alerts) >= limit:
                break
        return {"ok": True, "alerts": alerts}
    except OSError:
        return {"ok": True, "alerts": []}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "device-hub"}


# ---------------------------------------------------------------------------
# MQTT 集成
# ---------------------------------------------------------------------------
_mqtt_client: "_mqtt_lib.Client | None" = None


def _mqtt_process_ack(device_id: str, payload: dict) -> None:
    command_id = payload.get("command_id") or payload.get("id", "")
    status = payload.get("status", "done")
    message = str(payload.get("message", ""))
    if not command_id:
        return
    with DATA_LOCK:
        data = _load()
        rec = data.get(device_id)
        if rec is None:
            return
        for c in rec.get("commands", []):
            if c.get("id") == command_id:
                c["status"] = status if status in {"done", "failed", "unsupported"} else "done"
                c["message"] = message
                c["done_at"] = time.time()
                break
        done = [c for c in rec["commands"] if c.get("status") in {"done", "failed", "unsupported"}]
        active = [c for c in rec["commands"] if c.get("status") in {"pending", "dispatched"}]
        rec["commands"] = active + done[-MAX_COMMAND_HISTORY:]
        data[device_id] = rec
        _save(data)


def _mqtt_process_state(device_id: str, payload: dict) -> None:
    with DATA_LOCK:
        data = _load()
        rec = data.get(device_id)
        if rec is None:
            rec = _new_device(device_id, "unknown", "")
        rec["last_seen"] = time.time()
        rec.setdefault("state", {}).update(payload)
        data[device_id] = rec
        _save(data)


def _mqtt_process_log(device_id: str, payload: dict) -> None:
    with DATA_LOCK:
        data = _load()
        rec = data.get(device_id)
        if rec is None:
            rec = _new_device(device_id, "unknown", "")
        entry = {
            "level": payload.get("level", "info"),
            "message": str(payload.get("message", "")),
            "ts": float(payload.get("ts", time.time())),
        }
        logs = rec.setdefault("logs", [])
        logs.append(entry)
        del logs[:-MAX_LOGS]
        rec["last_seen"] = time.time()
        data[device_id] = rec
        _save(data)


def _on_mqtt_connect(client: Any, userdata: Any, flags: Any, rc: Any, props: Any = None) -> None:
    client.subscribe("devices/+/ack")
    client.subscribe("devices/+/state")
    client.subscribe("devices/+/log")
    client.subscribe("devices/+/presence")


def _on_mqtt_message(client: Any, userdata: Any, msg: Any) -> None:
    parts = msg.topic.split("/")
    if len(parts) < 3 or parts[0] != "devices":
        return
    device_id, msg_type = parts[1], parts[2]
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return
    if msg_type == "ack":
        _mqtt_process_ack(device_id, payload)
    elif msg_type in ("state", "presence"):
        _mqtt_process_state(device_id, payload)
    elif msg_type == "log":
        _mqtt_process_log(device_id, payload)


def _mqtt_publish(topic: str, payload: dict) -> None:
    if _mqtt_client is not None and _mqtt_client.is_connected():
        _mqtt_client.publish(topic, json.dumps(payload, ensure_ascii=False))


def _start_mqtt_thread() -> None:
    global _mqtt_client
    try:
        _mqtt_client = _mqtt_lib.Client(_mqtt_lib.CallbackAPIVersion.VERSION2)
        _mqtt_client.on_connect = _on_mqtt_connect
        _mqtt_client.on_message = _on_mqtt_message
        _mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        _mqtt_client.loop_forever()
    except Exception:
        pass  # MQTT 不可用时 HTTP heartbeat 继续工作


# ---------------------------------------------------------------------------
# PCM 工具
# ---------------------------------------------------------------------------
def _wav_to_pcm_s16le_24k(wav_bytes: bytes) -> bytes:
    """WAV → PCM s16le / 24000Hz / mono，用 audioop 重采样。"""
    import audioop
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    if nchannels == 2:
        pcm = audioop.tomono(pcm, sampwidth, 0.5, 0.5)
    if sampwidth != 2:
        pcm = audioop.lin2lin(pcm, sampwidth, 2)
    if framerate != PCM_SAMPLE_RATE:
        pcm, _ = audioop.ratecv(pcm, 2, 1, framerate, PCM_SAMPLE_RATE, None)
    return pcm


def _pcm_store_put(stream_id: str, pcm: bytes) -> None:
    PCM_STORE[stream_id] = pcm
    PCM_STORE_TTL[stream_id] = time.time()
    now = time.time()
    expired = [sid for sid, t in list(PCM_STORE_TTL.items()) if now - t > 300]
    for sid in expired:
        PCM_STORE.pop(sid, None)
        PCM_STORE_TTL.pop(sid, None)


# ---------------------------------------------------------------------------
# WebSocket PCM 流端点（ESP32 作为客户端连入）
# ---------------------------------------------------------------------------
@app.websocket("/ws/pcm/{device_id}")
async def ws_pcm_stream(websocket: WebSocket, device_id: str, stream_id: str = "") -> None:
    await websocket.accept()
    if not stream_id or stream_id not in PCM_STORE:
        await websocket.send_text(json.dumps({"event": "error", "message": f"unknown stream_id: {stream_id}"}))
        await websocket.close()
        return
    pcm = PCM_STORE[stream_id]
    total_bytes = len(pcm)
    sent_bytes = 0
    frames_sent = 0
    first_frame_ms: int = 0
    t0 = time.time()
    try:
        await websocket.send_text(json.dumps({
            "event": "stream_start",
            "stream_id": stream_id,
            "device_id": device_id,
            "total_bytes": total_bytes,
            "audio": {"codec": "pcm_s16le", "sample_rate": PCM_SAMPLE_RATE,
                      "channels": 1, "frame_ms": PCM_FRAME_MS},
            "server_time": time.time(),
        }))
        offset = 0
        while offset < len(pcm):
            chunk = pcm[offset: offset + PCM_FRAME_BYTES]
            await websocket.send_bytes(chunk)
            if frames_sent == 0:
                first_frame_ms = int((time.time() - t0) * 1000)
            offset += len(chunk)
            sent_bytes += len(chunk)
            frames_sent += 1
            await asyncio.sleep(0)
        elapsed = int((time.time() - t0) * 1000)
        await websocket.send_text(json.dumps({
            "event": "stream_end",
            "stream_id": stream_id,
            "sent_bytes": sent_bytes,
            "frames_sent": frames_sent,
            "first_frame_ms": first_frame_ms,
            "send_ms": elapsed,
            "elapsed_ms": elapsed,
        }))
    except WebSocketDisconnect:
        pass
    finally:
        PCM_STORE.pop(stream_id, None)
        PCM_STORE_TTL.pop(stream_id, None)


# ---------------------------------------------------------------------------
# speak_pcm: TTS → PCM → MQTT stream_prepare + WebSocket 流
# ---------------------------------------------------------------------------
class SpeakPcmReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    volume: Optional[int] = Field(None, ge=0, le=100)


@app.post("/api/device/{device_id}/speak_pcm")
def speak_pcm(device_id: str, req: SpeakPcmReq) -> Any:
    """文本 → TTS WAV → PCM → MQTT stream_prepare + WebSocket 流。"""
    with DATA_LOCK:
        data = _load()
        if device_id not in data:
            return _err(404, "not_found", "unknown device_id")

    try:
        body = json.dumps({"text": req.text}).encode()
        tts_req = urllib.request.Request(
            TTS_URL, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(tts_req, timeout=30) as resp:
            wav_bytes = resp.read()
    except urllib.error.URLError as e:
        return _err(502, "tts_error", f"TTS 服务不可达: {e}")

    try:
        pcm = _wav_to_pcm_s16le_24k(wav_bytes)
    except Exception as e:
        return _err(500, "pcm_convert_error", str(e))

    stream_id = "s-" + secrets.token_hex(4)
    command_id = "c-" + secrets.token_hex(3)
    ws_url = f"wss://www.wangyutang.cn/devices/ws/pcm/{device_id}?stream_id={stream_id}"
    _pcm_store_put(stream_id, pcm)

    mqtt_cmd = {
        "command_id": command_id,
        "action": "stream_prepare",
        "stream_id": stream_id,
        "transport": "websocket",
        "stream_url": ws_url,
        "audio": {"codec": "pcm_s16le", "sample_rate": PCM_SAMPLE_RATE,
                  "channels": 1, "frame_ms": PCM_FRAME_MS},
        "volume": req.volume or 80,
        "text": req.text,
        "published_at": time.time(),
    }
    now = time.time()
    mqtt_ok = False
    with DATA_LOCK:
        data = _load()
        rec = data.get(device_id)
        if rec is None:
            return _err(404, "not_found", "unknown device_id")
        # 先写 dispatching 防止 HTTP heartbeat 在 MQTT 发布期间重复取出
        rec.setdefault("commands", []).append({
            "id": command_id,
            "action": "stream_prepare",
            "args": {"stream_id": stream_id, "stream_url": ws_url, "text": req.text},
            "status": "dispatching",
            "created_at": now,
            "dispatched_at": 0.0,
            "done_at": 0.0,
            "message": "",
        })
        data[device_id] = rec
        _save(data)

    try:
        _mqtt_publish(f"devices/{device_id}/command", mqtt_cmd)
        mqtt_ok = True
    except Exception:
        pass

    # 更新为 dispatched（MQTT 成功）或 pending（失败则让心跳兜底）
    with DATA_LOCK:
        data = _load()
        rec = data.get(device_id)
        if rec:
            for c in rec.get("commands", []):
                if c.get("id") == command_id:
                    c["status"] = "dispatched" if mqtt_ok else "pending"
                    c["dispatched_at"] = time.time() if mqtt_ok else 0.0
                    break
            data[device_id] = rec
            _save(data)

    return {
        "ok": True,
        "command_id": command_id,
        "stream_id": stream_id,
        "stream_url": ws_url,
        "pcm_bytes": len(pcm),
        "text": req.text,
    }


@app.get("/api/mqtt/status")
def mqtt_status() -> Any:
    connected = _mqtt_client is not None and _mqtt_client.is_connected()
    return {"ok": True, "mqtt_connected": connected, "mqtt_host": MQTT_HOST, "mqtt_port": MQTT_PORT}


# ---------------------------------------------------------------------------
# 启动后台线程
# ---------------------------------------------------------------------------
@app.on_event("startup")
def _start_background() -> None:
    threading.Thread(target=_check_offline_alerts, daemon=True).start()
    threading.Thread(target=_start_mqtt_thread, daemon=True).start()


# ---------------------------------------------------------------------------
# 静态页面
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/device")
def device_page() -> FileResponse:
    # 详情页,前端用 ?id=xxx 读取。放在 /device 而非 /device/ 便于相对静态资源。
    return FileResponse(STATIC_DIR / "device.html")
