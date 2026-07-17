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
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_EVENT_VIDEO_INPUT_BYTES,
    MAX_IMAGE_BYTES,
    MIME_EXTENSION,
    POLL_INTERVAL_SECONDS,
)
from .ffmpeg_probe import probe_ffmpeg
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
    validate_event_job,
    validate_job_scope,
    validate_source_video,
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
        log("Waiting for Event Video Processing jobs.")
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
            message = f"Processing Event video job {self.active_job_id}. Accepting additional queued jobs."
        else:
            status = "idle"
            accepting = True
            active_job_id = None
            message = "Idle and accepting Event Video Processing jobs."
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
        log(f"Starting claimed Event Video Processing job; job_id={job_id}.")
        try:
            validate_job_scope(job, org_id=self.config.org_id, project_id=self.config.project_id, paired_user_id=self.config.paired_user_id)
            processing = validate_event_job(job)
            max_output_duration_seconds = event_job_max_output_duration_seconds(job)
            self.api.job_update(job_id, "progress", {"phase": "starting", "status": "Preparing local Event Video Processing.", "progress": 0})
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
                    self.api.job_update(job_id, "fail", {"reason": "cancel_requested", "message": "Event Video Processing was cancelled."})
                    return
                artifact = self.api.upload_video_artifact(job_id, result.path, artifact_index=0)
                complete_payload = {
                    "artifacts": [artifact],
                    "backend": "ffmpeg",
                    "model_id": "event_video_ffmpeg_processor",
                    "processor_id": "event_video_ffmpeg_processor",
                    "processing_metadata": result.metadata,
                }
                log(
                    "Completing Event Video Processing job; "
                    f"job_id={job_id} artifact={artifact} "
                    f"output={result.metadata.get('output_width')}x{result.metadata.get('output_height')} "
                    f"duration_seconds={result.metadata.get('output_duration_seconds')}."
                )
                self.api.job_update(job_id, "complete", complete_payload)
                log(f"Event Video Processing complete accepted by Midom; job_id={job_id}.")
        except RuntimeError as exc:
            if str(exc) == "cancel_requested":
                try:
                    self.api.job_update(job_id, "fail", {"reason": "cancel_requested", "message": "Event Video Processing was cancelled."})
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


def safe_input_filename(filename: Any, input_id: int, mime_type: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"}:
        suffix = MIME_EXTENSION.get(mime_type, ".bin")
    return f"input-{int(input_id)}{suffix}"


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
    family = str(candidate.get("family") or summary.get("family") or "").strip().lower()
    processing_task = str(candidate.get("processing_task") or summary.get("processing_task") or "").strip().lower()
    processor_id = str(candidate.get("processor_id") or candidate.get("model_id") or summary.get("processor_id") or "").strip()
    if family and family != "media_processing":
        return f"unsupported family: {family}"
    if processing_task and processing_task != "event_video_processing":
        return f"unsupported processing_task: {processing_task}"
    if processor_id and processor_id != "event_video_ffmpeg_processor":
        return f"unsupported processor_id: {processor_id}"
    return None
