from __future__ import annotations

from typing import Any

from .constants import (
    EVENT_VIDEO_H264_ENCODER,
    EVENT_VIDEO_OUTPUT_PROFILE,
    EVENT_VIDEO_PROCESSING_TASK,
    EVENT_VIDEO_PROCESSOR_ID,
    MAX_EVENT_VIDEO_DURATION_SECONDS,
    MAX_EVENT_VIDEO_INPUT_BYTES,
)
from .ffmpeg_probe import probe_ffmpeg


def build_capabilities() -> dict[str, Any]:
    probe = probe_ffmpeg()
    media_processing = []
    if probe["ffmpeg_available"] and probe["ffprobe_available"] and probe["libx264_available"]:
        source_types = ["video/mp4"]
        if probe["quicktime_demux_available"]:
            source_types.append("video/quicktime")
        media_processing.append({
            "worker_kind": "local_media_processor",
            "worker_display_name": "Local Event Video Processor",
            "family": "media_processing",
            "media_type": "video",
            "processing_task": EVENT_VIDEO_PROCESSING_TASK,
            "processor_id": EVENT_VIDEO_PROCESSOR_ID,
            "display_name": "Event Video Processing",
            "supported": True,
            "detected": True,
            "ffmpeg_available": True,
            "ffprobe_available": True,
            "nvenc_available": False,
            "h264_encoder": EVENT_VIDEO_H264_ENCODER,
            "supports_overlay_png": True,
            "supports_bumper": True,
            "supports_poster": False,
            "max_input_bytes": MAX_EVENT_VIDEO_INPUT_BYTES,
            "max_duration_seconds": MAX_EVENT_VIDEO_DURATION_SECONDS,
            "supported_output_profiles": [EVENT_VIDEO_OUTPUT_PROFILE],
            "output_mime_types": ["video/mp4"],
            "input_mime_types": {
                "source_video": source_types,
                "overlay_png": ["image/png"],
                "bumper_image": ["image/png", "image/jpeg", "image/webp"],
            },
            "output_dimensions": {
                "landscape": {"width": 1280, "height": 720},
                "portrait": {"width": 720, "height": 1280},
            },
            "scaling_mode": "scale_to_cover_crop",
        })
    return {
        "schema_version": 1,
        "worker_kind": "local_media_processor",
        "worker_display_name": "Local Event Video Processor",
        "media_types": ["video"],
        "models": [],
        "media_processing": media_processing,
        "unsupported_detected_models": [],
    }
