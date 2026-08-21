from __future__ import annotations

from typing import Any

from .constants import (
    EVENT_VIDEO_H264_ENCODER,
    EVENT_VIDEO_OUTPUT_PROFILE,
    EVENT_VIDEO_PROCESSING_TASK,
    EVENT_VIDEO_PROCESSOR_ID,
    MAX_EVENT_VIDEO_DURATION_SECONDS,
    MAX_EVENT_VIDEO_INPUT_BYTES,
    MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    MAX_STORYBOARD_VIDEO_INPUT_BYTES,
    STORYBOARD_FFMPEG_OPERATION_TYPES,
    STORYBOARD_FFMPEG_PROCESSING_TASK,
    STORYBOARD_FFMPEG_PROCESSOR_ID,
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
        storyboard_source_types = ["video/mp4", "video/webm"]
        if probe["quicktime_demux_available"]:
            storyboard_source_types.append("video/quicktime")
        media_processing.append({
            "worker_kind": "local_media_processor",
            "worker_display_name": "Local Event Video Processor",
            "family": "media_processing",
            "media_type": "video",
            "processing_task": STORYBOARD_FFMPEG_PROCESSING_TASK,
            "processor_id": STORYBOARD_FFMPEG_PROCESSOR_ID,
            "display_name": "Storyboard FFmpeg Processing",
            "supported": True,
            "detected": True,
            "ffmpeg_available": True,
            "ffprobe_available": True,
            "nvenc_available": False,
            "h264_encoder": EVENT_VIDEO_H264_ENCODER,
            "operation_types": sorted(STORYBOARD_FFMPEG_OPERATION_TYPES),
            "supported_operations": sorted(STORYBOARD_FFMPEG_OPERATION_TYPES),
            "max_input_bytes": MAX_STORYBOARD_VIDEO_INPUT_BYTES,
            "max_duration_seconds": MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
            "supports_trim": True,
            "supports_concat": True,
            "supports_overlay": True,
            "supports_faststart": True,
            "operation_features": {
                "multicam_card_overlay_take": [
                    "static_rectangle_overlay",
                    "animated_rectangle_overlay",
                    "animated_position",
                    "animated_opacity",
                    "fixed_canvas_animated_scale",
                    "luminance_matte_png",
                    "luminance_matte_jpeg",
                    "base_or_overlay_audio",
                ],
                "multicam_final_assembly": [
                    "ordered_segments",
                    "segment_trim",
                    "normalize_before_concat",
                    "silent_audio_fill",
                    "h264_aac_mp4_faststart",
                ],
                "multicam_optimize_video": [
                    "h264_aac_reencode",
                    "faststart",
                    "max_dimension_scale",
                    "crf_preset_audio_bitrate",
                ],
                "optimize_video": [
                    "h264_aac_reencode",
                    "faststart",
                    "max_dimension_scale",
                    "crf_preset_audio_bitrate",
                ],
                "replace_video_soundtrack": [
                    "source_video_stream",
                    "soundtrack_audio_replacement",
                    "soundtrack_video_container_audio",
                    "video_and_audio_start_offsets",
                    "duration_trim",
                    "head_tail_silence",
                    "h264_aac_mp4_faststart",
                ],
                "multicam_card_local_video_take": [
                    "one_image",
                    "two_image_fade",
                    "voice_over_video",
                    "silent_audio_fill",
                    "scene_audio_voice_over",
                    "h264_aac_mp4_faststart",
                ],
                "mediastoryboard_card_local_video_take": [
                    "one_image",
                    "two_image_fade",
                    "voice_over_video",
                    "silent_audio_fill",
                    "scene_audio_voice_over",
                    "h264_aac_mp4_faststart",
                ],
            },
            "output_mime_types": ["video/mp4"],
            "input_mime_types": {
                "video": storyboard_source_types,
                "source_video": storyboard_source_types,
                "input_video": storyboard_source_types,
                "take_video": storyboard_source_types,
                "card_video": storyboard_source_types,
                "segment_video": storyboard_source_types,
                "overlay_video": storyboard_source_types,
                "control_video": storyboard_source_types,
                "image": ["image/png", "image/jpeg", "image/webp"],
                "source_image": ["image/png", "image/jpeg", "image/webp"],
                "overlay_image": ["image/png", "image/jpeg", "image/webp"],
                "overlay_png": ["image/png", "image/jpeg", "image/webp"],
                "poster_image": ["image/png", "image/jpeg", "image/webp"],
                "start_image": ["image/png", "image/jpeg", "image/webp"],
                "end_image": ["image/png", "image/jpeg", "image/webp"],
                "matte_image": ["image/png", "image/jpeg"],
                "mask_image": ["image/png", "image/jpeg"],
                "overlay_matte": ["image/png", "image/jpeg"],
                "overlay_mask": ["image/png", "image/jpeg"],
                "audio": ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/webm", "audio/ogg"],
                "scene_audio": ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/webm", "audio/ogg"],
                "source_audio": ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/webm", "audio/ogg", "video/mp4", "video/quicktime", "video/webm"],
                "soundtrack_audio": ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/webm", "audio/ogg", "video/mp4", "video/quicktime", "video/webm"],
                "driving_audio": ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/webm", "audio/ogg"],
                "narration_audio": ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/webm", "audio/ogg"],
            },
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
