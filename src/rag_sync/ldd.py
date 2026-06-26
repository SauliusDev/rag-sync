from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from rag_sync.config import DEFAULT_DATA_DIR

_log_path_for_tests: Path | None = None
_log_lock = Lock()


def _log_path() -> Path:
    if _log_path_for_tests is not None:
        return _log_path_for_tests
    configured = os.environ.get("RAG_SYNC_LOG_PATH")
    if configured:
        return Path(configured)
    return DEFAULT_DATA_DIR / "rag-sync.log"


def set_log_path_for_tests(path: Path | None) -> None:
    global _log_path_for_tests
    _log_path_for_tests = path


def _json_default(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def log_event_to_path(path: Path, event: str, status: str, **fields: Any) -> None:
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "event": event,
        "status": status,
        **fields,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default)
    with _log_lock, path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def log_event(event: str, status: str, **fields: Any) -> None:
    log_event_to_path(_log_path(), event, status, **fields)
