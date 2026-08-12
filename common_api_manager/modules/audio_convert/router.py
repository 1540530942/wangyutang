from __future__ import annotations

from typing import Any

import os

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from common.settings import settings

from .service import (
    OUTPUT_FORMATS,
    SAMPLE_RATE,
    build_archive,
    convert_upload,
    delete_job,
    ffmpeg_version,
    list_jobs,
    read_manifest,
    resolve_file,
)


router = APIRouter(tags=["audio_convert"])

BASE_PATH = "api/audio/convert"


def _decorate(manifest: dict[str, Any]) -> dict[str, Any]:
    """Attach caller-resolvable download paths to a manifest."""
    job_id = manifest["job_id"]
    for chunk in manifest["chunks"]:
        chunk["path"] = f"{BASE_PATH}/{job_id}/files/{chunk['file']}"
    manifest["manifest_path"] = f"{BASE_PATH}/{job_id}"
    manifest["archive_path"] = f"{BASE_PATH}/{job_id}/archive"
    return manifest


@router.get("/api/audio/convert/capabilities")
def capabilities() -> dict[str, Any]:
    version = ffmpeg_version()
    return {
        "available": bool(version),
        "ffmpeg": version or "not installed",
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "formats": [
            {"name": name, "extension": fmt.extension, "content_type": fmt.content_type, "label": fmt.label}
            for name, fmt in sorted(OUTPUT_FORMATS.items())
        ],
        "max_upload_bytes": settings.audio_convert_max_upload_bytes,
        "max_jobs": settings.audio_convert_max_jobs,
        "max_age_hours": settings.audio_convert_max_age_hours,
    }


@router.post("/api/audio/convert")
def convert(
    file: UploadFile = File(...),
    format: str = Form("wav"),
    split: float = Form(0),
) -> dict[str, Any]:
    manifest = convert_upload(
        file.file,
        filename=file.filename or "audio",
        output_format=format.strip().lower(),
        split_seconds=split,
    )
    return _decorate(manifest)


@router.get("/api/audio/convert/jobs")
def jobs(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"jobs": list_jobs(limit)}


@router.get("/api/audio/convert/{job_id}")
def job_manifest(job_id: str) -> dict[str, Any]:
    return _decorate(read_manifest(job_id))


@router.get("/api/audio/convert/{job_id}/archive")
def job_archive(job_id: str) -> FileResponse:
    path = build_archive(job_id)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{job_id}.zip",
        background=BackgroundTask(os.remove, path),
    )


@router.get("/api/audio/convert/{job_id}/files/{filename}")
def job_file(job_id: str, filename: str) -> FileResponse:
    path, content_type = resolve_file(job_id, filename)
    return FileResponse(path, media_type=content_type, filename=filename)


@router.delete("/api/audio/convert/{job_id}")
def job_delete(job_id: str) -> dict[str, str]:
    delete_job(job_id)
    return {"status": "deleted", "job_id": job_id}
