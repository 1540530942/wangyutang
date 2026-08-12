from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from typing import Any, BinaryIO

from fastapi import HTTPException

from common.settings import settings


SAMPLE_RATE = 16000
SILENCE_NOISE_DB = -35
SILENCE_MIN_DURATION = 0.45
JOB_ID_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{8}$")
SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class OutputFormat:
    extension: str
    codec_args: tuple[str, ...]
    content_type: str
    label: str


OUTPUT_FORMATS: dict[str, OutputFormat] = {
    "wav": OutputFormat("wav", ("-c:a", "pcm_s16le"), "audio/wav", "16kHz 单声道 PCM，体积最大，兼容性最好"),
    "opus": OutputFormat("opus", ("-c:a", "libopus", "-b:a", "24k"), "audio/ogg", "16kHz 单声道 Opus 24kbps，体积最小"),
    "mp3": OutputFormat("mp3", ("-c:a", "libmp3lame", "-b:a", "32k"), "audio/mpeg", "16kHz 单声道 MP3 32kbps，通用播放"),
}

_slots = threading.BoundedSemaphore(max(1, settings.audio_convert_concurrency))


def storage_root() -> str:
    os.makedirs(settings.audio_convert_dir, exist_ok=True)
    return settings.audio_convert_dir


def ffmpeg_version() -> str:
    binary = shutil.which("ffmpeg")
    if binary is None:
        return ""
    result = subprocess.run([binary, "-version"], capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout.splitlines()[0].strip()


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"audio conversion unavailable: {', '.join(missing)} not installed on the API host",
        )


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.audio_convert_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="ffmpeg timed out while processing the audio") from exc


def _clean_error(message: str, path: str) -> str:
    """Trim an ffmpeg/ffprobe error and drop server-side paths from it."""
    return message.replace(path, "<input>").strip()[:400]


def probe_duration(path: str) -> float:
    result = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ])
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=f"unreadable audio file: {_clean_error(result.stderr, path)}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="could not determine audio duration") from exc


def detect_silences(path: str) -> list[tuple[float, float]]:
    """Return [(start, end), ...] for every detected silent stretch."""
    result = _run([
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", path,
        "-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_DURATION}",
        "-f", "null", "-",
    ])
    # silencedetect reports on stderr even when the run succeeds.
    starts = [float(value) for value in re.findall(r"silence_start: ([0-9.]+)", result.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end: ([0-9.]+)", result.stderr)]
    return list(zip(starts, ends))


def plan_cuts(duration: float, silences: list[tuple[float, float]], target: float) -> list[float]:
    """Pick cut points near every `target` boundary, preferring silence midpoints."""
    lower, upper = target * 0.5, target * 1.5
    candidates = sorted((start + end) / 2 for start, end in silences)

    cuts: list[float] = []
    position = 0.0
    while duration - position > upper:
        window = [value for value in candidates if position + lower <= value <= position + upper]
        cut = min(window, key=lambda value: abs(value - (position + target))) if window else position + target
        cuts.append(cut)
        position = cut
    return cuts


def _encode(source: str, target: str, fmt: OutputFormat, start: float | None, end: float | None) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", source]
    if start is not None and end is not None:
        cmd += ["-ss", f"{start:.3f}", "-to", f"{end:.3f}"]
    cmd += ["-ac", "1", "-ar", str(SAMPLE_RATE), *fmt.codec_args, target]

    result = _run(cmd)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=f"ffmpeg failed: {_clean_error(result.stderr, source)}")


def _sanitize_stem(filename: str) -> str:
    """ASCII-safe stem for output files; empty when nothing usable survives."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem[:60]


def _store_upload(stream: BinaryIO, destination: str) -> int:
    limit = settings.audio_convert_max_upload_bytes
    written = 0
    with open(destination, "wb") as handle:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            written += len(block)
            if written > limit:
                handle.close()
                os.remove(destination)
                raise HTTPException(
                    status_code=413,
                    detail=f"upload too large, max {limit} bytes ({limit // 1024 // 1024} MB)",
                )
            handle.write(block)
    if written == 0:
        os.remove(destination)
        raise HTTPException(status_code=400, detail="empty upload")
    return written


def _job_dir(job_id: str) -> str:
    if not JOB_ID_PATTERN.match(job_id):
        raise HTTPException(status_code=400, detail="invalid job id")
    path = os.path.join(storage_root(), job_id)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    return path


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def prune_jobs() -> list[str]:
    """Drop jobs that exceed the age, count or total-size budget. Returns removed ids."""
    root = storage_root()
    entries = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if JOB_ID_PATTERN.match(name) and os.path.isdir(path):
            entries.append((os.path.getmtime(path), name, path, _dir_size(path)))
    entries.sort(reverse=True)

    removed: list[str] = []
    cutoff = time.time() - settings.audio_convert_max_age_hours * 3600
    running_total = 0
    for index, (mtime, name, path, size) in enumerate(entries):
        running_total += size
        too_old = mtime < cutoff
        too_many = index >= settings.audio_convert_max_jobs
        too_big = running_total > settings.audio_convert_max_store_bytes and index > 0
        if too_old or too_many or too_big:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(name)
    return removed


def read_manifest(job_id: str) -> dict[str, Any]:
    path = os.path.join(_job_dir(job_id), MANIFEST_NAME)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"manifest not found for job {job_id}")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    root = storage_root()
    names = sorted(
        (name for name in os.listdir(root) if JOB_ID_PATTERN.match(name)),
        reverse=True,
    )
    jobs = []
    for name in names[:limit]:
        try:
            manifest = read_manifest(name)
        except HTTPException:
            continue
        jobs.append({
            "job_id": manifest["job_id"],
            "source_name": manifest["source_name"],
            "created_at": manifest["created_at"],
            "duration_seconds": manifest["duration_seconds"],
            "format": manifest["format"],
            "chunk_count": len(manifest["chunks"]),
            "output_bytes": manifest["output_bytes"],
        })
    return jobs


def resolve_file(job_id: str, filename: str) -> tuple[str, str]:
    """Return (absolute_path, content_type) for a file that belongs to a job."""
    if not SAFE_NAME_PATTERN.match(filename):
        raise HTTPException(status_code=400, detail="invalid file name")
    directory = _job_dir(job_id)
    path = os.path.join(directory, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"file not found: {filename}")

    extension = os.path.splitext(filename)[1].lstrip(".").lower()
    for fmt in OUTPUT_FORMATS.values():
        if fmt.extension == extension:
            return path, fmt.content_type
    return path, "application/json" if extension == "json" else "application/octet-stream"


def delete_job(job_id: str) -> None:
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)


def build_archive(job_id: str) -> str:
    """Zip a job's outputs into a throwaway temp file; the caller deletes it after sending.

    The archive is deliberately built outside the job directory — writing it inside
    would make the zip include itself while it is still growing.
    """
    directory = _job_dir(job_id)
    handle, archive_path = tempfile.mkstemp(prefix=f"{job_id}_", suffix=".zip")
    os.close(handle)
    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name in sorted(os.listdir(directory)):
                source = os.path.join(directory, name)
                if os.path.isfile(source):
                    bundle.write(source, arcname=name)
    except Exception:
        os.remove(archive_path)
        raise
    return archive_path


def convert_upload(stream: BinaryIO, filename: str, output_format: str, split_seconds: float) -> dict[str, Any]:
    require_ffmpeg()
    if output_format not in OUTPUT_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported format '{output_format}', expected one of: {', '.join(sorted(OUTPUT_FORMATS))}",
        )
    if split_seconds and split_seconds < 5:
        raise HTTPException(status_code=400, detail="split must be 0 (no split) or at least 5 seconds")

    if not _slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="converter busy, retry in a moment")

    started = time.time()
    fmt = OUTPUT_FORMATS[output_format]
    job_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    directory = os.path.join(storage_root(), job_id)
    os.makedirs(directory, exist_ok=True)
    upload_path = os.path.join(directory, "source.upload")

    try:
        source_bytes = _store_upload(stream, upload_path)
        # Non-ASCII names (common for Chinese recordings) fall back to the job id
        # so downloaded chunks stay unique across jobs.
        stem = _sanitize_stem(filename) or job_id
        duration = probe_duration(upload_path)

        chunks: list[dict[str, Any]] = []
        if split_seconds <= 0:
            name = f"{stem}.{fmt.extension}"
            _encode(upload_path, os.path.join(directory, name), fmt, None, None)
            chunks.append({"index": 0, "file": name, "start": 0.0, "end": round(duration, 3)})
            silence_count = 0
        else:
            silences = detect_silences(upload_path)
            silence_count = len(silences)
            bounds = [0.0, *plan_cuts(duration, silences, split_seconds), duration]
            for index in range(len(bounds) - 1):
                start, end = bounds[index], bounds[index + 1]
                name = f"{stem}_{index:03d}.{fmt.extension}"
                _encode(upload_path, os.path.join(directory, name), fmt, start, end)
                chunks.append({"index": index, "file": name, "start": round(start, 3), "end": round(end, 3)})

        for chunk in chunks:
            chunk["bytes"] = os.path.getsize(os.path.join(directory, chunk["file"]))

        manifest = {
            "job_id": job_id,
            "source_name": os.path.basename(filename or "audio"),
            "source_bytes": source_bytes,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "duration_seconds": round(duration, 3),
            "sample_rate": SAMPLE_RATE,
            "channels": 1,
            "format": fmt.extension,
            "split_seconds": split_seconds,
            "silence_stretches": silence_count,
            "chunks": chunks,
            "output_bytes": sum(chunk["bytes"] for chunk in chunks),
            "elapsed_seconds": round(time.time() - started, 3),
        }
        with open(os.path.join(directory, MANIFEST_NAME), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        if os.path.isfile(upload_path):
            os.remove(upload_path)
        _slots.release()

    manifest["pruned_jobs"] = prune_jobs()
    return manifest
