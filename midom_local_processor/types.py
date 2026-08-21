from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import USER_AGENT


@dataclass(frozen=True)
class WorkerConfig:
    api_base_url: str
    worker_id: int
    worker_token: str
    org_id: int
    project_id: int
    paired_user_id: int
    machine_name: str
    capabilities_revision: int = 1
    token_expires_at: str = ""
    allow_insecure_local_dev: bool = False
    allow_insecure_lan_dev: bool = False

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.worker_token}",
            "User-Agent": USER_AGENT,
        }


@dataclass(frozen=True)
class DownloadedInput:
    input_id: int
    kind: str
    path: Path
    mime_type: str
    sha256: str
    orientation: str = ""
    metadata: dict[str, Any] | None = None
    width: int | None = None
    height: int | None = None
    category: str = ""
    order: int = 0
    role: str = ""
    decoded_format: str = ""
    duration_seconds: float | None = None
    dbfileid: int | None = None
    dbfile_id: int | None = None
    file_id: int | None = None


@dataclass(frozen=True)
class ProcessingResult:
    path: Path
    metadata: dict[str, Any]
