from __future__ import annotations

import datetime as _dt
import sys
from typing import Any

from .config import config_dir


def log_path():
    return config_dir() / "processor.log"


def _append_log_file(line: str) -> None:
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
            rotated = path.with_suffix(".log.1")
            try:
                rotated.unlink()
            except FileNotFoundError:
                pass
            path.rename(rotated)
        with path.open("a", encoding="utf-8") as writer:
            writer.write(line + "\n")
    except OSError:
        pass


def log(message: str, *, stream: Any = None) -> None:
    stream = stream or sys.stdout
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[midom-local-processor {timestamp}] {message}"
    print(line, file=stream, flush=True)
    _append_log_file(line)


def redact_token(value: str) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return "<redacted>"
    return f"{text[:4]}...{text[-4:]}"
