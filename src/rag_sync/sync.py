from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rag_sync.config import DEFAULT_DATA_DIR, load_profiles
from rag_sync.db import RagSyncDb
from rag_sync.models import ParserMode, Profile, SourceState
from rag_sync.parsers import MarkerParser, MinerUParser, PassthroughParser
from rag_sync.quality import check_markdown_quality
from rag_sync.ragflow_client import RagFlowClient
from rag_sync.scanner import scan_profile, sha256_file


def _source_row(db: RagSyncDb, source_file_id: int) -> dict[str, Any]:
    for row in db.list_source_files():
        if int(row["id"]) == source_file_id:
            return row
    raise RuntimeError(f"Source file not found: {source_file_id}")


def _profile_by_name(profile_name: str) -> Profile:
    profiles = {profile.name: profile for profile in load_profiles()}
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
    return output_root / profile.name / parser_name / f"{_safe_stem(source_path)}.md"


def _parser_for_name(parser_name: str) -> MarkerParser | MinerUParser | PassthroughParser:
    parser_mode = ParserMode(parser_name)
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
            )
        )
    db.mark_missing_absent_paths(profile.name, seen_paths)
    return ids


def convert_source_file(
    db: RagSyncDb,
    source_file_id: int,
    parser_name: str | None = None,
) -> Path:
    row = _source_row(db, source_file_id)
    profile = _profile_by_name(str(row["profile_name"]))
    source_path = Path(str(row["source_path"]))
    chosen_parser = _chosen_parser_name(row, profile, parser_name)
    output_path = output_path_for(profile, source_path, chosen_parser)

    parser = _parser_for_name(chosen_parser)
    result = parser.convert(
        source_path=source_path,
        output_path=output_path,
        source_type=str(row["source_type"]),
        sha256=str(row["sha256"]),
    )
    math_heavy = str(row["source_type"]).lower() in {"book", "paper"}
    quality = check_markdown_quality(result.output_path, math_heavy=math_heavy)
    db.add_artifact(
        source_file_id=source_file_id,
        parser=result.parser,
        output_path=str(result.output_path),
        output_sha256=sha256_file(result.output_path),
        quality_status=quality.status,
        warnings_json=json.dumps(quality.warnings),
    )
    if quality.status == "blocked":
        raise RuntimeError(
            f"Conversion quality check blocked source file {source_file_id}: "
            f"{'; '.join(quality.warnings)}"
        )
    return result.output_path


def _required_id(payload: dict[str, Any], primary: str, fallback: str, kind: str) -> str:
    value = payload.get(primary) or payload.get(fallback)
    if value is None or not str(value).strip():
        raise RuntimeError(f"RAGFlow response missing {kind} id")
    return str(value)


async def upload_latest_artifact(
    db: RagSyncDb,
    source_file_id: int,
    client: RagFlowClient | None = None,
) -> dict[str, object]:
    row = _source_row(db, source_file_id)
    artifact = db.latest_artifact_for_source(source_file_id)
    if artifact is None:
        raise RuntimeError(f"No artifact found for source file {source_file_id}")

    profile = _profile_by_name(str(row["profile_name"]))
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
    with db.connect() as conn:
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
            ("parsed", source_file_id),
        )
        conn.execute(
            """
            UPDATE source_files
            SET state = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (SourceState.PARSED.value, source_file_id),
        )
    return response


def default_db(path: Path | None = None) -> RagSyncDb:
    db = RagSyncDb(path or DEFAULT_DATA_DIR / "rag-sync.sqlite")
    db.migrate()
    return db
