from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .constants import (
    EVENT_VIDEO_BUMPER_SECONDS,
    EVENT_VIDEO_H264_ENCODER,
    EVENT_VIDEO_OUTPUT_PROFILE,
    EVENT_VIDEO_PROCESSING_TASK,
    EVENT_VIDEO_PROCESSOR_ID,
    EVENT_VIDEO_TIMEOUT_BASE_SECONDS,
    EVENT_VIDEO_TIMEOUT_MAX_SECONDS,
    EVENT_VIDEO_TIMEOUT_MULTIPLIER,
    EVENT_VIDEO_X264_BUFSIZE,
    EVENT_VIDEO_X264_CRF,
    EVENT_VIDEO_X264_MAXRATE,
    MAX_EVENT_VIDEO_OUTPUT_BYTES,
)
from .ffmpeg_probe import ffmpeg_binary, probe_video_metadata
from .log import log
from .types import DownloadedInput, ProcessingResult

ProgressCallback = Callable[[str, str, int], None]


class LocalProcessJob:
    def __init__(self) -> None:
        self.done = False
        self.cancelled = False
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def set_process(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._process = process
            if self.cancelled and process.poll() is None:
                process.terminate()

    def clear_process(self, process: subprocess.Popen) -> None:
        with self._lock:
            if self._process is process:
                self._process = None

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def mark_done(self) -> None:
        with self._lock:
            self.done = True


class EventVideoProcessor:
    def __init__(self, progress: ProgressCallback | None = None, cancel_event: threading.Event | None = None):
        self.progress = progress or (lambda phase, status, percent: None)
        self.cancel_event = cancel_event or threading.Event()

    def process(
        self,
        *,
        job_id: int,
        downloaded_inputs: list[DownloadedInput],
        processing: dict[str, Any],
        temp_dir: Path,
        process_handle: LocalProcessJob,
        worker_id: int | None = None,
        max_output_duration_seconds: int | float | None = None,
    ) -> ProcessingResult:
        source_inputs = [item for item in downloaded_inputs if item.kind == "source_video"]
        if len(source_inputs) != 1:
            raise ValueError(f"Event Video Processing requires exactly one source_video input; got {len(source_inputs)}.")
        source = source_inputs[0]
        metadata = source.metadata or probe_video_metadata(source.path)
        output_width, output_height = event_video_output_size(metadata)
        output_orientation = "portrait" if output_height > output_width else "landscape"
        overlay_path = asset_for_orientation(downloaded_inputs, "overlay_png", output_orientation, bool(processing.get("apply_overlay")))
        bumper_path = asset_for_orientation(downloaded_inputs, "bumper_image", output_orientation, bool(processing.get("add_ending_bumper")))
        main_output = temp_dir / "event-video-main.mp4"
        final_output = temp_dir / "event-video-processed.mp4"
        source_duration = float(metadata.get("duration_seconds") or 0.0)
        requested_max_duration = float(max_output_duration_seconds or 0.0)
        bumper_seconds = EVENT_VIDEO_BUMPER_SECONDS if bumper_path is not None else 0.0
        main_duration_limit = 0.0
        trimmed_to_requested_duration = False
        if requested_max_duration > 0 and source_duration + bumper_seconds > requested_max_duration + 0.05:
            main_duration_limit = requested_max_duration - bumper_seconds
            if main_duration_limit <= 0.25:
                raise ValueError(
                    "Event Video Processing requested output duration is too short "
                    "for the configured ending bumper."
                )
            trimmed_to_requested_duration = True
            self.progress(
                "processing",
                f"Trimming Event video to requested {requested_max_duration:.1f}s output cap.",
                2,
            )
        self.progress("processing", f"Normalizing Event video to {output_width}x{output_height}.", 3)
        self._run_main_ffmpeg(
            job_id,
            source.path,
            overlay_path,
            main_output,
            metadata,
            output_width,
            output_height,
            process_handle,
            output_duration_limit_seconds=main_duration_limit if main_duration_limit > 0 else None,
        )
        final_path = main_output
        bumper_added = False
        if bumper_path is not None:
            bumper_clip = temp_dir / "event-video-bumper.mp4"
            self.progress("processing", "Rendering Event video ending bumper.", 76)
            self._run_bumper_ffmpeg(job_id, bumper_path, bumper_clip, output_width, output_height, process_handle)
            self.progress("processing", "Appending Event video ending bumper.", 86)
            self._concat_segments(
                job_id,
                [main_output, bumper_clip],
                final_output,
                source_duration + EVENT_VIDEO_BUMPER_SECONDS,
                process_handle,
            )
            final_path = final_output
            bumper_added = True
        if not final_path.is_file() or final_path.stat().st_size <= 0:
            raise ValueError("Event Video Processing did not produce a processed MP4.")
        expected_max_duration = requested_max_duration if requested_max_duration > 0 else source_duration + (EVENT_VIDEO_BUMPER_SECONDS if bumper_added else 0)
        output_metadata = validate_event_video_output(
            final_path,
            expected_width=output_width,
            expected_height=output_height,
            expected_max_duration_seconds=expected_max_duration,
            max_bytes=MAX_EVENT_VIDEO_OUTPUT_BYTES,
        )
        self.progress("uploading", "Event Video Processing finished; uploading processed MP4.", 96)
        return ProcessingResult(
            path=final_path,
            metadata={
                "processing_task": EVENT_VIDEO_PROCESSING_TASK,
                "processor_id": EVENT_VIDEO_PROCESSOR_ID,
                "output_profile": EVENT_VIDEO_OUTPUT_PROFILE,
                "ffmpeg_encoder": EVENT_VIDEO_H264_ENCODER,
                "scaling_mode": "scale_to_fit_pad",
                "source_duration_seconds": source_duration,
                "requested_max_duration_seconds": requested_max_duration or None,
                "output_duration_seconds": float(output_metadata.get("duration_seconds") or 0.0),
                "source_width": int(metadata.get("display_width") or metadata.get("width") or 0),
                "source_height": int(metadata.get("display_height") or metadata.get("height") or 0),
                "output_width": int(output_metadata.get("display_width") or output_width),
                "output_height": int(output_metadata.get("display_height") or output_height),
                "output_orientation": output_orientation,
                "overlay_applied": overlay_path is not None,
                "bumper_added": bumper_added,
                "trimmed_to_requested_duration": trimmed_to_requested_duration,
                **({"worker_id": int(worker_id)} if worker_id else {}),
            },
        )

    def _run_main_ffmpeg(
        self,
        job_id: int,
        source_path: Path,
        overlay_path: Path | None,
        output_path: Path,
        metadata: dict[str, Any],
        output_width: int,
        output_height: int,
        process_handle: LocalProcessJob,
        output_duration_limit_seconds: float | None = None,
    ) -> None:
        command = [
            ffmpeg_binary(),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-progress",
            "pipe:1",
            "-nostats",
            "-i",
            str(source_path),
        ]
        next_input_index = 1
        audio_input_label = "0:a:0"
        if not metadata.get("has_audio"):
            command.extend([
                "-f",
                "lavfi",
                "-t",
                f"{float(metadata.get('duration_seconds') or 1.0):.3f}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ])
            audio_input_label = f"{next_input_index}:a:0"
            next_input_index += 1
        overlay_input_index = None
        if overlay_path is not None:
            command.extend(["-i", str(overlay_path)])
            overlay_input_index = next_input_index
        filter_parts = [
            (
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
                f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,format=rgba[vbase]"
            ),
            f"[{audio_input_label}]aresample=48000,aformat=channel_layouts=stereo[aout]",
        ]
        if overlay_input_index is not None:
            filter_parts.extend([
                f"[{overlay_input_index}:v]scale={output_width}:{output_height},format=rgba[ov]",
                "[vbase][ov]overlay=0:0:format=auto,format=yuv420p[vout]",
            ])
        else:
            filter_parts.append("[vbase]format=yuv420p[vout]")
        command.extend([
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
        ])
        if output_duration_limit_seconds is not None and output_duration_limit_seconds > 0:
            command.extend(["-t", f"{float(output_duration_limit_seconds):.3f}"])
        command.extend([
            *event_video_encode_args(),
            str(output_path),
        ])
        progress_duration = float(output_duration_limit_seconds or metadata.get("duration_seconds") or 1.0)
        self._run_ffmpeg_with_progress(
            job_id,
            command,
            total_seconds=progress_duration,
            progress_start=5,
            progress_end=75,
            phase="processing",
            status="Normalizing and branding Event video.",
            process_handle=process_handle,
            timeout_seconds=event_video_step_timeout(progress_duration),
        )

    def _run_bumper_ffmpeg(
        self,
        job_id: int,
        bumper_path: Path,
        output_path: Path,
        output_width: int,
        output_height: int,
        process_handle: LocalProcessJob,
    ) -> None:
        command = [
            ffmpeg_binary(),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-progress",
            "pipe:1",
            "-nostats",
            "-loop",
            "1",
            "-t",
            str(EVENT_VIDEO_BUMPER_SECONDS),
            "-i",
            str(bumper_path),
            "-f",
            "lavfi",
            "-t",
            str(EVENT_VIDEO_BUMPER_SECONDS),
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-filter_complex",
            (
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
                f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,format=yuv420p[vout];"
                "[1:a:0]aresample=48000,aformat=channel_layouts=stereo[aout]"
            ),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *event_video_encode_args(),
            str(output_path),
        ]
        self._run_ffmpeg_with_progress(
            job_id,
            command,
            total_seconds=float(EVENT_VIDEO_BUMPER_SECONDS),
            progress_start=76,
            progress_end=85,
            phase="processing",
            status="Rendering Event video ending bumper.",
            process_handle=process_handle,
            timeout_seconds=event_video_step_timeout(float(EVENT_VIDEO_BUMPER_SECONDS)),
        )

    def _concat_segments(
        self,
        job_id: int,
        segment_paths: list[Path],
        output_path: Path,
        total_seconds: float,
        process_handle: LocalProcessJob,
    ) -> None:
        list_path = output_path.with_suffix(".txt")
        with list_path.open("w", encoding="utf-8") as writer:
            for path in segment_paths:
                escaped = str(path).replace("'", "'\\''")
                writer.write(f"file '{escaped}'\n")
        command = [
            ffmpeg_binary(),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-progress",
            "pipe:1",
            "-nostats",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self._run_ffmpeg_with_progress(
            job_id,
            command,
            total_seconds=max(1.0, float(total_seconds or 1.0)),
            progress_start=86,
            progress_end=95,
            phase="processing",
            status="Appending Event video ending bumper.",
            process_handle=process_handle,
            timeout_seconds=event_video_step_timeout(max(1.0, float(total_seconds or 1.0))),
        )

    def _run_ffmpeg_with_progress(
        self,
        job_id: int,
        command: list[str],
        *,
        total_seconds: float,
        progress_start: int,
        progress_end: int,
        phase: str,
        status: str,
        process_handle: LocalProcessJob,
        timeout_seconds: int,
    ) -> None:
        log(
            "Starting FFmpeg subprocess; "
            f"job_id={job_id} phase={phase!r} progress_range={progress_start}-{progress_end} "
            f"timeout_seconds={timeout_seconds} command={redacted_command_for_log(command)}."
        )
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        process_handle.set_process(process)
        last_progress_at = 0.0
        output_lines: deque[str] = deque(maxlen=40)
        output_queue: queue.Queue = queue.Queue()

        def reader() -> None:
            try:
                if process.stdout is not None:
                    for stream_line in process.stdout:
                        output_queue.put(stream_line)
            finally:
                output_queue.put(None)

        threading.Thread(target=reader, name="midom-ffmpeg-reader", daemon=True).start()
        deadline = time.monotonic() + max(1, int(timeout_seconds or EVENT_VIDEO_TIMEOUT_BASE_SECONDS))
        try:
            while True:
                if self.cancel_event.is_set() or process_handle.cancelled:
                    process_handle.cancel()
                    raise RuntimeError("cancel_requested")
                now = time.monotonic()
                if now >= deadline:
                    terminate_ffmpeg_process(process)
                    raise TimeoutError(f"Event Video Processing FFmpeg step timed out after {timeout_seconds} seconds.")
                try:
                    line = output_queue.get(timeout=0.2)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                if line is None:
                    if process.poll() is not None:
                        break
                    continue
                progress_value = ffmpeg_progress_from_line(line, total_seconds, progress_start, progress_end)
                now = time.monotonic()
                if progress_value is not None and now - last_progress_at >= 2.0:
                    last_progress_at = now
                    self.progress(phase, status, progress_value)
                elif progress_value is None and line.strip():
                    output_lines.append(line.strip())
            if process.returncode != 0:
                message = "\n".join(output_lines).strip() or f"ffmpeg exited with status {process.returncode}"
                raise ValueError(f"Event Video Processing FFmpeg step failed: {message[:1000]}")
        except RuntimeError:
            terminate_ffmpeg_process(process)
            raise
        finally:
            process_handle.clear_process(process)
        self.progress(phase, status, progress_end)
        log(f"FFmpeg subprocess finished; job_id={job_id} phase={phase!r} returncode={process.returncode}.")


def event_video_output_size(metadata: dict[str, Any]) -> tuple[int, int]:
    orientation = str(metadata.get("orientation") or "landscape").strip().lower()
    if orientation == "portrait":
        return 720, 1280
    return 1280, 720


def asset_for_orientation(inputs: list[DownloadedInput], kind: str, orientation: str, required: bool) -> Path | None:
    matches = [item.path for item in inputs if item.kind == kind and item.orientation == orientation]
    if len(matches) > 1:
        raise ValueError(f"Event Video Processing received multiple {kind} assets for {orientation} output.")
    if matches:
        return matches[0]
    if required:
        label = "overlay" if kind == "overlay_png" else "bumper"
        raise ValueError(f"Event video {label} is enabled, but no {orientation} {label} asset was provided.")
    return None


def validate_event_video_output(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_max_duration_seconds: float,
    max_bytes: int,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("Event Video Processing output MP4 is missing.")
    file_size = path.stat().st_size
    if file_size <= 0 or file_size > max_bytes:
        raise ValueError(f"Event Video Processing output size is outside allowed bounds: {file_size} bytes.")
    header = path.read_bytes()[:64]
    if not looks_like_mp4(header):
        raise ValueError("Event Video Processing output does not look like MP4 bytes.")
    metadata = probe_video_metadata(path)
    output_width = int(metadata.get("display_width") or metadata.get("width") or 0)
    output_height = int(metadata.get("display_height") or metadata.get("height") or 0)
    if (output_width, output_height) != (int(expected_width), int(expected_height)):
        raise ValueError(f"Event Video Processing output dimensions mismatch; expected {expected_width}x{expected_height}, got {output_width}x{output_height}.")
    duration = float(metadata.get("duration_seconds") or 0.0)
    duration_tolerance = max(2.0, float(expected_max_duration_seconds or 0.0) * 0.05)
    if duration <= 0:
        raise ValueError("Event Video Processing output duration could not be read.")
    if expected_max_duration_seconds > 0 and duration > expected_max_duration_seconds + duration_tolerance:
        raise ValueError(f"Event Video Processing output duration exceeds expected bounds; got {duration:.2f}s.")
    if not metadata.get("has_audio"):
        raise ValueError("Event Video Processing output must contain an AAC audio stream.")
    if str(metadata.get("video_codec") or "").lower() != "h264":
        raise ValueError(f"Event Video Processing output must use H.264 video; got {metadata.get('video_codec') or 'unknown'}.")
    if str(metadata.get("audio_codec") or "").lower() != "aac":
        raise ValueError(f"Event Video Processing output must use AAC audio; got {metadata.get('audio_codec') or 'unknown'}.")
    if int(metadata.get("rotation_degrees") or 0) % 360 != 0:
        raise ValueError("Event Video Processing output must have physically rotated pixels and no rotation metadata.")
    return metadata


def event_video_step_timeout(duration_seconds: float) -> int:
    try:
        duration = max(1.0, float(duration_seconds or 1.0))
    except (TypeError, ValueError):
        duration = 1.0
    return int(min(EVENT_VIDEO_TIMEOUT_MAX_SECONDS, max(EVENT_VIDEO_TIMEOUT_BASE_SECONDS, EVENT_VIDEO_TIMEOUT_BASE_SECONDS + duration * EVENT_VIDEO_TIMEOUT_MULTIPLIER)))


def event_video_encode_args() -> list[str]:
    return [
        "-c:v",
        EVENT_VIDEO_H264_ENCODER,
        "-preset",
        "veryfast",
        "-crf",
        str(EVENT_VIDEO_X264_CRF),
        "-maxrate",
        EVENT_VIDEO_X264_MAXRATE,
        "-bufsize",
        EVENT_VIDEO_X264_BUFSIZE,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-metadata:s:v:0",
        "rotate=0",
    ]


def ffmpeg_progress_from_line(line: str, total_seconds: float, progress_start: int, progress_end: int) -> int | None:
    text = str(line or "").strip()
    if not text.startswith("out_time"):
        return None
    value_text = text.split("=", 1)[1].strip() if "=" in text else ""
    seconds = 0.0
    if text.startswith("out_time_ms=") or text.startswith("out_time_us="):
        try:
            seconds = float(value_text) / 1_000_000.0
        except (TypeError, ValueError):
            seconds = 0.0
    elif text.startswith("out_time="):
        parts = value_text.split(":")
        if len(parts) == 3:
            try:
                seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            except (TypeError, ValueError):
                seconds = 0.0
    if seconds <= 0 or total_seconds <= 0:
        return None
    span = max(0, progress_end - progress_start)
    return max(progress_start, min(progress_end, int(progress_start + span * min(1.0, seconds / total_seconds))))


def terminate_ffmpeg_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def redacted_command_for_log(command: list[str]) -> str:
    parts = []
    for item in command:
        text = str(item)
        if len(text) > 180:
            text = f"{text[:177]}..."
        parts.append(text)
    return " ".join(parts)


def looks_like_mp4(data: bytes) -> bool:
    if len(data) < 12:
        return False
    return data[4:8] == b"ftyp" or b"ftyp" in data[:64]
