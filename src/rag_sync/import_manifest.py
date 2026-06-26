from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag_sync.models import ImportManifest, ManifestFileRecord


def load_manifest(path: Path) -> ImportManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")

    batch_id = _require_str(payload, "batch_id")
    files_payload = payload.get("files")
    if not isinstance(files_payload, list):
        raise ValueError("files")

    files = tuple(_parse_file_record(item, index) for index, item in enumerate(files_payload))
    tags = _parse_str_list(payload.get("tags", []), "tags")
    parser_flags = _parse_str_list(payload.get("parser_flags", []), "parser_flags")

    return ImportManifest(
        batch_id=batch_id,
        created_at=_optional_str(payload.get("created_at"), "created_at"),
        host=_optional_str(payload.get("host"), "host"),
        profile=_optional_str(payload.get("profile"), "profile"),
        tags=tags,
        parser=_optional_str(payload.get("parser"), "parser"),
        parser_version=_optional_str(payload.get("parser_version"), "parser_version"),
        parser_flags=parser_flags,
        files=files,
    )


def _parse_file_record(item: Any, index: int) -> ManifestFileRecord:
    if not isinstance(item, dict):
        raise ValueError(f"files[{index}]")

    return ManifestFileRecord(
        source_relpath=_require_str(item, "source_relpath"),
        source_filename=_require_str(item, "source_filename"),
        source_abspath_cluster=_require_str(item, "source_abspath_cluster"),
        source_sha256=_require_str(item, "source_sha256"),
        source_size_bytes=_require_int(item, "source_size_bytes"),
        source_mtime=_require_float(item, "source_mtime"),
        page_count=_optional_int(item.get("page_count"), "page_count"),
        markdown_relpath=_require_str(item, "markdown_relpath"),
        markdown_sha256=_require_str(item, "markdown_sha256"),
        markdown_size_bytes=_require_int(item, "markdown_size_bytes"),
        status=_require_str(item, "status"),
        started_at=_optional_str(item.get("started_at"), "started_at"),
        finished_at=_optional_str(item.get("finished_at"), "finished_at"),
        duration_seconds=_optional_float(item.get("duration_seconds"), "duration_seconds"),
        returncode=_optional_int(item.get("returncode"), "returncode"),
        error_type=_optional_str(item.get("error_type"), "error_type"),
        error_message=_optional_str(item.get("error_message"), "error_message"),
    )


def _require_str(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(field)
    return value


def _require_int(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(field)
    return value


def _require_float(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(field)
    return float(value)


def _optional_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(field)
    return value


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(field)
    return value


def _optional_float(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(field)
    return float(value)


def _parse_str_list(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(field)
    return tuple(value)
