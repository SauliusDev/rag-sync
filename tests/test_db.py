import sqlite3
from pathlib import Path

import pytest

from rag_sync.db import RagSyncDb
from rag_sync.models import SourceState


def test_db_upserts_source_file(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    file_id = db.upsert_source_file(
        profile_name="quant-articles",
        source_path="/tmp/a.md",
        source_type="article",
        extension="md",
        sha256="abc",
        size_bytes=12,
        mtime=1.0,
        state=SourceState.NEW,
    )

    rows = db.list_source_files()

    assert len(rows) == 1
    assert rows[0]["id"] == file_id
    assert rows[0]["source_path"] == "/tmp/a.md"
    assert rows[0]["state"] == "new"


def test_db_marks_changed_when_hash_changes(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    db.upsert_source_file("p", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW)

    db.upsert_source_file("p", "/tmp/a.md", "article", "md", "def", 13, 2.0, SourceState.CHANGED)

    row = db.list_source_files()[0]
    assert row["sha256"] == "def"
    assert row["state"] == "changed"


def test_migrate_is_idempotent(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")

    db.migrate()
    db.migrate()

    assert db.list_source_files() == []


def test_foreign_keys_reject_artifact_with_invalid_source_file_id(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    with pytest.raises(sqlite3.IntegrityError), db.connect() as conn:
        conn.execute(
            """
                INSERT INTO artifacts (
                  source_file_id, parser, output_path, output_sha256, quality_status
                ) VALUES (?, ?, ?, ?, ?)
                """,
            (999, "marker", "/tmp/out.md", "abc", "ok"),
        )


def test_same_source_path_can_exist_in_two_profiles(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()

    first_id = db.upsert_source_file(
        "profile-a", "/tmp/shared.md", "article", "md", "abc", 12, 1.0, SourceState.NEW
    )
    second_id = db.upsert_source_file(
        "profile-b", "/tmp/shared.md", "article", "md", "def", 13, 2.0, SourceState.NEW
    )

    rows = db.list_source_files()

    assert first_id != second_id
    assert len(rows) == 2
    assert {row["profile_name"] for row in rows} == {"profile-a", "profile-b"}


def test_upsert_preserves_curation_fields_on_rescan(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    file_id = db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW
    )
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE source_files
            SET included = 0, priority = 10, tags = 'quant,review', note = 'keep'
            WHERE id = ?
            """,
            (file_id,),
        )

    updated_id = db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "def", 13, 2.0, SourceState.CHANGED
    )

    row = db.list_source_files()[0]
    assert updated_id == file_id
    assert row["sha256"] == "def"
    assert row["included"] == 0
    assert row["priority"] == 10
    assert row["tags"] == "quant,review"
    assert row["note"] == "keep"


def test_existing_hashes_returns_paths_for_profile_only(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW
    )
    db.upsert_source_file(
        "profile-b", "/tmp/b.md", "article", "md", "def", 13, 2.0, SourceState.NEW
    )

    hashes = db.existing_hashes("profile-a")

    assert hashes == {"/tmp/a.md": "abc"}


def test_mark_missing_absent_paths_commits_missing_state(project_tmp: Path):
    db = RagSyncDb(project_tmp / "state.sqlite")
    db.migrate()
    db.upsert_source_file(
        "profile-a", "/tmp/a.md", "article", "md", "abc", 12, 1.0, SourceState.NEW
    )
    db.upsert_source_file(
        "profile-a", "/tmp/b.md", "article", "md", "def", 13, 2.0, SourceState.NEW
    )
    db.upsert_source_file(
        "profile-b", "/tmp/c.md", "article", "md", "ghi", 14, 3.0, SourceState.NEW
    )

    db.mark_missing_absent_paths("profile-a", {"/tmp/a.md"})

    rows = {row["source_path"]: row["state"] for row in db.list_source_files()}
    assert rows["/tmp/a.md"] == "new"
    assert rows["/tmp/b.md"] == "missing"
    assert rows["/tmp/c.md"] == "new"
