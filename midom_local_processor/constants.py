from __future__ import annotations

import ipaddress

APP_ID = "midom-local-media-processor"
USER_AGENT = f"{APP_ID}/0.1.1"

REQUEST_TIMEOUT_SECONDS = 30
UPLOAD_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 5
HEARTBEAT_INTERVAL_SECONDS = 15
PROGRESS_INTERVAL_SECONDS = 2

MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_AUDIO_BYTES = 52_428_800
MAX_EVENT_VIDEO_INPUT_BYTES = 1_073_741_824
MAX_EVENT_VIDEO_OUTPUT_BYTES = 536_870_912
MAX_EVENT_VIDEO_DURATION_SECONDS = 600
MAX_EVENT_ASSET_PIXELS = 33_177_600
MIN_EVENT_ASSET_DIMENSION = 16

EVENT_VIDEO_PROCESSOR_ID = "event_video_ffmpeg_processor"
EVENT_VIDEO_PROCESSING_TASK = "event_video_processing"
EVENT_VIDEO_OUTPUT_PROFILE = "mobile_public_720p"
EVENT_VIDEO_BUMPER_SECONDS = 2
EVENT_VIDEO_H264_ENCODER = "libx264"
EVENT_VIDEO_X264_CRF = 21
EVENT_VIDEO_X264_MAXRATE = "8M"
EVENT_VIDEO_X264_BUFSIZE = "16M"
EVENT_VIDEO_TIMEOUT_BASE_SECONDS = 120
EVENT_VIDEO_TIMEOUT_MULTIPLIER = 12
EVENT_VIDEO_TIMEOUT_MAX_SECONDS = 7200

STORYBOARD_FFMPEG_PROCESSOR_ID = "storyboard_ffmpeg_processor"
STORYBOARD_FFMPEG_PROCESSOR_IDS = {STORYBOARD_FFMPEG_PROCESSOR_ID, "storyboard_ffmpeg"}
STORYBOARD_FFMPEG_PROCESSING_TASK = "storyboard_ffmpeg_processing"
MAX_STORYBOARD_VIDEO_INPUT_BYTES = 1_073_741_824
MAX_STORYBOARD_VIDEO_OUTPUT_BYTES = 536_870_912
MAX_STORYBOARD_AUDIO_INPUT_BYTES = 104_857_600
MAX_STORYBOARD_IMAGE_INPUT_BYTES = MAX_IMAGE_BYTES
MAX_STORYBOARD_VIDEO_DURATION_SECONDS = 30 * 60
STORYBOARD_TIMEOUT_BASE_SECONDS = 120
STORYBOARD_TIMEOUT_MULTIPLIER = 12
STORYBOARD_TIMEOUT_MAX_SECONDS = 7200
STORYBOARD_OUTPUT_FPS = 30

ALLOWED_EVENT_SOURCE_VIDEO_MIME_TYPES = {"video/mp4", "video/quicktime"}
ALLOWED_STORYBOARD_SOURCE_VIDEO_MIME_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
ALLOWED_AUDIO_INPUT_MIME_TYPES = {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/webm", "audio/ogg"}
ALLOWED_STORYBOARD_AUDIO_CONTAINER_MIME_TYPES = ALLOWED_AUDIO_INPUT_MIME_TYPES | {"video/mp4", "video/quicktime", "video/webm"}
ALLOWED_STORYBOARD_MATTE_MIME_TYPES = {"image/png", "image/jpeg"}
ALLOWED_EVENT_OVERLAY_MIME_TYPES = {"image/png"}
ALLOWED_EVENT_BUMPER_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
ALLOWED_EVENT_INPUT_KINDS = {"source_video", "overlay_png", "bumper_image"}
STORYBOARD_FFMPEG_OPERATION_TYPES = {
    "multicam_card_overlay_take",
    "multicam_card_pass_through_take",
    "multicam_card_trim_take",
    "multicam_final_assembly",
    "multicam_optimize_video",
    "optimize_video",
    "replace_video_soundtrack",
    "segmented_media_segment_normalize",
    "multicam_seekable_mp4",
    "multicam_ai_video_take_prepare",
    "multicam_card_local_video_take",
    "mediastoryboard_card_pass_through_take",
    "mediastoryboard_card_local_video_take",
    "mediastoryboard_card_trim_take",
    "mediastoryboard_card_edge_trim_take",
}
STORYBOARD_TRIM_OPERATION_TYPES = {
    "multicam_card_trim_take",
    "mediastoryboard_card_trim_take",
    "mediastoryboard_card_edge_trim_take",
}
STORYBOARD_LOCAL_VIDEO_TAKE_OPERATION_TYPES = {
    "multicam_card_local_video_take",
    "mediastoryboard_card_local_video_take",
}
STORYBOARD_SINGLE_VIDEO_OPERATION_TYPES = {
    "multicam_card_overlay_take",
    "multicam_card_pass_through_take",
    "multicam_card_trim_take",
    "multicam_optimize_video",
    "optimize_video",
    "replace_video_soundtrack",
    "segmented_media_segment_normalize",
    "multicam_seekable_mp4",
    "multicam_ai_video_take_prepare",
    "mediastoryboard_card_pass_through_take",
    "mediastoryboard_card_local_video_take",
    "mediastoryboard_card_trim_take",
    "mediastoryboard_card_edge_trim_take",
}
STORYBOARD_VIDEO_INPUT_KINDS = {
    "source_video",
    "video",
    "input_video",
    "take_video",
    "card_video",
    "segment_video",
    "overlay_video",
    "control_video",
}
STORYBOARD_IMAGE_INPUT_KINDS = {
    "image",
    "source_image",
    "overlay_image",
    "overlay_png",
    "poster_image",
    "start_image",
    "end_image",
    "matte_image",
    "mask_image",
    "overlay_matte",
    "overlay_mask",
}
STORYBOARD_AUDIO_INPUT_KINDS = {
    "audio",
    "scene_audio",
    "source_audio",
    "soundtrack_audio",
    "driving_audio",
    "narration_audio",
}
ALLOWED_STORYBOARD_INPUT_KINDS = STORYBOARD_VIDEO_INPUT_KINDS | STORYBOARD_IMAGE_INPUT_KINDS | STORYBOARD_AUDIO_INPUT_KINDS
ALLOWED_VIDEO_OUTPUT_MIME_TYPES = {"video/mp4"}
ALLOWED_VIDEO_SUFFIXES = {".mp4"}
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
ALLOWED_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg"}

MIME_EXTENSION = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}

SHARED_DEV_NETWORK = ipaddress.ip_network("100.64.0.0/10")
