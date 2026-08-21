from __future__ import annotations

import signal
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .capabilities import build_capabilities
from .config import delete_config, save_config
from .constants import (
    ALLOWED_EVENT_BUMPER_MIME_TYPES,
    ALLOWED_EVENT_INPUT_KINDS,
    ALLOWED_EVENT_OVERLAY_MIME_TYPES,
    ALLOWED_EVENT_SOURCE_VIDEO_MIME_TYPES,
    ALLOWED_AUDIO_INPUT_MIME_TYPES,
    ALLOWED_AUDIO_SUFFIXES,
    ALLOWED_IMAGE_MIME_TYPES,
    ALLOWED_STORYBOARD_AUDIO_CONTAINER_MIME_TYPES,
    ALLOWED_STORYBOARD_INPUT_KINDS,
    ALLOWED_STORYBOARD_MATTE_MIME_TYPES,
    ALLOWED_STORYBOARD_SOURCE_VIDEO_MIME_TYPES,
    STORYBOARD_AUDIO_INPUT_KINDS,
    STORYBOARD_FFMPEG_OPERATION_TYPES,
    STORYBOARD_FFMPEG_PROCESSING_TASK,
    STORYBOARD_FFMPEG_PROCESSOR_ID,
    STORYBOARD_IMAGE_INPUT_KINDS,
    STORYBOARD_LOCAL_VIDEO_TAKE_OPERATION_TYPES,
    STORYBOARD_SINGLE_VIDEO_OPERATION_TYPES,
    STORYBOARD_VIDEO_INPUT_KINDS,
    MAX_STORYBOARD_AUDIO_INPUT_BYTES,
    MAX_STORYBOARD_IMAGE_INPUT_BYTES,
    MAX_STORYBOARD_VIDEO_INPUT_BYTES,
    MAX_STORYBOARD_VIDEO_DURATION_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_EVENT_VIDEO_INPUT_BYTES,
    MAX_IMAGE_BYTES,
    MIME_EXTENSION,
    POLL_INTERVAL_SECONDS,
)
from .ffmpeg_probe import probe_audio_metadata, probe_ffmpeg
from .ffmpeg_processor import EventVideoProcessor, LocalProcessJob
from .log import log
from .midom_api import MidomApi, MidomMethodNotAllowedError
from .types import DownloadedInput, WorkerConfig
from .validation import (
    coerce_bool,
    coerce_input_id,
    coerce_job_id,
    decode_image_info,
    read_limited_response_content,
    sha256_bytes,
    validate_asset_dimensions,
    event_job_max_output_duration_seconds,
    is_storyboard_ffmpeg_processing_job,
    is_event_video_processing_job,
    merged_processing_payload,
    validate_event_job,
    validate_job_scope,
    validate_source_video,
    validate_storyboard_ffmpeg_job,
    validate_storyboard_video,
    storyboard_operation_type,
)


class LocalProcessorWorker:
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.api = MidomApi(config)
        self.stop_event = threading.Event()
        self.active_job_id: int | None = None
        self.active_process: LocalProcessJob | None = None
        self.last_heartbeat_at = 0.0
        self.shutdown_action = "quit"
        self._shutdown_lock = threading.Lock()

    def request_stop(self, *_args: Any) -> None:
        self._set_shutdown_action("quit")
        log("Stop requested; finishing cancellation path.")
        self.stop_event.set()
        if self.active_process is not None:
            self.active_process.cancel()

    def request_disconnect(self) -> None:
        self._set_shutdown_action("disconnect")
        log("Disconnect requested; finishing cancellation path.")
        self.stop_event.set()
        if self.active_process is not None:
            self.active_process.cancel()

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        self.update_capabilities(allow_method_not_allowed=True)
        log(f"Worker loop started; worker_id={self.config.worker_id} project_id={self.config.project_id}.")
        log("Waiting for local media processing jobs.")
        self._start_interactive_controls()
        while not self.stop_event.is_set():
            now = time.monotonic()
            if now - self.last_heartbeat_at >= HEARTBEAT_INTERVAL_SECONDS:
                try:
                    self.send_heartbeat()
                except Exception as exc:
                    log(f"Heartbeat failed; will retry. {exc}")
                self.last_heartbeat_at = now
            try:
                if self.active_job_id is None:
                    self.poll_once()
            except Exception as exc:
                log(f"Worker loop poll failed: {exc}")
            self.stop_event.wait(POLL_INTERVAL_SECONDS)
        if self._get_shutdown_action() == "disconnect":
            self._disconnect_and_delete_config()
        else:
            try:
                self.api.heartbeat(status="paused", accepting=False, active_job_id=None, message="Local Event Video Processor stopped.")
            except Exception as exc:
                log(f"Final heartbeat failed: {exc}")
        log("Worker loop stopped.")

    def _set_shutdown_action(self, action: str) -> None:
        with self._shutdown_lock:
            if action == "disconnect" or self.shutdown_action != "disconnect":
                self.shutdown_action = action

    def _get_shutdown_action(self) -> str:
        with self._shutdown_lock:
            return self.shutdown_action

    def _start_interactive_controls(self) -> None:
        if not sys.stdin.isatty():
            return
        log("Controls: type Q then Enter to stop for now. Type T then Enter to disconnect this processor.")
        threading.Thread(target=self._interactive_control_loop, name="midom-worker-controls", daemon=True).start()

    def _interactive_control_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                command = input("Command [Q=quit, T=terminate]: ").strip().lower()
            except (EOFError, OSError):
                return
            if not command:
                continue
            if command in {"q", "quit"}:
                log("Quit command received. The saved pairing will be kept for next time.")
                self.request_stop()
                return
            if command in {"t", "terminate", "disconnect"}:
                print()
                print("This will disconnect this local processor from Midom and remove its saved pairing.")
                print("You will need a new pairing code to use this computer again.")
                try:
                    confirmation = input("Type TERMINATE to continue: ").strip()
                except (EOFError, OSError):
                    return
                if confirmation == "TERMINATE":
                    log("Terminate command confirmed. This processor will disconnect and remove local credentials.")
                    self.request_disconnect()
                    return
                log("Terminate command cancelled. The processor will keep running.")
                continue
            log("Unknown command. Type Q then Enter to stop for now, or T then Enter to disconnect this processor.")

    def _disconnect_and_delete_config(self) -> None:
        try:
            self.api.disconnect()
            log(f"Remote disconnect accepted; worker_id={self.config.worker_id}.")
        except Exception as exc:
            log(f"Remote disconnect failed; worker may remain visible in Midom: {exc}")
        delete_config()
        log("Local processor credentials removed. A new pairing code is required before this computer can process again.")

    def update_capabilities(self, *, allow_method_not_allowed: bool = False) -> None:
        capabilities = build_capabilities()
        media_processing = capabilities.get("media_processing") or []
        if not media_processing:
            probe = probe_ffmpeg()
            raise RuntimeError(f"Event Video Processing capability unavailable. FFmpeg probe={probe}")
        try:
            self.config = self.api.post_capabilities(capabilities)
        except MidomMethodNotAllowedError as exc:
            if not allow_method_not_allowed:
                raise
            return
        self.api = MidomApi(self.config)
        save_config(self.config)
        log(f"Capabilities updated; revision={self.config.capabilities_revision}.")

    def send_heartbeat(self) -> None:
        if self.active_job_id is not None:
            status = "busy"
            accepting = True
            active_job_id = self.active_job_id
            message = f"Processing local media job {self.active_job_id}. Accepting additional queued jobs."
        else:
            status = "idle"
            accepting = True
            active_job_id = None
            message = "Idle and accepting local media processing jobs."
        payload = self.api.heartbeat(status=status, accepting=accepting, active_job_id=active_job_id, message=message)
        if isinstance(payload, dict) and payload.get("revoke_requested"):
            log("Midom requested worker revocation; stopping.")
            self.request_stop()

    def poll_once(self) -> bool:
        jobs = self.api.candidates(limit=5)
        if not jobs:
            log(f"Candidate poll returned no jobs; worker_id={self.config.worker_id}.")
            return False
        log(f"Candidate poll returned {len(jobs)} candidate(s).")
        for candidate in jobs:
            reason = candidate_incompatibility_reason(candidate, self.config)
            if reason:
                log(f"Skipping incompatible candidate; job_id={candidate.get('job_id')} reason={reason}.")
                continue
            job_id = int(candidate["job_id"])
            log(f"Attempting claim; job_id={job_id}.")
            claimed = self.api.claim(job_id)
            if claimed is None:
                log(f"Claim conflict or unavailable candidate; job_id={job_id}.")
                continue
            self.run_job(claimed)
            return True
        return False

    def run_job(self, job: dict[str, Any]) -> None:
        job_id = coerce_job_id(job)
        self.active_job_id = job_id
        self.active_process = None
        log(f"Starting claimed local media processing job; job_id={job_id}.")
        try:
            validate_job_scope(job, org_id=self.config.org_id, project_id=self.config.project_id, paired_user_id=self.config.paired_user_id)
            is_storyboard_job = is_storyboard_ffmpeg_processing_job(job)
            if is_storyboard_job:
                processing = validate_storyboard_ffmpeg_job(job)
                max_output_duration_seconds = None
                start_status = "Preparing local Storyboard FFmpeg Processing."
                complete_model_id = STORYBOARD_FFMPEG_PROCESSOR_ID
                complete_processor_id = STORYBOARD_FFMPEG_PROCESSOR_ID
            else:
                processing = validate_event_job(job)
                max_output_duration_seconds = event_job_max_output_duration_seconds(job)
                start_status = "Preparing local Event Video Processing."
                complete_model_id = "event_video_ffmpeg_processor"
                complete_processor_id = "event_video_ffmpeg_processor"
            self.api.job_update(job_id, "progress", {"phase": "starting", "status": start_status, "progress": 0})
            with tempfile.TemporaryDirectory(prefix=f"midom-local-job-{job_id}-") as temp:
                temp_dir = Path(temp)
                downloaded_inputs = self.download_inputs(job, temp_dir)
                process_handle = LocalProcessJob()
                self.active_process = process_handle

                def progress(phase: str, status: str, percent: int) -> None:
                    payload = {"phase": phase, "status": status, "progress": int(percent)}
                    response = self.api.job_update(job_id, "progress", payload)
                    if isinstance(response, dict) and response.get("cancel_requested"):
                        log(f"Midom requested cancellation; job_id={job_id}.")
                        process_handle.cancel()

                processor = EventVideoProcessor(progress=progress, cancel_event=self.stop_event)
                if is_storyboard_job:
                    result = processor.process_storyboard(
                        job_id=job_id,
                        downloaded_inputs=downloaded_inputs,
                        processing=processing,
                        temp_dir=temp_dir,
                        process_handle=process_handle,
                        worker_id=self.config.worker_id,
                    )
                else:
                    result = processor.process(
                        job_id=job_id,
                        downloaded_inputs=downloaded_inputs,
                        processing=processing,
                        temp_dir=temp_dir,
                        process_handle=process_handle,
                        worker_id=self.config.worker_id,
                        max_output_duration_seconds=max_output_duration_seconds,
                    )
                process_handle.mark_done()
                self.active_process = None
                if self.stop_event.is_set() or process_handle.cancelled:
                    self.api.job_update(job_id, "fail", {"reason": "cancel_requested", "message": "Local media processing was cancelled."})
                    return
                artifact = self.api.upload_video_artifact(job_id, result.path, artifact_index=0)
                complete_payload = {
                    "artifacts": [artifact],
                    "backend": "ffmpeg",
                    "model_id": complete_model_id,
                    "processor_id": complete_processor_id,
                    "processing_metadata": result.metadata,
                }
                log(
                    "Completing FFmpeg media processing job; "
                    f"job_id={job_id} artifact={artifact} "
                    f"output={result.metadata.get('output_width')}x{result.metadata.get('output_height')} "
                    f"duration_seconds={result.metadata.get('output_duration_seconds')}."
                )
                self.api.job_update(job_id, "complete", complete_payload)
                log(f"FFmpeg media processing complete accepted by Midom; job_id={job_id}.")
        except RuntimeError as exc:
            if str(exc) == "cancel_requested":
                try:
                    self.api.job_update(job_id, "fail", {"reason": "cancel_requested", "message": "Local media processing was cancelled."})
                except Exception as fail_exc:
                    log(f"Failed to report cancellation; job_id={job_id} error={fail_exc}")
            else:
                self.fail_job(job_id, exc)
        except Exception as exc:
            self.fail_job(job_id, exc)
        finally:
            self.active_job_id = None
            self.active_process = None

    def fail_job(self, job_id: int, exc: BaseException) -> None:
        log(f"Job failed locally; job_id={job_id} error={exc}")
        try:
            self.api.job_update(job_id, "fail", {"reason": "validation_or_runtime_error", "message": str(exc)})
        except Exception as fail_exc:
            log(f"Failed to report job failure; job_id={job_id} error={fail_exc}")

    def download_inputs(self, job: dict[str, Any], temp_dir: Path) -> list[DownloadedInput]:
        if is_storyboard_ffmpeg_processing_job(job):
            return self.download_storyboard_inputs(job, temp_dir)
        job_id = coerce_job_id(job)
        processing = job.get("processing") if isinstance(job.get("processing"), dict) else {}
        apply_overlay = coerce_bool(processing.get("apply_overlay"), False)
        add_bumper = coerce_bool(processing.get("add_ending_bumper"), False)
        inputs = job.get("inputs") or []
        if not isinstance(inputs, list):
            raise ValueError("Job inputs must be a list.")
        source_mime_types = set(ALLOWED_EVENT_SOURCE_VIDEO_MIME_TYPES)
        if not probe_ffmpeg().get("quicktime_demux_available"):
            source_mime_types.discard("video/quicktime")
        downloaded: list[DownloadedInput] = []
        source_count = 0
        overlay_count = 0
        bumper_count = 0
        for item in inputs:
            if not isinstance(item, dict):
                raise ValueError("Event Video Processing input descriptor must be a JSON object.")
            input_id = coerce_input_id(item)
            kind = str(item.get("kind") or "").strip()
            if kind not in ALLOWED_EVENT_INPUT_KINDS:
                raise ValueError(f"Unsupported Event Video Processing input kind: {kind}")
            orientation = ""
            if kind == "source_video":
                source_count += 1
                allowed_mime = source_mime_types
                max_bytes = MAX_EVENT_VIDEO_INPUT_BYTES
            elif kind == "overlay_png":
                overlay_count += 1
                orientation = str(item.get("orientation") or "").strip().lower()
                allowed_mime = ALLOWED_EVENT_OVERLAY_MIME_TYPES
                max_bytes = MAX_IMAGE_BYTES
            else:
                bumper_count += 1
                orientation = str(item.get("orientation") or "").strip().lower()
                allowed_mime = ALLOWED_EVENT_BUMPER_MIME_TYPES
                max_bytes = MAX_IMAGE_BYTES
            response = self.api.download_input(job_id, input_id)
            mime_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if mime_type not in allowed_mime:
                raise ValueError(f"Unsupported Event Video Processing {kind} MIME type: {mime_type}")
            data = read_limited_response_content(response, input_id, max_bytes)
            expected_size = item.get("bytes")
            if expected_size is not None and len(data) != int(expected_size):
                raise ValueError(f"Event Video Processing input {input_id} size mismatch.")
            actual_sha = sha256_bytes(data)
            expected_sha = str(item.get("sha256") or response.headers.get("X-Midom-SHA256") or "").strip().lower()
            if expected_sha and actual_sha != expected_sha:
                raise ValueError(f"Event Video Processing input {input_id} SHA-256 mismatch.")
            path = temp_dir / safe_input_filename(item.get("filename"), input_id, mime_type)
            path.write_bytes(data)
            metadata = None
            width = height = None
            if kind == "source_video":
                metadata = validate_source_video(path)
            else:
                width, height, decoded_format = decode_image_info(path)
                validate_asset_dimensions(kind, width, height)
                if kind == "overlay_png" and decoded_format != "PNG":
                    raise ValueError(f"overlay_png input must decode as PNG; got {decoded_format}.")
                if kind == "bumper_image" and decoded_format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError(f"bumper_image decoded as unsupported format: {decoded_format}.")
            downloaded.append(DownloadedInput(input_id, kind, path, mime_type, actual_sha, orientation, metadata, width, height))
            log(f"Downloaded input; job_id={job_id} input_id={input_id} kind={kind} mime_type={mime_type} bytes={len(data)}.")
        if source_count != 1:
            raise ValueError(f"Event Video Processing requires exactly one source_video input; got {source_count}.")
        if apply_overlay and overlay_count <= 0:
            raise ValueError("apply_overlay is true but no overlay_png inputs were downloaded.")
        if add_bumper and bumper_count <= 0:
            raise ValueError("add_ending_bumper is true but no bumper_image inputs were downloaded.")
        if not apply_overlay and overlay_count:
            raise ValueError("overlay_png inputs were downloaded but apply_overlay is false.")
        if not add_bumper and bumper_count:
            raise ValueError("bumper_image inputs were downloaded but add_ending_bumper is false.")
        return downloaded

    def download_storyboard_inputs(self, job: dict[str, Any], temp_dir: Path) -> list[DownloadedInput]:
        job_id = coerce_job_id(job)
        operation_type = storyboard_operation_type(job)
        processing = merged_processing_payload(job)
        inputs = job.get("inputs") or []
        if not isinstance(inputs, list):
            raise ValueError("Storyboard FFmpeg inputs must be a list.")
        source_video_mime_types = set(ALLOWED_STORYBOARD_SOURCE_VIDEO_MIME_TYPES)
        if not probe_ffmpeg().get("quicktime_demux_available"):
            source_video_mime_types.discard("video/quicktime")
        downloaded: list[DownloadedInput] = []
        for index, item in enumerate(inputs):
            if not isinstance(item, dict):
                raise ValueError("Storyboard FFmpeg input descriptor must be a JSON object.")
            input_id = coerce_input_id(item)
            kind = str(item.get("kind") or "").strip()
            if kind not in ALLOWED_STORYBOARD_INPUT_KINDS:
                raise ValueError(f"Unsupported Storyboard FFmpeg input kind: {kind}")
            if kind in STORYBOARD_VIDEO_INPUT_KINDS:
                allowed_mime = source_video_mime_types
                max_bytes = MAX_STORYBOARD_VIDEO_INPUT_BYTES
                category = "video"
            elif kind in STORYBOARD_IMAGE_INPUT_KINDS:
                allowed_mime = ALLOWED_STORYBOARD_MATTE_MIME_TYPES if item_is_storyboard_matte(item, processing) else ALLOWED_IMAGE_MIME_TYPES
                max_bytes = MAX_STORYBOARD_IMAGE_INPUT_BYTES
                category = "image"
            else:
                if kind in {"source_audio", "soundtrack_audio"}:
                    allowed_mime = ALLOWED_STORYBOARD_AUDIO_CONTAINER_MIME_TYPES
                    max_bytes = MAX_STORYBOARD_VIDEO_INPUT_BYTES
                else:
                    allowed_mime = ALLOWED_AUDIO_INPUT_MIME_TYPES
                    max_bytes = MAX_STORYBOARD_AUDIO_INPUT_BYTES
                category = "audio"
            response = self.api.download_input(job_id, input_id)
            mime_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if mime_type not in allowed_mime:
                if category == "image" and item_is_storyboard_matte(item, processing):
                    raise ValueError(f"Overlay matte was provided but could not be applied: unsupported matte MIME type {mime_type or 'unknown'}.")
                raise ValueError(f"Unsupported Storyboard FFmpeg {kind} MIME type: {mime_type}")
            data = read_limited_response_content(response, input_id, max_bytes)
            expected_size = item.get("bytes")
            if expected_size is not None and len(data) != int(expected_size):
                raise ValueError(f"Storyboard FFmpeg input {input_id} size mismatch.")
            actual_sha = sha256_bytes(data)
            expected_sha = str(item.get("sha256") or response.headers.get("X-Midom-SHA256") or "").strip().lower()
            if expected_sha and actual_sha != expected_sha:
                raise ValueError(f"Storyboard FFmpeg input {input_id} SHA-256 mismatch.")
            path = temp_dir / safe_input_filename(item.get("filename"), input_id, mime_type)
            path.write_bytes(data)
            metadata = None
            width = height = None
            decoded_format = ""
            duration_seconds = None
            if category == "video":
                metadata = validate_storyboard_video(path)
            elif category == "image":
                try:
                    width, height, decoded_format = decode_image_info(path)
                except Exception as exc:
                    if item_is_storyboard_matte(item, processing):
                        raise ValueError(f"Overlay matte was provided but could not be applied: decode failed: {exc}") from exc
                    raise
                if item_is_storyboard_matte(item, processing) and decoded_format not in {"PNG", "JPEG"}:
                    raise ValueError(
                        "Overlay matte was provided but could not be applied: "
                        f"decoded matte format {decoded_format or 'unknown'} is not PNG or JPEG."
                    )
            else:
                metadata = probe_audio_metadata(path)
                duration_seconds = float(metadata.get("duration_seconds") or 0.0)
            downloaded.append(
                DownloadedInput(
                    input_id=input_id,
                    kind=kind,
                    path=path,
                    mime_type=mime_type,
                    sha256=actual_sha,
                    metadata=metadata,
                    width=width,
                    height=height,
                    category=category,
                    order=coerce_input_order(item.get("order", item.get("sequence", index)), index),
                    role=str(item.get("role") or "").strip(),
                    decoded_format=decoded_format,
                    duration_seconds=duration_seconds,
                    dbfileid=optional_int(item.get("dbfileid")),
                    dbfile_id=optional_int(item.get("dbfile_id")),
                    file_id=optional_int(item.get("file_id")),
                )
            )
            log(f"Downloaded Storyboard FFmpeg input; job_id={job_id} input_id={input_id} kind={kind} category={category} mime_type={mime_type} bytes={len(data)}.")
        video_count = sum(1 for item in downloaded if item.category == "video")
        if operation_type == "multicam_final_assembly":
            if video_count < 1:
                raise ValueError("Storyboard final assembly requires at least one downloaded video input.")
        elif operation_type == "replace_video_soundtrack":
            audio_count = sum(1 for item in downloaded if item.category == "audio")
            if video_count != 1:
                raise ValueError(f"Storyboard replace_video_soundtrack requires exactly one downloaded source video input; got {video_count}.")
            if audio_count != 1:
                raise ValueError(f"Storyboard replace_video_soundtrack requires exactly one downloaded soundtrack audio input; got {audio_count}.")
        elif operation_type in STORYBOARD_LOCAL_VIDEO_TAKE_OPERATION_TYPES:
            render_mode = str(processing.get("render_mode") or "").strip().lower()
            image_count = sum(1 for item in downloaded if item.category == "image")
            audio_count = sum(1 for item in downloaded if item.category == "audio")
            start_image_count = sum(1 for item in downloaded if item.kind == "start_image")
            end_image_count = sum(1 for item in downloaded if item.kind == "end_image")
            scene_audio_count = sum(1 for item in downloaded if item.kind == "scene_audio")
            if render_mode not in {"one_image", "two_image_fade", "voice_over_video"}:
                raise ValueError(f"Unsupported storyboard local video take render_mode: {render_mode or 'missing'}")
            if render_mode == "voice_over_video":
                if video_count != 1:
                    raise ValueError(f"Storyboard local voice_over_video render requires exactly one downloaded source_video; got {video_count}.")
                if scene_audio_count != 1 or audio_count != 1:
                    raise ValueError(
                        "Storyboard local voice_over_video render requires exactly one downloaded scene_audio and no other audio inputs; "
                        f"got scene_audio={scene_audio_count}, audio_inputs={audio_count}."
                    )
                if start_image_count or end_image_count:
                    raise ValueError(
                        "Storyboard local voice_over_video render does not use downloaded start_image or end_image inputs; "
                        f"got start_image={start_image_count}, end_image={end_image_count}, image_count={image_count}."
                    )
            else:
                if video_count:
                    raise ValueError(f"Storyboard local {render_mode} render does not support downloaded video inputs; got {video_count}.")
                if audio_count:
                    raise ValueError("Storyboard local video take one_image/two_image_fade does not support downloaded audio inputs yet.")
            if coerce_bool(processing.get("pan_enabled"), False):
                raise ValueError("Storyboard local video take pan_enabled=true is not supported by the bridge first pass.")
            if render_mode == "one_image" and (start_image_count != 1 or end_image_count):
                raise ValueError(
                    "Storyboard local one_image render requires exactly one downloaded start_image and no end_image; "
                    f"got start_image={start_image_count}, end_image={end_image_count}, image_count={image_count}."
                )
            if render_mode == "two_image_fade" and (start_image_count != 1 or end_image_count != 1):
                raise ValueError(
                    "Storyboard local two_image_fade render requires exactly one downloaded start_image and one end_image; "
                    f"got start_image={start_image_count}, end_image={end_image_count}, image_count={image_count}."
                )
        elif operation_type in STORYBOARD_SINGLE_VIDEO_OPERATION_TYPES and video_count < 1:
            raise ValueError(f"Storyboard operation {operation_type} requires at least one downloaded video input.")
        return downloaded


def safe_input_filename(filename: Any, input_id: int, mime_type: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", *ALLOWED_AUDIO_SUFFIXES}:
        suffix = MIME_EXTENSION.get(mime_type, ".bin")
    return f"input-{int(input_id)}{suffix}"


def coerce_input_order(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(0, min(10_000, number))


def optional_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def optional_positive_candidate_float(value: Any, maximum: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number > float(maximum):
        return None
    return number


def item_is_storyboard_matte(item: dict[str, Any], processing: dict[str, Any] | None = None) -> bool:
    role = str(item.get("role") or "").strip().lower()
    kind = str(item.get("kind") or "").strip().lower()
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
    for value in (item.get("input_id"), item.get("dbfileid"), item.get("dbfile_id"), item.get("file_id")):
        try:
            if int(value) in selector_ids:
                return True
        except (TypeError, ValueError):
            continue
    return False


def candidate_incompatibility_reason(candidate: Any, config: WorkerConfig) -> str | None:
    if not isinstance(candidate, dict):
        return "candidate is not an object"
    try:
        worker_id = int(candidate.get("worker_id"))
        org_id = int(candidate.get("org_id"))
        project_id = int(candidate.get("project_id"))
        requested_by_user_id = int(candidate.get("requested_by_user_id"))
        job_id = int(candidate.get("job_id"))
    except (TypeError, ValueError):
        return "candidate has invalid ids"
    if job_id <= 0:
        return "job_id is missing or invalid"
    if worker_id != int(config.worker_id):
        return f"worker_id mismatch: {worker_id}"
    if org_id != int(config.org_id):
        return f"org_id mismatch: {org_id}"
    if project_id != int(config.project_id):
        return f"project_id mismatch: {project_id}"
    if requested_by_user_id != int(config.paired_user_id):
        return f"requested_by_user_id mismatch: {requested_by_user_id}"
    media_type = str(candidate.get("media_type") or "").strip().lower()
    if media_type != "video":
        return f"unsupported media_type: {media_type}"
    summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
    processing = candidate.get("processing") if isinstance(candidate.get("processing"), dict) else {}
    family = str(candidate.get("family") or summary.get("family") or "").strip().lower()
    processing_task = str(candidate.get("processing_task") or summary.get("processing_task") or "").strip().lower()
    processor_id = str(candidate.get("processor_id") or candidate.get("model_id") or summary.get("processor_id") or "").strip()
    if family and family != "media_processing":
        return f"unsupported family: {family}"
    operation_type = storyboard_operation_type(candidate)
    if operation_type in STORYBOARD_FFMPEG_OPERATION_TYPES or processing_task == STORYBOARD_FFMPEG_PROCESSING_TASK or processor_id == STORYBOARD_FFMPEG_PROCESSOR_ID:
        if operation_type not in STORYBOARD_FFMPEG_OPERATION_TYPES:
            return f"unsupported storyboard operation_type: {operation_type}"
        if processor_id and processor_id != STORYBOARD_FFMPEG_PROCESSOR_ID:
            return f"unsupported storyboard processor_id: {processor_id}"
        try:
            output_count = int(summary.get("output_count") or 1)
        except (TypeError, ValueError):
            return f"invalid storyboard output_count: {summary.get('output_count')}"
        if output_count != 1:
            return f"unsupported storyboard output_count: {output_count}"
        output_format = str(summary.get("output_format") or summary.get("format") or "mp4").strip().lower()
        if output_format != "mp4":
            return f"unsupported storyboard output_format: {output_format}"
        video_count = coerce_input_order(summary.get("video_input_count", summary.get("source_video_count", summary.get("input_video_count"))), 1)
        if operation_type == "multicam_final_assembly":
            if video_count < 1:
                return "final assembly requires video inputs"
        elif operation_type == "replace_video_soundtrack":
            if video_count != 1:
                return f"replace_video_soundtrack requires exactly one source video input; got {video_count}"
            audio_count = coerce_input_order(summary.get("audio_input_count", summary.get("soundtrack_audio_count")), 1)
            if audio_count != 1:
                return f"replace_video_soundtrack requires exactly one soundtrack audio input; got {audio_count}"
        elif operation_type in STORYBOARD_LOCAL_VIDEO_TAKE_OPERATION_TYPES:
            render_mode = str(
                candidate.get("render_mode") or processing.get("render_mode") or summary.get("render_mode") or ""
            ).strip().lower()
            if render_mode not in {"one_image", "two_image_fade", "voice_over_video"}:
                return f"{operation_type} unsupported render_mode: {render_mode or 'missing'}"
            if coerce_bool(candidate.get("pan_enabled", processing.get("pan_enabled", summary.get("pan_enabled"))), False):
                return "local video take pan_enabled=true is not supported"
            audio_count = coerce_input_order(summary.get("audio_input_count", summary.get("soundtrack_audio_count")), 0)
            if render_mode == "voice_over_video":
                if video_count != 1:
                    return f"voice_over_video requires exactly one source video input; got {video_count}"
                scene_audio_count = coerce_input_order(summary.get("scene_audio_count"), 1)
                if scene_audio_count != 1 or audio_count != 1:
                    return f"voice_over_video requires exactly one scene_audio input; got scene_audio={scene_audio_count}, audio_inputs={audio_count}"
                transition = str(
                    candidate.get("video_transition")
                    or processing.get("video_transition")
                    or summary.get("video_transition")
                    or ""
                ).strip().lower()
                if transition not in {"", "none"}:
                    return f"voice_over_video unsupported video_transition: {transition}"
                duration_value = candidate.get("duration_seconds", processing.get("duration_seconds", summary.get("duration_seconds")))
                if optional_positive_candidate_float(duration_value, MAX_STORYBOARD_VIDEO_DURATION_SECONDS) is None:
                    return "voice_over_video requires duration_seconds"
                return None
            if audio_count:
                return f"{render_mode} local video take does not support audio inputs yet"
            image_count_value = summary.get("image_input_count", summary.get("source_image_count"))
            start_image_value = summary.get("start_image_count")
            end_image_value = summary.get("end_image_count")
            if render_mode == "one_image":
                if start_image_value is not None and coerce_input_order(start_image_value, 0) != 1:
                    return f"one_image requires exactly one start_image input; got {start_image_value}"
                if end_image_value is not None and coerce_input_order(end_image_value, 0) != 0:
                    return f"one_image does not support end_image inputs; got {end_image_value}"
                if image_count_value is not None and coerce_input_order(image_count_value, 0) < 1:
                    return "one_image requires at least one image input"
            elif render_mode == "two_image_fade":
                if start_image_value is not None and coerce_input_order(start_image_value, 0) != 1:
                    return f"two_image_fade requires exactly one start_image input; got {start_image_value}"
                if end_image_value is not None and coerce_input_order(end_image_value, 0) != 1:
                    return f"two_image_fade requires exactly one end_image input; got {end_image_value}"
                if image_count_value is not None and coerce_input_order(image_count_value, 0) < 2:
                    return "two_image_fade requires at least two image inputs"
        elif operation_type in STORYBOARD_SINGLE_VIDEO_OPERATION_TYPES and video_count < 1:
            return f"{operation_type} requires a video input"
        return None
    if processing_task and processing_task != "event_video_processing":
        return f"unsupported processing_task: {processing_task}"
    if processor_id and processor_id != "event_video_ffmpeg_processor":
        return f"unsupported processor_id: {processor_id}"
    return None
