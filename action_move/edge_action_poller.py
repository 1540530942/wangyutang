from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SERVER = "https://www.wangyutang.cn/action"
# Cloud action_move reports completed motion to slam_mapping; the Pi poller keeps executing the same task protocol.
IDLE_HEARTBEAT_SECONDS = 5.0
ACTION_TIMEOUT_SECONDS = 20
DIAGNOSTIC_HEARTBEAT_SECONDS = 30.0
DEFAULT_DISABLED_ACTIONS = "move_forward,move_backward,move_left,move_right,turn_left,turn_right"
VOICE_PROMPT_DIR = BASE_DIR / "voice_prompts"
# Synthesized speech, kept on disk and keyed by what determines the audio.
# Cloud TTS costs ~13 s for 30 characters and ~25 s for 60, which is the whole
# latency of a speak task; a phrase the robot says more than once should not pay
# it twice. Also makes a repeated phrase byte-identical, which is what lets it
# serve as a controlled stimulus in the AEC measurements.
SPEAK_CACHE_DIR = BASE_DIR / "speak_cache"
SPEAK_CACHE_MAX_ENTRIES = 200
DEFAULT_TTS_URL = "https://www.wangyutang.cn/common/api/tts/speech"
DEFAULT_TTS_MODEL = "qwen3-tts-12hz-1.7b-customvoice"
DEFAULT_TTS_VOICE = "vivian"
DEFAULT_TTS_LANGUAGE = "chinese"
VOICE_ACTION_TEXT = {
    "move_forward": "前进",
    "move_backward": "后退",
    "move_left": "左移",
    "move_right": "右移",
    "turn_left": "左转",
    "turn_right": "右转",
    "look_left": "向左看",
    "look_right": "向右看",
    "look_up": "向上看",
    "look_down": "向下看",
    "reset_pose": "复位",
    "emergency_stop": "停止",
    "rgb_on": "开灯",
    "rgb_off": "关灯",
}


DEFAULT_TTS_INSTRUCTIONS = "用清新自然、甜美温柔的语气说，声音明亮亲切，语调轻快柔和"


def fetch_tts_audio(
    text: str,
    url: str,
    model: str,
    voice: str,
    language: str,
    timeout: float = 0.0,
    instructions: str = "",
) -> bytes:
    if timeout <= 0:
        # Synthesis time scales with the text -- roughly 13 s for 30 characters
        # and 25 s for 60 -- so a flat 30 s silently killed anything longer than
        # a short sentence, well inside the skill's 200-character ceiling.
        timeout = min(150.0, 15.0 + 0.5 * len(text))
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "language": language,
        "instructions": instructions or DEFAULT_TTS_INSTRUCTIONS,
        "response_format": "wav",
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        audio = response.read()
    if not audio.startswith(b"RIFF") and not audio.startswith(b"ID3"):
        raise RuntimeError("TTS response is not an audio payload")
    return audio


def play_audio_file(path: Path, player: str, device: str = "") -> tuple[bool, str]:
    selected_device = device or os.getenv("ACTION_VOICE_DEVICE", "").strip()
    if not selected_device and Path(player).name == "aplay":
        selected_device = detect_usb_audio_device()
    if Path(player).name == "aplay":
        command = [player, "-q"]
        if selected_device:
            command.extend(["-D", selected_device])
        command.append(str(path))
    else:
        command = [player, str(path)]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=8)
    device_label = selected_device or "default"
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        return False, f"rc={completed.returncode}: {stderr[:300]}"
    return True, device_label


def apply_voice_volume(device: str, volume_percent: object) -> str:
    try:
        volume = max(0, min(100, int(round(float(volume_percent)))))
    except (TypeError, ValueError):
        return ""
    card = ""
    match = re.match(r"(?:plug)?hw:(\d+),", device or "")
    if match:
        card = match.group(1)
    controls = available_mixer_controls(card)
    control = choose_mixer_control(controls)
    if not control:
        return "[WARN] voice volume set skipped: no mixer control found"
    command = ["amixer"]
    if card:
        command.extend(["-c", card])
    mute_state = "mute" if volume <= 0 else "unmute"
    command.extend(["set", control, f"{volume}%", mute_state])
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=3)
    except (subprocess.SubprocessError, OSError) as exc:
        return f"[WARN] voice volume set failed: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return f"[WARN] voice volume set failed rc={completed.returncode}: {detail[:200]}"
    return f"[INFO] voice_volume_percent={volume} mixer_control={control}"


def available_mixer_controls(card: str) -> list[str]:
    command = ["amixer"]
    if card:
        command.extend(["-c", card])
    command.append("scontrols")
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=3)
    except (subprocess.SubprocessError, OSError):
        return []
    if completed.returncode != 0:
        return []
    controls: list[str] = []
    for line in completed.stdout.splitlines():
        match = re.search(r"Simple mixer control '([^']+)'", line)
        if match:
            controls.append(match.group(1))
    return controls


def choose_mixer_control(controls: list[str]) -> str:
    if not controls:
        return ""
    by_name = {control.lower(): control for control in controls}
    for preferred in ("speaker", "pcm", "master", "headphone", "digital", "playback"):
        if preferred in by_name:
            return by_name[preferred]
    for preferred in ("speaker", "pcm", "master", "headphone", "digital", "playback"):
        for control in controls:
            if preferred in control.lower():
                return control
    return controls[0]


def detect_usb_audio_device() -> str:
    try:
        result = subprocess.run(["aplay", "-l"], text=True, capture_output=True, timeout=2)
    except (subprocess.SubprocessError, OSError):
        return ""
    for line in result.stdout.splitlines():
        if "USB" not in line and "Device" not in line:
            continue
        match = re.search(r"card\s+(\d+):.*device\s+(\d+):", line)
        if match:
            return f"plughw:{match.group(1)},{match.group(2)}"
    return ""


def request_json(url: str, method: str = "GET", payload: dict | None = None, token: str = "", timeout: float = 12) -> dict:
    data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Action-Token"] = token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def command_text(command: list[str], timeout: float = 2.0) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def compact_lines(text: str, limit: int = 80) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return lines[:limit]


def collect_diagnostics() -> dict[str, Any]:
    disabled_actions = os.getenv("ACTION_DISABLED_ACTIONS", DEFAULT_DISABLED_ACTIONS)
    service_output = command_text(
        ["systemctl", "is-active", "action-move-poller", "action-move-controller", "camera-snapshot-sender", "docker"],
        timeout=3,
    )
    process_output = command_text(
        [
            "bash",
            "-lc",
            "ps -eo pid,user,stat,pcpu,pmem,cmd --sort=-pcpu | head -25",
        ],
        timeout=3,
    )
    ros_output = command_text(
        [
            "docker",
            "exec",
            "-u",
            "ubuntu",
            "turbopi",
            "bash",
            "-lc",
            "source /opt/ros/humble/setup.bash && source /home/ubuntu/ros2_ws/install/setup.bash "
            "&& echo NODES && ros2 node list "
            "&& echo TOPICS && ros2 topic list -t | grep -E 'cmd_vel|motor|servo|camera|image|sonar' "
            "&& echo CMDVEL && ros2 topic info /cmd_vel "
            "&& echo MOTOR && ros2 topic info /ros_robot_controller/set_motor_speeds",
        ],
        timeout=10,
    )
    docker_output = command_text(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"], timeout=3)
    return {
        "reported_at": time.time(),
        "uptime": command_text(["uptime"], timeout=2),
        "throttled": command_text(["vcgencmd", "get_throttled"], timeout=2),
        "disabled_actions": disabled_actions,
        "services": compact_lines(service_output, 12),
        "docker": compact_lines(docker_output, 12),
        "top_processes": compact_lines(process_output, 30),
        "ros": compact_lines(ros_output, 120),
    }


def announce_completion(
    action: str,
    enabled: bool = True,
    device: str = "",
    tts_url: str = "",
    tts_model: str = DEFAULT_TTS_MODEL,
    tts_voice: str = DEFAULT_TTS_VOICE,
    tts_language: str = DEFAULT_TTS_LANGUAGE,
    volume_percent: object = None,
) -> str:
    if not enabled:
        return ""
    try:
        volume = max(0, min(100, int(round(float(volume_percent)))))
    except (TypeError, ValueError):
        volume = 90
    if volume <= 0:
        selected_device = device or os.getenv("ACTION_VOICE_DEVICE", "").strip() or detect_usb_audio_device()
        volume_output = apply_voice_volume(selected_device, volume_percent)
        return "\n".join(part for part in [volume_output, "[INFO] voice_prompt_skipped=muted"] if part)
    text = f"{VOICE_ACTION_TEXT.get(action, action)}完成"
    prompt = VOICE_PROMPT_DIR / f"{action}_complete.wav"
    player = shutil.which("aplay") or ""
    if not player:
        player = shutil.which("paplay") or ""
    if not player:
        return "[WARN] no audio playback command found"
    selected_device = device or os.getenv("ACTION_VOICE_DEVICE", "").strip()
    if not selected_device and Path(player).name == "aplay":
        selected_device = detect_usb_audio_device()
    volume_output = apply_voice_volume(selected_device, volume_percent)

    tts_endpoint = tts_url or os.getenv("ACTION_TTS_URL", DEFAULT_TTS_URL).strip()
    if tts_endpoint:
        try:
            audio = fetch_tts_audio(text, tts_endpoint, tts_model, tts_voice, tts_language)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                handle.write(audio)
                temp_path = Path(handle.name)
            try:
                ok, detail = play_audio_file(temp_path, player, selected_device)
            finally:
                temp_path.unlink(missing_ok=True)
            if ok:
                return "\n".join(
                    part
                    for part in [
                        volume_output,
                        f"[INFO] tts_prompt_played model={tts_model} voice={tts_voice} device={detail} text={text}",
                    ]
                    if part
                )
            return f"[WARN] tts prompt playback failed: {detail}"
        except Exception as exc:  # noqa: BLE001 - fallback to the static prompt below
            fallback_reason = f"[WARN] tts prompt failed: {exc}"
    else:
        fallback_reason = "[WARN] tts prompt disabled"

    if not prompt.exists():
        return f"{fallback_reason}\n[WARN] voice prompt missing: {prompt}"
    try:
        ok, detail = play_audio_file(prompt, player, selected_device)
        if not ok:
            return f"{fallback_reason}\n[WARN] voice prompt playback failed: {detail}"
    except (subprocess.SubprocessError, OSError) as exc:
        return f"{fallback_reason}\n[WARN] voice prompt playback failed: {exc}"
    suffix = f" device={detail}" if detail else " device=default"
    return "\n".join(part for part in [fallback_reason, volume_output, f"[INFO] voice_prompt_played={prompt.name}{suffix}"] if part)


def schedule_completion_voice(
    action: str,
    enabled: bool = True,
    device: str = "",
    tts_url: str = "",
    tts_model: str = DEFAULT_TTS_MODEL,
    tts_voice: str = DEFAULT_TTS_VOICE,
    tts_language: str = DEFAULT_TTS_LANGUAGE,
    volume_percent: object = None,
) -> str:
    if not enabled:
        return ""
    try:
        volume = max(0, min(100, int(round(float(volume_percent)))))
    except (TypeError, ValueError):
        volume = 90
    if volume <= 0:
        selected_device = device or os.getenv("ACTION_VOICE_DEVICE", "").strip() or detect_usb_audio_device()
        volume_output = apply_voice_volume(selected_device, volume_percent)
        return "\n".join(part for part in [volume_output, "[INFO] voice_prompt_skipped=muted"] if part)

    def worker() -> None:
        try:
            output = announce_completion(
                action,
                enabled=enabled,
                device=device,
                tts_url=tts_url,
                tts_model=tts_model,
                tts_voice=tts_voice,
                tts_language=tts_language,
                volume_percent=volume_percent,
            )
            if output:
                print(output, flush=True)
        except Exception as exc:  # noqa: BLE001 - voice must not block task result reporting
            print(f"[WARN] async voice prompt failed: {type(exc).__name__}: {exc}", flush=True)

    threading.Thread(target=worker, daemon=True).start()
    return "[INFO] voice_prompt_scheduled=async"


LISTEN_MAX_SECONDS = 60
LISTEN_ASR_URL = "http://110.40.154.41/common/api/asr/transcribe"
LISTEN_ENVELOPE_MS = 100


def detect_usb_capture_device() -> str:
    """Mirror of detect_usb_audio_device for the capture side: `arecord -l`
    numbers capture cards independently of playback ones."""
    try:
        result = subprocess.run(["arecord", "-l"], text=True, capture_output=True, timeout=2)
    except (subprocess.SubprocessError, OSError):
        return ""
    for line in (result.stdout or "").splitlines():
        match = re.search(r"card\s+(\d+):.*device\s+(\d+):", line)
        if match:
            return f"plughw:{match.group(1)},{match.group(2)}"
    return ""


def rms_envelope(pcm: bytes, sample_rate: int, window_ms: int) -> list[int]:
    """Per-window RMS of 16-bit mono PCM.

    Returned inline with the task result instead of shipping the WAV anywhere.
    It is all the barge-in measurement needs: in one recording from the robot's
    own microphone you can see when the near end started talking and when the
    far end's speech collapsed, and the gap between those two is the latency --
    with no clock to synchronise between the two devices.
    """
    import audioop

    step = max(1, (sample_rate * window_ms) // 1000) * 2
    out: list[int] = []
    for offset in range(0, len(pcm) - step + 1, step):
        out.append(int(audioop.rms(pcm[offset:offset + step], 2)))
    return out


def transcribe_wav(audio: bytes, timeout: float = 120.0) -> str:
    boundary = "----listen" + secrets.token_hex(8)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="listen.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + audio + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        LISTEN_ASR_URL,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return str(json.loads(response.read().decode("utf-8")).get("text") or "")


def listen_and_report(
    seconds: int,
    capture_device: str = "",
    transcribe: bool = True,
) -> tuple[bool, str, str]:
    """Record the room from the robot's own microphone and report what it heard.

    The Pi's speaker and microphone stand in for a person in the room: the
    speaker is the mouth talking to the ESP32, this is the ears. Rather than move
    the WAV around, the level envelope and the transcript -- the two things the
    measurements actually consume -- come back inline in the task result.
    """
    seconds = max(1, min(LISTEN_MAX_SECONDS, int(seconds)))
    recorder = shutil.which("arecord")
    if not recorder:
        return False, "", "listen: arecord not found"
    selected = capture_device or os.getenv("ACTION_CAPTURE_DEVICE", "").strip()
    if not selected:
        selected = detect_usb_capture_device()
    rate = 16000
    command = [recorder, "-q", "-f", "S16_LE", "-r", str(rate), "-c", "1", "-d", str(seconds)]
    if selected:
        command.extend(["-D", selected])
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        wav_path = Path(handle.name)
    command.append(str(wav_path))
    started = time.time()
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=seconds + 15)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return False, "", f"listen: arecord rc={completed.returncode}: {detail[:200]}"
        audio = wav_path.read_bytes()
    except (subprocess.SubprocessError, OSError) as exc:
        return False, "", f"listen: record failed: {exc}"
    finally:
        wav_path.unlink(missing_ok=True)

    try:
        import audioop
        import wave as wave_mod
        import io

        with wave_mod.open(io.BytesIO(audio)) as handle:
            rate = handle.getframerate()
            pcm = handle.readframes(handle.getnframes())
        overall = int(audioop.rms(pcm, 2))
    except Exception as exc:  # noqa: BLE001
        return False, "", f"listen: wav parse failed: {exc}"

    envelope = rms_envelope(pcm, rate, LISTEN_ENVELOPE_MS)
    lines = [
        f"[INFO] listen_recorded seconds={seconds} device={selected or 'default'} "
        f"rate={rate} rms={overall} started_at={started:.3f}",
        f"[ENV{LISTEN_ENVELOPE_MS}] " + ",".join(str(v) for v in envelope),
    ]
    if transcribe:
        try:
            lines.append("[ASR] " + transcribe_wav(audio))
        except Exception as exc:  # noqa: BLE001
            lines.append(f"[WARN] listen asr failed: {exc}")
    return True, "\n".join(lines), ""


def speak_while_listening(
    text: str,
    seconds: int,
    speak_at_ms: int = 3000,
    repeat: int = 1,
    gap_ms: int = 10000,
    device: str = "",
    capture_device: str = "",
    tts_url: str = "",
    tts_model: str = DEFAULT_TTS_MODEL,
    tts_voice: str = DEFAULT_TTS_VOICE,
    tts_language: str = DEFAULT_TTS_LANGUAGE,
    instructions: str = "",
    volume_percent: object = None,
    transcribe: bool = True,
) -> tuple[bool, str, str]:
    """Talk and record at the same time, from one task.

    speak and listen are separate skills, but the poller runs one task at a time,
    so asking for both gets them back to back rather than overlapping -- measured:
    the second was claimed 0.3 s after the first finished. Barge-in needs them
    concurrent, and doing it inside a single action also removes the guesswork:
    playback starts a known offset after recording starts, so the moment the
    "person" began talking is a number rather than something inferred from the
    waveform.

    Audio is resolved before recording opens, so a cache miss cannot push the
    speech outside the window.
    """
    text = (text or "").strip()
    if not text:
        return False, "", "speak_listen: empty text"
    seconds = max(1, min(LISTEN_MAX_SECONDS, int(seconds)))
    speak_at_ms = max(0, min(seconds * 1000, int(speak_at_ms)))
    repeat = max(1, min(10, int(repeat)))
    gap_ms = max(0, min(seconds * 1000, int(gap_ms)))

    player = shutil.which("aplay") or shutil.which("paplay") or ""
    recorder = shutil.which("arecord")
    if not player:
        return False, "", "speak_listen: no audio playback command found"
    if not recorder:
        return False, "", "speak_listen: arecord not found"

    out_device = device or os.getenv("ACTION_VOICE_DEVICE", "").strip()
    if not out_device and Path(player).name == "aplay":
        out_device = detect_usb_audio_device()
    in_device = capture_device or os.getenv("ACTION_CAPTURE_DEVICE", "").strip()
    if not in_device:
        in_device = detect_usb_capture_device()
    volume_output = apply_voice_volume(out_device, volume_percent) if volume_percent is not None else ""

    # Resolve the audio up front: a cache miss costs 15-150 s of synthesis.
    wav_path, source, err = resolve_speech(
        text, tts_url, tts_model, tts_voice, tts_language, instructions
    )
    if err:
        return False, volume_output, err

    rate = 16000
    rec_cmd = [recorder, "-q", "-f", "S16_LE", "-r", str(rate), "-c", "1", "-d", str(seconds)]
    if in_device:
        rec_cmd.extend(["-D", in_device])
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        rec_path = Path(handle.name)
    rec_cmd.append(str(rec_path))

    play_cmd = [player, "-q"]
    if Path(player).name == "aplay" and out_device:
        play_cmd.extend(["-D", out_device])
    play_cmd.append(str(wav_path))

    offsets: list[int] = []
    try:
        rec_proc = subprocess.Popen(rec_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        t_rec = time.time()
        try:
            for k in range(repeat):
                # Each utterance starts at a planned offset, so the quiet stretch
                # between them is where duck-and-recover is visible. Playing them
                # back to back would merge four interruptions into one event.
                target = (speak_at_ms + k * gap_ms) / 1000.0
                wait = target - (time.time() - t_rec)
                if wait > 0:
                    time.sleep(wait)
                if time.time() - t_rec >= seconds:
                    break
                offsets.append(int((time.time() - t_rec) * 1000))
                subprocess.run(play_cmd, capture_output=True, timeout=seconds + 10)
            rec_proc.wait(timeout=seconds + 15)
        finally:
            if rec_proc.poll() is None:
                rec_proc.kill()
                rec_proc.wait(timeout=5)
        if rec_proc.returncode not in (0, None):
            detail = (rec_proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            return False, volume_output, f"speak_listen: arecord rc={rec_proc.returncode}: {detail[:200]}"
        audio = rec_path.read_bytes()
    except (subprocess.SubprocessError, OSError) as exc:
        return False, volume_output, f"speak_listen: failed: {exc}"
    finally:
        rec_path.unlink(missing_ok=True)
        if source.startswith("uncached"):
            wav_path.unlink(missing_ok=True)

    try:
        import audioop
        import io
        import wave as wave_mod

        with wave_mod.open(io.BytesIO(audio)) as handle:
            rate = handle.getframerate()
            pcm = handle.readframes(handle.getnframes())
        overall = int(audioop.rms(pcm, 2))
    except Exception as exc:  # noqa: BLE001
        return False, volume_output, f"speak_listen: wav parse failed: {exc}"

    lines = [
        f"[INFO] speak_listen seconds={seconds} in={in_device or 'default'} out={out_device or 'default'} "
        f"rate={rate} rms={overall} source={source} chars={len(text)}",
        f"[SPEAK_MS] " + ",".join(str(v) for v in offsets),
        f"[ENV{LISTEN_ENVELOPE_MS}] " + ",".join(str(v) for v in rms_envelope(pcm, rate, LISTEN_ENVELOPE_MS)),
    ]
    if transcribe:
        try:
            lines.append("[ASR] " + transcribe_wav(audio))
        except Exception as exc:  # noqa: BLE001
            lines.append(f"[WARN] speak_listen asr failed: {exc}")
    return True, "\n".join(part for part in [volume_output, *lines] if part), ""


def speak_cache_path(
    text: str, model: str, voice: str, language: str, instructions: str
) -> Path:
    """Cache key covers everything that changes the waveform, so switching voice
    or instructions produces a new file instead of replaying the old one."""
    key = "\x00".join([text, model, voice, language, instructions or DEFAULT_TTS_INSTRUCTIONS])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return SPEAK_CACHE_DIR / f"{digest}.wav"


def prune_speak_cache() -> None:
    """Keep the newest SPEAK_CACHE_MAX_ENTRIES files. The Pi's SD card is small
    and the cache grows with every distinct phrase ever spoken."""
    try:
        files = sorted(
            SPEAK_CACHE_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except OSError:
        return
    for stale in files[SPEAK_CACHE_MAX_ENTRIES:]:
        try:
            stale.unlink()
        except OSError:
            pass


def resolve_speech(
    text: str,
    tts_url: str,
    tts_model: str,
    tts_voice: str,
    tts_language: str,
    instructions: str,
) -> tuple[Path, str, str]:
    """Return a playable WAV for `text`, from cache when possible.

    Returns (path, source, error). source is "cache", "tts", or
    "uncached(<reason>)" -- in the last case the caller owns the temp file and
    must delete it.
    """
    cached = speak_cache_path(text, tts_model, tts_voice, tts_language, instructions)
    if cached.is_file():
        return cached, "cache", ""
    endpoint = tts_url or os.getenv("ACTION_TTS_URL", DEFAULT_TTS_URL).strip()
    if not endpoint:
        return cached, "", "speak: TTS endpoint not configured"
    try:
        audio = fetch_tts_audio(
            text, endpoint, tts_model, tts_voice, tts_language, instructions=instructions
        )
    except Exception as exc:  # noqa: BLE001
        return cached, "", f"speak: tts failed: {exc}"
    # Stage beside the target and rename, so a crash or a second poller mid-write
    # can never leave a half-file that later plays as garbage.
    try:
        SPEAK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=SPEAK_CACHE_DIR, suffix=".part", delete=False) as handle:
            handle.write(audio)
            staged = Path(handle.name)
        staged.replace(cached)
        prune_speak_cache()
    except OSError as exc:
        # A read-only or full disk must not break speech.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(audio)
            return Path(handle.name), f"uncached({exc.__class__.__name__})", ""
    return cached, "tts", ""


def speak_text(
    text: str,
    device: str = "",
    tts_url: str = "",
    tts_model: str = DEFAULT_TTS_MODEL,
    tts_voice: str = DEFAULT_TTS_VOICE,
    tts_language: str = DEFAULT_TTS_LANGUAGE,
    instructions: str = "",
    volume_percent: object = None,
) -> tuple[bool, str, str]:
    """Synthesize caller-supplied text via the cloud TTS API and play it on the
    local speaker. Backs the catalog `speak` skill; unlike announce_completion the
    text is arbitrary rather than a per-action canned phrase, and it is not gated
    by --no-voice (which only silences automatic completion prompts).
    """
    text = (text or "").strip()
    if not text:
        return False, "", "speak: empty text"
    player = shutil.which("aplay") or shutil.which("paplay") or ""
    if not player:
        return False, "", "speak: no audio playback command found"
    selected_device = device or os.getenv("ACTION_VOICE_DEVICE", "").strip()
    if not selected_device and Path(player).name == "aplay":
        selected_device = detect_usb_audio_device()
    volume_output = apply_voice_volume(selected_device, volume_percent) if volume_percent is not None else ""
    cached, source, err = resolve_speech(
        text, tts_url, tts_model, tts_voice, tts_language, instructions
    )
    if err:
        return False, volume_output, err
    try:
        ok, detail = play_audio_file(cached, player, selected_device)
    finally:
        if source.startswith("uncached"):
            cached.unlink(missing_ok=True)
    if not ok:
        return False, volume_output, f"speak: playback failed: {detail}"
    info = (
        f"[INFO] speak_played chars={len(text)} device={detail} "
        f"voice={tts_voice} source={source} text={text[:80]}"
    )
    return True, "\n".join(part for part in [volume_output, info] if part), ""


def first_ip_address() -> str:
    hostname_ips = command_text(["hostname", "-I"])
    for item in hostname_ips.split():
        if ":" not in item:
            return item
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as handle:
            handle.settimeout(1)
            handle.connect(("8.8.8.8", 80))
            return str(handle.getsockname()[0])
    except OSError:
        return ""


def wifi_ssid() -> str:
    ssid = command_text(["iwgetid", "-r"])
    if ssid:
        return ssid
    nmcli = command_text(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
    for line in nmcli.splitlines():
        active, _, name = line.partition(":")
        if active == "yes" and name:
            return name
    iw = command_text(["iw", "dev"])
    for line in iw.splitlines():
        stripped = line.strip()
        if stripped.startswith("ssid "):
            return stripped[5:].strip()
    return ""


def network_status() -> dict[str, str]:
    route = command_text(["ip", "route"])
    gateway = ""
    for line in route.splitlines():
        parts = line.split()
        if parts[:1] == ["default"] and "via" in parts:
            gateway = parts[parts.index("via") + 1]
            break
    return {
        "hostname": socket.gethostname(),
        "ip_address": first_ip_address(),
        "wifi_ssid": wifi_ssid(),
        "gateway": gateway,
    }


def controller_execute(controller_url: str, action: str, settings: dict[str, Any] | None = None) -> tuple[bool, str, str]:
    started = time.time()
    payload = {"action": action, "settings": settings or {}}
    try:
        result = request_json(
            f"{controller_url.rstrip('/')}/execute",
            method="POST",
            payload=payload,
            timeout=ACTION_TIMEOUT_SECONDS,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, "", f"controller unavailable: {exc}"
    elapsed = round(time.time() - started, 3)
    output = str(result.get("output") or "")
    output = f"{output}\n[INFO] poller_controller_roundtrip_seconds={elapsed}".strip()
    if bool(result.get("ok")):
        return True, output, ""
    return False, output, str(result.get("error") or "controller rejected action")


def run_action(action: str, settings: dict | None = None) -> tuple[bool, str, str]:
    command = [
        "python3",
        str(BASE_DIR / "action_move_executor.py"),
        action,
        "--params-json",
        json.dumps(settings or {}, ensure_ascii=False),
    ]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=ACTION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return False, str(stdout), f"action timed out after {ACTION_TIMEOUT_SECONDS}s\n{stderr}"
    return completed.returncode == 0, completed.stdout, completed.stderr


def run_remote_shutdown() -> tuple[bool, str, str]:
    command = ["sudo", "shutdown", "-h", "now"]
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return False, "", f"failed to start shutdown: {exc}"
    return True, "[INFO] remote shutdown requested with: sudo shutdown -h now", ""


def execute_action(
    action: str,
    settings: dict[str, Any],
    controller_url: str,
    no_controller: bool,
) -> tuple[bool, str, str]:
    if action == "remote_shutdown":
        return run_remote_shutdown()
    disabled_actions = {
        item.strip()
        for item in os.getenv("ACTION_DISABLED_ACTIONS", DEFAULT_DISABLED_ACTIONS).split(",")
        if item.strip()
    }
    if action in disabled_actions:
        return (
            False,
            "",
            (
                f"action disabled on edge: {action}; recent chassis motor load caused a Raspberry Pi reboot. "
                "Check motor power, wiring, common ground, and peak-current capacity before re-enabling."
            ),
        )
    if no_controller:
        return run_action(action, settings)
    ok, stdout, stderr = controller_execute(controller_url, action, settings)
    if not ok and stderr.startswith("controller unavailable:"):
        fallback_ok, fallback_stdout, fallback_stderr = run_action(action, settings)
        stdout = "\n".join(part for part in [stdout, fallback_stdout] if part)
        stderr = "\n".join(part for part in [stderr, fallback_stderr] if part)
        ok = fallback_ok
    return ok, stdout, stderr


def execute_with_running_heartbeats(
    server: str,
    token: str,
    device_id: str,
    task_id: str,
    action: str,
    settings: dict[str, Any],
    controller_url: str,
    no_controller: bool,
) -> tuple[bool, str, str]:
    result: dict[str, object] = {"ok": False, "stdout": "", "stderr": ""}
    done = threading.Event()

    def worker() -> None:
        try:
            ok, stdout, stderr = execute_action(action, settings, controller_url, no_controller)
            result.update({"ok": ok, "stdout": stdout, "stderr": stderr})
        except Exception as exc:  # noqa: BLE001 - keep poller alive and report task failure
            result.update({"ok": False, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"})
        finally:
            done.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    last_running_heartbeat = 0.0
    while not done.is_set():
        now = time.monotonic()
        if now - last_running_heartbeat >= IDLE_HEARTBEAT_SECONDS:
            try:
                heartbeat(server, token, device_id, "running", current_task_id=task_id)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"[WARN] running heartbeat failed: {exc}", flush=True)
            last_running_heartbeat = now
        done.wait(0.2)
    thread.join(timeout=0.1)
    return bool(result["ok"]), str(result["stdout"]), str(result["stderr"])


def heartbeat(
    server: str,
    token: str,
    device_id: str,
    status: str,
    detail: str = "",
    current_task_id: str = "",
    diagnostics: dict[str, Any] | None = None,
) -> None:
    network = network_status()
    payload = {
        "device_id": device_id,
        "status": status,
        "detail": detail[:300],
        "current_task_id": current_task_id,
        **network,
    }
    if diagnostics:
        payload["diagnostics"] = diagnostics
    request_json(
        f"{server.rstrip('/')}/api/device/heartbeat",
        method="POST",
        payload=payload,
        token=token,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll TurboPi Action Move cloud tasks and execute local skills.")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--token", default="")
    parser.add_argument("--device-id", default="turbopi-01")
    parser.add_argument("--interval", type=float, default=0.35)
    parser.add_argument("--long-poll-seconds", type=float, default=10.0)
    parser.add_argument("--controller-url", default="http://127.0.0.1:8765")
    parser.add_argument("--no-controller", action="store_true")
    parser.add_argument("--no-voice", action="store_true", help="Disable local completion voice prompt playback.")
    parser.add_argument("--voice-device", default="", help="Audio output device for aplay, for example plughw:2,0.")
    parser.add_argument("--capture-device", default="", help="Audio input device for arecord, for example plughw:2,0.")
    parser.add_argument("--tts-url", default=DEFAULT_TTS_URL)
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--tts-voice", default=DEFAULT_TTS_VOICE)
    parser.add_argument("--tts-language", default=DEFAULT_TTS_LANGUAGE)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    last_idle_heartbeat = 0.0
    last_diagnostic_heartbeat = 0.0

    while True:
        try:
            wait_seconds = max(args.long_poll_seconds, 0.0)
            data = request_json(
                f"{args.server.rstrip('/')}/api/tasks/next?wait_seconds={wait_seconds:.2f}",
                token=args.token,
                timeout=max(12.0, wait_seconds + 5.0),
            )
            task = data.get("task")
            if task:
                task_id = str(task["id"])
                action = str(task["skill_id"])
                settings = task.get("settings") if isinstance(task.get("settings"), dict) else {}
                params = task.get("params") if isinstance(task.get("params"), dict) else {}
                if action == "remote_shutdown":
                    heartbeat(args.server, args.token, args.device_id, "running", current_task_id=task_id)
                    ok, stdout, stderr = run_remote_shutdown()
                elif action == "speak":
                    # speak has no ROS action; synthesize + play locally instead of /execute.
                    heartbeat(args.server, args.token, args.device_id, "running", current_task_id=task_id)
                    ok, stdout, stderr = speak_text(
                        str(params.get("text") or ""),
                        device=args.voice_device,
                        tts_url=args.tts_url,
                        tts_model=args.tts_model,
                        tts_voice=str(params.get("voice") or args.tts_voice),
                        tts_language=args.tts_language,
                        instructions=str(params.get("instructions") or ""),
                        volume_percent=settings.get("voice_volume_percent"),
                    )
                elif action == "speak_listen":
                    # One task, both directions: see speak_while_listening.
                    heartbeat(args.server, args.token, args.device_id, "running", current_task_id=task_id)
                    ok, stdout, stderr = speak_while_listening(
                        str(params.get("text") or ""),
                        int(params.get("seconds") or 30),
                        speak_at_ms=int(params.get("speak_at_ms") or 3000),
                        repeat=int(params.get("repeat") or 1),
                        gap_ms=int(params.get("gap_ms") or 10000),
                        device=args.voice_device,
                        capture_device=args.capture_device,
                        tts_url=args.tts_url,
                        tts_model=args.tts_model,
                        tts_voice=str(params.get("voice") or args.tts_voice),
                        tts_language=args.tts_language,
                        instructions=str(params.get("instructions") or ""),
                        volume_percent=settings.get("voice_volume_percent"),
                        transcribe=bool(params.get("transcribe", True)),
                    )
                elif action == "listen":
                    # Also no ROS action: record locally and ship the WAV.
                    heartbeat(args.server, args.token, args.device_id, "running", current_task_id=task_id)
                    ok, stdout, stderr = listen_and_report(
                        int(params.get("seconds") or 10),
                        capture_device=args.capture_device,
                        transcribe=bool(params.get("transcribe", True)),
                    )
                else:
                    ok, stdout, stderr = execute_with_running_heartbeats(
                        args.server,
                        args.token,
                        args.device_id,
                        task_id,
                        action,
                        settings,
                        args.controller_url,
                        args.no_controller,
                    )
                if ok and action not in ("speak", "listen", "speak_listen"):
                    voice_output = schedule_completion_voice(
                        action,
                        enabled=not args.no_voice,
                        device=args.voice_device,
                        tts_url=args.tts_url,
                        tts_model=args.tts_model,
                        tts_voice=args.tts_voice,
                        tts_language=args.tts_language,
                        volume_percent=settings.get("voice_volume_percent"),
                    )
                    if voice_output:
                        stdout = "\n".join(part for part in [stdout, voice_output] if part)
                request_json(
                    f"{args.server.rstrip('/')}/api/tasks/result",
                    method="POST",
                    payload={
                        "task_id": task_id,
                        "device_id": args.device_id,
                        "status": "complete" if ok else "failed",
                        "output": stdout[-5000:],
                        "error": stderr[-2000:],
                    },
                    token=args.token,
                )
                last_idle_heartbeat = 0.0
            elif time.monotonic() - last_idle_heartbeat >= IDLE_HEARTBEAT_SECONDS:
                diagnostics = None
                now = time.monotonic()
                if now - last_diagnostic_heartbeat >= DIAGNOSTIC_HEARTBEAT_SECONDS:
                    diagnostics = collect_diagnostics()
                    last_diagnostic_heartbeat = now
                heartbeat(args.server, args.token, args.device_id, "idle", diagnostics=diagnostics)
                last_idle_heartbeat = time.monotonic()
        except (urllib.error.URLError, TimeoutError, subprocess.SubprocessError, OSError) as exc:
            print(f"[WARN] {exc}", flush=True)
        if args.once:
            return 0
        time.sleep(max(args.interval, 0.05))


if __name__ == "__main__":
    raise SystemExit(main())
