from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag_sync.db import RagSyncDb
from rag_sync.models import ImportManifest, ManifestFileRecord
from rag_sync.models import ImportValidationStatus


def load_manifest(path: Path) -> ImportManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")

    batch_id = _require_str(payload, "batch_id")
    profile = _require_str(payload, "profile")
    parser = _require_str(payload, "parser")
    parser_version = _require_str(payload, "parser_version")
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
        profile=profile,
        tags=tags,
        parser=parser,
        parser_version=parser_version,
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


def classify_manifest_record(
    manifest_record: ManifestFileRecord,
    *,
    batch_dir: Path,
    local_source: Path | None,
    local_sha256: str | None,
) -> ImportValidationStatus:
    if manifest_record.status != "ok":
        return ImportValidationStatus.FAILED_REMOTE_CONVERSION
    if not (batch_dir / manifest_record.markdown_relpath).exists():
        return ImportValidationStatus.MISSING_MARKDOWN
    if local_source is None:
        return ImportValidationStatus.MISSING_SOURCE
    if local_sha256 != manifest_record.source_sha256:
        return ImportValidationStatus.HASH_MISMATCH
    return ImportValidationStatus.MATCH


def preview_manifest_batch(
    db: RagSyncDb,
    batch_dir: Path,
    *,
    selected_relpaths: list[str] | None = None,
) -> dict[str, object]:
    manifest = load_manifest(batch_dir / "manifest.json")
    selected = set(selected_relpaths or [])
    records = [
        record
        for record in manifest.files
        if not selected or record.source_relpath in selected
    ]
    summary_counts = {status.value: 0 for status in ImportValidationStatus}
    files: list[dict[str, object]] = []

    for record in records:
        source_row = db.find_source_file(
            profile_name=manifest.profile,
            source_path=record.source_relpath,
        )
        local_sha256 = str(source_row["sha256"]) if source_row is not None else None
        validation_status = classify_manifest_record(
            record,
            batch_dir=batch_dir,
            local_source=(
                Path(str(source_row["source_path"])) if source_row is not None else None
            ),
            local_sha256=local_sha256,
        )
        summary_counts[validation_status.value] += 1
        files.append(
            {
                "source_relpath": record.source_relpath,
                "source_filename": record.source_filename,
                "markdown_relpath": record.markdown_relpath,
                "status": record.status,
                "validation_status": validation_status.value,
                "local_source_sha256": local_sha256,
                "manifest_source_sha256": record.source_sha256,
            }
        )

    return {
        "batch_id": manifest.batch_id,
        "profile": manifest.profile,
        "parser": manifest.parser,
        "parser_version": manifest.parser_version,
        "files": files,
        "summary": {
            "total": len(records),
            "importable": summary_counts[ImportValidationStatus.MATCH.value],
            **summary_counts,
        },
    }


def import_manifest_batch(
    db: RagSyncDb,
    batch_dir: Path,
    *,
    force: bool = False,
    reason: str = "",
    selected_relpaths: list[str] | None = None,
) -> dict[str, int | str]:
    if force and not reason.strip():
        raise ValueError("force import requires an override reason")

    manifest_path = batch_dir / "manifest.json"
    manifest = load_manifest(manifest_path)
    batch_import_id = db.create_import_batch(
        batch_id=manifest.batch_id,
        manifest_path=str(manifest_path),
        profile_name=manifest.profile,
        parser=manifest.parser,
        parser_version=manifest.parser_version,
    )

    selected = set(selected_relpaths or [])
    records = [
        record
        for record in manifest.files
        if not selected or record.source_relpath in selected
    ]

    imported = 0
    for record in records:
        source_row = db.find_source_file(
            profile_name=manifest.profile,
            source_path=record.source_relpath,
        )
        local_source = Path(str(source_row["source_path"])) if source_row is not None else None
        local_sha256 = str(source_row["sha256"]) if source_row is not None else None
        validation_status = classify_manifest_record(
            record,
            batch_dir=batch_dir,
            local_source=local_source,
            local_sha256=local_sha256,
        )

        imported_flag = 0
        if source_row is not None and (
            validation_status is ImportValidationStatus.MATCH or force
        ):
            markdown_path = batch_dir / record.markdown_relpath
            db.add_artifact(
                source_file_id=int(source_row["id"]),
                parser=manifest.parser,
                output_path=str(markdown_path),
                output_sha256=record.markdown_sha256,
                quality_status="ok",
                warnings_json="[]",
            )
            imported += 1
            imported_flag = 1

        db.record_import_decision(
            batch_import_id=batch_import_id,
            source_file_id=int(source_row["id"]) if source_row is not None else None,
            source_relpath=record.source_relpath,
            manifest_source_sha256=record.source_sha256,
            local_source_sha256=local_sha256 or "",
            markdown_path=str(batch_dir / record.markdown_relpath),
            markdown_sha256=record.markdown_sha256,
            validation_status=validation_status,
            import_mode=(
                "force"
                if force and validation_status is not ImportValidationStatus.MATCH
                else "strict"
            ),
            override_reason=(
                reason if force and validation_status is not ImportValidationStatus.MATCH else ""
            ),
            imported=imported_flag,
        )

    return {
        "batch_id": manifest.batch_id,
        "files": len(records),
        "imported": imported,
    }
