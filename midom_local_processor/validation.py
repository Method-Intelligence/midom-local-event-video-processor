from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from .constants import (
    ALLOWED_EVENT_BUMPER_MIME_TYPES,
    ALLOWED_EVENT_INPUT_KINDS,
    ALLOWED_EVENT_OVERLAY_MIME_TYPES,
    ALLOWED_EVENT_SOURCE_VIDEO_MIME_TYPES,
    MAX_EVENT_ASSET_PIXELS,
    MAX_EVENT_VIDEO_DURATION_SECONDS,
    MIN_EVENT_ASSET_DIMENSION,
)
from .ffmpeg_probe import probe_video_metadata


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def coerce_job_id(job: dict[str, Any]) -> int:
    try:
        job_id = int(job.get("job_id"))
    except (TypeError, ValueError):
        raise ValueError("Job id is required.")
    if job_id <= 0:
        raise ValueError("Job id is required.")
    return job_id


def coerce_input_id(item: dict[str, Any]) -> int:
    try:
        input_id = int(item.get("input_id"))
    except (TypeError, ValueError):
        raise ValueError("Job input descriptor did not include a valid input_id.")
    if input_id <= 0:
        raise ValueError("Job input descriptor did not include a valid input_id.")
    return input_id


def read_limited_response_content(response: requests.Response, input_id: int, max_bytes: int) -> bytes:
    content_length = str(response.headers.get("Content-Length") or "").strip()
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            raise ValueError(f"Input {input_id} returned an invalid Content-Length.")
        if declared_size < 0 or declared_size > max_bytes:
            raise ValueError(f"Input {input_id} exceeds maximum allowed download size.")
    data = bytearray()
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        data.extend(chunk)
        if len(data) > max_bytes:
            raise ValueError(f"Input {input_id} exceeds maximum allowed download size.")
    return bytes(data)


def validate_job_scope(job: dict[str, Any], *, org_id: int, project_id: int, paired_user_id: int) -> None:
    target = job.get("target") or {}
    if not isinstance(target, dict):
        raise ValueError("Job target must be a JSON object when provided.")
    checks = {
        "org_id": int(org_id),
        "project_id": int(project_id),
        "requested_by_user_id": int(paired_user_id),
    }
    for key, expected in checks.items():
        try:
            actual = int(target.get(key))
        except (TypeError, ValueError):
            raise ValueError(f"Job target {key} does not match paired worker scope.")
        if actual != expected:
            raise ValueError(f"Job target {key} does not match paired worker scope.")


def validate_event_job(job: dict[str, Any]) -> dict[str, Any]:
    if str(job.get("family") or "").strip().lower() != "media_processing":
        raise ValueError("Unsupported family for standalone local processor.")
    if str(job.get("media_type") or "").strip().lower() != "video":
        raise ValueError("Unsupported media_type for standalone local processor.")
    if str(job.get("processing_task") or "").strip().lower() != "event_video_processing":
        raise ValueError("Unsupported processing_task for standalone local processor.")
    if str(job.get("processor_id") or job.get("model_id") or "").strip() != "event_video_ffmpeg_processor":
        raise ValueError("Unsupported processor_id for standalone local processor.")
    output = job.get("output") or {}
    if not isinstance(output, dict):
        raise ValueError("Event Video Processing output must be a JSON object.")
    if int(output.get("count") or 1) != 1:
        raise ValueError("Event Video Processing output count must be 1.")
    if str(output.get("format") or "mp4").strip().lower() != "mp4":
        raise ValueError("Event Video Processing output format must be mp4.")
    if str(output.get("profile") or "mobile_public_720p").strip() != "mobile_public_720p":
        raise ValueError("Unsupported Event Video Processing output profile.")
    processing = job.get("processing") or {}
    if not isinstance(processing, dict):
        raise ValueError("Event Video Processing processing payload must be a JSON object.")
    if not coerce_bool(processing.get("normalize"), True):
        raise ValueError("Event Video Processing requires processing.normalize = true.")
    if not coerce_bool(processing.get("optimize_for_web"), True):
        raise ValueError("Event Video Processing requires processing.optimize_for_web = true.")
    if not coerce_bool(processing.get("preserve_orientation"), True):
        raise ValueError("Event Video Processing requires processing.preserve_orientation = true.")
    inputs = job.get("inputs") or []
    if not isinstance(inputs, list):
        raise ValueError("Event Video Processing inputs must be a list.")
    source_count = 0
    overlay_count = 0
    bumper_count = 0
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("Event Video Processing input descriptor must be a JSON object.")
        kind = str(item.get("kind") or "").strip()
        if kind not in ALLOWED_EVENT_INPUT_KINDS:
            raise ValueError(f"Unsupported Event Video Processing input kind: {kind}")
        if kind == "source_video":
            source_count += 1
        else:
            orientation = str(item.get("orientation") or "").strip().lower()
            if orientation not in {"portrait", "landscape"}:
                raise ValueError(f"{kind} inputs require orientation portrait or landscape.")
            if kind == "overlay_png":
                overlay_count += 1
            else:
                bumper_count += 1
    apply_overlay = coerce_bool(processing.get("apply_overlay"), False)
    add_bumper = coerce_bool(processing.get("add_ending_bumper"), False)
    if source_count != 1:
        raise ValueError(f"Event Video Processing requires exactly one source_video input; got {source_count}.")
    if apply_overlay and overlay_count <= 0:
        raise ValueError("Event Video Processing apply_overlay is true but no overlay_png inputs were provided.")
    if add_bumper and bumper_count <= 0:
        raise ValueError("Event Video Processing add_ending_bumper is true but no bumper_image inputs were provided.")
    if not apply_overlay and overlay_count:
        raise ValueError("Event Video Processing received overlay_png inputs but apply_overlay is false.")
    if not add_bumper and bumper_count:
        raise ValueError("Event Video Processing received bumper_image inputs but add_ending_bumper is false.")
    return processing


def event_job_max_output_duration_seconds(job: dict[str, Any]) -> int:
    output = job.get("output") if isinstance(job.get("output"), dict) else {}
    configured = output.get("max_duration_seconds")
    if configured is None:
        configured = job.get("max_duration_seconds")
    return coerce_int(configured, MAX_EVENT_VIDEO_DURATION_SECONDS, 1, MAX_EVENT_VIDEO_DURATION_SECONDS)


def validate_asset_dimensions(kind: str, width: int, height: int) -> None:
    if width < MIN_EVENT_ASSET_DIMENSION or height < MIN_EVENT_ASSET_DIMENSION:
        raise ValueError(f"Event Video Processing {kind} asset is too small: {width}x{height}.")
    pixels = int(width) * int(height)
    if pixels > MAX_EVENT_ASSET_PIXELS:
        raise ValueError(f"Event Video Processing {kind} asset is too large: {width}x{height}.")


def decode_image_info(path: Path) -> tuple[int, int, str]:
    with Image.open(path) as image:
        width, height = image.size
        decoded_format = str(image.format or "").upper()
    return width, height, decoded_format


def validate_source_video(path: Path) -> dict[str, Any]:
    metadata = probe_video_metadata(path)
    duration = float(metadata.get("duration_seconds") or 0.0)
    if duration > MAX_EVENT_VIDEO_DURATION_SECONDS + 0.05:
        raise ValueError(f"Event source video exceeds {MAX_EVENT_VIDEO_DURATION_SECONDS} seconds; got {duration:.2f}s.")
    return metadata


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
