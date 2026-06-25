from __future__ import annotations

from pathlib import Path

from rag_sync.config import DEFAULT_DATA_DIR
from rag_sync.db import RagSyncDb
from rag_sync.models import Profile, SourceState
from rag_sync.scanner import scan_profile


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


def default_db(path: Path | None = None) -> RagSyncDb:
    db = RagSyncDb(path or DEFAULT_DATA_DIR / "rag-sync.sqlite")
    db.migrate()
    return db
