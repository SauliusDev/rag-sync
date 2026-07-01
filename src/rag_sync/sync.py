from __future__ import annotations

import inspect
import json
import re
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from rag_sync.config import DEFAULT_DATA_DIR, DEFAULT_PROFILE_PATH, load_profiles
from rag_sync.db import RagSyncDb
from rag_sync.glm_ocr import GLM_OCR_MODEL, glm_ocr_raw_dir
from rag_sync.ldd import log_event
from rag_sync.models import ParserMode, Profile, SourceState
from rag_sync.parsers import GlmOcrParser, MarkerParser, MinerUParser, PassthroughParser
from rag_sync.quality import check_markdown_quality
from rag_sync.ragflow_client import RagFlowClient
from rag_sync.scanner import scan_profile, sha256_file


def _source_row(db: RagSyncDb, source_file_id: int) -> dict[str, Any]:
    for row in db.list_source_files():
        if int(row["id"]) == source_file_id:
            return row
    raise RuntimeError(f"Source file not found: {source_file_id}")


def _profile_by_name(profile_name: str, profile_path: Path = DEFAULT_PROFILE_PATH) -> Profile:
    profiles = {profile.name: profile for profile in load_profiles(profile_path)}
    profile = profiles.get(profile_name)
    if profile is None:
        raise RuntimeError(f"Profile not found for source file: {profile_name}")
    return profile


def _safe_stem(source_path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_path.stem)
    stem = re.sub(r"_+", "_", stem).strip("._-")
    return stem or "source"


def output_path_for(profile: Profile, source_path: Path, parser_name: str) -> Path:
    output_root = profile.output_dir or DEFAULT_DATA_DIR / "outputs"
    path_hash = sha256(str(source_path).encode("utf-8")).hexdigest()[:12]
    return output_root / profile.name / parser_name / f"{_safe_stem(source_path)}-{path_hash}.md"


def _parser_for_name(
    parser_name: str,
) -> GlmOcrParser | MarkerParser | MinerUParser | PassthroughParser:
    parser_mode = ParserMode(parser_name)
    if parser_mode is ParserMode.GLM_OCR:
        return GlmOcrParser()
    if parser_mode is ParserMode.MARKER:
        return MarkerParser()
    if parser_mode is ParserMode.MINERU:
        return MinerUParser()
    return PassthroughParser()


def _chosen_parser_name(row: dict[str, Any], profile: Profile, parser_name: str | None) -> str:
    if parser_name is not None:
        try:
            return ParserMode(parser_name).value
        except ValueError as exc:
            raise RuntimeError(
                f"Unknown parser: {parser_name}. Expected one of "
                f"{', '.join(mode.value for mode in ParserMode)}"
            ) from exc
    if str(row["extension"]).lower().lstrip(".") == "md":
        return ParserMode.PASSTHROUGH.value
    return profile.parser_mode.value


def _supports_marker_fallback(row: dict[str, Any], parser_name: str) -> bool:
    if parser_name != ParserMode.MARKER.value:
        return False
    if str(row["extension"]).lower().lstrip(".") != "pdf":
        return False
    return str(row["source_type"]).lower() in {"book", "paper"}


def _convert_with_parser(
    db: RagSyncDb,
    row: dict[str, Any],
    source_path: Path,
    parser_name: str,
    profile: Profile,
) -> Path:
    output_path = output_path_for(profile, source_path, parser_name)
    parser = _parser_for_name(parser_name)
    log_event(
        "conversion.started",
        "ok",
        source_file_id=int(row["id"]),
        profile_name=str(row["profile_name"]),
        source_type=str(row["source_type"]),
        source_path=str(source_path),
        parser=parser_name,
        output_path=str(output_path),
    )
    try:
        convert_kwargs = {
            "source_path": source_path,
            "output_path": output_path,
            "source_type": str(row["source_type"]),
            "sha256": str(row["sha256"]),
        }
        if "pdf_producer" in inspect.signature(parser.convert).parameters:
            convert_kwargs["pdf_producer"] = str(row.get("pdf_producer", ""))
        result = parser.convert(
            **convert_kwargs,
        )
    except Exception as exc:
        log_event(
            "conversion.failed",
            "error",
            source_file_id=int(row["id"]),
            profile_name=str(row["profile_name"]),
            source_type=str(row["source_type"]),
            source_path=str(source_path),
            parser=parser_name,
            output_path=str(output_path),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise
    math_heavy = str(row["source_type"]).lower() in {"book", "paper"}
    quality = check_markdown_quality(
        result.output_path,
        math_heavy=math_heavy,
        page_count=int(row["page_count"]) if row.get("page_count") is not None else None,
    )
    output_bytes = result.output_path.stat().st_size if result.output_path.exists() else 0
    log_event(
        "conversion.quality.checked",
        "error" if quality.status == "blocked" else "ok",
        source_file_id=int(row["id"]),
        profile_name=str(row["profile_name"]),
        source_type=str(row["source_type"]),
        source_path=str(source_path),
        parser=result.parser,
        output_path=str(result.output_path),
        output_bytes=output_bytes,
        quality_status=quality.status,
        warnings=quality.warnings,
    )
    db.add_artifact(
        source_file_id=int(row["id"]),
        parser=result.parser,
        output_path=str(result.output_path),
        output_sha256=sha256_file(result.output_path),
        quality_status=quality.status,
        warnings_json=json.dumps(quality.warnings),
    )
    _record_conversion_usage(db, row, result.parser, result.output_path)
    if quality.status == "blocked":
        db.update_source_state(int(row["id"]), SourceState.FAILED)
        error = (
            f"Conversion quality check blocked source file {row['id']}: "
            f"{'; '.join(quality.warnings)}"
        )
        log_event(
            "conversion.failed",
            "error",
            source_file_id=int(row["id"]),
            profile_name=str(row["profile_name"]),
            source_type=str(row["source_type"]),
            source_path=str(source_path),
            parser=result.parser,
            output_path=str(result.output_path),
            output_bytes=output_bytes,
            quality_status=quality.status,
            warnings=quality.warnings,
            error_type="RuntimeError",
            error=error,
        )
        raise RuntimeError(error)
    log_event(
        "conversion.completed",
        "ok",
        source_file_id=int(row["id"]),
        profile_name=str(row["profile_name"]),
        source_type=str(row["source_type"]),
        source_path=str(source_path),
        parser=result.parser,
        output_path=str(result.output_path),
        output_bytes=output_bytes,
        quality_status=quality.status,
        warnings=quality.warnings,
    )
    return result.output_path


def _record_conversion_usage(
    db: RagSyncDb,
    row: dict[str, Any],
    parser_name: str,
    output_path: Path,
) -> None:
    if parser_name != ParserMode.GLM_OCR.value:
        return
    manifest_path = glm_ocr_raw_dir(output_path) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tokens = int(manifest.get("total_tokens") or 0)
        cost_usd = float(manifest.get("estimated_cost_usd") or 0)
    except Exception as exc:
        log_event(
            "usage.record.failed",
            "error",
            source_file_id=int(row["id"]),
            provider="z-ai",
            service="glm-ocr",
            model=GLM_OCR_MODEL,
            output_path=str(output_path),
            manifest_path=str(manifest_path),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return
    db.record_usage_event(
        provider="z-ai",
        service="glm-ocr",
        model=GLM_OCR_MODEL,
        source_file_id=int(row["id"]),
        tokens=tokens,
        cost_usd=cost_usd,
        metadata={
            "profile_name": str(row["profile_name"]),
            "source_type": str(row["source_type"]),
            "source_path": str(row["source_path"]),
            "output_path": str(output_path),
            "page_count": manifest.get("page_count"),
        },
    )
    log_event(
        "usage.recorded",
        "ok",
        source_file_id=int(row["id"]),
        provider="z-ai",
        service="glm-ocr",
        model=GLM_OCR_MODEL,
        tokens=tokens,
        cost_usd=cost_usd,
        output_path=str(output_path),
    )


def persist_scan(db: RagSyncDb, profile: Profile) -> list[int]:
    existing = db.existing_hashes(profile.name)
    results = scan_profile(profile, existing_hashes=existing)
    seen_paths = {str(result.source_path) for result in results}
    ids: list[int] = []
    for result in results:
        ids.append(
            db.upsert_source_file(
                profile_name=result.profile_name,
                source_path=str(result.source_path),
                source_type=result.source_type,
                extension=result.extension,
                sha256=result.sha256,
                size_bytes=result.size_bytes,
                mtime=result.mtime,
                state=SourceState(result.state),
                page_count=result.page_count,
                pdf_producer=result.pdf_producer,
            )
        )
    db.mark_missing_absent_paths(profile.name, seen_paths)
    return ids


def convert_source_file(
    db: RagSyncDb,
    source_file_id: int,
    parser_name: str | None = None,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> Path:
    row = _source_row(db, source_file_id)
    profile = _profile_by_name(str(row["profile_name"]), profile_path)
    source_path = Path(str(row["source_path"]))
    chosen_parser = _chosen_parser_name(row, profile, parser_name)
    try:
        return _convert_with_parser(db, row, source_path, chosen_parser, profile)
    except RuntimeError as exc:
        if parser_name is not None or not _supports_marker_fallback(row, chosen_parser):
            raise
        log_event(
            "conversion.fallback.started",
            "ok",
            source_file_id=int(row["id"]),
            profile_name=str(row["profile_name"]),
            source_type=str(row["source_type"]),
            source_path=str(source_path),
            from_parser=chosen_parser,
            to_parser=ParserMode.MINERU.value,
            reason=str(exc),
        )
    try:
        output_path = _convert_with_parser(db, row, source_path, ParserMode.MINERU.value, profile)
    except Exception as fallback_exc:
        log_event(
            "conversion.fallback.failed",
            "error",
            source_file_id=int(row["id"]),
            profile_name=str(row["profile_name"]),
            source_type=str(row["source_type"]),
            source_path=str(source_path),
            from_parser=chosen_parser,
            to_parser=ParserMode.MINERU.value,
            error_type=type(fallback_exc).__name__,
            error=str(fallback_exc),
        )
        raise
    log_event(
        "conversion.fallback.completed",
        "ok",
        source_file_id=int(row["id"]),
        profile_name=str(row["profile_name"]),
        source_type=str(row["source_type"]),
        source_path=str(source_path),
        from_parser=chosen_parser,
        to_parser=ParserMode.MINERU.value,
        output_path=str(output_path),
    )
    return output_path


def _required_id(payload: dict[str, Any], primary: str, fallback: str, kind: str) -> str:
    value = payload.get(primary) or payload.get(fallback)
    if value is None or not str(value).strip():
        raise RuntimeError(f"RAGFlow response missing {kind} id")
    return str(value)


def _ragflow_parse_status(document: Mapping[str, Any]) -> str:
    run = str(document.get("run") or "").strip().upper()
    status = str(document.get("status") or "").strip().upper()
    progress = document.get("progress")
    try:
        progress_value = float(progress) if progress is not None else None
    except (TypeError, ValueError):
        progress_value = None

    if run == "DONE" or status == "1" or progress_value == 1.0:
        return "parsed"
    if run in {"CANCEL", "CANCELED", "CANCELLED", "STOP", "STOPPED"}:
        return "stopped"
    if run in {"FAIL", "FAILED", "ERROR"} or status in {"-1", "ERROR"}:
        return "failed"
    if run:
        return "parsing"
    return "not_started"


async def refresh_ragflow_documents(
    db: RagSyncDb,
    client: RagFlowClient | None = None,
) -> int:
    with db.session() as conn:
        rows = conn.execute(
            """
            SELECT source_file_id, dataset_id, dataset_name, document_id, parse_status
            FROM ragflow_documents
            WHERE upload_status = 'uploaded'
            """
        ).fetchall()

    if not rows:
        return 0

    try:
        client = client or RagFlowClient()
    except RuntimeError as exc:
        log_event(
            "ragflow.refresh.skipped",
            "warning",
            reason=str(exc),
        )
        return 0
    by_dataset: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_dataset.setdefault(str(row["dataset_id"]), []).append(dict(row))

    refreshed = 0
    for dataset_id, dataset_rows in by_dataset.items():
        log_event(
            "ragflow.refresh.started",
            "ok",
            dataset_id=dataset_id,
            document_count=len(dataset_rows),
        )
        try:
            remote_documents = await client.list_documents(dataset_id)
        except Exception as exc:
            log_event(
                "ragflow.refresh.failed",
                "error",
                dataset_id=dataset_id,
                document_count=len(dataset_rows),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            continue
        remote_by_id = {str(document.get("id")): document for document in remote_documents}
        dataset_refreshed = 0
        for row in dataset_rows:
            source_file_id = int(row["source_file_id"])
            document_id = str(row["document_id"])
            remote = remote_by_id.get(document_id)
            if remote is None:
                continue
            parse_status = _ragflow_parse_status(remote)
            chunk_count = remote.get("chunk_count")
            token_count = remote.get("token_count")
            document_name = str(
                remote.get("name") or remote.get("location") or document_id
            )
            db.upsert_ragflow_document(
                source_file_id=source_file_id,
                dataset_id=dataset_id,
                dataset_name=str(remote.get("dataset_name") or row["dataset_name"] or ""),
                document_id=document_id,
                document_name=document_name,
                upload_status="uploaded",
                parse_status=parse_status,
                chunk_count=int(chunk_count) if chunk_count is not None else None,
                token_count=int(token_count) if token_count is not None else None,
            )
            if parse_status == "parsed":
                db.update_source_state(source_file_id, SourceState.PARSED)
            elif parse_status in {"parsing", "not_started"}:
                db.update_source_state(source_file_id, SourceState.UPLOADED)
            dataset_refreshed += 1
        refreshed += dataset_refreshed
        log_event(
            "ragflow.refresh.completed",
            "ok",
            dataset_id=dataset_id,
            document_count=len(dataset_rows),
            refreshed_count=dataset_refreshed,
        )
    return refreshed


async def upload_latest_artifact(
    db: RagSyncDb,
    source_file_id: int,
    client: RagFlowClient | None = None,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> dict[str, object]:
    row = _source_row(db, source_file_id)
    artifact = db.latest_artifact_for_source(source_file_id)
    if artifact is None:
        raise RuntimeError(f"No artifact found for source file {source_file_id}")
    if str(artifact["quality_status"]) == "blocked":
        raise RuntimeError(f"Latest artifact is blocked for source file {source_file_id}")

    profile = _profile_by_name(str(row["profile_name"]), profile_path)
    client = client or RagFlowClient()
    dataset = await client.ensure_dataset(profile.target_dataset)
    dataset_id = _required_id(dataset, "id", "dataset_id", "dataset")
    uploaded = await client.upload_document(dataset_id, Path(str(artifact["output_path"])))
    document_id = _required_id(uploaded, "id", "document_id", "document")
    document_name = str(
        uploaded.get("name")
        or uploaded.get("display_name")
        or Path(str(artifact["output_path"])).name
    )
    dataset_name = str(dataset.get("name") or profile.target_dataset)

    db.upsert_ragflow_document(
        source_file_id=source_file_id,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        document_id=document_id,
        document_name=document_name,
        upload_status="uploaded",
        parse_status="not_started",
    )
    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "document_name": document_name,
    }


async def parse_uploaded_document(
    db: RagSyncDb,
    source_file_id: int,
    client: RagFlowClient | None = None,
) -> dict[str, object]:
    with db.session() as conn:
        row = conn.execute(
            "SELECT * FROM ragflow_documents WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()
    if row is None or str(row["upload_status"]) != "uploaded":
        raise RuntimeError(f"No uploaded document found for source file {source_file_id}")

    client = client or RagFlowClient()
    response = await client.parse_documents(str(row["dataset_id"]), [str(row["document_id"])])
    with db.session() as conn:
        conn.execute(
            """
            UPDATE ragflow_documents
            SET parse_status = ?, last_synced_at = CURRENT_TIMESTAMP
            WHERE source_file_id = ?
            """,
            ("parsing", source_file_id),
        )
    return response


async def delete_ragflow_document(
    db: RagSyncDb,
    source_file_id: int,
    client: RagFlowClient | None = None,
) -> dict[str, object]:
    with db.session() as conn:
        row = conn.execute(
            "SELECT * FROM ragflow_documents WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"No RAGFlow document found for source file {source_file_id}")

    client = client or RagFlowClient()
    dataset_id = str(row["dataset_id"])
    document_id = str(row["document_id"])
    await client.delete_documents(dataset_id, [document_id])
    db.clear_ragflow_document(source_file_id)
    return {"dataset_id": dataset_id, "document_id": document_id}


async def restart_ragflow_document(
    db: RagSyncDb,
    source_file_id: int,
    client: RagFlowClient | None = None,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> dict[str, object]:
    client = client or RagFlowClient()
    with db.session() as conn:
        row = conn.execute(
            "SELECT * FROM ragflow_documents WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()
    if row is not None:
        await client.stop_documents(str(row["dataset_id"]), [str(row["document_id"])])
        await delete_ragflow_document(db, source_file_id, client)
    uploaded = await upload_latest_artifact(
        db, source_file_id, client=client, profile_path=profile_path
    )
    await parse_uploaded_document(db, source_file_id, client=client)
    return uploaded


def default_db(path: Path | None = None) -> RagSyncDb:
    db = RagSyncDb(path or DEFAULT_DATA_DIR / "rag-sync.sqlite")
    db.migrate()
    return db
