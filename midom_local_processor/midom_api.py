from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any

import requests

from .constants import REQUEST_TIMEOUT_SECONDS, UPLOAD_TIMEOUT_SECONDS, USER_AGENT
from .log import log
from .types import WorkerConfig


class WorkerAuthorizationError(RuntimeError):
    """Raised when Midom rejects the stored worker token."""


class MidomMethodNotAllowedError(RuntimeError):
    """Raised when a Midom deployment lacks the expected worker route/method."""


def midom_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("message", "detail", "error", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        detail = payload.get("detail")
        if isinstance(detail, list) and detail:
            return str(detail[0])
    text = str(response.text or "").strip()
    return text[:300] if text else response.reason


def raise_for_midom_error(response: requests.Response, action: str) -> None:
    if response.status_code < 400:
        return
    message = midom_error_message(response)
    if response.status_code in {401, 403}:
        raise WorkerAuthorizationError(
            f"{action} failed: Midom rejected the stored worker token with HTTP {response.status_code}. "
            "The worker token may be expired, revoked, or no longer allowed for this project. "
            "Disconnect locally if needed, then generate a fresh Midom pairing code and run pair again. "
            f"Midom said: {message}"
        )
    if response.status_code == 405:
        raise MidomMethodNotAllowedError(f"{action} failed: HTTP 405 Method Not Allowed. Midom said: {message}")
    raise RuntimeError(f"{action} failed: HTTP {response.status_code}. {message}")


class MidomApi:
    def __init__(self, config: WorkerConfig):
        self.config = config

    def post_capabilities(self, capabilities: dict[str, Any]) -> WorkerConfig:
        response = requests.post(
            f"{self.config.api_base_url}/b1/media-workers/{self.config.worker_id}/capabilities",
            headers={**self.config.headers(), "Content-Type": "application/json"},
            json=capabilities,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        raise_for_midom_error(response, "Capabilities update")
        payload = response.json() if response.content else {}
        revision = int(payload.get("capabilities_revision") or self.config.capabilities_revision) if isinstance(payload, dict) else self.config.capabilities_revision
        return WorkerConfig(**{**self.config.__dict__, "capabilities_revision": max(1, revision)})

    def heartbeat(self, *, status: str, accepting: bool, active_job_id: int | None, message: str) -> dict[str, Any]:
        response = requests.post(
            f"{self.config.api_base_url}/b1/media-workers/{self.config.worker_id}/heartbeat",
            headers={**self.config.headers(), "Content-Type": "application/json"},
            json={
                "status": status,
                "accepting": bool(accepting),
                "active_job_id": active_job_id,
                "message": message,
                "capabilities_revision": self.config.capabilities_revision,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        raise_for_midom_error(response, "Heartbeat")
        return response.json() if response.content else {}

    def candidates(self, limit: int = 5) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.config.api_base_url}/b1/media-workers/{self.config.worker_id}/jobs/candidates",
            headers=self.config.headers(),
            params={"limit": int(limit)},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        raise_for_midom_error(response, "Candidate poll")
        payload = response.json()
        jobs = payload.get("jobs") if isinstance(payload, dict) else []
        return jobs if isinstance(jobs, list) else []

    def claim(self, job_id: int) -> dict[str, Any] | None:
        response = requests.post(
            f"{self.config.api_base_url}/b1/media-workers/{self.config.worker_id}/jobs/{int(job_id)}/claim",
            headers={**self.config.headers(), "Content-Type": "application/json"},
            json={"capabilities_revision": self.config.capabilities_revision},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 409:
            return None
        raise_for_midom_error(response, "Claim")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Claim response must be a JSON object.")
        return payload

    def download_input(self, job_id: int, input_id: int) -> requests.Response:
        response = requests.get(
            f"{self.config.api_base_url}/b1/media-workers/{self.config.worker_id}/jobs/{int(job_id)}/inputs/{int(input_id)}",
            headers=self.config.headers(),
            timeout=REQUEST_TIMEOUT_SECONDS,
            stream=True,
        )
        raise_for_midom_error(response, "Input download")
        return response

    def job_update(self, job_id: int, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        if route not in {"progress", "complete", "fail"}:
            raise ValueError(f"Unsupported job update route: {route}")
        response = requests.post(
            f"{self.config.api_base_url}/b1/media-workers/{self.config.worker_id}/jobs/{int(job_id)}/{route}",
            headers={**self.config.headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        raise_for_midom_error(response, f"Job {route} update")
        return response.json() if response.content else {}

    def upload_video_artifact(self, job_id: int, path: Path, artifact_index: int = 0) -> dict[str, Any]:
        path = Path(path)
        if not path.is_file():
            raise ValueError("Video artifact path does not exist.")
        mime_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
        if mime_type != "video/mp4":
            mime_type = "video/mp4"
        data = path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        log(f"Uploading processed MP4 artifact; job_id={job_id} bytes={len(data)} sha256={sha256[:12]}...")
        with path.open("rb") as reader:
            response = requests.post(
                f"{self.config.api_base_url}/b1/media-workers/{self.config.worker_id}/jobs/{int(job_id)}/artifacts",
                headers=self.config.headers(),
                data={
                    "artifact_index": str(int(artifact_index)),
                    "sha256": sha256,
                    "mime_type": "video/mp4",
                    "filename": path.name,
                },
                files={"file": (path.name, reader, "video/mp4")},
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
        raise_for_midom_error(response, "Artifact upload")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Artifact upload response must be a JSON object.")
        artifact = {
            "artifact_id": int(payload.get("artifact_id")),
            "file_id": int(payload.get("file_id")),
            "artifact_index": int(payload.get("artifact_index", artifact_index)),
        }
        log(
            "Processed MP4 artifact upload accepted; "
            f"job_id={job_id} artifact_id={artifact['artifact_id']} file_id={artifact['file_id']} "
            f"artifact_index={artifact['artifact_index']}."
        )
        return artifact

    def disconnect(self, reason: str = "local_processor_disconnect") -> None:
        response = requests.post(
            f"{self.config.api_base_url}/b1/media-workers/{self.config.worker_id}/disconnect",
            headers={**self.config.headers(), "Content-Type": "application/json"},
            json={"reason": reason, "active_job_policy": "cancel"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        raise_for_midom_error(response, "Disconnect")


def pair_worker(
    api_base_url: str,
    pairing_code: str,
    machine_name: str,
    capabilities: dict[str, Any],
    *,
    allow_local_http: bool,
    allow_lan_http: bool,
) -> WorkerConfig:
    response = requests.post(
        f"{api_base_url}/b1/media-workers/pair",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        json={
            "pairing_code": pairing_code.strip(),
            "plugin_id": "midom-local-media-processor",
            "machine_name": machine_name.strip(),
            "capabilities": capabilities,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Pairing failed: HTTP {response.status_code}. Pairing codes are short-lived and one-time-use. "
            f"Generate a fresh Midom pairing code and try again. Midom said: {midom_error_message(response)}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Pairing response must be a JSON object.")
    return WorkerConfig(
        api_base_url=api_base_url,
        worker_id=int(payload["worker_id"]),
        worker_token=str(payload["worker_token"]),
        org_id=int(payload["org_id"]),
        project_id=int(payload["project_id"]),
        paired_user_id=int(payload["paired_user_id"]),
        machine_name=str(payload.get("machine_name") or machine_name),
        capabilities_revision=max(1, int(payload.get("capabilities_revision") or 1)),
        token_expires_at=str(payload.get("token_expires_at") or ""),
        allow_insecure_local_dev=bool(allow_local_http),
        allow_insecure_lan_dev=bool(allow_lan_http),
    )
