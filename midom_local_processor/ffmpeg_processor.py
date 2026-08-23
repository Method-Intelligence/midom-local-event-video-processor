from __future__ import annotations

import queue
import math
import re
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
    MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    MAX_STORYBOARD_VIDEO_OUTPUT_BYTES,
    STORYBOARD_FFMPEG_PROCESSING_TASK,
    STORYBOARD_FFMPEG_PROCESSOR_ID,
    STORYBOARD_LOCAL_VIDEO_TAKE_OPERATION_TYPES,
    STORYBOARD_OUTPUT_FPS,
    STORYBOARD_TIMEOUT_BASE_SECONDS,
    STORYBOARD_TIMEOUT_MAX_SECONDS,
    STORYBOARD_TIMEOUT_MULTIPLIER,
)
from .ffmpeg_probe import ffmpeg_binary, probe_audio_metadata, probe_video_metadata
from .log import log
from .types import DownloadedInput, ProcessingResult
from .validation import coerce_float, coerce_int, first_present, optional_positive_float

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
                "scaling_mode": "scale_to_cover_crop",
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

    def process_storyboard(
        self,
        *,
        job_id: int,
        downloaded_inputs: list[DownloadedInput],
        processing: dict[str, Any],
        temp_dir: Path,
        process_handle: LocalProcessJob,
        worker_id: int | None = None,
    ) -> ProcessingResult:
        operation_type = str(processing.get("operation_type") or "").strip()
        video_inputs = storyboard_video_inputs(downloaded_inputs)
        image_inputs = storyboard_image_inputs(downloaded_inputs)
        audio_inputs = storyboard_audio_inputs(downloaded_inputs)
        render_mode = str(processing.get("render_mode") or "").strip().lower()
        if operation_type in STORYBOARD_LOCAL_VIDEO_TAKE_OPERATION_TYPES:
            if render_mode == "voice_over_video":
                if not video_inputs:
                    raise ValueError("Storyboard local voice_over_video requires a source_video input.")
                output_width, output_height = storyboard_output_size(video_inputs[0], processing)
            else:
                if not image_inputs:
                    raise ValueError("Storyboard local video take requires image inputs.")
                output_width, output_height = storyboard_local_take_output_size(image_inputs, processing)
        else:
            if not video_inputs:
                raise ValueError("Storyboard FFmpeg Processing requires at least one video input.")
            output_width, output_height = storyboard_output_size(video_inputs[0], processing)
        final_output = temp_dir / "storyboard-processed.mp4"
        self.progress("processing", f"Running storyboard FFmpeg operation {operation_type}.", 3)
        if operation_type in STORYBOARD_LOCAL_VIDEO_TAKE_OPERATION_TYPES:
            if render_mode == "voice_over_video":
                primary = storyboard_primary_video_input(video_inputs)
                scene_audio = storyboard_audio_input_by_kind(audio_inputs, "scene_audio")
                if scene_audio is None:
                    raise ValueError("Storyboard local voice_over_video requires one scene_audio input.")
                self._run_storyboard_voice_over_video_take_ffmpeg(
                    job_id,
                    primary,
                    scene_audio,
                    final_output,
                    output_width,
                    output_height,
                    process_handle,
                    processing=processing,
                    progress_start=5,
                    progress_end=95,
                    status=f"Rendering storyboard voice-over video operation {operation_type}.",
                )
            else:
                self._run_storyboard_local_video_take_ffmpeg(
                    job_id,
                    image_inputs,
                    final_output,
                    output_width,
                    output_height,
                    process_handle,
                    processing=processing,
                    progress_start=5,
                    progress_end=95,
                    status=f"Rendering storyboard local video take operation {operation_type}.",
                )
        elif operation_type == "multicam_final_assembly":
            assembly_inputs = storyboard_ordered_assembly_inputs(video_inputs, processing)
            segment_paths: list[Path] = []
            progress_cursor = 5
            progress_span = max(1, 75 // max(1, len(assembly_inputs)))
            total_seconds = 0.0
            for index, video_input in enumerate(assembly_inputs):
                segment_output = temp_dir / f"storyboard-segment-{index}.mp4"
                segment_end = min(80, progress_cursor + progress_span)
                segment_processing = storyboard_segment_processing(video_input, processing, index)
                segment_duration = storyboard_effective_segment_duration(video_input, segment_processing)
                total_seconds += segment_duration
                self._run_storyboard_video_ffmpeg(
                    job_id,
                    video_input,
                    None,
                    None,
                    None,
                    segment_output,
                    output_width,
                    output_height,
                    process_handle,
                    processing=segment_processing,
                    progress_start=progress_cursor,
                    progress_end=segment_end,
                    status=f"Preparing storyboard assembly segment {index + 1} of {len(assembly_inputs)}.",
                )
                segment_paths.append(segment_output)
                progress_cursor = min(81, segment_end + 1)
            self._concat_segments(
                job_id,
                segment_paths,
                final_output,
                total_seconds,
                process_handle,
                progress_start=82,
                progress_end=95,
                status="Assembling storyboard final MP4.",
            )
            video_inputs = assembly_inputs
        elif operation_type in {"multicam_optimize_video", "optimize_video"}:
            primary = storyboard_primary_video_input(video_inputs)
            output_width, output_height = storyboard_optimize_output_size(primary, processing)
            self._run_storyboard_optimize_video_ffmpeg(
                job_id,
                primary,
                final_output,
                output_width,
                output_height,
                process_handle,
                processing=processing,
                progress_start=5,
                progress_end=95,
                status=f"Optimizing storyboard video operation {operation_type}.",
            )
        elif operation_type == "segmented_media_segment_normalize":
            primary = storyboard_primary_video_input(video_inputs)
            self._run_storyboard_segment_normalize_ffmpeg(
                job_id,
                primary,
                final_output,
                output_width,
                output_height,
                process_handle,
                processing=processing,
                progress_start=5,
                progress_end=95,
                status="Normalizing segmented media segment for storyboard use.",
            )
        elif operation_type == "replace_video_soundtrack":
            primary = storyboard_primary_video_input(video_inputs)
            soundtrack_input = storyboard_audio_input(downloaded_inputs)
            if soundtrack_input is None:
                raise ValueError("Storyboard replace_video_soundtrack requires one soundtrack_audio input.")
            self._run_storyboard_replace_soundtrack_ffmpeg(
                job_id,
                primary,
                soundtrack_input,
                final_output,
                output_width,
                output_height,
                process_handle,
                processing=processing,
                progress_start=5,
                progress_end=95,
                status="Replacing storyboard video soundtrack.",
            )
        else:
            primary = storyboard_primary_video_input(video_inputs)
            overlay = storyboard_overlay_input(downloaded_inputs, processing) if operation_type == "multicam_card_overlay_take" else None
            matte = storyboard_matte_input(downloaded_inputs, processing) if operation_type == "multicam_card_overlay_take" else None
            audio_input = storyboard_audio_input(downloaded_inputs)
            self._run_storyboard_video_ffmpeg(
                job_id,
                primary,
                overlay,
                matte,
                audio_input,
                final_output,
                output_width,
                output_height,
                process_handle,
                processing=processing,
                progress_start=5,
                progress_end=95,
                status=f"Rendering storyboard operation {operation_type}.",
            )
        if not final_output.is_file() or final_output.stat().st_size <= 0:
            raise ValueError("Storyboard FFmpeg Processing did not produce a processed MP4.")
        output_metadata = validate_storyboard_ffmpeg_output(
            final_output,
            expected_width=output_width,
            expected_height=output_height,
            max_bytes=MAX_STORYBOARD_VIDEO_OUTPUT_BYTES,
        )
        self.progress("uploading", "Storyboard FFmpeg Processing finished; uploading processed MP4.", 96)
        result_metadata = {
            "processing_task": STORYBOARD_FFMPEG_PROCESSING_TASK,
            "processor_id": STORYBOARD_FFMPEG_PROCESSOR_ID,
            "operation_type": operation_type,
            "ffmpeg_encoder": EVENT_VIDEO_H264_ENCODER,
            "output_width": int(output_metadata.get("display_width") or output_width),
            "output_height": int(output_metadata.get("display_height") or output_height),
            "output_duration_seconds": float(output_metadata.get("duration_seconds") or 0.0),
            "fps": coerce_int(processing.get("fps"), STORYBOARD_OUTPUT_FPS, 1, 120),
            "video_input_count": len(video_inputs),
            "image_input_count": sum(1 for item in downloaded_inputs if item.category == "image"),
            "audio_input_count": sum(1 for item in downloaded_inputs if item.category == "audio"),
            **({"worker_id": int(worker_id)} if worker_id else {}),
        }
        if operation_type == "multicam_final_assembly":
            result_metadata["segment_count"] = len(video_inputs)
        return ProcessingResult(path=final_output, metadata=result_metadata)

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
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,"
                f"crop={output_width}:{output_height},"
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
        *,
        progress_start: int = 86,
        progress_end: int = 95,
        status: str = "Appending Event video ending bumper.",
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
            progress_start=progress_start,
            progress_end=progress_end,
            phase="processing",
            status=status,
            process_handle=process_handle,
            timeout_seconds=event_video_step_timeout(max(1.0, float(total_seconds or 1.0))),
        )

    def _run_storyboard_local_video_take_ffmpeg(
        self,
        job_id: int,
        image_inputs: list[DownloadedInput],
        output_path: Path,
        output_width: int,
        output_height: int,
        process_handle: LocalProcessJob,
        *,
        processing: dict[str, Any],
        progress_start: int,
        progress_end: int,
        status: str,
    ) -> None:
        render_mode = str(processing.get("render_mode") or "").strip().lower()
        if render_mode not in {"one_image", "two_image_fade", "voice_over_video"}:
            raise ValueError(f"Unsupported storyboard local video take render_mode: {render_mode or 'missing'}")
        if coerce_bool_local(processing.get("pan_enabled"), False):
            raise ValueError("Storyboard local video take pan_enabled=true is not supported by the bridge first pass.")
        if render_mode == "voice_over_video":
            raise ValueError("Storyboard local voice_over_video render must be routed through the source-video renderer.")
        duration = storyboard_local_take_duration(processing)
        fps = coerce_int(processing.get("fps"), STORYBOARD_OUTPUT_FPS, 1, 120)
        start_image = storyboard_image_input_by_kind(image_inputs, "start_image")
        if start_image is None:
            raise ValueError("Storyboard local video take requires one start_image input.")
        if render_mode == "one_image":
            self._run_storyboard_one_image_take_ffmpeg(
                job_id,
                start_image,
                output_path,
                output_width,
                output_height,
                duration,
                fps,
                process_handle,
                processing=processing,
                progress_start=progress_start,
                progress_end=progress_end,
                status=status,
            )
            return
        end_image = storyboard_image_input_by_kind(image_inputs, "end_image")
        if end_image is None:
            raise ValueError("Storyboard local two_image_fade render requires one end_image input.")
        self._run_storyboard_two_image_fade_take_ffmpeg(
            job_id,
            start_image,
            end_image,
            output_path,
            output_width,
            output_height,
            duration,
            fps,
            process_handle,
            processing=processing,
            progress_start=progress_start,
            progress_end=progress_end,
            status=status,
        )

    def _run_storyboard_voice_over_video_take_ffmpeg(
        self,
        job_id: int,
        video_input: DownloadedInput,
        scene_audio_input: DownloadedInput,
        output_path: Path,
        output_width: int,
        output_height: int,
        process_handle: LocalProcessJob,
        *,
        processing: dict[str, Any],
        progress_start: int,
        progress_end: int,
        status: str,
    ) -> None:
        duration = storyboard_local_take_duration(processing)
        tolerance = coerce_float(processing.get("duration_tolerance_seconds"), 0.25, 0.0, 2.0)
        video_trim_start = coerce_float(
            first_present(processing, "video_trim_start_seconds", "trim_start_seconds", "start_seconds"),
            0.0,
            0.0,
            MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
        )
        video_metadata = video_input.metadata if isinstance(video_input.metadata, dict) else {}
        video_duration = float(video_metadata.get("duration_seconds") or 0.0)
        if video_duration <= 0:
            raise ValueError("Storyboard local voice_over_video source_video duration could not be read.")
        available_video_duration = max(0.0, video_duration - video_trim_start)
        if available_video_duration + tolerance < duration:
            raise ValueError(
                "Storyboard local voice_over_video source_video is shorter than requested duration after trim; "
                f"available={available_video_duration:.3f}s requested={duration:.3f}s tolerance={tolerance:.3f}s."
            )
        audio_metadata = scene_audio_input.metadata if isinstance(scene_audio_input.metadata, dict) else {}
        audio_duration = float(scene_audio_input.duration_seconds or audio_metadata.get("duration_seconds") or 0.0)
        if audio_duration <= 0:
            raise ValueError("Storyboard local voice_over_video scene_audio duration could not be read.")
        if audio_duration + tolerance < duration:
            raise ValueError(
                "Storyboard local voice_over_video scene_audio is shorter than requested duration; "
                f"available={audio_duration:.3f}s requested={duration:.3f}s tolerance={tolerance:.3f}s."
            )
        transition = str(processing.get("video_transition") or "").strip().lower()
        if transition not in {"", "none"}:
            raise ValueError(f"Storyboard local voice_over_video does not support video_transition={transition!r}.")
        fps = coerce_int(processing.get("fps"), STORYBOARD_OUTPUT_FPS, 1, 120)
        fit_mode = str(processing.get("video_fit_mode") or processing.get("fit_mode") or processing.get("fit") or "contain").strip().lower()
        if fit_mode in {"crop_to_fill", "cover", "crop"}:
            video_filter = (
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,"
                f"crop={output_width}:{output_height},setsar=1,setpts=PTS-STARTPTS,format=yuv420p[vout]"
            )
        else:
            video_filter = (
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
                f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,setpts=PTS-STARTPTS,format=yuv420p[vout]"
            )
        audio_start = coerce_float(
            first_present(processing, "scene_audio_start_seconds", "audio_start_seconds", "soundtrack_start"),
            0.0,
            0.0,
            MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
        )
        command = [ffmpeg_binary(), "-y", "-hide_banner", "-v", "error", "-progress", "pipe:1", "-nostats"]
        if video_trim_start > 0:
            command.extend(["-ss", f"{video_trim_start:.3f}"])
        command.extend(["-i", str(video_input.path)])
        if audio_start > 0:
            command.extend(["-ss", f"{audio_start:.3f}"])
        command.extend([
            "-i",
            str(scene_audio_input.path),
            "-filter_complex",
            f"{video_filter};[1:a:0]aresample=48000,aformat=channel_layouts=stereo[aout]",
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-r",
            str(fps),
            "-t",
            f"{duration:.3f}",
            *storyboard_encode_args(processing),
            str(output_path),
        ])
        self._run_ffmpeg_with_progress(
            job_id,
            command,
            total_seconds=duration,
            progress_start=progress_start,
            progress_end=progress_end,
            phase="processing",
            status=status,
            process_handle=process_handle,
            timeout_seconds=storyboard_step_timeout(duration),
        )

    def _run_storyboard_one_image_take_ffmpeg(
        self,
        job_id: int,
        image_input: DownloadedInput,
        output_path: Path,
        output_width: int,
        output_height: int,
        duration: float,
        fps: int,
        process_handle: LocalProcessJob,
        *,
        processing: dict[str, Any],
        progress_start: int,
        progress_end: int,
        status: str,
    ) -> None:
        filter_parts = [
            storyboard_local_image_filter("0:v", "vout", output_width, output_height, fps, processing),
            "[1:a:0]aresample=48000,aformat=channel_layouts=stereo[aout]",
        ]
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
            "-framerate",
            str(fps),
            "-t",
            f"{duration:.3f}",
            "-i",
            str(image_input.path),
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-r",
            str(fps),
            "-t",
            f"{duration:.3f}",
            *storyboard_encode_args(processing),
            str(output_path),
        ]
        self._run_ffmpeg_with_progress(
            job_id,
            command,
            total_seconds=duration,
            progress_start=progress_start,
            progress_end=progress_end,
            phase="processing",
            status=status,
            process_handle=process_handle,
            timeout_seconds=storyboard_step_timeout(duration),
        )

    def _run_storyboard_two_image_fade_take_ffmpeg(
        self,
        job_id: int,
        start_image: DownloadedInput,
        end_image: DownloadedInput,
        output_path: Path,
        output_width: int,
        output_height: int,
        duration: float,
        fps: int,
        process_handle: LocalProcessJob,
        *,
        processing: dict[str, Any],
        progress_start: int,
        progress_end: int,
        status: str,
    ) -> None:
        fade_duration = coerce_float(processing.get("fade_duration_seconds"), 1.0, 0.05, MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
        fade_duration = min(fade_duration, max(0.05, duration - 0.05))
        fade_start = coerce_float(processing.get("fade_start_seconds"), 0.0, 0.0, MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
        fade_start = min(fade_start, max(0.0, duration - fade_duration))
        filter_parts = [
            storyboard_local_image_filter("0:v", "vstart", output_width, output_height, fps, processing, pixel_format="rgba"),
            storyboard_local_image_filter("1:v", "vend", output_width, output_height, fps, processing, pixel_format="rgba"),
            f"[vend]fade=t=in:st={fade_start:.3f}:d={fade_duration:.3f}:alpha=1[vendfade]",
            "[vstart][vendfade]overlay=x=0:y=0:format=auto,format=yuv420p[vout]",
            "[2:a:0]aresample=48000,aformat=channel_layouts=stereo[aout]",
        ]
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
            "-framerate",
            str(fps),
            "-t",
            f"{duration:.3f}",
            "-i",
            str(start_image.path),
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-t",
            f"{duration:.3f}",
            "-i",
            str(end_image.path),
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-r",
            str(fps),
            "-t",
            f"{duration:.3f}",
            *storyboard_encode_args(processing),
            str(output_path),
        ]
        self._run_ffmpeg_with_progress(
            job_id,
            command,
            total_seconds=duration,
            progress_start=progress_start,
            progress_end=progress_end,
            phase="processing",
            status=status,
            process_handle=process_handle,
            timeout_seconds=storyboard_step_timeout(duration),
        )

    def _run_storyboard_optimize_video_ffmpeg(
        self,
        job_id: int,
        video_input: DownloadedInput,
        output_path: Path,
        output_width: int,
        output_height: int,
        process_handle: LocalProcessJob,
        *,
        processing: dict[str, Any],
        progress_start: int,
        progress_end: int,
        status: str,
    ) -> None:
        metadata = video_input.metadata or probe_video_metadata(video_input.path)
        source_duration = float(metadata.get("duration_seconds") or 1.0)
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
            str(video_input.path),
        ]
        audio_label = "0:a:0"
        if not metadata.get("has_audio"):
            command.extend(["-f", "lavfi", "-t", f"{source_duration:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
            audio_label = "1:a:0"
        filter_complex = (
            f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={coerce_int(processing.get('fps'), STORYBOARD_OUTPUT_FPS, 1, 120)},"
            "setsar=1,format=yuv420p[vout];"
            f"[{audio_label}]aresample=48000,aformat=channel_layouts=stereo[aout]"
        )
        command.extend(["-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]", *storyboard_encode_args(processing), str(output_path)])
        self._run_ffmpeg_with_progress(
            job_id,
            command,
            total_seconds=max(1.0, source_duration),
            progress_start=progress_start,
            progress_end=progress_end,
            phase="processing",
            status=status,
            process_handle=process_handle,
            timeout_seconds=storyboard_step_timeout(source_duration),
        )

    def _run_storyboard_segment_normalize_ffmpeg(
        self,
        job_id: int,
        video_input: DownloadedInput,
        output_path: Path,
        output_width: int,
        output_height: int,
        process_handle: LocalProcessJob,
        *,
        processing: dict[str, Any],
        progress_start: int,
        progress_end: int,
        status: str,
    ) -> None:
        metadata = video_input.metadata or probe_video_metadata(video_input.path)
        source_duration = float(metadata.get("duration_seconds") or 1.0)
        has_audio = bool(metadata.get("has_audio"))
        fps = coerce_int(processing.get("fps"), STORYBOARD_OUTPUT_FPS, 1, 120)
        resize_mode = str(processing.get("resize_mode") or "scale_to_cover_crop").strip().lower()
        if resize_mode not in {"scale_to_cover_crop", "cover", "crop"}:
            raise ValueError(f"Unsupported segmented media segment resize_mode: {resize_mode}")
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
            str(video_input.path),
        ]
        audio_label = "0:a:0"
        if not has_audio:
            command.extend(["-f", "lavfi", "-t", f"{source_duration:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
            audio_label = "1:a:0"
        filter_parts = [
            (
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,"
                f"crop={output_width}:{output_height},"
                f"fps={fps},setsar=1,format=yuv420p[vout]"
            ),
            f"[{audio_label}]aresample=48000,aformat=channel_layouts=stereo[aout]",
        ]
        command.extend([
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-r",
            str(fps),
            "-t",
            f"{source_duration:.3f}",
            *storyboard_encode_args(processing),
            str(output_path),
        ])
        log(
            "Normalizing segmented media segment; "
            f"job_id={job_id} input_id={video_input.input_id} "
            f"track_key={processing.get('track_key')!r} segment_index={processing.get('segment_index')!r} "
            f"output={output_width}x{output_height} resize_mode={resize_mode!r} fps={fps} "
            f"duration_seconds={source_duration:.3f} has_audio={has_audio}."
        )
        self._run_ffmpeg_with_progress(
            job_id,
            command,
            total_seconds=max(1.0, source_duration),
            progress_start=progress_start,
            progress_end=progress_end,
            phase="processing",
            status=status,
            process_handle=process_handle,
            timeout_seconds=storyboard_step_timeout(source_duration),
        )

    def _run_storyboard_replace_soundtrack_ffmpeg(
        self,
        job_id: int,
        video_input: DownloadedInput,
        soundtrack_input: DownloadedInput,
        output_path: Path,
        output_width: int,
        output_height: int,
        process_handle: LocalProcessJob,
        *,
        processing: dict[str, Any],
        progress_start: int,
        progress_end: int,
        status: str,
    ) -> None:
        video_metadata = video_input.metadata or probe_video_metadata(video_input.path)
        audio_metadata = soundtrack_input.metadata or probe_audio_metadata(soundtrack_input.path)
        source_duration = float(video_metadata.get("duration_seconds") or 1.0)
        audio_duration = float(audio_metadata.get("duration_seconds") or 1.0)
        video_start = coerce_float(
            first_present(processing, "video_start_seconds", "trim_start_seconds", "start_seconds"),
            0.0,
            0.0,
            MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
        )
        requested_duration = optional_positive_float(
            first_present(processing, "output_duration_seconds", "duration_seconds", "trim_duration_seconds"),
            MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
        )
        remaining_video_duration = max(0.1, source_duration - video_start)
        effective_duration = max(0.1, min(requested_duration or remaining_video_duration, remaining_video_duration))
        video_filter = (
            f"[0:v]trim=start={video_start:.3f}:duration={effective_duration:.3f},setpts=PTS-STARTPTS,"
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={coerce_int(processing.get('fps'), STORYBOARD_OUTPUT_FPS, 1, 120)},"
            "setsar=1,format=yuv420p[vout]"
        )
        filter_parts = [video_filter, *storyboard_external_audio_filter_parts("1:a:0", processing, effective_duration)]
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
            str(video_input.path),
            "-i",
            str(soundtrack_input.path),
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{effective_duration:.3f}",
            *storyboard_encode_args(processing),
            str(output_path),
        ]
        log(
            "Replacing storyboard video soundtrack; "
            f"job_id={job_id} source_input_id={video_input.input_id} soundtrack_input_id={soundtrack_input.input_id} "
            f"soundtrack_kind={soundtrack_input.kind} soundtrack_mime={soundtrack_input.mime_type} "
            f"video_start={video_start:.3f} audio_duration={audio_duration:.3f} output_duration={effective_duration:.3f}."
        )
        self._run_ffmpeg_with_progress(
            job_id,
            command,
            total_seconds=max(1.0, effective_duration),
            progress_start=progress_start,
            progress_end=progress_end,
            phase="processing",
            status=status,
            process_handle=process_handle,
            timeout_seconds=storyboard_step_timeout(effective_duration),
        )

    def _run_storyboard_video_ffmpeg(
        self,
        job_id: int,
        video_input: DownloadedInput,
        overlay_input: DownloadedInput | None,
        matte_input: DownloadedInput | None,
        audio_input: DownloadedInput | None,
        output_path: Path,
        output_width: int,
        output_height: int,
        process_handle: LocalProcessJob,
        *,
        processing: dict[str, Any],
        progress_start: int,
        progress_end: int,
        status: str,
    ) -> None:
        metadata = video_input.metadata or probe_video_metadata(video_input.path)
        source_duration = float(metadata.get("duration_seconds") or 1.0)
        trim_start = coerce_float(processing.get("trim_start_seconds"), 0.0, 0.0, MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
        trim_duration = storyboard_trim_duration(source_duration, processing, trim_start)
        effective_duration = float(trim_duration or max(0.1, source_duration - trim_start))
        command = [ffmpeg_binary(), "-y", "-hide_banner", "-v", "error", "-progress", "pipe:1", "-nostats"]
        if trim_start > 0:
            command.extend(["-ss", f"{trim_start:.3f}"])
        command.extend(["-i", str(video_input.path)])
        next_input_index = 1
        audio_input_label = "0:a:0"
        external_audio = audio_input is not None
        if external_audio and audio_input is not None:
            command.extend(["-i", str(audio_input.path)])
            audio_input_label = f"{next_input_index}:a:0"
            next_input_index += 1
        overlay_input_index = None
        matte_input_index = None
        if overlay_input is not None:
            if overlay_input.category == "image":
                command.extend(["-loop", "1", "-t", f"{effective_duration:.3f}", "-i", str(overlay_input.path)])
            else:
                command.extend(["-i", str(overlay_input.path)])
            overlay_input_index = next_input_index
            next_input_index += 1
        if matte_input is not None and overlay_input_index is None:
            raise ValueError("Overlay matte was provided but could not be applied: no overlay input was provided.")
        if matte_input is not None:
            command.extend(["-loop", "1", "-t", f"{effective_duration:.3f}", "-i", str(matte_input.path)])
            matte_input_index = next_input_index
            next_input_index += 1
        if overlay_input is not None and overlay_input.category == "video":
            overlay_metadata = overlay_input.metadata or {}
            overlay_duration = float(overlay_metadata.get("duration_seconds") or 0.0)
            if overlay_duration > 0:
                effective_duration = max(0.1, min(effective_duration, overlay_duration))
                trim_duration = effective_duration
        audio_source = str(processing.get("audio_source") or "").strip().lower()
        if overlay_input_index is not None and overlay_input is not None and audio_source in {"base", "overlay"}:
            if audio_source == "overlay":
                audio_input_label = f"{overlay_input_index}:a:0"
                selected_audio_has_stream = bool((overlay_input.metadata or {}).get("has_audio"))
            else:
                audio_input_label = "0:a:0"
                selected_audio_has_stream = bool(metadata.get("has_audio"))
        else:
            selected_audio_has_stream = bool(external_audio or metadata.get("has_audio"))
        if overlay_input_index is not None and audio_source in {"base", "overlay"} and not selected_audio_has_stream:
            source_name = "overlay_video" if audio_source == "overlay" else "source_video"
            raise ValueError(f"Storyboard overlay audio_source={audio_source!r} requested, but {source_name} has no audio stream.")
        if not selected_audio_has_stream:
            command.extend(["-f", "lavfi", "-t", f"{effective_duration:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
            audio_input_label = f"{next_input_index}:a:0"
            next_input_index += 1
        fit_mode = str(processing.get("fit_mode") or processing.get("fit") or "contain").strip().lower()
        if coerce_bool_local(processing.get("crop"), False) or fit_mode in {"cover", "crop"}:
            video_filter = (
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=increase,"
                f"crop={output_width}:{output_height},setsar=1,format=rgba[vbase]"
            )
        else:
            video_filter = (
                f"[0:v]scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
                f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
                "setsar=1,format=rgba[vbase]"
            )
        filter_parts = [video_filter]
        if external_audio:
            filter_parts.extend(storyboard_external_audio_filter_parts(audio_input_label, processing, effective_duration))
        else:
            filter_parts.append(f"[{audio_input_label}]aresample=48000,aformat=channel_layouts=stereo[aout]")
        if overlay_input_index is not None and overlay_input is not None:
            try:
                filter_parts.extend(
                    storyboard_overlay_filter_parts(
                        overlay_input,
                        overlay_input_index,
                        matte_input_index,
                        output_width,
                        output_height,
                        processing,
                        effective_duration,
                    )
                )
            except Exception as exc:
                if matte_input is not None:
                    raise ValueError(f"Overlay matte was provided but could not be applied: {exc}") from exc
                raise
        else:
            filter_parts.append("[vbase]format=yuv420p[vout]")
        command.extend([
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-r",
            str(coerce_int(processing.get("fps"), STORYBOARD_OUTPUT_FPS, 1, 120)),
            *(["-t", f"{trim_duration:.3f}"] if trim_duration is not None else []),
            *event_video_encode_args(),
            str(output_path),
        ])
        try:
            self._run_ffmpeg_with_progress(
                job_id,
                command,
                total_seconds=max(1.0, effective_duration),
                progress_start=progress_start,
                progress_end=progress_end,
                phase="processing",
                status=status,
                process_handle=process_handle,
                timeout_seconds=storyboard_step_timeout(effective_duration),
            )
        except Exception as exc:
            if matte_input is not None and str(exc) != "cancel_requested":
                raise ValueError(f"Overlay matte was provided but could not be applied: {exc}") from exc
            raise

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


def storyboard_video_inputs(downloaded_inputs: list[DownloadedInput]) -> list[DownloadedInput]:
    return sorted(
        [item for item in downloaded_inputs if item.category == "video"],
        key=lambda item: (int(item.order or 0), int(item.input_id or 0)),
    )


def storyboard_image_inputs(downloaded_inputs: list[DownloadedInput]) -> list[DownloadedInput]:
    return sorted(
        [item for item in downloaded_inputs if item.category == "image"],
        key=lambda item: (int(item.order or 0), int(item.input_id or 0)),
    )


def storyboard_image_input_by_kind(image_inputs: list[DownloadedInput], kind: str) -> DownloadedInput | None:
    for item in image_inputs:
        if str(item.kind or "").strip().lower() == kind:
            return item
    return None


def storyboard_audio_inputs(downloaded_inputs: list[DownloadedInput]) -> list[DownloadedInput]:
    return sorted(
        [item for item in downloaded_inputs if item.category == "audio"],
        key=lambda item: (int(item.order or 0), int(item.input_id or 0)),
    )


def storyboard_audio_input_by_kind(audio_inputs: list[DownloadedInput], kind: str) -> DownloadedInput | None:
    for item in audio_inputs:
        if str(item.kind or "").strip().lower() == kind:
            return item
    return None


def storyboard_primary_video_input(video_inputs: list[DownloadedInput]) -> DownloadedInput:
    preferred_roles = {"primary", "source", "take", "card", "input"}
    for item in video_inputs:
        role = str(item.role or "").strip().lower()
        kind = str(item.kind or "").strip().lower()
        if role in preferred_roles or kind in {"source_video", "input_video", "take_video", "card_video", "video"}:
            return item
    return video_inputs[0]


def storyboard_overlay_input(downloaded_inputs: list[DownloadedInput], processing: dict[str, Any] | None = None) -> DownloadedInput | None:
    for item in downloaded_inputs:
        role = str(item.role or "").strip().lower()
        kind = str(item.kind or "").strip().lower()
        if kind == "overlay_video" or (role == "overlay" and item.category == "video"):
            return item
    for item in downloaded_inputs:
        role = str(item.role or "").strip().lower()
        kind = str(item.kind or "").strip().lower()
        if is_storyboard_matte_descriptor(item, processing):
            continue
        if role == "overlay" or kind in {"overlay_image", "overlay_png"}:
            return item
    return None


def storyboard_matte_input(downloaded_inputs: list[DownloadedInput], processing: dict[str, Any] | None = None) -> DownloadedInput | None:
    for item in downloaded_inputs:
        if item.category == "image" and is_storyboard_matte_descriptor(item, processing):
            return item
    return None


def is_storyboard_matte_descriptor(item: DownloadedInput | dict[str, Any], processing: dict[str, Any] | None = None) -> bool:
    if isinstance(item, DownloadedInput):
        role = str(item.role or "").strip().lower()
        kind = str(item.kind or "").strip().lower()
        item_ids = {
            item.input_id,
            item.dbfileid,
            item.dbfile_id,
            item.file_id,
        }
    else:
        role = str(item.get("role") or "").strip().lower()
        kind = str(item.get("kind") or "").strip().lower()
        item_ids = {item.get("input_id"), item.get("dbfileid"), item.get("dbfile_id"), item.get("file_id")}
    if role in {"matte", "mask"} or kind in {"matte_image", "mask_image", "overlay_matte", "overlay_mask"}:
        return True
    if not isinstance(processing, dict):
        return False
    selector_values = {
        processing.get("mask_dbfileid"),
        processing.get("mask_input_id"),
        processing.get("matte_dbfileid"),
        processing.get("matte_input_id"),
        processing.get("overlay_mask_dbfileid"),
        processing.get("overlay_matte_dbfileid"),
    }
    selector_ids = set()
    for value in selector_values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            selector_ids.add(number)
    if not selector_ids:
        return False
    for value in item_ids:
        try:
            if int(value) in selector_ids:
                return True
        except (TypeError, ValueError):
            continue
    return False


def storyboard_ordered_assembly_inputs(video_inputs: list[DownloadedInput], processing: dict[str, Any]) -> list[DownloadedInput]:
    segments = processing.get("segments")
    if not isinstance(segments, list) or not segments:
        return list(video_inputs)
    input_by_id = {int(item.input_id): item for item in video_inputs}
    ordered = []
    missing_ids = []
    segment_entries = [
        (coerce_int(segment.get("order", segment.get("sequence", index)), index, 0, 10_000), index, segment)
        for index, segment in enumerate(segments)
        if isinstance(segment, dict)
    ]
    for _order, index, segment in sorted(segment_entries, key=lambda entry: (entry[0], entry[1])):
        input_id = coerce_int(segment.get("input_id"), 0, 0, 2_147_483_647)
        if input_id <= 0:
            continue
        match = input_by_id.get(input_id)
        if not match:
            missing_ids.append(input_id)
            continue
        ordered.append(DownloadedInput(**{**match.__dict__, "order": index, "metadata": {**(match.metadata or {}), "_segment_payload": segment, "_segment_index": index}}))
    if missing_ids:
        raise ValueError(f"Storyboard final assembly referenced missing input_id(s): {missing_ids}")
    return ordered or list(video_inputs)


def storyboard_segment_processing(video_input: DownloadedInput, processing: dict[str, Any], index: int) -> dict[str, Any]:
    metadata = video_input.metadata if isinstance(video_input.metadata, dict) else {}
    segment = metadata.get("_segment_payload") if isinstance(metadata.get("_segment_payload"), dict) else {}
    trim_start = coerce_float(
        first_present(segment, "trim_start_seconds", "start_seconds", "start_time_seconds"),
        coerce_float(processing.get("trim_start_seconds"), 0.0, 0.0, MAX_STORYBOARD_VIDEO_DURATION_SECONDS),
        0.0,
        MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    )
    trim_duration = optional_positive_float(first_present(segment, "trim_duration_seconds", "duration_seconds"), MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
    trim_end = optional_positive_float(first_present(segment, "trim_end_seconds", "end_seconds", "end_time_seconds"), MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
    if trim_duration is None and trim_end is None:
        trim_duration = optional_positive_float(processing.get("trim_duration_seconds"), MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
        trim_end = optional_positive_float(processing.get("trim_end_seconds"), MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
    return {
        **processing,
        "operation_type": "multicam_final_assembly_segment",
        "trim_start_seconds": trim_start,
        "trim_duration_seconds": trim_duration,
        "trim_end_seconds": trim_end,
        "segment_index": int(metadata.get("_segment_index", index)),
    }


def storyboard_effective_segment_duration(video_input: DownloadedInput, processing: dict[str, Any]) -> float:
    metadata = video_input.metadata if isinstance(video_input.metadata, dict) else {}
    source_duration = float(metadata.get("duration_seconds") or 1.0)
    trim_start = coerce_float(processing.get("trim_start_seconds"), 0.0, 0.0, MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
    trim_duration = storyboard_trim_duration(source_duration, processing, trim_start)
    return max(0.1, float(trim_duration or max(0.1, source_duration - trim_start)))


def storyboard_output_size(video_input: DownloadedInput, processing: dict[str, Any]) -> tuple[int, int]:
    metadata = video_input.metadata if isinstance(video_input.metadata, dict) else {}
    width = coerce_int(processing.get("target_width") or processing.get("output_width") or processing.get("width"), 0, 0, 4096)
    height = coerce_int(processing.get("target_height") or processing.get("output_height") or processing.get("height"), 0, 0, 4096)
    if width <= 0 or height <= 0:
        width = coerce_int(metadata.get("display_width") or metadata.get("width"), 1280, 2, 4096)
        height = coerce_int(metadata.get("display_height") or metadata.get("height"), 720, 2, 4096)
    return even_video_size(width, height)


def storyboard_optimize_output_size(video_input: DownloadedInput, processing: dict[str, Any]) -> tuple[int, int]:
    metadata = video_input.metadata if isinstance(video_input.metadata, dict) else {}
    source_width = coerce_int(metadata.get("display_width") or metadata.get("width"), 1280, 2, 8192)
    source_height = coerce_int(metadata.get("display_height") or metadata.get("height"), 720, 2, 8192)
    requested_width = coerce_int(processing.get("output_width") or processing.get("width"), 0, 0, 8192)
    requested_height = coerce_int(processing.get("output_height") or processing.get("height"), 0, 0, 8192)
    if requested_width > 0 and requested_height > 0:
        return even_video_size(requested_width, requested_height)
    max_dimension = coerce_int(processing.get("max_dimension"), 0, 0, 8192)
    if max_dimension <= 0 or max(source_width, source_height) <= max_dimension:
        return even_video_size(source_width, source_height)
    ratio = float(max_dimension) / float(max(source_width, source_height))
    return even_video_size(max(2, int(round(source_width * ratio))), max(2, int(round(source_height * ratio))))


def storyboard_local_take_output_size(image_inputs: list[DownloadedInput], processing: dict[str, Any]) -> tuple[int, int]:
    width = coerce_int(processing.get("output_width") or processing.get("width"), 0, 0, 4096)
    height = coerce_int(processing.get("output_height") or processing.get("height"), 0, 0, 4096)
    if width > 0 and height > 0:
        return even_video_size(width, height)
    ratio_size = storyboard_ratio_output_size(processing)
    if ratio_size is not None:
        return ratio_size
    first_image = image_inputs[0] if image_inputs else None
    width = coerce_int(first_image.width if first_image is not None else None, 1280, 2, 4096)
    height = coerce_int(first_image.height if first_image is not None else None, 720, 2, 4096)
    return even_video_size(width, height)


def storyboard_ratio_output_size(processing: dict[str, Any]) -> tuple[int, int] | None:
    resolution = str(processing.get("resolution") or processing.get("output_resolution") or "").strip().lower()
    ratio = str(processing.get("ratio") or processing.get("aspect_ratio") or "").strip().lower()
    if not resolution and not ratio:
        return None
    resolution_match = re.search(r"([1-9][0-9]{2,3})", resolution)
    base = int(resolution_match.group(1)) if resolution_match else 720
    ratio_aliases = {
        "16:9": (16, 9),
        "landscape": (16, 9),
        "landscape_16_9": (16, 9),
        "9:16": (9, 16),
        "portrait": (9, 16),
        "portrait_9_16": (9, 16),
        "1:1": (1, 1),
        "square": (1, 1),
    }
    ratio_pair = ratio_aliases.get(ratio)
    if ratio_pair is None:
        ratio_match = re.fullmatch(r"\s*([1-9][0-9]?)\s*[:x]\s*([1-9][0-9]?)\s*", ratio)
        if ratio_match:
            ratio_pair = (int(ratio_match.group(1)), int(ratio_match.group(2)))
    if ratio_pair is None:
        ratio_pair = (16, 9)
    ratio_width, ratio_height = ratio_pair
    if ratio_width >= ratio_height:
        height = base
        width = round(base * ratio_width / ratio_height)
    else:
        width = base
        height = round(base * ratio_height / ratio_width)
    return even_video_size(width, height)


def even_video_size(width: int, height: int) -> tuple[int, int]:
    return max(2, int(width) - (int(width) % 2)), max(2, int(height) - (int(height) % 2))


def storyboard_local_take_duration(processing: dict[str, Any]) -> float:
    duration = optional_positive_float(
        first_present(processing, "duration_seconds", "trim_duration_seconds", "output_duration_seconds"),
        MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    )
    return float(duration if duration is not None else 3.0)


def storyboard_local_image_filter(
    input_label: str,
    output_label: str,
    output_width: int,
    output_height: int,
    fps: int,
    processing: dict[str, Any],
    *,
    pixel_format: str = "yuv420p",
) -> str:
    fit_mode = str(processing.get("fit_mode") or processing.get("fit") or "contain").strip().lower()
    if coerce_bool_local(processing.get("crop"), False) or fit_mode in {"cover", "crop"}:
        transform = (
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=increase,"
            f"crop={output_width}:{output_height}"
        )
    else:
        transform = (
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease,"
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    return (
        f"[{input_label}]fps={fps},{transform},setsar=1,setpts=PTS-STARTPTS,"
        f"format={pixel_format}[{output_label}]"
    )


def storyboard_trim_duration(source_duration: float, processing: dict[str, Any], trim_start: float) -> float | None:
    trim_duration = optional_positive_float(processing.get("trim_duration_seconds"), MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
    if trim_duration is not None:
        return min(trim_duration, max(0.1, float(source_duration or 0.0) - trim_start))
    trim_end = optional_positive_float(processing.get("trim_end_seconds"), MAX_STORYBOARD_VIDEO_DURATION_SECONDS)
    if trim_end is not None and trim_end > trim_start:
        return min(trim_end - trim_start, max(0.1, float(source_duration or 0.0) - trim_start))
    if trim_start > 0:
        return max(0.1, float(source_duration or 0.0) - trim_start)
    return None


def storyboard_audio_input(downloaded_inputs: list[DownloadedInput]) -> DownloadedInput | None:
    for preferred_kind in ("soundtrack_audio", "source_audio"):
        for item in downloaded_inputs:
            if item.category == "audio" and str(item.kind or "").strip().lower() == preferred_kind:
                return item
    for item in downloaded_inputs:
        role = str(item.role or "").strip().lower()
        if item.category == "audio" and role in {"soundtrack", "source", "audio", "primary"}:
            return item
    return None


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


def validate_storyboard_ffmpeg_output(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    max_bytes: int,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("Storyboard FFmpeg output MP4 is missing.")
    file_size = path.stat().st_size
    if file_size <= 0 or file_size > max_bytes:
        raise ValueError(f"Storyboard FFmpeg output size is outside allowed bounds: {file_size} bytes.")
    header = path.read_bytes()[:64]
    if not looks_like_mp4(header):
        raise ValueError("Storyboard FFmpeg output does not look like MP4 bytes.")
    metadata = probe_video_metadata(path)
    output_width = int(metadata.get("display_width") or metadata.get("width") or 0)
    output_height = int(metadata.get("display_height") or metadata.get("height") or 0)
    if expected_width > 0 and expected_height > 0 and (output_width, output_height) != (int(expected_width), int(expected_height)):
        raise ValueError(f"Storyboard FFmpeg output dimensions mismatch; expected {expected_width}x{expected_height}, got {output_width}x{output_height}.")
    duration = float(metadata.get("duration_seconds") or 0.0)
    if duration <= 0:
        raise ValueError("Storyboard FFmpeg output duration could not be read.")
    if duration > MAX_STORYBOARD_VIDEO_DURATION_SECONDS + 0.05:
        raise ValueError(f"Storyboard FFmpeg output duration exceeds safety limit; got={duration:.2f}s.")
    if str(metadata.get("video_codec") or "").lower() != "h264":
        raise ValueError(f"Storyboard FFmpeg output must use H.264 video; got {metadata.get('video_codec') or 'unknown'}.")
    if str(metadata.get("audio_codec") or "").lower() != "aac":
        raise ValueError(f"Storyboard FFmpeg output must use AAC audio; got {metadata.get('audio_codec') or 'unknown'}.")
    return metadata


def storyboard_external_audio_filter_parts(audio_input_label: str, processing: dict[str, Any], effective_duration: float) -> list[str]:
    soundtrack_start = coerce_float(
        first_present(processing, "soundtrack_start", "soundtrack_start_seconds", "audio_start_seconds", "audio_offset_seconds"),
        0.0,
        0.0,
        MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    )
    head_silence = coerce_float(
        first_present(processing, "soundtrack_head_silence", "soundtrack_head_silence_seconds", "audio_head_silence_seconds"),
        0.0,
        0.0,
        MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    )
    tail_silence = coerce_float(
        first_present(processing, "soundtrack_tail_silence", "soundtrack_tail_silence_seconds", "audio_tail_silence_seconds"),
        0.0,
        0.0,
        MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    )
    total_duration = max(0.1, float(effective_duration or 0.1))
    head_silence = min(head_silence, total_duration)
    tail_silence = min(tail_silence, max(0.0, total_duration - head_silence))
    body_duration = max(0.0, total_duration - head_silence - tail_silence)
    if body_duration <= 0.001:
        return [
            "anullsrc=channel_layout=stereo:sample_rate=48000,"
            f"atrim=duration={total_duration:.3f},asetpts=PTS-STARTPTS,"
            "aformat=channel_layouts=stereo[aout]"
        ]
    if head_silence <= 0.001 and tail_silence <= 0.001:
        return [
            f"[{audio_input_label}]atrim=start={soundtrack_start:.3f}:duration={body_duration:.3f},"
            "asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[aout]"
        ]
    parts = []
    labels = []
    if head_silence > 0.001:
        parts.append(
            "anullsrc=channel_layout=stereo:sample_rate=48000,"
            f"atrim=duration={head_silence:.3f},asetpts=PTS-STARTPTS,"
            "aformat=channel_layouts=stereo[ahead]"
        )
        labels.append("[ahead]")
    parts.append(
        f"[{audio_input_label}]atrim=start={soundtrack_start:.3f}:duration={body_duration:.3f},"
        "asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[amain]"
    )
    labels.append("[amain]")
    if tail_silence > 0.001:
        parts.append(
            "anullsrc=channel_layout=stereo:sample_rate=48000,"
            f"atrim=duration={tail_silence:.3f},asetpts=PTS-STARTPTS,"
            "aformat=channel_layouts=stereo[atail]"
        )
        labels.append("[atail]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1,aformat=channel_layouts=stereo[aout]")
    return parts


def storyboard_overlay_filter_parts(
    overlay_input: DownloadedInput,
    overlay_input_index: int,
    matte_input_index: int | None,
    output_width: int,
    output_height: int,
    processing: dict[str, Any],
    effective_duration: float,
) -> list[str]:
    variant = str(processing.get("overlay_variant") or "static_rectangle").strip().lower()
    if variant in {"animated_rectangle", "interpolated_rectangle"}:
        return storyboard_animated_overlay_filter_parts(
            overlay_input,
            overlay_input_index,
            matte_input_index,
            output_width,
            output_height,
            processing,
            effective_duration,
        )
    return storyboard_static_overlay_filter_parts(
        overlay_input,
        overlay_input_index,
        matte_input_index,
        output_width,
        output_height,
        processing,
    )


def storyboard_static_overlay_filter_parts(
    overlay_input: DownloadedInput,
    overlay_input_index: int,
    matte_input_index: int | None,
    output_width: int,
    output_height: int,
    processing: dict[str, Any],
) -> list[str]:
    overlay_x = coerce_int(first_present(processing, "overlay_x", "x", "left", "pip_x"), 0, -4096, 4096)
    overlay_y = coerce_int(first_present(processing, "overlay_y", "y", "top", "pip_y"), 0, -4096, 4096)
    overlay_width = coerce_int(first_present(processing, "overlay_width", "overlay_w", "pip_width", "pip_w"), 0, 0, 4096)
    overlay_height = coerce_int(first_present(processing, "overlay_height", "overlay_h", "pip_height", "pip_h"), 0, 0, 4096)
    if overlay_width <= 0 or overlay_height <= 0:
        preset = str(processing.get("overlay_preset") or "").strip().lower()
        scale = optional_positive_float(processing.get("overlay_scale"), 1.0)
        if preset and scale:
            overlay_x, overlay_y, overlay_width, overlay_height = storyboard_overlay_rect(
                output_width,
                output_height,
                overlay_input,
                preset,
                scale,
                coerce_int(processing.get("margin_x"), 0, 0, 4096),
                coerce_int(processing.get("margin_y"), 0, 0, 4096),
            )
    start_opacity = coerce_float(processing.get("start_opacity", processing.get("opacity")), 1.0, 0.0, 1.0)
    end_opacity = coerce_float(processing.get("end_opacity"), start_opacity, 0.0, 1.0)
    opacity = end_opacity
    parts = []
    overlay_chain = f"[{overlay_input_index}:v]"
    if overlay_width > 0 and overlay_height > 0:
        overlay_chain += f"scale={overlay_width}:{overlay_height},"
    overlay_chain += "format=rgb24[ovbase]" if matte_input_index is not None else "format=rgba[ovbase]"
    parts.append(overlay_chain)
    source_label = "ovbase"
    if matte_input_index is not None:
        if overlay_width > 0 and overlay_height > 0:
            parts.append(f"[{matte_input_index}:v]scale={overlay_width}:{overlay_height},format=rgb24,format=gray[ovmask]")
        else:
            parts.append(f"[{matte_input_index}:v]format=rgb24,format=gray[ovmask]")
        parts.append("[ovbase][ovmask]alphamerge,format=rgba[ovmatte]")
        source_label = "ovmatte"
    if opacity < 0.999:
        parts.append(f"[{source_label}]geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*{opacity:.6f}'[ov]")
    else:
        parts.append(f"[{source_label}]copy[ov]")
    parts.append(f"[vbase][ov]overlay={overlay_x}:{overlay_y}:format=auto:eof_action=pass,format=yuv420p[vout]")
    return parts


def storyboard_animated_overlay_filter_parts(
    overlay_input: DownloadedInput,
    overlay_input_index: int,
    matte_input_index: int | None,
    output_width: int,
    output_height: int,
    processing: dict[str, Any],
    effective_duration: float,
) -> list[str]:
    start_preset = str(processing.get("overlay_preset") or "bottom_right").strip().lower()
    start_scale = coerce_float(processing.get("overlay_scale"), 0.28, 0.01, 4.0)
    start_margin_x = coerce_int(processing.get("margin_x"), 32, 0, 4096)
    start_margin_y = coerce_int(processing.get("margin_y"), 32, 0, 4096)
    end_preset = str(processing.get("end_overlay_preset") or processing.get("overlay_preset") or "bottom_right").strip().lower()
    end_scale = coerce_float(processing.get("end_overlay_scale"), start_scale, 0.01, 4.0)
    end_margin_x = coerce_int(processing.get("end_margin_x"), start_margin_x, 0, 4096)
    end_margin_y = coerce_int(processing.get("end_margin_y"), start_margin_y, 0, 4096)
    start_rect = storyboard_overlay_rect(output_width, output_height, overlay_input, start_preset, start_scale, start_margin_x, start_margin_y)
    end_rect = storyboard_overlay_rect(output_width, output_height, overlay_input, end_preset, end_scale, end_margin_x, end_margin_y)
    _sx, _sy, sw, sh = start_rect
    _ex, _ey, ew, eh = end_rect
    tile_width, tile_height = even_video_size(max(sw, ew), max(sh, eh))
    min_width_ratio = max(0.01, min(float(sw), float(ew)) / float(tile_width))
    min_height_ratio = max(0.01, min(float(sh), float(eh)) / float(tile_height))
    zoom_canvas_width, zoom_canvas_height = even_video_size(
        int(math.ceil(float(tile_width) / min_width_ratio)),
        int(math.ceil(float(tile_height) / min_height_ratio)),
    )
    if zoom_canvas_width > 8192 or zoom_canvas_height > 8192:
        raise ValueError(
            "Storyboard animated overlay scale range is too large for fixed-canvas rendering; "
            f"tile={tile_width}x{tile_height} zoom_canvas={zoom_canvas_width}x{zoom_canvas_height}."
        )
    easing = str(processing.get("overlay_easing") or "ease_in_out")
    fps = coerce_int(processing.get("fps"), STORYBOARD_OUTPUT_FPS, 1, 120)
    progress_t = storyboard_overlay_progress_expr(effective_duration, easing, "t")
    progress_t_upper = storyboard_overlay_progress_expr(effective_duration, easing, "T")
    progress_on = storyboard_overlay_frame_progress_expr(effective_duration, fps, easing)
    current_width_t = f"({sw}+({ew}-{sw})*({progress_t}))"
    current_height_t = f"({sh}+({eh}-{sh})*({progress_t}))"
    current_width_on = f"({sw}+({ew}-{sw})*({progress_on}))"
    current_height_on = f"({sh}+({eh}-{sh})*({progress_on}))"
    desired_x_expr = f"({start_rect[0]}+({end_rect[0]}-{start_rect[0]})*({progress_t}))"
    desired_y_expr = f"({start_rect[1]}+({end_rect[1]}-{start_rect[1]})*({progress_t}))"
    x_expr = f"({desired_x_expr}-(({tile_width})-({current_width_t}))/2)"
    y_expr = f"({desired_y_expr}-(({tile_height})-({current_height_t}))/2)"
    zoom_x_expr = f"(({current_width_on})/{float(tile_width):.6f})/{min_width_ratio:.6f}"
    zoom_y_expr = f"(({current_height_on})/{float(tile_height):.6f})/{min_height_ratio:.6f}"
    zoom_expr = f"min(({zoom_x_expr})\\,({zoom_y_expr}))"
    start_opacity = coerce_float(processing.get("start_opacity"), 1.0, 0.0, 1.0)
    end_opacity = coerce_float(processing.get("end_opacity"), start_opacity, 0.0, 1.0)
    opacity_expr = f"({start_opacity:.6f}+({end_opacity:.6f}-{start_opacity:.6f})*({progress_t_upper}))"
    parts = []
    if matte_input_index is not None:
        parts.extend([
            f"[{overlay_input_index}:v]setpts=PTS-STARTPTS,fps={fps},format=rgb24,"
            f"scale={tile_width}:{tile_height}:force_original_aspect_ratio=decrease,"
            f"pad={zoom_canvas_width}:{zoom_canvas_height}:(ow-iw)/2:(oh-ih)/2:color=black[ovscaledrgb]",
            f"[{matte_input_index}:v]format=rgb24,scale={tile_width}:{tile_height}:force_original_aspect_ratio=decrease,"
            f"pad={zoom_canvas_width}:{zoom_canvas_height}:(ow-iw)/2:(oh-ih)/2:color=black,format=gray[ovmask]",
            "[ovscaledrgb][ovmask]alphamerge,format=rgba[ovmatte]",
        ])
        source_label = "ovmatte"
    else:
        parts.append(
            f"[{overlay_input_index}:v]setpts=PTS-STARTPTS,fps={fps},format=rgba,"
            f"scale={tile_width}:{tile_height}:force_original_aspect_ratio=decrease,"
            f"pad={zoom_canvas_width}:{zoom_canvas_height}:(ow-iw)/2:(oh-ih)/2:color=black@0[ovscaled]"
        )
        source_label = "ovscaled"
    parts.extend([
        f"[{source_label}]zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={tile_width}x{tile_height}:fps={fps},setpts=N/({fps}*TB),format=rgba[ovtile]",
        f"[ovtile]geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({opacity_expr})'[ov]",
        f"[vbase][ov]overlay=x='{x_expr}':y='{y_expr}':format=auto:eval=frame:eof_action=pass,format=yuv420p[vout]",
    ])
    return parts


def storyboard_overlay_rect(
    canvas_width: int,
    canvas_height: int,
    overlay_input: DownloadedInput,
    preset: str,
    scale: float,
    margin_x: int,
    margin_y: int,
) -> tuple[int, int, int, int]:
    source_width, source_height = storyboard_input_display_size(overlay_input)
    aspect = max(0.01, float(source_width) / float(source_height or 1))
    rect_width = max(2, int(round(float(canvas_width) * float(scale))))
    rect_height = max(2, int(round(rect_width / aspect)))
    if rect_height > canvas_height:
        rect_height = max(2, int(round(float(canvas_height) * float(scale))))
        rect_width = max(2, int(round(rect_height * aspect)))
    rect_width, rect_height = even_video_size(min(rect_width, canvas_width), min(rect_height, canvas_height))
    preset = preset if preset in {"bottom_right", "bottom_left", "top_right", "top_left", "center"} else "bottom_right"
    if preset == "bottom_right":
        x = canvas_width - rect_width - int(margin_x)
        y = canvas_height - rect_height - int(margin_y)
    elif preset == "bottom_left":
        x = int(margin_x)
        y = canvas_height - rect_height - int(margin_y)
    elif preset == "top_right":
        x = canvas_width - rect_width - int(margin_x)
        y = int(margin_y)
    elif preset == "top_left":
        x = int(margin_x)
        y = int(margin_y)
    else:
        x = (canvas_width - rect_width) // 2
        y = (canvas_height - rect_height) // 2
    return max(0, min(canvas_width - rect_width, x)), max(0, min(canvas_height - rect_height, y)), rect_width, rect_height


def storyboard_input_display_size(item: DownloadedInput) -> tuple[int, int]:
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    width = int(metadata.get("display_width") or metadata.get("width") or item.width or 0)
    height = int(metadata.get("display_height") or metadata.get("height") or item.height or 0)
    return max(2, width or 2), max(2, height or 2)


def storyboard_overlay_progress_expr(duration_seconds: float, easing: str, time_var: str) -> str:
    duration = max(0.001, float(duration_seconds or 0.001))
    p = f"if(gte({time_var}\\,{duration:.6f})\\,1\\,if(lte({time_var}\\,0)\\,0\\,{time_var}/{duration:.6f}))"
    if str(easing or "").strip().lower() == "linear":
        return p
    return f"(({p})*({p})*(3-2*({p})))"


def storyboard_overlay_frame_progress_expr(duration_seconds: float, fps: int, easing: str) -> str:
    frame_count = max(1.0, float(duration_seconds or 0.001) * float(max(1, int(fps or STORYBOARD_OUTPUT_FPS))))
    p = f"if(gte(on\\,{frame_count:.6f})\\,1\\,if(lte(on\\,0)\\,0\\,on/{frame_count:.6f}))"
    if str(easing or "").strip().lower() == "linear":
        return p
    return f"(({p})*({p})*(3-2*({p})))"


def event_video_step_timeout(duration_seconds: float) -> int:
    try:
        duration = max(1.0, float(duration_seconds or 1.0))
    except (TypeError, ValueError):
        duration = 1.0
    return int(min(EVENT_VIDEO_TIMEOUT_MAX_SECONDS, max(EVENT_VIDEO_TIMEOUT_BASE_SECONDS, EVENT_VIDEO_TIMEOUT_BASE_SECONDS + duration * EVENT_VIDEO_TIMEOUT_MULTIPLIER)))


def storyboard_step_timeout(duration_seconds: float) -> int:
    try:
        duration = max(1.0, float(duration_seconds or 1.0))
    except (TypeError, ValueError):
        duration = 1.0
    return int(min(STORYBOARD_TIMEOUT_MAX_SECONDS, max(STORYBOARD_TIMEOUT_BASE_SECONDS, STORYBOARD_TIMEOUT_BASE_SECONDS + duration * STORYBOARD_TIMEOUT_MULTIPLIER)))


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


def storyboard_encode_args(processing: dict[str, Any]) -> list[str]:
    preset = str(processing.get("preset") or "veryfast").strip().lower()
    if preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}:
        preset = "veryfast"
    tune = str(processing.get("tune") or "none").strip().lower()
    if tune not in {"none", "film", "animation", "grain", "stillimage", "fastdecode", "zerolatency"}:
        tune = "none"
    crf = coerce_int(processing.get("crf"), EVENT_VIDEO_X264_CRF, 0, 51)
    audio_bitrate = str(processing.get("audio_bitrate") or "128k").strip().lower()
    if not re.fullmatch(r"[1-9][0-9]{1,3}k", audio_bitrate):
        audio_bitrate = "128k"
    args = [
        "-c:v",
        EVENT_VIDEO_H264_ENCODER,
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        "-metadata:s:v:0",
        "rotate=0",
    ]
    if tune != "none":
        args[6:6] = ["-tune", tune]
    return args


def coerce_bool_local(value: Any, default: bool = False) -> bool:
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
