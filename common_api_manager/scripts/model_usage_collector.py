#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_LOG_DIR = Path("/data/models/logs")
EVENTS_FILE = "model_usage_events.jsonl"
SUMMARY_FILE = "model_usage_summary.json"

summary: dict[str, dict[str, Any]] = {}
log_dir = DEFAULT_LOG_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_event(payload: dict[str, Any]) -> dict[str, Any]:
    model = str(payload.get("model") or "unknown")
    capability = str(payload.get("capability") or "unknown")
    input_tokens = int(payload.get("input_tokens") or 0)
    output_tokens = int(payload.get("output_tokens") or 0)
    total_tokens = int(payload.get("total_tokens") or input_tokens + output_tokens)
    return {
        "ts": float(payload.get("ts") or time.time()),
        "time_utc": utc_now(),
        "model": model,
        "capability": capability,
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "total_tokens": max(0, total_tokens),
        "estimated": bool(payload.get("estimated", True)),
    }


def load_summary() -> None:
    global summary
    path = log_dir / SUMMARY_FILE
    if not path.exists():
        summary = {}
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        summary = data.get("models") or {}
    except Exception:
        summary = {}


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_name, path)


def append_event(event: dict[str, Any]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / EVENTS_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def update_summary(event: dict[str, Any]) -> None:
    key = event["model"]
    item = summary.setdefault(
        key,
        {
            "model": key,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "capabilities": {},
            "first_seen_utc": event["time_utc"],
            "last_seen_utc": event["time_utc"],
        },
    )
    item["calls"] += 1
    item["input_tokens"] += event["input_tokens"]
    item["output_tokens"] += event["output_tokens"]
    item["total_tokens"] += event["total_tokens"]
    item["last_seen_utc"] = event["time_utc"]

    capability = event["capability"]
    cap = item["capabilities"].setdefault(
        capability,
        {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    )
    cap["calls"] += 1
    cap["input_tokens"] += event["input_tokens"]
    cap["output_tokens"] += event["output_tokens"]
    cap["total_tokens"] += event["total_tokens"]

    write_json_atomic(
        log_dir / SUMMARY_FILE,
        {
            "updated_utc": utc_now(),
            "events_file": str(log_dir / EVENTS_FILE),
            "models": summary,
        },
    )


class UsageHandler(BaseHTTPRequestHandler):
    server_version = "ModelUsageCollector/0.1"

    def do_GET(self) -> None:
        if self.path not in {"/", "/summary"}:
            self.send_error(404)
            return
        self._send_json({"status": "ok", "models": summary})

    def do_POST(self) -> None:
        if self.path != "/usage":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length") or 0)
        if length <= 0 or length > 65536:
            self.send_error(400, "invalid content length")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            event = normalize_event(payload)
            append_event(event)
            update_summary(event)
        except Exception as exc:
            self.send_error(400, str(exc))
            return
        self._send_json({"status": "ok", "event": event})

    def log_message(self, fmt: str, *args: Any) -> None:
        line = f"{utc_now()} {self.client_address[0]} {fmt % args}\n"
        with (log_dir / "model_usage_collector.log").open("a", encoding="utf-8") as handle:
            handle.write(line)

    def _send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    args = parser.parse_args()

    global log_dir
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    load_summary()

    server = ThreadingHTTPServer((args.host, args.port), UsageHandler)
    signal.signal(signal.SIGTERM, lambda *_: server.shutdown())
    print(f"model usage collector listening on http://{args.host}:{args.port}/usage")
    server.serve_forever()


if __name__ == "__main__":
    main()
