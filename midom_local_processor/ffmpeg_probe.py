from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .constants import EVENT_VIDEO_H264_ENCODER


def _clean_env_path(value: str | None) -> Path | None:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return None
    return Path(os.path.expandvars(text)).expanduser()


def _platform_bin_dir() -> Path | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64"}:
        return None
    if system == "linux":
        platform_name = "linux-x86_64"
    elif system == "windows":
        platform_name = "windows-x86_64"
    else:
        return None
    return Path(__file__).resolve().parent / "vendor" / "ffmpeg" / platform_name


def _bundled_binary(binary_name: str) -> str | None:
    bin_dir = _platform_bin_dir()
    if bin_dir is None:
        return None
    filename = f"{binary_name}.exe" if platform.system().lower() == "windows" else binary_name
    candidate = bin_dir / filename
    if candidate.is_file():
        return str(candidate)
    return None


def _sibling_ffprobe(ffmpeg: str) -> str | None:
    ffmpeg_path = Path(ffmpeg)
    sibling = ffmpeg_path.with_name("ffprobe.exe" if ffmpeg_path.name.lower().endswith(".exe") else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    return None


def ffmpeg_binary() -> str:
    env_path = _clean_env_path(os.environ.get("MIDOM_FFMPEG"))
    if env_path and env_path.is_file():
        return str(env_path)
    bundled = _bundled_binary("ffmpeg")
    if bundled:
        return bundled
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_binary() -> str:
    env_path = _clean_env_path(os.environ.get("MIDOM_FFPROBE"))
    if env_path and env_path.is_file():
        return str(env_path)
    env_ffmpeg = _clean_env_path(os.environ.get("MIDOM_FFMPEG"))
    if env_ffmpeg and env_ffmpeg.is_file():
        sibling = _sibling_ffprobe(str(env_ffmpeg))
        if sibling:
            return sibling
    bundled = _bundled_binary("ffprobe")
    if bundled:
        return bundled
    ffmpeg = Path(ffmpeg_binary())
    sibling = _sibling_ffprobe(str(ffmpeg))
    if sibling:
        return sibling
    candidate = shutil.which("ffprobe")
    if candidate:
        return candidate
    return "ffprobe"


def _command_ok(command: list[str], timeout: int = 5) -> tuple[bool, str]:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return False, str(exc)
    output = f"{completed.stdout}\n{completed.stderr}"
    return completed.returncode == 0, output


def probe_ffmpeg() -> dict[str, Any]:
    ffmpeg = ffmpeg_binary()
    ffprobe = ffprobe_binary()
    ffmpeg_available, _ = _command_ok([ffmpeg, "-hide_banner", "-version"])
    ffprobe_available, _ = _command_ok([ffprobe, "-hide_banner", "-version"])
    quicktime_demux_available = False
    libx264_available = False
    if ffmpeg_available:
        demux_ok, demux_output = _command_ok([ffmpeg, "-hide_banner", "-demuxers"])
        quicktime_demux_available = demux_ok and "mov,mp4,m4a,3gp,3g2,mj2" in demux_output.lower()
        enc_ok, enc_output = _command_ok([ffmpeg, "-hide_banner", "-encoders"])
        libx264_available = enc_ok and EVENT_VIDEO_H264_ENCODER in enc_output
    return {
        "ffmpeg_binary": ffmpeg,
        "ffprobe_binary": ffprobe,
        "ffmpeg_available": ffmpeg_available,
        "ffprobe_available": ffprobe_available,
        "quicktime_demux_available": quicktime_demux_available,
        "libx264_available": libx264_available,
        "nvenc_available": False,
    }


def probe_video_metadata(path: Path, *, ffprobe: str | None = None) -> dict[str, Any]:
    ffprobe = ffprobe or ffprobe_binary()
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        stderr = str(completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"Could not inspect video metadata: {stderr}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse video metadata: {exc}")
    streams = payload.get("streams") or []
    if not isinstance(streams, list):
        streams = []
    video_stream = next((stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"), None)
    if not isinstance(video_stream, dict):
        raise ValueError("Video must contain a video stream.")
    format_block = payload.get("format") if isinstance(payload.get("format"), dict) else {}

    def duration_seconds(block: Any) -> float:
        if not isinstance(block, dict):
            return 0.0
        try:
            value = float(block.get("duration") or 0)
        except (TypeError, ValueError):
            value = 0.0
        return value if value > 0 else 0.0

    def fps_from(text: Any) -> float | None:
        value = str(text or "").strip()
        try:
            if "/" in value:
                numerator, denominator = value.split("/", 1)
                return float(numerator) / float(denominator)
            return float(value)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("Video width/height could not be read.")
    rotation = video_rotation_degrees(video_stream)
    display_width, display_height = (height, width) if int(abs(rotation)) % 180 == 90 else (width, height)
    duration = duration_seconds(video_stream) or duration_seconds(format_block)
    if duration <= 0:
        raise ValueError("Video duration could not be read.")
    audio_duration = duration_seconds(audio_stream) if isinstance(audio_stream, dict) else 0.0
    if audio_duration <= 0 and isinstance(audio_stream, dict):
        audio_duration = duration_seconds(format_block)
    return {
        "width": width,
        "height": height,
        "display_width": display_width,
        "display_height": display_height,
        "orientation": "portrait" if display_height > display_width else "landscape",
        "duration_seconds": duration,
        "has_audio": isinstance(audio_stream, dict) and audio_duration > 0,
        "audio_duration_seconds": audio_duration,
        "video_codec": str(video_stream.get("codec_name") or "").strip().lower(),
        "audio_codec": str(audio_stream.get("codec_name") or "").strip().lower() if isinstance(audio_stream, dict) else "",
        "rotation_degrees": rotation,
        "fps": fps_from(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
    }


def video_rotation_degrees(video_stream: dict[str, Any]) -> int:
    rotation_values = []
    tags = video_stream.get("tags") if isinstance(video_stream.get("tags"), dict) else {}
    if tags.get("rotate") not in {None, ""}:
        rotation_values.append(tags.get("rotate"))
    for side_data in video_stream.get("side_data_list") or []:
        if isinstance(side_data, dict) and side_data.get("rotation") not in {None, ""}:
            rotation_values.append(side_data.get("rotation"))
    for rotation in rotation_values:
        try:
            return int(round(float(rotation))) % 360
        except (TypeError, ValueError):
            continue
    return 0
