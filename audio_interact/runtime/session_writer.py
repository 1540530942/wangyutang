from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from runtime.audio_writer import write_pcm16_wav, write_wav_bytes, wav_duration_ms
from runtime.event_logger import EventLogger
from runtime.id_generator import ordinal_id, safe_id


EVENT_FILES = {
    "runtime": "runtime_events.jsonl",
    "vad": "vad_runtime.jsonl",
    "asr": "asr_runtime.jsonl",
    "tts": "tts_runtime.jsonl",
    "bargein": "bargein_runtime.jsonl",
}


class SessionWriter:
    """Create and populate a session-level audio interaction package."""

    def __init__(
        self,
        root: Path,
        *,
        session_id: str,
        device_id: str = "unknown",
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_ms: int = 32,
        source: str = "audio_interact",
        capture_point: str = "unknown",
        session_day: str | None = None,
    ) -> None:
        self.root = root
        self.session_id = safe_id(session_id)
        self.device_id = device_id
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.source = source
        self.capture_point = capture_point
        self.session_day = session_day
        self.created_at = time.time()
        self.session_dir = self._session_dir(root, self.session_id, self.session_day)
        self.audio_dir = self.session_dir / "audio"
        self.events_dir = self.session_dir / "events"
        self.labels_dir = self.session_dir / "labels"
        self.replay_dir = self.session_dir / "replay"
        self.reports_dir = self.session_dir / "reports"
        for path in (self.audio_dir, self.events_dir, self.labels_dir, self.replay_dir, self.reports_dir):
            path.mkdir(parents=True, exist_ok=True)
        self._loggers = {name: EventLogger(self.events_dir / filename) for name, filename in EVENT_FILES.items()}
        self.emit("runtime", ts_ms=0, type="session.start", session_id=self.session_id, device_id=self.device_id)
        self.ensure_label_stubs()

    @staticmethod
    def _session_dir(root: Path, session_id: str, session_day: str | None = None) -> Path:
        day = session_day or time.strftime("%Y-%m-%d")
        return root / "sessions" / day / safe_id(session_id)

    def relative_path(self, data_root: Path) -> str:
        try:
            return self.session_dir.relative_to(data_root).as_posix()
        except ValueError:
            return self.session_dir.as_posix()

    def emit(self, category: str, **event: Any) -> dict[str, Any]:
        if category not in self._loggers:
            raise ValueError(f"unknown event category: {category}")
        event.setdefault("session_id", self.session_id)
        if "ts_ms" not in event:
            event["ts_ms"] = int((time.time() - self.created_at) * 1000)
        emitted = self._loggers[category].emit(**event)
        if category != "runtime":
            self._loggers["runtime"].emit(**emitted)
        return emitted

    def write_manifest(self, *, duration_ms: int = 0, extra: dict[str, Any] | None = None) -> Path:
        manifest = {
            "schema_version": "1.1",
            "session_id": self.session_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.created_at)),
            "device_id": self.device_id,
            "duration_ms": int(duration_ms),
            "source": self.source,
            # 采集点:browser_processed = 浏览器已做 AEC/降噪/AGC,"raw" 并非真原始
            "capture_point": self.capture_point,
            # 当前服务端无独立前处理链,proc 与 raw 逐字节相同
            "proc_same_as_raw": True,
            "audio": {
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "format": "wav_pcm_s16le",
                "chunk_ms": self.chunk_ms,
            },
            "versions": {
                "audio_pipeline": "audio_interact",
                "vad": "silero_streaming",
            },
            "tags": [],
        }
        if extra:
            manifest.update(extra)
        path = self.session_dir / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_audio_wav(self, name: str, wav_bytes: bytes) -> Path:
        path = self.audio_dir / name
        write_wav_bytes(path, wav_bytes)
        return path

    def write_audio_pcm16(self, name: str, pcm16: bytes) -> Path:
        path = self.audio_dir / name
        write_pcm16_wav(path, pcm16, self.sample_rate, self.channels)
        return path

    def copy_audio(self, source: Path, name: str) -> Path:
        target = self.audio_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target

    def ensure_label_stubs(self) -> None:
        stubs = {
            "vad_label.json": {"session_id": self.session_id, "speech_segments": []},
            "asr_label.json": {"session_id": self.session_id, "turns": []},
            "tts_label.json": {"session_id": self.session_id, "tts_segments": []},
            "bargein_label.json": {"session_id": self.session_id, "bargein_cases": []},
        }
        for filename, payload in stubs.items():
            path = self.labels_dir / filename
            if not path.exists():
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def close(self, *, duration_ms: int | None = None, extra_manifest: dict[str, Any] | None = None) -> Path:
        if duration_ms is None:
            duration_ms = 0
            for candidate in ("mic_proc_16k.wav", "mic_raw_16k.wav"):
                path = self.audio_dir / candidate
                if path.exists():
                    duration_ms = wav_duration_ms(path)
                    break
        self.emit("runtime", ts_ms=duration_ms, type="session.end", session_id=self.session_id)
        return self.write_manifest(duration_ms=duration_ms, extra=extra_manifest)


def write_streaming_session_package(
    data_root: Path,
    *,
    session_id: str,
    device_id: str,
    sample_rate: int,
    full_pcm: bytes,
    utterances: list[dict[str, Any]],
    chunk_ms: int = 32,
) -> str | None:
    if not full_pcm:
        return None
    writer = SessionWriter(
        data_root,
        session_id=session_id,
        device_id=device_id,
        sample_rate=sample_rate,
        chunk_ms=chunk_ms,
        source="websocket_stream",
        capture_point="browser_processed" if device_id.startswith("web") else "pi_alsa_raw",
    )
    writer.write_audio_pcm16("mic_raw_16k.wav", full_pcm)
    writer.write_audio_pcm16("mic_proc_16k.wav", full_pcm)
    for index, item in enumerate(utterances):
        segment_id = ordinal_id("seg", index)
        turn_id = ordinal_id("turn", index)
        start_ms = _seconds_to_ms(item.get("vad_start_seconds"))
        end_ms = _seconds_to_ms(item.get("vad_end_seconds"))
        asr_elapsed_ms = item.get("asr_elapsed_ms")
        route_elapsed_ms = item.get("route_elapsed_ms")
        tts_elapsed_ms = item.get("tts_elapsed_ms")
        writer.emit(
            "vad",
            ts_ms=start_ms,
            type="vad.speech_start",
            segment_id=segment_id,
            confidence=item.get("confidence"),
        )
        writer.emit(
            "vad",
            ts_ms=end_ms,
            type="vad.speech_end",
            segment_id=segment_id,
            start_ms=start_ms,
            end_ms=end_ms,
            reason=item.get("reason"),
        )
        text = str(item.get("text") or "")
        asr_event: dict[str, Any] = dict(
            ts_ms=end_ms,
            type="asr.final",
            segment_id=segment_id,
            turn_id=turn_id,
            audio_start_ms=start_ms,
            audio_end_ms=end_ms,
            text=text,
            wake_status=item.get("wake_status"),
            status=item.get("status"),
            skill_id=str(item.get("skill_id") or ""),
        )
        if asr_elapsed_ms is not None:
            asr_event["asr_elapsed_ms"] = asr_elapsed_ms
        writer.emit("asr", **asr_event)
        skill_id = str(item.get("skill_id") or "")
        action_task = item.get("action_task")
        tts_text = str(item.get("tts_text") or "")
        action_error = str(item.get("action_error") or "")
        if skill_id and item.get("status") == "ok":
            cmd_event: dict[str, Any] = dict(
                ts_ms=end_ms,
                type="robot.command",
                turn_id=turn_id,
                skill_id=skill_id,
                action_task=action_task,
                tts_text=tts_text,
                action_error=action_error,
            )
            if route_elapsed_ms is not None:
                cmd_event["route_elapsed_ms"] = route_elapsed_ms
            writer.emit("runtime", **cmd_event)
        if tts_elapsed_ms is not None:
            writer.emit("tts", ts_ms=end_ms, type="tts.request", turn_id=turn_id)
            writer.emit("tts", ts_ms=end_ms, type="tts.audio_ready", turn_id=turn_id,
                        tts_elapsed_ms=tts_elapsed_ms)
    writer.close()
    return writer.relative_path(data_root)


def write_segment_session_package(
    data_root: Path,
    *,
    session_id: str,
    device_id: str,
    wav_bytes: bytes,
    asr_text: str = "",
    wake_status: str = "",
    command: dict[str, Any] | None = None,
    tts_text: str = "",
    tts_wav_bytes: bytes | None = None,
) -> str:
    # web-* 设备来自浏览器(已过浏览器处理链);其余为树莓派 ALSA 直采
    capture_point = "browser_processed" if device_id.startswith("web") else "pi_alsa_raw"
    writer = SessionWriter(data_root, session_id=session_id, device_id=device_id, source="segment_upload", capture_point=capture_point)
    writer.write_audio_wav("mic_raw_16k.wav", wav_bytes)
    writer.write_audio_wav("mic_proc_16k.wav", wav_bytes)
    duration_ms = wav_duration_ms(writer.audio_dir / "mic_proc_16k.wav")
    # 定长上传没有真实 VAD,整段即一个"段";标 fixed_window,评测时不得计入 VAD 预测
    writer.emit("vad", ts_ms=0, type="vad.segment", segment_id="seg_001", start_ms=0, end_ms=duration_ms, source="fixed_window", source_audio="mic_proc_16k.wav")
    writer.emit("asr", ts_ms=duration_ms, type="asr.final", segment_id="seg_001", turn_id="turn_001", audio_start_ms=0, audio_end_ms=duration_ms, text=asr_text, wake_status=wake_status)
    if command is not None:
        writer.emit("runtime", ts_ms=duration_ms, type="robot.command", turn_id="turn_001", command=command)
    if tts_text:
        writer.emit("tts", ts_ms=duration_ms, type="tts.request", tts_id="tts_001", text=tts_text)
    if tts_wav_bytes:
        writer.write_audio_wav("tts_ref_16k.wav", tts_wav_bytes)
        writer.emit("tts", ts_ms=duration_ms, type="tts.audio_ready", tts_id="tts_001", audio_file="audio/tts_ref_16k.wav")
    writer.close(duration_ms=duration_ms)
    return writer.relative_path(data_root)


def _seconds_to_ms(value: Any) -> int:
    try:
        return max(0, int(round(float(value) * 1000)))
    except (TypeError, ValueError):
        return 0
