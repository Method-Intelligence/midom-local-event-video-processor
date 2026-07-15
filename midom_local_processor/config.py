from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .types import WorkerConfig


def config_dir() -> Path:
    override = os.environ.get("MIDOM_LOCAL_PROCESSOR_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "MidomLocalProcessor"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MidomLocalProcessor"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "midom-local-processor"


def config_path() -> Path:
    return config_dir() / "config.json"


def _required_int(data: dict[str, Any], key: str) -> int:
    try:
        value = int(data.get(key))
    except (TypeError, ValueError):
        raise ValueError(f"Config is missing a valid {key}.")
    if value <= 0:
        raise ValueError(f"Config is missing a valid {key}.")
    return value


def load_config(path: Path | None = None) -> WorkerConfig:
    path = path or config_path()
    if not path.exists():
        raise FileNotFoundError(f"No local processor config exists at {path}. Run pair first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a JSON object.")
    token = str(data.get("worker_token") or "").strip()
    api_base_url = str(data.get("api_base_url") or "").strip().rstrip("/")
    if not token or not api_base_url:
        raise ValueError("Config is missing api_base_url or worker_token.")
    return WorkerConfig(
        api_base_url=api_base_url,
        worker_id=_required_int(data, "worker_id"),
        worker_token=token,
        org_id=_required_int(data, "org_id"),
        project_id=_required_int(data, "project_id"),
        paired_user_id=_required_int(data, "paired_user_id"),
        machine_name=str(data.get("machine_name") or "").strip(),
        capabilities_revision=max(1, int(data.get("capabilities_revision") or 1)),
        token_expires_at=str(data.get("token_expires_at") or "").strip(),
        allow_insecure_local_dev=bool(data.get("allow_insecure_local_dev", False)),
        allow_insecure_lan_dev=bool(data.get("allow_insecure_lan_dev", False)),
    )


def save_config(config: WorkerConfig, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "api_base_url": config.api_base_url,
        "worker_id": int(config.worker_id),
        "worker_token": config.worker_token,
        "org_id": int(config.org_id),
        "project_id": int(config.project_id),
        "paired_user_id": int(config.paired_user_id),
        "machine_name": config.machine_name,
        "capabilities_revision": int(config.capabilities_revision),
        "token_expires_at": config.token_expires_at,
        "allow_insecure_local_dev": bool(config.allow_insecure_local_dev),
        "allow_insecure_lan_dev": bool(config.allow_insecure_lan_dev),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def delete_config(path: Path | None = None) -> None:
    path = path or config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
