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
    ALLOWED_STORYBOARD_INPUT_KINDS,
    MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    MAX_EVENT_ASSET_PIXELS,
    MAX_EVENT_VIDEO_DURATION_SECONDS,
    MIN_EVENT_ASSET_DIMENSION,
    STORYBOARD_FFMPEG_OPERATION_TYPES,
    STORYBOARD_FFMPEG_PROCESSING_TASK,
    STORYBOARD_FFMPEG_PROCESSOR_ID,
    STORYBOARD_FFMPEG_PROCESSOR_IDS,
    STORYBOARD_AUDIO_INPUT_KINDS,
    STORYBOARD_IMAGE_INPUT_KINDS,
    STORYBOARD_LOCAL_VIDEO_TAKE_OPERATION_TYPES,
    STORYBOARD_SINGLE_VIDEO_OPERATION_TYPES,
    STORYBOARD_VIDEO_INPUT_KINDS,
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


def coerce_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def optional_positive_float(value: Any, maximum: float) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return min(float(maximum), number)


def first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    return None


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


def storyboard_operation_type(payload: dict[str, Any]) -> str:
    processing = payload.get("processing") if isinstance(payload.get("processing"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return str(
        payload.get("operation_type")
        or payload.get("operation")
        or payload.get("mediaassembly_operation_type")
        or payload.get("mediaassembly_operation")
        or payload.get("mediaassemblyjob_operation_type")
        or processing.get("operation_type")
        or processing.get("operation")
        or processing.get("mediaassembly_operation_type")
        or processing.get("mediaassembly_operation")
        or summary.get("operation_type")
        or summary.get("operation")
        or summary.get("mediaassembly_operation_type")
        or summary.get("mediaassembly_operation")
        or ""
    ).strip()


def is_event_video_processing_job(job: dict[str, Any]) -> bool:
    processing_task = str(job.get("processing_task") or "").strip().lower()
    processor_id = str(job.get("processor_id") or job.get("model_id") or "").strip()
    operation_type = storyboard_operation_type(job)
    return (
        operation_type not in STORYBOARD_FFMPEG_OPERATION_TYPES
        and (processing_task == "event_video_processing" or processor_id == "event_video_ffmpeg_processor")
    )


def is_storyboard_ffmpeg_processing_job(job: dict[str, Any]) -> bool:
    family = str(job.get("family") or "").strip().lower()
    processing_task = str(job.get("processing_task") or "").strip().lower()
    processor_id = str(job.get("processor_id") or job.get("model_id") or "").strip()
    operation_type = storyboard_operation_type(job)
    return (
        operation_type in STORYBOARD_FFMPEG_OPERATION_TYPES
        or processing_task == STORYBOARD_FFMPEG_PROCESSING_TASK
        or processor_id in STORYBOARD_FFMPEG_PROCESSOR_IDS
        or (family == "media_processing" and operation_type in STORYBOARD_FFMPEG_OPERATION_TYPES)
    )


def merged_processing_payload(job: dict[str, Any]) -> dict[str, Any]:
    raw_processing = job.get("processing") if isinstance(job.get("processing"), dict) else {}
    operation_payload = job.get("operation_payload") if isinstance(job.get("operation_payload"), dict) else {}
    nested_operation_payload = raw_processing.get("operation_payload") if isinstance(raw_processing.get("operation_payload"), dict) else {}
    return {
        **operation_payload,
        **nested_operation_payload,
        **{key: value for key, value in raw_processing.items() if key != "operation_payload"},
    }


def validate_storyboard_ffmpeg_job(job: dict[str, Any]) -> dict[str, Any]:
    family = str(job.get("family") or "").strip().lower()
    if family != "media_processing":
        raise ValueError(f"Unsupported storyboard processing family: {family}")
    media_type = str(job.get("media_type") or "").strip().lower()
    if media_type != "video":
        raise ValueError(f"Unsupported storyboard processing media_type: {media_type}")
    operation_type = storyboard_operation_type(job)
    if operation_type not in STORYBOARD_FFMPEG_OPERATION_TYPES:
        raise ValueError(f"Unsupported storyboard FFmpeg operation_type: {operation_type}")
    processing_task = str(job.get("processing_task") or STORYBOARD_FFMPEG_PROCESSING_TASK).strip().lower()
    if processing_task not in {"", STORYBOARD_FFMPEG_PROCESSING_TASK, "mediaassemblyjob", "mediaassembly_ffmpeg", "storyboard_ffmpeg"}:
        raise ValueError(f"Unsupported storyboard processing_task: {processing_task}")
    processor_id = str(job.get("processor_id") or job.get("model_id") or STORYBOARD_FFMPEG_PROCESSOR_ID).strip()
    if processor_id not in {"", *STORYBOARD_FFMPEG_PROCESSOR_IDS}:
        raise ValueError(f"Unsupported storyboard processor_id: {processor_id}")
    output = job.get("output") or {}
    if not isinstance(output, dict):
        raise ValueError("Storyboard FFmpeg output must be a JSON object.")
    try:
        output_count = int(output.get("count") or 1)
    except (TypeError, ValueError):
        raise ValueError(f"Unsupported storyboard output count: {output.get('count')}")
    if output_count != 1:
        raise ValueError(f"Unsupported storyboard output count: {output_count}")
    output_format = str(output.get("format") or "mp4").strip().lower()
    if output_format != "mp4":
        raise ValueError(f"Unsupported storyboard output format: {output_format}")
    processing = merged_processing_payload(job)
    width = coerce_int(output.get("width") or processing.get("target_width") or processing.get("output_width") or processing.get("width"), 0, 0, 4096)
    height = coerce_int(output.get("height") or processing.get("target_height") or processing.get("output_height") or processing.get("height"), 0, 0, 4096)
    if (width == 0) != (height == 0):
        raise ValueError("Storyboard output width and height must be supplied together.")
    trim_start = coerce_float(
        first_present(processing, "trim_start_seconds", "start_seconds", "start_time_seconds"),
        0.0,
        0.0,
        MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    )
    trim_duration = optional_positive_float(
        first_present(processing, "duration_seconds", "trim_duration_seconds"),
        MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    )
    trim_end = optional_positive_float(
        first_present(processing, "trim_end_seconds", "end_seconds", "end_time_seconds"),
        MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    )
    inputs = job.get("inputs") or []
    if not isinstance(inputs, list):
        raise ValueError("Storyboard FFmpeg inputs must be a list.")
    render_mode = str(processing.get("render_mode") or "").strip().lower()
    video_count = 0
    image_count = 0
    audio_count = 0
    start_image_count = 0
    end_image_count = 0
    scene_audio_count = 0
    source_video_count = 0
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("Storyboard FFmpeg input descriptor must be a JSON object.")
        kind = str(item.get("kind") or "").strip()
        if kind not in ALLOWED_STORYBOARD_INPUT_KINDS:
            raise ValueError(f"Unsupported storyboard FFmpeg input kind: {kind}")
        if kind in STORYBOARD_VIDEO_INPUT_KINDS:
            video_count += 1
            if kind == "source_video":
                source_video_count += 1
        elif kind in STORYBOARD_IMAGE_INPUT_KINDS:
            image_count += 1
            if kind == "start_image":
                start_image_count += 1
            elif kind == "end_image":
                end_image_count += 1
        elif kind in STORYBOARD_AUDIO_INPUT_KINDS:
            audio_count += 1
            if kind == "scene_audio":
                scene_audio_count += 1
        else:
            audio_count += 1
    if operation_type == "multicam_final_assembly":
        if video_count < 1:
            raise ValueError("Storyboard final assembly requires at least one video input.")
    elif operation_type == "replace_video_soundtrack":
        if video_count != 1:
            raise ValueError(f"Storyboard replace_video_soundtrack requires exactly one source video input; got {video_count}.")
        if audio_count != 1:
            raise ValueError(f"Storyboard replace_video_soundtrack requires exactly one soundtrack audio input; got {audio_count}.")
    elif operation_type == "segmented_media_segment_normalize":
        if video_count != 1 or source_video_count != 1 or image_count or audio_count:
            raise ValueError(
                "Storyboard segmented_media_segment_normalize requires exactly one source_video input; "
                f"got source_video={source_video_count}, video_inputs={video_count}, image_inputs={image_count}, audio_inputs={audio_count}."
            )
        if width <= 0 or height <= 0:
            raise ValueError("Storyboard segmented_media_segment_normalize requires target_width and target_height.")
        resize_mode = str(processing.get("resize_mode") or "scale_to_cover_crop").strip().lower()
        if resize_mode not in {"scale_to_cover_crop", "cover", "crop"}:
            raise ValueError(f"Unsupported segmented media segment resize_mode: {resize_mode}")
    elif operation_type in STORYBOARD_LOCAL_VIDEO_TAKE_OPERATION_TYPES:
        if render_mode not in {"one_image", "two_image_fade", "voice_over_video"}:
            raise ValueError(f"Unsupported storyboard local video take render_mode: {render_mode or 'missing'}")
        if render_mode == "voice_over_video":
            if video_count != 1:
                raise ValueError(f"Storyboard local voice_over_video render requires exactly one source_video input; got {video_count}.")
            if scene_audio_count != 1 or audio_count != 1:
                raise ValueError(
                    "Storyboard local voice_over_video render requires exactly one scene_audio input and no other audio inputs; "
                    f"got scene_audio={scene_audio_count}, audio_inputs={audio_count}."
                )
            if start_image_count or end_image_count:
                raise ValueError(
                    "Storyboard local voice_over_video render does not use start_image or end_image inputs; "
                    f"got start_image={start_image_count}, end_image={end_image_count}."
                )
            transition = str(processing.get("video_transition") or "").strip().lower()
            if transition not in {"", "none"}:
                raise ValueError(f"Storyboard local voice_over_video does not support video_transition={transition!r}.")
            duration = optional_positive_float(processing.get("duration_seconds"), MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
            if duration is None:
                raise ValueError("Storyboard local voice_over_video requires explicit duration_seconds.")
        else:
            if video_count:
                raise ValueError(f"Storyboard local {render_mode} render does not support video inputs; got {video_count}.")
            if audio_count:
                raise ValueError("Storyboard local video take one_image/two_image_fade does not support audio inputs yet.")
        if coerce_bool(processing.get("pan_enabled"), False):
            raise ValueError("Storyboard local video take pan_enabled=true is not supported by the bridge first pass.")
        if render_mode == "one_image":
            if start_image_count != 1:
                raise ValueError(f"Storyboard local one_image render requires exactly one start_image input; got {start_image_count}.")
            if end_image_count:
                raise ValueError(f"Storyboard local one_image render does not support end_image inputs; got {end_image_count}.")
        elif render_mode == "two_image_fade":
            if start_image_count != 1 or end_image_count != 1:
                raise ValueError(
                    "Storyboard local two_image_fade render requires exactly one start_image and one end_image input; "
                    f"got start_image={start_image_count}, end_image={end_image_count}."
                )
    elif operation_type in STORYBOARD_SINGLE_VIDEO_OPERATION_TYPES and video_count < 1:
        raise ValueError(f"Storyboard operation {operation_type} requires at least one video input.")
    return {
        **processing,
        "operation_type": operation_type,
        "output_width": width,
        "output_height": height,
        "trim_start_seconds": trim_start,
        "trim_duration_seconds": trim_duration,
        "trim_end_seconds": trim_end,
    }


def validate_media_processing_job(job: dict[str, Any]) -> dict[str, Any]:
    if is_storyboard_ffmpeg_processing_job(job):
        return validate_storyboard_ffmpeg_job(job)
    return validate_event_job(job)


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


def validate_storyboard_video(path: Path) -> dict[str, Any]:
    metadata = probe_video_metadata(path)
    duration = float(metadata.get("duration_seconds") or 0.0)
    if duration > MAX_STORYBOARD_VIDEO_DURATION_SECONDS + 0.05:
        raise ValueError(f"Storyboard FFmpeg video input exceeds {MAX_STORYBOARD_VIDEO_DURATION_SECONDS} seconds; got {duration:.2f}s.")
    return metadata


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
